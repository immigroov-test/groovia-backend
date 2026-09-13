from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import db
from core.auth import AuthUser
from routers.webinars import WebinarBody, admin_action, admin_create, join, register


def _user() -> AuthUser:
    return AuthUser(id="user-1", email="user@example.com", role="authenticated")


def _webinar(**extra):
    return {
        "id": "webinar-1", "status": "published", "is_paid": False,
        "starts_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "meeting_provider": "jitsi_public", "meeting_room": "secret-room", **extra,
    }


def test_admin_create_free_webinar_generates_zero_price():
    body = WebinarBody(title="Moving to Canada", description="A practical introduction", starts_at=datetime.now(timezone.utc) + timedelta(days=2), duration_minutes=60)
    with patch.object(db, "create_webinar", return_value={"id": "webinar-1"}) as create:
        result = admin_create(body, user=_user())
    assert result["id"] == "webinar-1"
    assert create.call_args.args[0]["price"] == 0
    assert create.call_args.args[0]["meeting_provider"] == "jitsi_public"


def test_free_registration_confirms_without_payment_order():
    with patch.object(db, "get_webinar", return_value=_webinar()), patch.object(db, "register", return_value={"id": "reg-1", "status": "confirmed"}):
        result = register("webinar-1", user=_user())
    assert result["payment_required"] is False
    assert result["registration"]["status"] == "confirmed"


def test_paid_registration_creates_razorpay_order():
    paid = _webinar(is_paid=True)
    with patch.object(db, "get_webinar", return_value=paid), patch.object(db, "register", return_value={"id": "reg-1", "amount": 499, "currency": "INR"}), patch.object(db, "create_razorpay_order", return_value={"id": "order-1", "amount": 49900, "currency": "INR"}):
        result = register("webinar-1", user=_user())
    assert result["payment_required"] is True
    assert result["order"]["order_id"] == "order-1"


def test_join_returns_protected_external_jitsi_url():
    with patch.object(db, "get_profile_role", return_value="candidate"), patch.object(db, "get_mentor_by_profile_id", return_value=None), patch.object(db, "join_details", return_value={"meeting_url": "https://meet.jit.si/secret-room"}):
        result = join("webinar-1", user=_user())
    assert result["meeting_url"] == "https://meet.jit.si/secret-room"


def test_cannot_publish_past_webinar():
    old = _webinar(starts_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    with patch.object(db, "get_webinar", return_value=old):
        try:
            admin_action("webinar-1", "publish", user=_user())
            assert False, "expected conflict"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 409


def test_webhook_finalizer_rejects_wrong_amount():
    registration = {"id": "reg-1", "amount": 499, "currency": "INR", "provider_order_id": "order-1"}
    remote = {"id": "pay-1", "order_id": "order-1", "amount": 1, "currency": "INR", "status": "captured"}
    try:
        db.finalize_webinar_payment(registration, remote)
        assert False, "expected mismatch"
    except ValueError as exc:
        assert str(exc) == "PAYMENT_MISMATCH"
