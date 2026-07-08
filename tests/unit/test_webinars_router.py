# tests/unit/test_webinars_router.py
# HTTP-layer acceptance tests for routers/webinars.py + the webinar-adjacent
# endpoints added to routers/mentor.py and routers/admin.py. The SQL business
# rules (idempotent registration, capacity gating, timeline/reminder windows)
# live in the RPCs ported in the Webinars migration commit and are NOT
# re-verified here (no live Postgres in this environment, consistent with
# every prior module) — these tests cover request validation, routing,
# ownership-gating, and RPC-error -> HTTP-status mapping, with db.* mocked.
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import db
from core.auth import AuthUser, get_current_user


@contextmanager
def _as_mentor(client, mentor_row):
    user = AuthUser(id=str(uuid.uuid4()), email="mentor@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        with patch.object(db, "get_mentor_by_profile_id", return_value=mentor_row):
            yield user
    finally:
        client.app.dependency_overrides.clear()


def _as_admin(client):
    admin_user = AuthUser(id=str(uuid.uuid4()), email="admin@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: admin_user
    return admin_user


# ── GET /webinars ──────────────────────────────────────────────────────────────

def test_list_webinars_public(client):
    fake = [{"id": str(uuid.uuid4()), "title": "Visa Q&A"}]
    with patch.object(db, "list_webinars", return_value=fake) as mocked:
        resp = client.get("/webinars")
    assert resp.status_code == 200
    assert resp.json() == fake
    mocked.assert_called_once()


# ── GET /webinars/{id} ─────────────────────────────────────────────────────────

def test_webinar_detail_happy_path(client):
    webinar_id = str(uuid.uuid4())
    fake = {"id": webinar_id, "title": "Visa Q&A", "status": "scheduled"}
    with patch.object(db, "webinar_public", return_value=fake):
        resp = client.get(f"/webinars/{webinar_id}")
    assert resp.status_code == 200
    assert resp.json() == fake


def test_webinar_detail_not_found_maps_to_404(client):
    with patch.object(db, "webinar_public", return_value=None):
        resp = client.get(f"/webinars/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── POST /webinars/{id}/register ──────────────────────────────────────────────

def test_register_happy_path_sends_confirmation(client):
    webinar_id = str(uuid.uuid4())
    fake_result = {"ok": True, "already": False, "room_url": "https://meet.jit.si/x",
                    "title": "Visa Q&A", "start_time": "2026-08-01T10:00:00Z"}
    with patch.object(db, "register_webinar", return_value=fake_result) as mocked, \
         patch("services.mailer.send_transactional") as mocked_send:
        resp = client.post(f"/webinars/{webinar_id}/register", json={"email": "a@example.com", "name": "A"})
    assert resp.status_code == 200
    assert resp.json() == fake_result
    mocked.assert_called_once_with(webinar_id, "a@example.com", "A")
    mocked_send.assert_called_once()
    assert mocked_send.call_args.args[0] == "a@example.com"
    assert mocked_send.call_args.args[1] == "webinar_registered"


def test_register_already_registered_no_duplicate_email(client):
    webinar_id = str(uuid.uuid4())
    fake_result = {"ok": True, "already": True, "room_url": "https://meet.jit.si/x",
                    "title": "Visa Q&A", "start_time": "2026-08-01T10:00:00Z"}
    with patch.object(db, "register_webinar", return_value=fake_result), \
         patch("services.mailer.send_transactional") as mocked_send:
        resp = client.post(f"/webinars/{webinar_id}/register", json={"email": "a@example.com"})
    assert resp.status_code == 200
    mocked_send.assert_not_called()


def test_register_invalid_email_rejected(client):
    resp = client.post(f"/webinars/{uuid.uuid4()}/register", json={"email": "not-an-email"})
    assert resp.status_code == 422


def test_register_not_found_maps_to_404(client):
    with patch.object(db, "register_webinar", side_effect=Exception("Webinar not found")):
        resp = client.post(f"/webinars/{uuid.uuid4()}/register", json={"email": "a@example.com"})
    assert resp.status_code == 404


def test_register_full_maps_to_409(client):
    with patch.object(db, "register_webinar", side_effect=Exception("This webinar is full")):
        resp = client.post(f"/webinars/{uuid.uuid4()}/register", json={"email": "a@example.com"})
    assert resp.status_code == 409


def test_register_already_started_maps_to_409(client):
    with patch.object(db, "register_webinar", side_effect=Exception("This webinar has already started")):
        resp = client.post(f"/webinars/{uuid.uuid4()}/register", json={"email": "a@example.com"})
    assert resp.status_code == 409


# ── Mentor self-service ────────────────────────────────────────────────────────

def test_list_my_webinars_requires_mentor_profile(client):
    with _as_mentor(client, None):
        resp = client.get("/mentor/webinars")
    assert resp.status_code == 404


def test_list_my_webinars_happy_path(client):
    mentor = {"id": str(uuid.uuid4())}
    with _as_mentor(client, mentor):
        with patch.object(db, "mentor_webinars", return_value=[{"id": "w1"}]) as mocked:
            resp = client.get("/mentor/webinars")
    assert resp.status_code == 200
    assert resp.json() == [{"id": "w1"}]
    mocked.assert_called_once_with(mentor["id"])


def test_create_webinar_happy_path(client):
    mentor = {"id": str(uuid.uuid4())}
    with _as_mentor(client, mentor):
        with patch.object(db, "create_webinar", return_value="w1") as mocked:
            resp = client.post("/mentor/webinars", json={
                "title": "Visa Q&A", "start_time": "2026-08-01T10:00:00Z",
            })
    assert resp.status_code == 200
    assert resp.json() == {"id": "w1"}
    mocked.assert_called_once()


def test_create_webinar_invalid_visibility_rejected(client):
    mentor = {"id": str(uuid.uuid4())}
    with _as_mentor(client, mentor):
        resp = client.post("/mentor/webinars", json={
            "title": "Visa Q&A", "start_time": "2026-08-01T10:00:00Z", "visibility": "secret",
        })
    assert resp.status_code == 422


def test_create_webinar_past_start_maps_to_400(client):
    mentor = {"id": str(uuid.uuid4())}
    with _as_mentor(client, mentor):
        with patch.object(db, "create_webinar", side_effect=Exception("Start time must be in the future")):
            resp = client.post("/mentor/webinars", json={
                "title": "Visa Q&A", "start_time": "2020-01-01T10:00:00Z",
            })
    assert resp.status_code == 400


def test_cancel_my_webinar_happy_path(client):
    mentor = {"id": str(uuid.uuid4())}
    webinar_id = str(uuid.uuid4())
    with _as_mentor(client, mentor):
        with patch.object(db, "get_webinar_mentor_id", return_value=mentor["id"]), \
             patch.object(db, "cancel_webinar") as mocked:
            resp = client.post(f"/mentor/webinars/{webinar_id}/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"cancelled": True}
    mocked.assert_called_once_with(webinar_id)


def test_cancel_someone_elses_webinar_maps_to_403(client):
    mentor = {"id": str(uuid.uuid4())}
    other_mentor_id = str(uuid.uuid4())
    webinar_id = str(uuid.uuid4())
    with _as_mentor(client, mentor):
        with patch.object(db, "get_webinar_mentor_id", return_value=other_mentor_id):
            resp = client.post(f"/mentor/webinars/{webinar_id}/cancel")
    assert resp.status_code == 403


def test_cancel_unknown_webinar_maps_to_404(client):
    mentor = {"id": str(uuid.uuid4())}
    with _as_mentor(client, mentor):
        with patch.object(db, "get_webinar_mentor_id", return_value=None):
            resp = client.post(f"/mentor/webinars/{uuid.uuid4()}/cancel")
    assert resp.status_code == 404


def test_my_webinar_registrants_happy_path(client):
    mentor = {"id": str(uuid.uuid4())}
    webinar_id = str(uuid.uuid4())
    with _as_mentor(client, mentor):
        with patch.object(db, "get_webinar_mentor_id", return_value=mentor["id"]), \
             patch.object(db, "webinar_registrants", return_value=[{"email": "a@example.com"}]) as mocked:
            resp = client.get(f"/mentor/webinars/{webinar_id}/registrants")
    assert resp.status_code == 200
    assert resp.json() == [{"email": "a@example.com"}]
    mocked.assert_called_once_with(webinar_id)


# ── Admin ──────────────────────────────────────────────────────────────────────

def test_admin_webinars_requires_admin(client):
    user = AuthUser(id=str(uuid.uuid4()), email="candidate@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        with patch.object(db, "get_profile_role", return_value="candidate"):
            resp = client.get("/admin/webinars")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


def test_admin_webinars_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_webinars", return_value=[{"id": "w1"}]) as mocked:
            resp = client.get("/admin/webinars")
        assert resp.status_code == 200
        assert resp.json() == [{"id": "w1"}]
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_admin_webinar_registrants_no_ownership_check(client):
    _as_admin(client)
    webinar_id = str(uuid.uuid4())
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "webinar_registrants", return_value=[{"email": "a@example.com"}]) as mocked:
            resp = client.get(f"/admin/webinars/{webinar_id}/registrants")
        assert resp.status_code == 200
        mocked.assert_called_once_with(webinar_id)
    finally:
        client.app.dependency_overrides.clear()


def test_send_webinar_reminders_happy_path(client):
    _as_admin(client)
    due = [
        {"webinar_id": "w1", "stage": "1d", "title": "Visa Q&A", "start_time": "2026-08-01T10:00:00Z",
         "room_url": "https://x", "registrant_email": "a@example.com", "registrant_name": "A"},
        {"webinar_id": "w1", "stage": "1d", "title": "Visa Q&A", "start_time": "2026-08-01T10:00:00Z",
         "room_url": "https://x", "registrant_email": "b@example.com", "registrant_name": "B"},
    ]
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "claim_due_webinar_reminders", return_value=due) as mocked_claim, \
             patch("services.mailer.send_transactional") as mocked_send:
            resp = client.post("/admin/webinars/send-reminders")
        assert resp.status_code == 200
        assert resp.json() == {"emails_sent": 2, "webinars_marked": 1}
        assert mocked_send.call_count == 2
        mocked_claim.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_send_webinar_reminders_second_overlapping_call_sends_nothing_new(client):
    """Regression test for the race the old due_webinar_reminders()/
    mark_webinar_reminded() split had: a read-only "what's due" query plus a
    separate "mark it sent" write meant two overlapping calls could both read
    the same due webinar before either marked it, so both would send —
    duplicate emails to every registrant. claim_due_webinar_reminders()
    claims atomically (the SQL UPDATE ... RETURNING flips the flag as part of
    selecting the rows), so a second call for the same tick sees nothing left
    to claim. Simulated here as two sequential admin-endpoint calls where the
    second's claim mock returns empty — exactly what the real RPC would
    return for a row another caller already claimed."""
    _as_admin(client)
    due = [
        {"webinar_id": "w1", "stage": "1h", "title": "Visa Q&A", "start_time": "2026-08-01T10:00:00Z",
         "room_url": "https://x", "registrant_email": "a@example.com", "registrant_name": "A"},
    ]
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "claim_due_webinar_reminders", side_effect=[due, []]), \
             patch("services.mailer.send_transactional") as mocked_send:
            first = client.post("/admin/webinars/send-reminders")
            second = client.post("/admin/webinars/send-reminders")
        assert first.status_code == 200 and first.json()["emails_sent"] == 1
        assert second.status_code == 200 and second.json()["emails_sent"] == 0
        mocked_send.assert_called_once()   # not twice — the registrant was only emailed by the first call
    finally:
        client.app.dependency_overrides.clear()


def test_send_webinar_reminders_requires_admin(client):
    user = AuthUser(id=str(uuid.uuid4()), email="candidate@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        with patch.object(db, "get_profile_role", return_value="candidate"):
            resp = client.post("/admin/webinars/send-reminders")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()
