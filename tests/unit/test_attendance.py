# tests/unit/test_attendance.py
# Attendance engine (join-link no-show tracking), ported from immigroov's
# 0079_attendance_tracking.sql. Shipped inert - attendance_engine_enabled is
# 'false' and no cron calls evaluate_attendance_after_grace_period yet (see
# the migration files and COMPLETION_PLAN.md B6) - these tests cover the
# HTTP-layer contract (request validation, RPC-error -> HTTP-status mapping,
# auth gating) with db.* mocked, same limitation as every other module (no
# live Postgres in this environment to exercise the SQL functions directly).
import uuid
from unittest.mock import patch

import db
from core.auth import AuthUser, get_current_user, require_admin


def _as_admin(client):
    admin_user = AuthUser(id=str(uuid.uuid4()), email="admin@example.com", role="authenticated")
    client.app.dependency_overrides[require_admin] = lambda: admin_user
    return admin_user


# ── GET /booking/join/{token}/check ─────────────────────────────────────────

def test_check_join_window_returns_status(client):
    token = str(uuid.uuid4())
    status_payload = {
        "state": "waiting", "slot_time": "2026-08-01T10:00:00+00:00",
        "window_opens_at": "2026-08-01T09:58:00+00:00", "window_closes_at": "2026-08-01T10:10:00+00:00",
        "already_joined": False, "meeting_url": "https://meet.jit.si/Immigroov-abc",
    }
    with patch.object(db, "check_join_window_by_token", return_value=status_payload) as mocked:
        resp = client.get(f"/booking/join/{token}/check")
    assert resp.status_code == 200
    assert resp.json() == status_payload
    mocked.assert_called_once_with(token)


def test_check_join_window_404s_on_invalid_token(client):
    with patch.object(db, "check_join_window_by_token", side_effect=Exception("Invalid join link")):
        resp = client.get(f"/booking/join/{uuid.uuid4()}/check")
    assert resp.status_code == 404


# check_join_window and record_session_join are called with the unauthenticated
# `client` fixture directly throughout this file (no _as_user/dependency_overrides
# applied) and still get real 200/404/409 responses rather than 401/403 -
# confirming the token itself is the credential, matching immigroov's own
# no-login-required join flow.

# ── POST /booking/join/{token} ──────────────────────────────────────────────

def test_record_session_join_happy_path(client):
    token = str(uuid.uuid4())
    booking_id = str(uuid.uuid4())
    result = {"booking_id": booking_id, "role": "candidate", "ok": True, "meeting_url": "https://meet.jit.si/Immigroov-abc"}
    with patch.object(db, "record_session_join_by_token", return_value=result) as mocked:
        resp = client.post(f"/booking/join/{token}")
    assert resp.status_code == 200
    assert resp.json()["meeting_url"] == "https://meet.jit.si/Immigroov-abc"
    mocked.assert_called_once_with(token)


def test_record_session_join_maps_window_errors_to_409(client):
    for msg in ["Too early to join - check back closer to the start time",
                "The join window for this session has closed",
                "This session is not currently joinable"]:
        with patch.object(db, "record_session_join_by_token", side_effect=Exception(msg)):
            resp = client.post(f"/booking/join/{uuid.uuid4()}")
        assert resp.status_code == 409, msg


def test_record_session_join_404s_on_invalid_token(client):
    with patch.object(db, "record_session_join_by_token", side_effect=Exception("Invalid join link")):
        resp = client.post(f"/booking/join/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── GET /admin/attendance/pending ───────────────────────────────────────────

def test_pending_attendance_reviews_requires_admin(client):
    resp = client.get("/admin/attendance/pending")
    assert resp.status_code in (401, 403)


def test_pending_attendance_reviews_happy_path(client):
    _as_admin(client)
    review_id = str(uuid.uuid4())
    queue = [{"review_id": review_id, "booking_id": str(uuid.uuid4()), "reason": "neither_joined", "created_at": "2026-08-01T10:10:00+00:00"}]
    try:
        with patch.object(db, "admin_attendance_review_queue", return_value=queue) as mocked:
            resp = client.get("/admin/attendance/pending")
        assert resp.status_code == 200
        assert resp.json() == queue
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


# ── POST /admin/attendance/{review_id}/resolve ──────────────────────────────

def test_resolve_attendance_review_requires_admin(client):
    resp = client.post(f"/admin/attendance/{uuid.uuid4()}/resolve", json={"outcome": "no_fault", "note": "n/a"})
    assert resp.status_code in (401, 403)


def test_resolve_attendance_review_happy_path(client):
    _as_admin(client)
    review_id = str(uuid.uuid4())
    try:
        with patch.object(db, "admin_resolve_attendance_review", return_value=None) as mocked:
            resp = client.post(f"/admin/attendance/{review_id}/resolve",
                                json={"outcome": "mentor_fault", "note": "Mentor confirmed they missed it"})
        assert resp.status_code == 200
        assert resp.json() == {"resolved": True}
        mocked.assert_called_once_with(review_id, "mentor_fault", "Mentor confirmed they missed it")
    finally:
        client.app.dependency_overrides.clear()


def test_resolve_attendance_review_maps_already_resolved_to_409(client):
    _as_admin(client)
    try:
        with patch.object(db, "admin_resolve_attendance_review", side_effect=Exception("Review not found or already resolved")):
            resp = client.post(f"/admin/attendance/{uuid.uuid4()}/resolve", json={"outcome": "no_fault", "note": "x"})
        assert resp.status_code == 409
    finally:
        client.app.dependency_overrides.clear()


def test_resolve_attendance_review_maps_missing_note_to_400(client):
    _as_admin(client)
    try:
        with patch.object(db, "admin_resolve_attendance_review", side_effect=Exception("A note is required to resolve an attendance review")):
            resp = client.post(f"/admin/attendance/{uuid.uuid4()}/resolve", json={"outcome": "no_fault", "note": "x"})
        assert resp.status_code == 400
    finally:
        client.app.dependency_overrides.clear()
