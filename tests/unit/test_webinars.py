from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import db
import pytest
from pydantic import ValidationError
from core.auth import AuthUser
from routers.webinars import WebinarBody, admin_action, admin_create, join, register
from services import notifications


def _user() -> AuthUser:
    return AuthUser(id="user-1", email="user@example.com", role="authenticated")


def _webinar(**extra):
    return {
        "id": "webinar-1", "status": "published", "is_paid": False,
        "starts_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "slug": "moving-to-canada", "title": "Moving to Canada",
        "meeting_provider": "google_meet", "meeting_url": "https://meet.google.com/abc-defg-hij", **extra,
    }


def test_admin_create_free_webinar_uses_google_meet():
    body = WebinarBody(title="Moving to Canada", description="A practical introduction", starts_at=datetime.now(timezone.utc) + timedelta(days=2), duration_minutes=60, meeting_url="https://meet.google.com/abc-defg-hij")
    with patch.object(db, "create_webinar", return_value={"id": "webinar-1"}) as create:
        result = admin_create(body, user=_user())
    assert result["id"] == "webinar-1"
    assert create.call_args.args[0]["price"] == 0
    assert create.call_args.args[0]["meeting_provider"] == "google_meet"


def test_cannot_create_past_webinar():
    with pytest.raises(ValidationError, match="future"):
        WebinarBody(
            title="Moving to Canada",
            description="A practical introduction",
            starts_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            duration_minutes=60,
        )


def test_webinar_accepts_public_poster_and_media_urls():
    body = WebinarBody(
        title="Moving to Canada",
        description="A practical introduction",
        starts_at=datetime.now(timezone.utc) + timedelta(days=2),
        duration_minutes=60,
        meeting_url="https://meet.google.com/abc-defg-hij",
        banner_url="https://cdn.example.com/poster.jpg",
        media_url="https://www.youtube.com/watch?v=example",
    )
    assert body.db_fields()["media_url"].startswith("https://")


def test_free_registration_confirms_without_payment_order():
    with patch.object(db, "get_webinar", return_value=_webinar()), patch.object(db, "register", return_value={"id": "reg-1", "status": "confirmed"}):
        result = register("webinar-1", user=_user())
    assert result["payment_required"] is False
    assert result["registration"]["status"] == "confirmed"


def test_paid_registration_creates_razorpay_order():
    paid = _webinar(is_paid=True)
    with patch.object(db, "get_webinar", return_value=paid), patch.object(db, "register", return_value={"id": "reg-1", "amount": 499, "currency": "INR"}), patch.object(db, "create_webinar_razorpay_order", return_value={"id": "order-1", "amount": 49900, "currency": "INR"}):
        result = register("webinar-1", user=_user())
    assert result["payment_required"] is True
    assert result["order"]["order_id"] == "order-1"


def test_join_returns_protected_external_google_meet_url():
    with patch.object(db, "get_profile_role", return_value="candidate"), patch.object(db, "get_mentor_by_profile_id", return_value=None), patch.object(db, "join_details", return_value={"meeting_url": "https://meet.google.com/abc-defg-hij"}):
        result = join("webinar-1", user=_user())
    assert result["meeting_url"] == "https://meet.google.com/abc-defg-hij"


def test_webinar_reminder_uses_masked_join_link():
    due = [{"id": "reg-1", "user_id": "user-1", "webinars": {
        "id": "webinar-1", "slug": "moving-to-canada", "title": "Moving to Canada",
        "starts_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
    }}]
    with patch.object(db, "due_webinar_reminders", return_value=due), \
         patch.object(db, "claim_webinar_reminder", return_value=True), \
         patch.object(db, "webinar_attendee", return_value={"email": "user@example.com", "full_name": "User"}), \
         patch("services.mailer.send_transactional") as send:
        result = notifications.send_webinar_reminders()
    assert result == {"webinar_reminders_sent": 1}
    payload = send.call_args.args[2]
    assert payload["join_url"].endswith("/webinars/moving-to-canada/join")
    assert "meet.google.com" not in payload["join_url"]


def test_webinar_reminder_sends_nothing_when_no_published_webinar_is_due():
    with patch.object(db, "due_webinar_reminders", return_value=[]), \
         patch("services.mailer.send_transactional") as send:
        result = notifications.send_webinar_reminders()
    assert result == {"webinar_reminders_sent": 0}
    send.assert_not_called()


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
