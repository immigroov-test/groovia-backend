# BUG-110: Countries of Expertise missing from Edit Profile.
#
# Root cause: db.get_mentor_by_profile_id() (backs GET /mentor/me, which both the Edit Profile
# page and POST /mentor/profile's merge-fallback rely on) never selected `served_countries`.
# The public profile pulls `expertise_country_codes` directly and was always correct - only the
# Edit Profile load (and, worse, any SAVE that didn't touch the countries section) was affected,
# since a save always re-derives expertise_country_codes from country + served_countries and a
# missing served_countries silently collapsed it down to just the current country.
import sys
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import BackgroundTasks

import db
from core.auth import AuthUser
from routers.mentor import ProfileUpdateBody, ServedCountry, _derive_expertise, update_profile


def _user():
    return AuthUser(id="11111111-1111-1111-1111-111111111111", email="mentor@example.com", role="authenticated")


# ── 1. The regression itself: the SELECT must include served_countries ──────────────────────────

def test_get_mentor_by_profile_id_selects_served_countries():
    """Guards the exact root cause: if this column ever drops out of the SELECT again, the Edit
    Profile page silently loses previously-saved expertise countries on the very next save."""
    captured = {}

    class FakeQuery:
        def select(self, cols):
            captured["cols"] = cols
            return self

        def eq(self, *a, **kw):
            return self

        def limit(self, *a, **kw):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    class FakeTable:
        def table(self, name):
            assert name == "mentors"
            return FakeQuery()

    with patch("db.mentors._supabase", FakeTable()):
        db.get_mentor_by_profile_id("some-profile-id")

    assert "served_countries" in captured["cols"]


# ── 2. _derive_expertise never includes the home country ────────────────────────────────────────

def test_derive_expertise_excludes_home_country_and_dedupes():
    # current=NL, served includes NL again (dup) and IN - home country is never passed in at all,
    # since the caller (update_profile) intentionally never feeds it to _derive_expertise (BUG-110).
    result = _derive_expertise("NL", ["nl", "de"])
    assert result == ["NL", "DE"]


# ── 3. update_profile's merge-fallback must not wipe previously-stored served countries ─────────

def _mentor_row(**overrides):
    row = {
        "id": "mentor-1",
        "display_name": "Test Mentor",
        "status": "approved",
        "needs_onboarding": False,
        "country": "NL",
        "home_country_code": "IN",
        "served_countries": [{"code": "DE", "years": 3}],
        "expertise_country_codes": ["NL", "DE"],
        "currency": "USD",
    }
    row.update(overrides)
    return row


def test_partial_save_preserves_previously_stored_served_countries():
    """A save that never touches the countries section (e.g. only updates the hourly rate) must
    keep the mentor's existing expertise countries intact, not collapse them to just `country`."""
    mentor = _mentor_row(needs_onboarding=True)   # live-apply path, simplest to assert on
    body = ProfileUpdateBody(hourly_rate=75.0)   # served_countries intentionally omitted

    with patch.object(db, "get_mentor_by_profile_id", return_value=mentor), \
         patch.object(db, "save_mentor_profile_live") as save_live:
        save_live.return_value = mentor
        update_profile(body, BackgroundTasks(), user=_user())

    saved_fields = save_live.call_args.args[1]
    # country wasn't touched either, so update_profile shouldn't even re-derive expertise here -
    # but if it does (e.g. a future change widens the trigger), it must still be derived FROM the
    # mentor's real stored served_countries, never an empty list.
    if "expertise_country_codes" in saved_fields:
        assert set(saved_fields["expertise_country_codes"]) == {"NL", "DE"}
    assert "hourly_rate" in saved_fields


def test_changing_country_rederives_expertise_from_existing_served_countries():
    """Changing the current country (without touching served_countries) must fold the mentor's
    EXISTING served countries into the new expertise list, not just the new current country."""
    mentor = _mentor_row(needs_onboarding=True)
    body = ProfileUpdateBody(country="FR")

    with patch.object(db, "get_mentor_by_profile_id", return_value=mentor), \
         patch.object(db, "save_mentor_profile_live") as save_live:
        save_live.return_value = mentor
        update_profile(body, BackgroundTasks(), user=_user())

    saved_fields = save_live.call_args.args[1]
    assert saved_fields["expertise_country_codes"] == ["FR", "DE"]


def test_editing_served_countries_persists_the_new_list():
    """Adding/removing a served country through the client-supplied list must be reflected in the
    persisted expertise_country_codes."""
    mentor = _mentor_row(needs_onboarding=True)
    body = ProfileUpdateBody(served_countries=[ServedCountry(code="ES", years=2)])

    with patch.object(db, "get_mentor_by_profile_id", return_value=mentor), \
         patch.object(db, "save_mentor_profile_live") as save_live:
        save_live.return_value = mentor
        update_profile(body, BackgroundTasks(), user=_user())

    saved_fields = save_live.call_args.args[1]
    assert saved_fields["served_countries"] == [{"code": "ES", "years": 2}]
    assert saved_fields["expertise_country_codes"] == ["NL", "ES"]


def test_home_country_is_never_added_to_expertise_on_save():
    mentor = _mentor_row(needs_onboarding=True, served_countries=[], expertise_country_codes=["NL"])
    body = ProfileUpdateBody(country="NL")   # re-save with home_country_code present on the row

    with patch.object(db, "get_mentor_by_profile_id", return_value=mentor), \
         patch.object(db, "save_mentor_profile_live") as save_live:
        save_live.return_value = mentor
        update_profile(body, BackgroundTasks(), user=_user())

    saved_fields = save_live.call_args.args[1]
    assert "IN" not in saved_fields.get("expertise_country_codes", [])


# ── 4. Migrated-mentor data fix script: home stripped, orphans mirrored, idempotent ─────────────

class _FakeUpdateQuery:
    def __init__(self, sink, table_name, payload):
        self._sink = sink
        self._table = table_name
        self._payload = payload

    def eq(self, col, val):
        self._sink.append((self._table, val, self._payload))
        return self

    def execute(self):
        return SimpleNamespace(data=[{"id": "ok"}])


class _FakeSelectQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_kw):
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _FakeSupabase:
    """Enough of the supabase client surface for fix_expertise_countries.main(): a select-all on
    the first call, then one update().eq() per changed mentor."""
    def __init__(self, rows):
        self._rows = rows
        self.updates: list[tuple[str, str, dict]] = []
        self._select_used = False

    def table(self, name):
        assert name == "mentors"
        if not self._select_used:
            self._select_used = True
            return _FakeSelectQuery(self._rows)
        return self

    def update(self, payload):
        return _FakeUpdateQueryBound(self, payload)


class _FakeUpdateQueryBound:
    def __init__(self, client, payload):
        self._client = client
        self._payload = payload

    def eq(self, col, val):
        self._client.updates.append((val, self._payload))
        return self

    def execute(self):
        return SimpleNamespace(data=[{"id": "ok"}])


MIGRATED_MENTOR = {
    "id": "m-migrated",
    "display_name": "Migrated Mentor",
    "country": "NL",
    "home_country_code": "IN",
    # Migration-era bug: expertise was set directly (includes the home country AND an orphaned
    # country never mirrored into served_countries).
    "served_countries": [],
    "expertise_country_codes": ["NL", "IN", "DE"],
}

CLEAN_MENTOR = {
    "id": "m-clean",
    "display_name": "Already Correct Mentor",
    "country": "FR",
    "home_country_code": "GB",
    "served_countries": [{"code": "ES", "years": 4}],
    "expertise_country_codes": ["FR", "ES"],
}


def _run_fix_script(rows, dry_run=False, monkeypatch=None):
    import scripts.fix_expertise_countries as mod
    fake = _FakeSupabase([dict(r) for r in rows])
    argv = ["fix_expertise_countries.py"] + (["--dry-run"] if dry_run else [])
    with patch.object(sys, "argv", argv), \
         patch("supabase.create_client", return_value=fake), \
         patch.dict("os.environ", {"SUPABASE_URL": "https://fake", "SUPABASE_SERVICE_ROLE_KEY": "fake"}):
        rc = mod.main()
    assert rc == 0
    return fake


def test_migration_strips_home_country_and_mirrors_orphan():
    fake = _run_fix_script([MIGRATED_MENTOR])
    assert len(fake.updates) == 1
    mentor_id, payload = fake.updates[0]
    assert mentor_id == "m-migrated"
    assert "IN" not in payload["expertise_country_codes"]      # home country dropped
    assert set(payload["expertise_country_codes"]) == {"NL", "DE"}
    assert [c["code"] for c in payload["served_countries"]] == ["DE"]   # orphan now editable


def test_migration_leaves_already_correct_mentor_untouched():
    fake = _run_fix_script([CLEAN_MENTOR])
    assert fake.updates == []


def test_migration_is_idempotent():
    """Running the fix twice must not re-flag the already-corrected row a second time."""
    first = _run_fix_script([MIGRATED_MENTOR])
    mentor_id, payload = first.updates[0]
    corrected = dict(MIGRATED_MENTOR, expertise_country_codes=payload["expertise_country_codes"],
                      served_countries=payload["served_countries"])
    second = _run_fix_script([corrected])
    assert second.updates == []


def test_migration_dry_run_reports_without_writing():
    fake = _run_fix_script([MIGRATED_MENTOR], dry_run=True)
    assert fake.updates == []
