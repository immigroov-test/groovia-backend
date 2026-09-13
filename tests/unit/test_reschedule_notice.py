# BUG-119: the customer dashboard showed a hardcoded 24-hour reschedule notice even when the
# mentor configured a different value (e.g. 6 hours).
#
# Root cause: TWO separate deadline-state computations existed. booking_detail() (the session
# detail page) already correctly derived the window from this mentor's own `cancel_notice_hours`
# (mentors table) - but reschedule_slots() (the actual Reschedule page/flow, reached by clicking
# the Reschedule button) had its own, independent computation that hardcoded 24 hours:
#     deadline = "buffer" if hours < 2 else "late" if hours < 24 else "free"
# This meant the reschedule page could show/behave differently from what the session detail page
# had just told the customer, for any mentor whose configured notice wasn't exactly 24h.
#
# Fix: both endpoints now share one `_deadline_state()` helper (routers/booking.py), fed by the
# SAME source - the booking's mentor's `mentors.cancel_notice_hours` - so they can't diverge.
# `get_booking_reschedule_target()` now also selects that column. The frontend
# (RescheduleClient.tsx) reads the resolved `cancel_notice_hours` from the API response and
# displays it via the shared `hoursText()` helper instead of hardcoded "24 hours" copy.
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import db
from fastapi import Request
from core.auth import AuthUser
from routers.booking import _deadline_state, booking_detail, reschedule_slots

BOOKING_ID = "11111111-1111-1111-1111-111111111111"
CANDIDATE_ID = "22222222-2222-2222-2222-222222222222"


def _user():
    return AuthUser(id=CANDIDATE_ID, email="candidate@example.com", role="authenticated")


# ── 1. The shared helper itself, across the notice values the task asks us to check ─────────────

def test_deadline_state_uses_configured_notice_hours_not_a_hardcoded_24():
    now = datetime.now(timezone.utc)
    for notice_hours, hours_out, expected in [
        (2, 1, "buffer"),     # inside the hard 2h floor regardless of notice
        (2, 3, "free"),       # 2h notice - 3h out is already free
        (6, 3, "late"),       # 6h notice - 3h out needs approval
        (6, 8, "free"),       # 6h notice - 8h out is free
        (24, 10, "late"),     # 24h notice - 10h out still needs approval
        (24, 30, "free"),     # 24h notice - 30h out is free
    ]:
        slot = now + timedelta(hours=hours_out)
        state, free_hours = _deadline_state(slot, now, notice_hours)
        assert state == expected, f"notice={notice_hours} hours_out={hours_out}: expected {expected}, got {state}"
        assert free_hours == max(notice_hours, 2)


def test_deadline_state_defaults_missing_legacy_config_to_24():
    now = datetime.now(timezone.utc)
    slot = now + timedelta(hours=20)
    state, free_hours = _deadline_state(slot, now, None)
    assert free_hours == 24
    assert state == "late"   # 20h out, default 24h notice -> still late


def test_deadline_state_never_reports_free_inside_the_2h_hard_floor():
    now = datetime.now(timezone.utc)
    slot = now + timedelta(hours=1)
    state, _ = _deadline_state(slot, now, 0)   # even a mentor configured with 0 can't waive the floor
    assert state == "buffer"


# ── 2. get_booking_reschedule_target selects the mentor's own cancel_notice_hours ────────────────

def test_get_booking_reschedule_target_includes_cancel_notice_hours():
    captured = {}

    class FakeQuery:
        def select(self, cols):
            captured["cols"] = cols
            return self

        def eq(self, *a, **kw):
            return self

        def single(self):
            return self

        def execute(self):
            from types import SimpleNamespace
            return SimpleNamespace(data={
                "candidate_id": CANDIDATE_ID, "mentor_id": "m-1", "service_id": "s-1",
                "slot_time": "2026-08-20T10:00:00Z", "mentors": {"cancel_notice_hours": 6},
            })

    class FakeTable:
        def table(self, name):
            assert name == "bookings"
            return FakeQuery()

    with patch("db.direct_booking._supabase", FakeTable()):
        result = db.get_booking_reschedule_target(BOOKING_ID)

    assert "cancel_notice_hours" in captured["cols"]
    assert result["cancel_notice_hours"] == 6
    assert "mentors" not in result   # flattened, not left nested


# ── 3. reschedule_slots() returns and USES the real per-mentor value, not 24 ─────────────────────

def _reschedule_target(cancel_notice_hours, hours_out):
    slot = (datetime.now(timezone.utc) + timedelta(hours=hours_out)).isoformat()
    return {
        "candidate_id": CANDIDATE_ID, "mentor_id": "m-1", "service_id": "s-1",
        "slot_time": slot, "cancel_notice_hours": cancel_notice_hours,
    }


def test_reschedule_slots_reflects_a_6_hour_mentor_notice():
    """The exact regression: 8h out from a session, mentor configured 6h notice -> free (bookable
    directly). The old hardcoded-24h logic would have wrongly said 'late' here."""
    target = _reschedule_target(cancel_notice_hours=6, hours_out=8)
    with patch.object(db, "get_booking_reschedule_target", return_value=target), \
         patch.object(db, "get_active_mentor_proposal", return_value=None), \
         patch.object(db, "get_available_slots", return_value=[]):
        result = reschedule_slots(BOOKING_ID, user=_user())

    assert result["deadline_state"] == "free"
    assert result["cancel_notice_hours"] == 6


def test_reschedule_slots_reflects_a_2_hour_mentor_notice():
    target = _reschedule_target(cancel_notice_hours=2, hours_out=3)
    with patch.object(db, "get_booking_reschedule_target", return_value=target), \
         patch.object(db, "get_active_mentor_proposal", return_value=None), \
         patch.object(db, "get_available_slots", return_value=[]):
        result = reschedule_slots(BOOKING_ID, user=_user())

    assert result["deadline_state"] == "free"
    assert result["cancel_notice_hours"] == 2


def test_reschedule_slots_reflects_a_24_hour_mentor_notice():
    target = _reschedule_target(cancel_notice_hours=24, hours_out=10)
    with patch.object(db, "get_booking_reschedule_target", return_value=target), \
         patch.object(db, "get_active_mentor_proposal", return_value=None), \
         patch.object(db, "get_available_slots", return_value=[]):
        result = reschedule_slots(BOOKING_ID, user=_user())

    assert result["deadline_state"] == "late"
    assert result["cancel_notice_hours"] == 24


def test_reschedule_slots_defaults_missing_mentor_config_to_24():
    target = _reschedule_target(cancel_notice_hours=None, hours_out=20)
    with patch.object(db, "get_booking_reschedule_target", return_value=target), \
         patch.object(db, "get_active_mentor_proposal", return_value=None), \
         patch.object(db, "get_available_slots", return_value=[]):
        result = reschedule_slots(BOOKING_ID, user=_user())

    assert result["cancel_notice_hours"] == 24
    assert result["deadline_state"] == "late"


def _request():
    """booking_detail now takes a Request (it resolves the caller's country for pricing).
    Nothing this test asserts depends on it, so a bare ASGI scope is enough."""
    return Request({
        "type": "http", "method": "GET", "path": "/booking/x",
        "headers": [], "query_string": b"", "client": ("127.0.0.1", 0),
    })


# ── 4. Cross-check: booking_detail() and reschedule_slots() must never disagree ──────────────────

def _full_detail(cancel_notice_hours, hours_out):
    slot = (datetime.now(timezone.utc) + timedelta(hours=hours_out)).isoformat()
    return {
        "id": BOOKING_ID, "status": "confirmed", "slot_time": slot, "slot_end": None,
        "reschedule_count": 0, "no_show_by": None,
        "candidate_id": CANDIDATE_ID, "candidate_name": "Cara", "candidate_email": "c@example.com",
        "candidate_phone": None, "attendee_tz": "UTC",
        "mentor_id": "m-1", "service_id": "s-1", "mentor_profile_id": "mp-1",
        "mentor_name": "Max", "mentor_photo": None, "mentor_slug": "max", "mentor_tz": "UTC",
        "mentor_country": "NL", "cancel_notice_hours": cancel_notice_hours or 24,
        "service_title": "Career Chat", "service_duration": 30, "service_type": "video",
        "offer": None, "request": None,
    }


def test_session_detail_and_reschedule_page_never_disagree_for_a_6_hour_mentor():
    """The exact contradiction BUG-119 describes: a mentor with a 6h notice, 8h before the
    session. Both pages must agree it's freely reschedulable."""
    hours_out = 8
    full_detail = _full_detail(cancel_notice_hours=6, hours_out=hours_out)
    target = _reschedule_target(cancel_notice_hours=6, hours_out=hours_out)

    with patch.object(db, "get_booking_full_detail", return_value=full_detail), \
         patch.object(db, "get_booking_answers", return_value=[]):
        detail = booking_detail(BOOKING_ID, _request(), user=_user())

    with patch.object(db, "get_booking_reschedule_target", return_value=target), \
         patch.object(db, "get_active_mentor_proposal", return_value=None), \
         patch.object(db, "get_available_slots", return_value=[]):
        reschedule = reschedule_slots(BOOKING_ID, user=_user())

    assert detail["deadline_state"] == reschedule["deadline_state"] == "free"
    assert detail["cancel_notice_hours"] == reschedule["cancel_notice_hours"] == 6
