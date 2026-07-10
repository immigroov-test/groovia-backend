# tests/unit/test_meeting_ics.py
# Video (Jitsi) + ICS calendar invite, ported from immigroov's
# 0012_jitsi_meeting_link.sql (room assignment, at the DB trigger level - not
# unit-testable without live Postgres, same limitation as every other SQL
# trigger/function in this codebase) and 0031_ics_calendar_invite.sql
# (booking_ics -> ported to Python instead, see routers/booking.py's
# _build_ics_attachment docstring for why). These tests cover: the ICS
# builder's string output, and GET /booking/{booking_id}/meeting's HTTP
# contract (auth gating + response shape), with db.* mocked.
import uuid
from unittest.mock import patch

import db
import routers.booking as booking_router
from core.auth import AuthUser, get_current_user


def _meeting_info(**overrides):
    info = {
        "id": str(uuid.uuid4()), "status": "confirmed",
        "slot_time": "2026-08-01T10:00:00+00:00", "slot_end": "2026-08-01T10:30:00+00:00",
        "meeting_url": "https://meet.jit.si/Immigroov-abc123", "service_title": "Career chat",
        "mentor_name": "Mentor M", "candidate_name": "Candidate C",
    }
    info.update(overrides)
    return info


# ── _build_ics_attachment ────────────────────────────────────────────────────

def test_build_ics_attachment_returns_none_without_slot_time():
    with patch.object(db, "get_booking_meeting_info", return_value=_meeting_info(slot_time=None)):
        assert booking_router._build_ics_attachment(str(uuid.uuid4())) is None


def test_build_ics_attachment_returns_none_when_booking_missing():
    with patch.object(db, "get_booking_meeting_info", return_value=None):
        assert booking_router._build_ics_attachment(str(uuid.uuid4())) is None


def test_build_ics_attachment_contains_expected_fields():
    import base64
    with patch.object(db, "get_booking_meeting_info", return_value=_meeting_info()):
        attachment = booking_router._build_ics_attachment(str(uuid.uuid4()))
    assert attachment is not None
    assert attachment["filename"] == "invite.ics"
    ics = base64.b64decode(attachment["content"]).decode("utf-8")
    assert "BEGIN:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "DTSTART:20260801T100000Z" in ics
    assert "DTEND:20260801T103000Z" in ics
    assert "STATUS:CONFIRMED" in ics
    assert "LOCATION:https://meet.jit.si/Immigroov-abc123" in ics
    assert "SUMMARY:Career chat - Immigroov" in ics
    assert "END:VEVENT" in ics
    assert "END:VCALENDAR" in ics


def test_build_ics_attachment_cancelled_flips_status():
    with patch.object(db, "get_booking_meeting_info", return_value=_meeting_info()):
        attachment = booking_router._build_ics_attachment(str(uuid.uuid4()), cancelled=True)
    import base64
    ics = base64.b64decode(attachment["content"]).decode("utf-8")
    assert "STATUS:CANCELLED" in ics


def test_build_ics_attachment_omits_location_for_dm_service():
    """'dm' services get no meeting_url (set_meeting_url's trigger only
    assigns one for 'video') - the event is still useful without one."""
    with patch.object(db, "get_booking_meeting_info", return_value=_meeting_info(meeting_url=None)):
        attachment = booking_router._build_ics_attachment(str(uuid.uuid4()))
    import base64
    ics = base64.b64decode(attachment["content"]).decode("utf-8")
    assert "LOCATION:" not in ics
    assert "Session with Mentor M" in ics


def test_build_ics_attachment_uid_stable_across_calls():
    """Same booking -> same UID, so calendar apps update the existing event
    on reschedule/cancel instead of creating a duplicate."""
    import base64
    booking_id = str(uuid.uuid4())
    with patch.object(db, "get_booking_meeting_info", return_value=_meeting_info()):
        ics1 = base64.b64decode(booking_router._build_ics_attachment(booking_id)["content"]).decode("utf-8")
        ics2 = base64.b64decode(booking_router._build_ics_attachment(booking_id, cancelled=True)["content"]).decode("utf-8")
    uid_line = next(l for l in ics1.splitlines() if l.startswith("UID:"))
    assert uid_line in ics2.splitlines()


# ── GET /booking/{booking_id}/meeting ────────────────────────────────────────

def _as_user(client, user_id=None):
    user = AuthUser(id=user_id or str(uuid.uuid4()), email="a@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: user
    return user


def test_get_meeting_info_requires_auth(client):
    resp = client.get(f"/booking/{uuid.uuid4()}/meeting")
    assert resp.status_code in (401, 403)


def test_get_meeting_info_404s_when_booking_missing(client):
    user = _as_user(client)
    try:
        with patch.object(db, "get_booking_principals", return_value=None):
            resp = client.get(f"/booking/{uuid.uuid4()}/meeting")
        assert resp.status_code == 404
    finally:
        client.app.dependency_overrides.clear()


def test_get_meeting_info_403s_for_a_non_party(client):
    user = _as_user(client)
    try:
        with patch.object(db, "get_booking_principals",
                           return_value={"candidate_id": str(uuid.uuid4()), "mentor_id": str(uuid.uuid4())}), \
             patch.object(db, "get_mentor_by_profile_id", return_value=None):
            resp = client.get(f"/booking/{uuid.uuid4()}/meeting")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


def test_get_meeting_info_returns_info_for_the_candidate(client):
    user = _as_user(client)
    info = _meeting_info()
    try:
        with patch.object(db, "get_booking_principals",
                           return_value={"candidate_id": user.id, "mentor_id": str(uuid.uuid4())}), \
             patch.object(db, "get_mentor_by_profile_id", return_value=None), \
             patch.object(db, "get_booking_meeting_info", return_value=info):
            resp = client.get(f"/booking/{info['id']}/meeting")
        assert resp.status_code == 200
        assert resp.json()["meeting_url"] == info["meeting_url"]
        assert resp.json()["viewer_role"] == "candidate"
    finally:
        client.app.dependency_overrides.clear()


def test_get_meeting_info_reports_mentor_viewer_role(client):
    """Regression coverage for the role-labeling bug this endpoint exists to
    avoid: the frontend can't tell candidate from mentor by comparing IDs
    itself (candidate_id/mentor_id aren't in the response), so the endpoint
    must resolve and report viewer_role explicitly."""
    user = _as_user(client)
    info = _meeting_info()
    mentor_row = {"id": str(uuid.uuid4())}
    try:
        with patch.object(db, "get_booking_principals",
                           return_value={"candidate_id": str(uuid.uuid4()), "mentor_id": mentor_row["id"]}), \
             patch.object(db, "get_mentor_by_profile_id", return_value=mentor_row), \
             patch.object(db, "get_booking_meeting_info", return_value=info):
            resp = client.get(f"/booking/{info['id']}/meeting")
        assert resp.status_code == 200
        assert resp.json()["viewer_role"] == "mentor"
    finally:
        client.app.dependency_overrides.clear()
