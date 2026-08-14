# BUG-137: a mentor had no way to view/edit an existing service's text/details (only toggle
# active or delete existed), no confirmation step before a catalogue tap sent a service straight
# into the admin approval queue, and a REJECTED service permanently occupied its duration slot,
# hiding the rest of the catalogue.
#
# This file covers the backend piece: the new POST /mentor/services/{id}/update endpoint and its
# db.update_service() data layer. The add-confirmation-step and duration-slot-freeing fixes are
# frontend-only (ServicesManager.tsx); this test file focuses on what's testable server-side.
from unittest.mock import patch

import db
from routers.services import ServiceEditBody, update_service


def _mentor(mentor_id="mentor-1"):
    return {"id": mentor_id, "currency": "USD"}


def test_update_service_persists_title_description_category_tags():
    body = ServiceEditBody(title="New title", description="<p>New body</p>", category="Jobs & Careers", tags=["cv", "resume"])
    with patch.object(db, "get_service_mentor_id", return_value="mentor-1"), \
         patch.object(db, "update_service") as upd:
        result = update_service("svc-1", body, mentor=_mentor())

    assert result == {"updated": True}
    upd.assert_called_once()
    svc_id, fields = upd.call_args.args
    assert svc_id == "svc-1"
    assert fields["title"] == "New title"
    assert fields["description"] == "<p>New body</p>"
    assert fields["category"] == "Jobs & Careers"
    assert fields["tags"] == ["cv", "resume"]


def test_update_service_allows_duration_matching_another_active_service():
    """Duration is no longer a unique key: two active services may share a length."""
    body = ServiceEditBody(duration=90)
    existing = [
        {"id": "svc-1", "is_active": True, "duration": 60, "set_price": 0},
        {"id": "svc-2", "is_active": True, "duration": 90, "set_price": 0},
    ]
    with patch.object(db, "get_service_mentor_id", return_value="mentor-1"), \
         patch.object(db, "list_services", return_value=existing), \
         patch.object(db, "update_service") as upd:
        result = update_service("svc-1", body, mentor={"id": "mentor-1", "currency": "USD", "hourly_rate": 60})

    assert result == {"updated": True}
    fields = upd.call_args.args[1]
    assert fields["duration"] == 90


def test_update_service_persists_duration_and_reprices():
    body = ServiceEditBody(duration=90)
    existing = [{"id": "svc-1", "is_active": True, "duration": 60, "set_price": 60.0}]
    with patch.object(db, "get_service_mentor_id", return_value="mentor-1"), \
         patch.object(db, "list_services", return_value=existing), \
         patch.object(db, "update_service") as upd:
        update_service("svc-1", body, mentor={"id": "mentor-1", "currency": "USD", "hourly_rate": 60})

    fields = upd.call_args.args[1]
    assert fields["duration"] == 90
    assert fields["set_price"] == 90.0


def test_update_service_rejects_non_owner():
    body = ServiceEditBody(title="New title")
    with patch.object(db, "get_service_mentor_id", return_value="someone-elses-mentor-id"):
        try:
            update_service("svc-1", body, mentor=_mentor("mentor-1"))
            assert False, "expected 403"
        except Exception as e:
            assert getattr(e, "status_code", None) == 403


def test_update_service_partial_edit_only_sends_touched_fields():
    """Editing just the title must not clobber description/category/tags with None."""
    body = ServiceEditBody(title="Only the title changed")
    with patch.object(db, "get_service_mentor_id", return_value="mentor-1"), \
         patch.object(db, "update_service") as upd:
        update_service("svc-1", body, mentor=_mentor())

    fields = upd.call_args.args[1]
    assert fields == {"title": "Only the title changed"}


def test_update_service_clearing_category_falls_back_to_general_guidance():
    """BUG-118: an edit must never be able to put category back to NULL - same safe default the
    create path and the migrated-services backfill both use."""
    body = ServiceEditBody(category="")
    with patch.object(db, "get_service_mentor_id", return_value="mentor-1"), \
         patch.object(db, "update_service") as upd:
        update_service("svc-1", body, mentor=_mentor())

    fields = upd.call_args.args[1]
    assert fields["category"] == "General Guidance"


def test_update_service_rejects_empty_title():
    try:
        ServiceEditBody(title="   ")
        assert False, "expected a validation error"
    except Exception:
        pass


def test_update_service_no_fields_is_a_400():
    body = ServiceEditBody()
    with patch.object(db, "get_service_mentor_id", return_value="mentor-1"):
        try:
            update_service("svc-1", body, mentor=_mentor())
            assert False, "expected 400"
        except Exception as e:
            assert getattr(e, "status_code", None) == 400


# ── db.update_service: only editable columns are ever written, tags get deduped/capped ──────────

def test_db_update_service_ignores_unknown_fields():
    captured = {}

    class FakeQuery:
        def update(self, payload):
            captured["payload"] = payload
            return self

        def eq(self, *a, **kw):
            return self

        def execute(self):
            return None

    class FakeTable:
        def table(self, name):
            assert name == "services"
            return FakeQuery()

    with patch("db.direct_booking._supabase", FakeTable()):
        db.update_service("svc-1", {"title": "T", "set_price": 999, "status": "approved"})

    # status is NOT in the editable set; title/set_price are.
    assert captured["payload"] == {"title": "T", "set_price": 999}


def test_db_update_service_dedupes_and_caps_tags():
    captured = {}

    class FakeQuery:
        def update(self, payload):
            captured["payload"] = payload
            return self

        def eq(self, *a, **kw):
            return self

        def execute(self):
            return None

    class FakeTable:
        def table(self, name):
            return FakeQuery()

    with patch("db.direct_booking._supabase", FakeTable()):
        db.update_service("svc-1", {"tags": ["CV", "cv", "resume", "interview", "career", "job", "extra"]})

    assert captured["payload"]["tags"] == ["CV", "resume", "interview", "career", "job"]


def test_db_update_service_noop_when_no_editable_fields():
    class FakeTable:
        def table(self, name):
            raise AssertionError("should never touch the table for an empty/unknown-only update")

    with patch("db.direct_booking._supabase", FakeTable()):
        db.update_service("svc-1", {"status": "approved"})   # not editable -> no-op, no call
