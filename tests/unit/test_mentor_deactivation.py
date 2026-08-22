# FEAT-020: a mentor can hide or delete their own profile.
#
# The endpoint that existed before set status to 'suspended' - the ADMIN state - which the hub
# renders as a dead end telling the mentor to contact support. A mentor who paused their own
# profile could not undo it. These tests pin the two self-service states, the reactivation path,
# and the fact that the purge only ever scrubs personal fields (bookings.mentor_id is ON DELETE
# RESTRICT, so the row itself can never be deleted for a mentor who has traded).
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import db
from core.auth import AuthUser
from core.permissions import require_mentor
from routers.mentor import (
    DeactivateBody, deactivate_mentor, deactivation_status, reactivate_mentor,
)


def _user():
    return AuthUser(id="profile-1", email="mentor@example.com", role="mentor")


def _mentor(**over):
    base = {"id": "mentor-1", "status": "approved", "slug": "a-mentor"}
    base.update(over)
    return base


# ── choosing a state ──────────────────────────────────────────────────────────

def test_deactivate_does_not_use_the_admin_suspended_state():
    """The whole bug: 'suspended' is an admin action the mentor cannot undo."""
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor()), \
         patch.object(db, "set_mentor_self_status", return_value={"status": "deactivated"}) as setter:
        result = deactivate_mentor(DeactivateBody(delete=False), user=_user())

    assert result["status"] == "deactivated"
    assert setter.call_args.kwargs["delete"] is False


def test_delete_starts_the_grace_clock():
    row = {"status": "deletion_pending", "purge_due_at": "2026-11-20T00:00:00+00:00"}
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor()), \
         patch.object(db, "set_mentor_self_status", return_value=row) as setter:
        result = deactivate_mentor(DeactivateBody(delete=True), user=_user())

    assert result["status"] == "deletion_pending"
    assert result["purge_due_at"] == "2026-11-20T00:00:00+00:00"
    assert result["grace_days"] == 90
    assert setter.call_args.kwargs["delete"] is True


def test_only_an_approved_profile_can_be_deactivated():
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor(status="pending_review")), \
         pytest.raises(HTTPException) as exc:
        deactivate_mentor(DeactivateBody(), user=_user())
    assert exc.value.status_code == 400


# ── the mentor is told what they still owe ────────────────────────────────────

def test_status_reports_sessions_that_will_still_go_ahead():
    """Confirmed sessions are honoured, not cancelled, so the count has to be visible up front."""
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor()), \
         patch.object(db, "count_upcoming_mentor_sessions", return_value=3):
        result = deactivation_status(user=_user())

    assert result["upcoming_sessions"] == 3
    assert result["grace_days"] == 90


# ── getting back ──────────────────────────────────────────────────────────────

def test_reactivate_restores_a_deactivated_profile():
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor(status="deactivated")), \
         patch.object(db, "reactivate_mentor", return_value={"status": "approved"}):
        assert reactivate_mentor(user=_user()) == {"status": "approved"}


def test_reactivate_surfaces_the_reason_it_cannot():
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor(status="deletion_pending")), \
         patch.object(db, "reactivate_mentor",
                      side_effect=ValueError("This profile has already been deleted and cannot be restored")), \
         pytest.raises(HTTPException) as exc:
        reactivate_mentor(user=_user())

    assert exc.value.status_code == 400
    assert "already been deleted" in exc.value.detail


@pytest.mark.parametrize("blocked", ["suspended", "deactivated", "deletion_pending"])
def test_a_hidden_profile_cannot_edit_services_or_availability(blocked):
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor(status=blocked)), \
         pytest.raises(HTTPException) as exc:
        require_mentor(user=_user())
    assert exc.value.status_code == 403


@pytest.mark.parametrize("allowed", ["approved", "pending_review", "changes_requested", "rejected"])
def test_require_mentor_still_allows_the_states_it_always_did(allowed):
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor(status=allowed)):
        assert require_mentor(user=_user())["status"] == allowed


# ── the purge ─────────────────────────────────────────────────────────────────
# The irreversible half. These pin WHAT gets cleared and, just as importantly, what does not:
# bookings / payments / payouts are business records and must survive a profile deletion.

class _FakeQuery:
    """Records every table operation so a test can assert on the whole interaction."""

    def __init__(self, table, log, rows):
        self.table, self.log, self.rows = table, log, rows
        self.op = None

    def select(self, *a, **k):
        self.op = "select"
        return self

    def eq(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def update(self, payload):
        self.op = "update"
        self.log.append((self.table, "update", payload))
        return self

    def delete(self):
        self.op = "delete"
        self.log.append((self.table, "delete", None))
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = self.rows.get(self.table, []) if self.op == "select" else [{"id": "mentor-1"}]
        r.count = len(r.data)
        return r


class _FakeSupabase:
    def __init__(self, rows):
        self.log, self.rows = [], rows

    def table(self, name):
        return _FakeQuery(name, self.log, self.rows)


def _run_purge(due_rows):
    fake = _FakeSupabase({"mentors": due_rows})
    with patch.object(db.mentors, "_supabase", fake):
        count = db.purge_due_mentor_deletions()
    return count, fake.log


def test_purge_clears_personal_fields_and_stamps_anonymized_at():
    count, log = _run_purge([{"id": "mentor-1", "slug": "maya-singh"}])
    assert count == 1

    payload = next(p for (t, op, p) in log if t == "mentors" and op == "update")
    for field in ("headline", "bio", "photo_url", "phone", "email", "city",
                  "home_country_code", "public_notes", "booking_url", "legacy_data"):
        assert payload[field] is None, field
    assert payload["display_name"] == "Former mentor"
    assert payload["social_links"] == []
    assert payload["is_active"] is False
    assert payload["anonymized_at"]
    # The old slug carried their real name and sat in a public URL.
    assert payload["slug"].startswith("former-mentor-")
    assert "maya-singh" not in payload["slug"]
    # Without this the account would be re-linked by email at the next sign-in.
    assert payload["profile_id"] is None


def test_purge_never_touches_the_financial_record():
    _, log = _run_purge([{"id": "mentor-1", "slug": "x"}])
    touched = {t for (t, _op, _p) in log}
    for ledger in ("bookings", "customer_payments", "mentor_payouts", "booking_pricing", "legacy_sessions"):
        assert ledger not in touched, f"purge must not write to {ledger}"


def test_purge_removes_payout_details_and_takes_services_offline():
    _, log = _run_purge([{"id": "mentor-1", "slug": "x"}])
    assert ("mentor_bank_accounts", "delete", None) in log
    svc = next(p for (t, op, p) in log if t == "services" and op == "update")
    assert svc == {"is_active": False}


def test_purge_reports_nothing_to_do_when_none_are_due():
    count, log = _run_purge([])
    assert count == 0
    assert not [x for x in log if x[1] in ("update", "delete")]


def test_purge_keeps_going_when_one_mentor_fails():
    """One bad row must not strand every other expired deletion."""
    fake = _FakeSupabase({"mentors": [{"id": "bad", "slug": "a"}, {"id": "good", "slug": "b"}]})
    real_table = fake.table

    def flaky(name):
        q = real_table(name)
        if name == "mentors" and getattr(flaky, "seen", None) is None:
            flaky.seen = True          # let the initial SELECT through
            return q
        if name == "mentors" and not getattr(flaky, "failed", False):
            flaky.failed = True
            raise RuntimeError("row is unhappy")
        return q

    with patch.object(db.mentors, "_supabase", fake), patch.object(fake, "table", flaky):
        assert db.purge_due_mentor_deletions() == 1
