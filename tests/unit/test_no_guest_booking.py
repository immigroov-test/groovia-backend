# tests/unit/test_no_guest_booking.py
# BUG-025: bookings must not be creatable without an account. POST /payments/reserve
# (the quote->reserve->confirm flow the widget actually uses) and POST /booking (a
# legacy, superseded-but-still-routed endpoint) both used to accept
# get_current_user_optional, letting an unauthenticated request through as a guest
# with candidate_id=None. Both now require get_current_user - verify the endpoint
# rejects an unauthenticated caller and still works for a real one.
import uuid
from unittest.mock import patch

import db
from core.auth import AuthUser, get_current_user


def _as_user(client):
    user = AuthUser(id=str(uuid.uuid4()), email="candidate@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: user
    return user


def _reserve_body(**overrides):
    body = {
        "quote_id": "quote-1", "mentor_id": "mentor-1", "service_id": "service-1",
        "slot_time": "2026-08-01T10:00:00Z", "email": "candidate@example.com", "phone": "+15551234567",
    }
    body.update(overrides)
    return body


def _booking_body(**overrides):
    body = {
        "mentor_id": "mentor-1", "service_id": "service-1", "slot_time": "2026-08-01T10:00:00Z",
        "email": "candidate@example.com", "phone": "+15551234567",
    }
    body.update(overrides)
    return body


# ── POST /payments/reserve ───────────────────────────────────────────────────────

def test_reserve_rejects_unauthenticated_request(client):
    resp = client.post("/payments/reserve", json=_reserve_body())
    assert resp.status_code in (401, 403)


def test_reserve_succeeds_for_authenticated_user_and_attaches_candidate_id(client):
    user = _as_user(client)
    try:
        with patch.object(db, "reserve_booking", return_value={"booking_id": "booking-1"}) as mocked_reserve, \
             patch.object(db, "set_booking_phone"), patch.object(db, "set_profile_phone_if_empty"):
            resp = client.post("/payments/reserve", json=_reserve_body())
        assert resp.status_code == 200
        assert mocked_reserve.call_args.kwargs["candidate_id"] == user.id
    finally:
        client.app.dependency_overrides.clear()


# ── POST /booking (legacy) ───────────────────────────────────────────────────────

def test_legacy_booking_endpoint_rejects_unauthenticated_request(client):
    resp = client.post("/booking", json=_booking_body())
    assert resp.status_code in (401, 403)


def test_legacy_booking_endpoint_succeeds_for_authenticated_user(client):
    user = _as_user(client)
    try:
        with patch.object(db, "get_booking_by_idempotency_key", return_value=None), \
             patch.object(db, "book_session", return_value=[{"booking_id": "booking-2", "status": "confirmed"}]) as mocked_book, \
             patch.object(db, "set_booking_phone"), patch.object(db, "set_profile_phone_if_empty"), \
             patch("routers.booking._send_booking_confirmation"):
            resp = client.post("/booking", json=_booking_body())
        assert resp.status_code == 200
        assert mocked_book.call_args.kwargs["candidate_id"] == user.id
    finally:
        client.app.dependency_overrides.clear()
