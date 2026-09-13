# tests/unit/test_guest_booking.py
# Guest checkout (flight-style): POST /payments/reserve (the quote->reserve->confirm flow the
# widget uses) and POST /booking (legacy, superseded-but-still-routed) both accept guests via
# get_current_user_optional. A guest books with candidate_id=None and their identity in
# candidate_email/name/phone (claimed on later signup); a signed-in caller attaches candidate_id.
import uuid
from unittest.mock import patch

import db
from core.auth import AuthUser, get_current_user_optional


def _as_user(client):
    # Both endpoints resolve the caller via get_current_user_optional, so override THAT.
    user = AuthUser(id=str(uuid.uuid4()), email="candidate@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user_optional] = lambda: user
    return user


def _reserve_body(**overrides):
    body = {
        "quote_id": "quote-1", "mentor_id": "mentor-1", "service_id": "service-1",
        "slot_time": "2026-08-01T10:00:00Z", "email": "candidate@example.com", "phone": "+15551234567",
        # Checkout now gates on the consent checkbox (Consent Flow Spec Section 4).
        "accepted_terms": True,
    }
    body.update(overrides)
    return body


def _booking_body(**overrides):
    body = {
        "mentor_id": "mentor-1", "service_id": "service-1", "slot_time": "2026-08-01T10:00:00Z",
        "email": "candidate@example.com", "phone": "+15551234567",
        # Same consent gate on the free/mock-confirm half of checkout.
        "accepted_terms": True,
    }
    body.update(overrides)
    return body


# ── POST /payments/reserve ───────────────────────────────────────────────────────

def test_reserve_allows_guest_with_null_candidate_id(client):
    with patch.object(db, "reserve_booking", return_value={"booking_id": "booking-1"}) as mocked_reserve, \
         patch.object(db, "set_booking_phone"), patch.object(db, "set_profile_phone_if_empty"), \
         patch.object(db, "record_legal_consent"), \
         patch.object(db, "is_self_booking", return_value=False):
        resp = client.post("/payments/reserve", json=_reserve_body(email="guest@example.com"))
    assert resp.status_code == 200
    assert mocked_reserve.call_args.kwargs["candidate_id"] is None
    assert mocked_reserve.call_args.kwargs["email"] == "guest@example.com"


def test_reserve_attaches_candidate_id_for_authenticated_user(client):
    user = _as_user(client)
    try:
        with patch.object(db, "reserve_booking", return_value={"booking_id": "booking-1"}) as mocked_reserve, \
             patch.object(db, "set_booking_phone"), patch.object(db, "set_profile_phone_if_empty"), \
         patch.object(db, "record_legal_consent"), \
         patch.object(db, "is_self_booking", return_value=False):
            resp = client.post("/payments/reserve", json=_reserve_body())
        assert resp.status_code == 200
        assert mocked_reserve.call_args.kwargs["candidate_id"] == user.id
    finally:
        client.app.dependency_overrides.clear()


# ── POST /booking (legacy) ───────────────────────────────────────────────────────

def test_legacy_booking_allows_guest_with_null_candidate_id(client):
    with patch.object(db, "get_booking_by_idempotency_key", return_value=None), \
         patch.object(db, "book_session", return_value=[{"booking_id": "booking-2", "status": "confirmed"}]) as mocked_book, \
         patch.object(db, "set_booking_phone"), patch.object(db, "set_profile_phone_if_empty"), \
         patch.object(db, "record_legal_consent"), \
         patch.object(db, "is_self_booking", return_value=False), \
         patch("routers.booking._send_booking_confirmation"):
        resp = client.post("/booking", json=_booking_body(email="guest@example.com"))
    assert resp.status_code == 200
    assert mocked_book.call_args.kwargs["candidate_id"] is None
    assert mocked_book.call_args.kwargs["email"] == "guest@example.com"


def test_legacy_booking_attaches_candidate_id_for_authenticated_user(client):
    user = _as_user(client)
    try:
        with patch.object(db, "get_booking_by_idempotency_key", return_value=None), \
             patch.object(db, "book_session", return_value=[{"booking_id": "booking-2", "status": "confirmed"}]) as mocked_book, \
             patch.object(db, "set_booking_phone"), patch.object(db, "set_profile_phone_if_empty"), \
         patch.object(db, "record_legal_consent"), \
         patch.object(db, "is_self_booking", return_value=False), \
             patch("routers.booking._send_booking_confirmation"):
            resp = client.post("/booking", json=_booking_body())
        assert resp.status_code == 200
        assert mocked_book.call_args.kwargs["candidate_id"] == user.id
    finally:
        client.app.dependency_overrides.clear()
