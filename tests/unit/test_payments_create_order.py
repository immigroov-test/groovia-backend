"""The booking order route, end to end through the router with only the outbound Razorpay call
replaced. This is the route that every paid booking goes through and that nothing exercised before
a name collision in db broke it on production."""
from unittest.mock import patch

import db
from db import payments as db_payments


class _Resp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return {"id": "order_test_1", "amount": 229215, "currency": "INR"}


def test_create_order_builds_a_booking_order(client):
    seen = {}

    def fake_post(url, **kw):
        seen.update(url=url, json=kw.get("json"), idem=(kw.get("headers") or {}).get("X-Razorpay-Idempotency-Key"))
        return _Resp()

    with patch.object(db, "get_payment_by_booking", return_value={"id": "pay-1", "state": "created", "amount": 2292.15, "currency": "INR"}), \
         patch.object(db_payments.httpx, "post", side_effect=fake_post), \
         patch.object(db, "set_provider_order") as set_order, \
         patch.object(db, "record_provider_payload"):
        resp = client.post("/payments/razorpay/create-order", json={"booking_id": "booking-1"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["order_id"] == "order_test_1"
    assert seen["url"].endswith("/orders")
    assert seen["json"]["amount"] == 229215 and seen["json"]["currency"] == "INR"
    assert seen["idem"] == "booking-booking-1"
    set_order.assert_called_once_with("booking-1", "order_test_1")


def test_create_order_refuses_a_settled_payment(client):
    with patch.object(db, "get_payment_by_booking", return_value={"id": "pay-1", "state": "captured", "amount": 1, "currency": "INR"}):
        resp = client.post("/payments/razorpay/create-order", json={"booking_id": "booking-1"})
    assert resp.status_code == 409
