# tests/unit/test_payments_verify.py
# BUG-024: customer never received a booking-confirmation email. Root cause: POST
# /payments/verify (the webhook-independent backstop the frontend calls right after
# Checkout succeeds, used whenever Razorpay's webhook hasn't landed - e.g. no public
# HTTPS URL registered, common in local/staging dev) finalized the payment via the
# same db.verify_order()/finalize_captured_payment() path as the webhook handler, but
# never sent the confirmation email the webhook path does. Fixed by firing
# _send_confirmation_email as a BackgroundTask on a fresh confirmation.
from unittest.mock import patch

import db


def test_verify_sends_confirmation_email_on_fresh_confirmation(client):
    with patch.object(db, "verify_order", return_value={"order_id": "order_1", "confirmed": True, "status": "confirmed"}), \
         patch.object(db, "get_payment_by_provider_order", return_value={"booking_id": "booking-1"}), \
         patch("routers.payments._send_confirmation_email") as mocked_email:
        resp = client.post("/payments/verify", json={"order_id": "order_1"})
    assert resp.status_code == 200
    assert resp.json()["confirmed"] is True
    mocked_email.assert_called_once_with("booking-1")


def test_verify_does_not_resend_email_when_already_confirmed(client):
    with patch.object(db, "verify_order", return_value={"order_id": "order_1", "confirmed": True, "status": "already"}), \
         patch.object(db, "get_payment_by_provider_order") as mocked_lookup, \
         patch("routers.payments._send_confirmation_email") as mocked_email:
        resp = client.post("/payments/verify", json={"order_id": "order_1"})
    assert resp.status_code == 200
    mocked_email.assert_not_called()
    mocked_lookup.assert_not_called()


def test_verify_does_not_send_email_when_not_yet_captured(client):
    with patch.object(db, "verify_order", return_value={"order_id": "order_1", "confirmed": False, "status": "none"}), \
         patch("routers.payments._send_confirmation_email") as mocked_email:
        resp = client.post("/payments/verify", json={"order_id": "order_1"})
    assert resp.status_code == 200
    assert resp.json()["confirmed"] is False
    mocked_email.assert_not_called()


def test_verify_resolves_order_id_from_booking_id(client):
    with patch.object(db, "get_payment_by_booking", return_value={"provider_order_id": "order_from_booking"}), \
         patch.object(db, "verify_order", return_value={"order_id": "order_from_booking", "confirmed": False, "status": "none"}) as mocked_verify:
        resp = client.post("/payments/verify", json={"booking_id": "booking-1"})
    assert resp.status_code == 200
    mocked_verify.assert_called_once_with("order_from_booking")


def test_verify_404_when_no_order_found(client):
    with patch.object(db, "get_payment_by_booking", return_value=None):
        resp = client.post("/payments/verify", json={"booking_id": "booking-1"})
    assert resp.status_code == 404
