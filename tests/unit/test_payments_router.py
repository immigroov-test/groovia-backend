# tests/unit/test_payments_router.py
# HTTP-layer acceptance tests for routers/payments.py. The money MATH (fee
# derivation, PPP, FX) is tested in tests/sql/pricing_engine_test.sql — these
# tests cover the reserve/confirm/webhook HTTP contract: routing, validation,
# RPC-error -> HTTP-status mapping, webhook signature verification + dedup,
# and the event-type -> handler dispatch, all with db.* mocked.
import hashlib
import hmac
import json
import uuid
from unittest.mock import call, patch

import db
import db.payments as payments
import config


def _future_iso():
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()


# ── /payments/reserve ────────────────────────────────────────────────────────

def test_reserve_happy_path(client):
    fake_result = {"booking_id": str(uuid.uuid4()), "amount": 3333.33, "currency": "INR",
                    "hold_expires_at": "2026-01-01T00:10:00Z"}
    with patch.object(db, "reserve_booking", return_value=fake_result) as mocked:
        resp = client.post("/payments/reserve", json={
            "quote_id": str(uuid.uuid4()), "mentor_id": str(uuid.uuid4()),
            "service_id": str(uuid.uuid4()), "slot_time": _future_iso(),
            "email": "buyer@example.com", "name": "Buyer",
        })
    assert resp.status_code == 200
    assert resp.json() == fake_result
    mocked.assert_called_once()


def test_reserve_quote_expired_maps_to_409(client):
    with patch.object(db, "reserve_booking", side_effect=Exception("QUOTE_EXPIRED: quote has expired")):
        resp = client.post("/payments/reserve", json={
            "quote_id": str(uuid.uuid4()), "mentor_id": str(uuid.uuid4()),
            "service_id": str(uuid.uuid4()), "slot_time": _future_iso(),
            "email": "buyer@example.com",
        })
    assert resp.status_code == 409


def test_reserve_slot_taken_maps_to_409(client):
    with patch.object(db, "reserve_booking", side_effect=Exception("That time was just taken")):
        resp = client.post("/payments/reserve", json={
            "quote_id": str(uuid.uuid4()), "mentor_id": str(uuid.uuid4()),
            "service_id": str(uuid.uuid4()), "slot_time": _future_iso(),
            "email": "buyer@example.com",
        })
    assert resp.status_code == 409


def test_reserve_rejects_invalid_email(client):
    resp = client.post("/payments/reserve", json={
        "quote_id": str(uuid.uuid4()), "mentor_id": str(uuid.uuid4()),
        "service_id": str(uuid.uuid4()), "slot_time": _future_iso(),
        "email": "not-an-email",
    })
    assert resp.status_code == 422


# ── /payments/status/{booking_id} ────────────────────────────────────────────

def test_status_happy_path(client):
    with patch.object(db, "booking_status", return_value="pending"):
        resp = client.get(f"/payments/status/{uuid.uuid4()}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "pending"}


def test_status_missing_booking_is_404(client):
    with patch.object(db, "booking_status", return_value=None):
        resp = client.get(f"/payments/status/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── /payments/razorpay/create-order ─────────────────────────────────────────

def test_create_order_happy_path(client):
    booking_id = str(uuid.uuid4())
    payment = {"id": str(uuid.uuid4()), "booking_id": booking_id, "amount": 100.0,
               "currency": "USD", "state": "created"}
    order = {"id": "order_abc123", "amount": 10000, "currency": "USD"}
    with patch.object(db, "get_payment_by_booking", return_value=payment), \
         patch.object(db, "create_razorpay_order", return_value=order) as mocked_order, \
         patch.object(db, "set_provider_order") as mocked_set, \
         patch.object(db, "record_provider_payload"):
        resp = client.post("/payments/razorpay/create-order", json={"booking_id": booking_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_id"] == "order_abc123"
    assert body["amount"] == 10000
    mocked_order.assert_called_once_with(booking_id=booking_id, amount_major=100.0, currency="USD")
    mocked_set.assert_called_once_with(booking_id, "order_abc123")


def test_create_order_no_payment_is_404(client):
    with patch.object(db, "get_payment_by_booking", return_value=None):
        resp = client.post("/payments/razorpay/create-order", json={"booking_id": str(uuid.uuid4())})
    assert resp.status_code == 404


def test_create_order_already_progressed_is_409(client):
    payment = {"id": str(uuid.uuid4()), "amount": 100.0, "currency": "USD", "state": "captured"}
    with patch.object(db, "get_payment_by_booking", return_value=payment):
        resp = client.post("/payments/razorpay/create-order", json={"booking_id": str(uuid.uuid4())})
    assert resp.status_code == 409


# ── /payments/razorpay/webhook ───────────────────────────────────────────────
# MOCK_SERVICES=true (set in conftest) skips signature verification, matching
# config.py's own documented MOCK_SERVICES behavior ("webhook sig checks").

def _post_webhook(client, event_type: str, payload_entity: dict, event_id: str = None):
    body = {"event": event_type, "payload": payload_entity}
    headers = {}
    if event_id:
        headers["x-razorpay-event-id"] = event_id
    return client.post("/payments/razorpay/webhook", json=body, headers=headers)


def test_webhook_rejects_bad_signature_when_not_mocked(client):
    raw = json.dumps({"event": "payment.captured"}).encode()
    with patch.object(config, "MOCK_SERVICES", False), \
         patch.object(config, "RAZORPAY_WEBHOOK_SECRET", "whsec"):
        resp = client.post(
            "/payments/razorpay/webhook",
            content=raw,
            headers={"content-type": "application/json", "x-razorpay-signature": "wrong"},
        )
    assert resp.status_code == 400


def test_webhook_accepts_good_signature_when_not_mocked(client):
    raw = json.dumps({"event": "payment.failed", "payload": {}}).encode()
    good_sig = hmac.new(b"whsec", raw, hashlib.sha256).hexdigest()
    with patch.object(config, "MOCK_SERVICES", False), \
         patch.object(config, "RAZORPAY_WEBHOOK_SECRET", "whsec"), \
         patch.object(db, "payment_event_seen", return_value=False), \
         patch.object(db, "upsert_payment_event"), \
         patch.object(db, "mark_payment_event_processed"):
        resp = client.post(
            "/payments/razorpay/webhook",
            content=raw,
            headers={"content-type": "application/json", "x-razorpay-signature": good_sig},
        )
    assert resp.status_code == 200


def test_webhook_dedup_skips_reprocessing(client):
    with patch.object(db, "payment_event_seen", return_value=True) as mocked_seen, \
         patch.object(db, "upsert_payment_event") as mocked_upsert:
        resp = _post_webhook(client, "payment.captured", {}, event_id="evt_123")
    assert resp.status_code == 200
    assert resp.json()["dedup"] is True
    mocked_seen.assert_called_once()
    mocked_upsert.assert_not_called()


def test_webhook_payment_captured_confirms_booking(client):
    booking_id = str(uuid.uuid4())
    local_payment = {"id": str(uuid.uuid4()), "booking_id": booking_id}
    remote_payment = {"id": "pay_xyz", "amount": 10000}
    entity = {"payment": {"entity": {"id": "pay_xyz", "order_id": "order_abc"}}}
    with patch.object(db, "payment_event_seen", return_value=False), \
         patch.object(db, "upsert_payment_event"), \
         patch.object(db, "mark_payment_event_processed") as mocked_mark, \
         patch.object(db, "get_payment_by_provider_order", return_value=local_payment), \
         patch.object(db, "fetch_razorpay_payment", return_value=remote_payment), \
         patch.object(payments, "confirm_booking_payment", return_value="confirmed") as mocked_confirm, \
         patch.object(payments, "record_provider_payload"), \
         patch.object(db, "get_booking_notify_info", return_value=None):
        resp = _post_webhook(client, "payment.captured", entity, event_id="evt_captured")
    assert resp.status_code == 200
    mocked_confirm.assert_called_once_with(booking_id, "pay_xyz")
    mocked_mark.assert_called_once_with("evt_captured")  # no error arg = success


def test_webhook_payment_captured_hold_expired_triggers_refund(client):
    booking_id = str(uuid.uuid4())
    local_payment = {"id": str(uuid.uuid4()), "booking_id": booking_id}
    remote_payment = {"id": "pay_xyz", "amount": 10000}
    entity = {"payment": {"entity": {"id": "pay_xyz", "order_id": "order_abc"}}}
    with patch.object(db, "payment_event_seen", return_value=False), \
         patch.object(db, "upsert_payment_event"), \
         patch.object(db, "mark_payment_event_processed") as mocked_mark, \
         patch.object(db, "get_payment_by_provider_order", return_value=local_payment), \
         patch.object(db, "fetch_razorpay_payment", return_value=remote_payment), \
         patch.object(payments, "confirm_booking_payment", side_effect=Exception("HOLD_EXPIRED: too late")), \
         patch.object(payments, "issue_razorpay_refund") as mocked_refund, \
         patch.object(payments, "set_payment_state") as mocked_state:
        resp = _post_webhook(client, "payment.captured", entity, event_id="evt_expired")
    assert resp.status_code == 200
    mocked_refund.assert_called_once_with(payment_id="pay_xyz", amount_minor=10000, booking_id=booking_id)
    # Money was genuinely captured by Razorpay before we refunded it, so the
    # state machine must visit 'captured' before 'refunded' — 'created' ->
    # 'refunded' directly is not a legal transition (set_payment_state's own
    # CHECK). Regression coverage for a bug this refactor fixed: the original
    # webhook-only version of this branch called set_payment_state once,
    # straight to 'refunded', which would have raised in production the first
    # time this branch actually ran against a real 'created'-state payment.
    assert mocked_state.call_args_list == [
        call(local_payment["id"], "captured"),
        call(local_payment["id"], "refunded"),
    ]
    mocked_mark.assert_called_once_with("evt_expired")


def test_webhook_payment_failed_records_error(client):
    local_payment = {"id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4())}
    entity = {"payment": {"entity": {
        "order_id": "order_abc", "error_code": "BAD_CARD", "error_description": "card declined",
    }}}
    with patch.object(db, "payment_event_seen", return_value=False), \
         patch.object(db, "upsert_payment_event"), \
         patch.object(db, "mark_payment_event_processed"), \
         patch.object(db, "get_payment_by_provider_order", return_value=local_payment), \
         patch.object(db, "set_payment_state") as mocked_state, \
         patch.object(db, "record_payment_error") as mocked_error:
        resp = _post_webhook(client, "payment.failed", entity, event_id="evt_failed")
    assert resp.status_code == 200
    mocked_state.assert_called_once_with(local_payment["id"], "failed")
    mocked_error.assert_called_once_with(local_payment["id"], "BAD_CARD", "card declined")


def test_webhook_refund_processed_marks_fully_refunded(client):
    local_payment = {"id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4())}
    entity = {"refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_xyz"}}}
    with patch.object(db, "payment_event_seen", return_value=False), \
         patch.object(db, "upsert_payment_event"), \
         patch.object(db, "mark_payment_event_processed"), \
         patch.object(db, "update_refund_status") as mocked_update, \
         patch.object(db, "get_payment_by_provider_payment_id", return_value=local_payment), \
         patch.object(db, "refund_owed_minor", return_value=0), \
         patch.object(db, "set_payment_state") as mocked_state:
        resp = _post_webhook(client, "refund.processed", entity, event_id="evt_refund")
    assert resp.status_code == 200
    mocked_update.assert_called_once_with("rfnd_1", "processed")
    mocked_state.assert_called_once_with(local_payment["id"], "refunded")


def test_webhook_refund_partial_marks_partially_refunded(client):
    local_payment = {"id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4())}
    entity = {"refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_xyz"}}}
    with patch.object(db, "payment_event_seen", return_value=False), \
         patch.object(db, "upsert_payment_event"), \
         patch.object(db, "mark_payment_event_processed"), \
         patch.object(db, "update_refund_status"), \
         patch.object(db, "get_payment_by_provider_payment_id", return_value=local_payment), \
         patch.object(db, "refund_owed_minor", return_value=500), \
         patch.object(db, "set_payment_state") as mocked_state:
        resp = _post_webhook(client, "refund.processed", entity, event_id="evt_refund_partial")
    assert resp.status_code == 200
    mocked_state.assert_called_once_with(local_payment["id"], "partially_refunded")


def test_webhook_handler_exception_still_returns_200(client):
    entity = {"payment": {"entity": {"id": "pay_xyz", "order_id": "order_abc"}}}
    with patch.object(db, "payment_event_seen", return_value=False), \
         patch.object(db, "upsert_payment_event"), \
         patch.object(db, "mark_payment_event_processed") as mocked_mark, \
         patch.object(db, "get_payment_by_provider_order", side_effect=RuntimeError("db down")):
        resp = _post_webhook(client, "payment.captured", entity, event_id="evt_boom")
    assert resp.status_code == 200
    # error kwarg present = failure was logged, not silently swallowed
    assert mocked_mark.call_args.kwargs.get("error") is not None


# ── /payments/config + /payments/confirm-mock ───────────────────────────────
# Gated at runtime by platform_settings.payments_enabled (business toggle),
# not by MOCK_SERVICES (dev/test env toggle) — see the note in payments.py.

def test_config_reports_payments_enabled(client):
    with patch.object(db, "payments_enabled", return_value=True):
        resp = client.get("/payments/config")
    assert resp.status_code == 200
    assert resp.json() == {"payments_enabled": True}


def test_confirm_mock_happy_path(client):
    booking_id = str(uuid.uuid4())
    with patch.object(db, "payments_enabled", return_value=False), \
         patch.object(db, "confirm_booking_payment", return_value="confirmed") as mocked, \
         patch.object(db, "get_booking_notify_info", return_value=None):
        resp = client.post("/payments/confirm-mock", json={"booking_id": booking_id})
    assert resp.status_code == 200
    assert resp.json() == {"result": "confirmed"}
    mocked.assert_called_once_with(booking_id, f"mock_{booking_id}")


def test_confirm_mock_hold_expired_maps_to_409(client):
    with patch.object(db, "payments_enabled", return_value=False), \
         patch.object(db, "confirm_booking_payment", side_effect=Exception("HOLD_EXPIRED: too late")):
        resp = client.post("/payments/confirm-mock", json={"booking_id": str(uuid.uuid4())})
    assert resp.status_code == 409


def test_confirm_mock_rejected_once_real_payments_enabled(client):
    with patch.object(db, "payments_enabled", return_value=True), \
         patch.object(db, "confirm_booking_payment") as mocked:
        resp = client.post("/payments/confirm-mock", json={"booking_id": str(uuid.uuid4())})
    assert resp.status_code == 403
    mocked.assert_not_called()


# ── to_minor / from_minor ────────────────────────────────────────────────────

def test_to_minor_standard_currency():
    assert db.to_minor(12.34, "USD") == 1234


def test_to_minor_zero_decimal_currency():
    assert db.to_minor(1000, "JPY") == 1000


def test_from_minor_round_trip():
    assert db.from_minor(db.to_minor(99.99, "INR"), "INR") == 99.99


# ── db.finalize_captured_payment (shared webhook/verify/sweep confirm logic) ───
# See DISPATCHER_SAFETY_CHECKLIST.md's razorpay_verify_sweep finding and this
# function's own docstring in db/payments.py for the concurrency contract this
# is regression-testing.

def test_finalize_captured_payment_confirms_and_records_payload():
    local = {"id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4())}
    remote = {"id": "pay_1", "amount": 5000}
    with patch.object(payments, "confirm_booking_payment", return_value="confirmed") as mocked_confirm, \
         patch.object(payments, "record_provider_payload") as mocked_record, \
         patch.object(payments, "issue_razorpay_refund") as mocked_refund:
        result = payments.finalize_captured_payment(local, remote)
    assert result == "confirmed"
    mocked_confirm.assert_called_once_with(local["booking_id"], "pay_1")
    mocked_record.assert_called_once_with(local["id"], remote)
    mocked_refund.assert_not_called()


def test_finalize_captured_payment_already_confirmed_is_a_clean_no_op():
    """Idempotent main path: calling this twice for an already-confirmed
    booking (e.g. the webhook landed, then a verify-sweep also picks it up)
    never touches Razorpay a second time."""
    local = {"id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4())}
    remote = {"id": "pay_1", "amount": 5000}
    with patch.object(payments, "confirm_booking_payment", return_value="already_confirmed"), \
         patch.object(payments, "record_provider_payload") as mocked_record, \
         patch.object(payments, "issue_razorpay_refund") as mocked_refund:
        result = payments.finalize_captured_payment(local, remote)
    assert result == "already_confirmed"
    mocked_record.assert_called_once_with(local["id"], remote)
    mocked_refund.assert_not_called()


def test_finalize_captured_payment_hold_expired_visits_captured_before_refunded():
    """Regression test for the state-machine bug fixed in this refactor: going
    straight from 'created' to 'refunded' is not a legal transition per
    set_payment_state's own CHECK (see migrations/production_db_setup.sql) —
    the payment was genuinely captured by Razorpay before we refund it, so the
    state machine must visit 'captured' first."""
    local = {"id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4())}
    remote = {"id": "pay_1", "amount": 5000}
    with patch.object(payments, "confirm_booking_payment", side_effect=Exception("HOLD_EXPIRED: too late")), \
         patch.object(payments, "issue_razorpay_refund") as mocked_refund, \
         patch.object(payments, "set_payment_state") as mocked_state:
        result = payments.finalize_captured_payment(local, remote)
    assert result == "refunded"
    mocked_refund.assert_called_once_with(payment_id="pay_1", amount_minor=5000, booking_id=local["booking_id"])
    assert mocked_state.call_args_list == [call(local["id"], "captured"), call(local["id"], "refunded")]


def test_finalize_captured_payment_reraises_non_hold_expired_errors():
    local = {"id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4())}
    remote = {"id": "pay_1", "amount": 5000}
    with patch.object(payments, "confirm_booking_payment", side_effect=RuntimeError("db is down")):
        try:
            payments.finalize_captured_payment(local, remote)
            assert False, "expected the non-HOLD_EXPIRED error to propagate"
        except RuntimeError as e:
            assert "db is down" in str(e)


def test_finalize_captured_payment_concurrent_hold_expired_calls_share_one_idempotency_key():
    """Demonstrates the actual race this function is safe against: two
    'concurrent' callers (simulated sequentially here — a true DB-level race
    can't be reproduced without live Postgres, same limitation noted
    throughout MIGRATION_STATUS.md) both hit HOLD_EXPIRED for the SAME
    payment. Both attempt a refund (that part can't be prevented at the
    Python layer without a lock this function doesn't take), but what
    prevents an actual double refund is that both calls compute the exact
    same Razorpay idempotency key — booking_id + amount_minor, not a version
    counter — so Razorpay itself collapses them into one real refund. This
    test asserts that determinism directly: the key must be byte-identical
    across both calls."""
    local = {"id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4())}
    remote = {"id": "pay_1", "amount": 5000}

    captured_keys = []

    def fake_issue_refund(*, payment_id, amount_minor, booking_id):
        # Mirrors the real key formula in db/payments.py::issue_razorpay_refund.
        captured_keys.append(f"refund-{booking_id}-{amount_minor}")
        return {"id": "rfnd_1", "status": "processed"}

    with patch.object(payments, "confirm_booking_payment", side_effect=Exception("HOLD_EXPIRED: too late")), \
         patch.object(payments, "issue_razorpay_refund", side_effect=fake_issue_refund), \
         patch.object(payments, "set_payment_state"):
        payments.finalize_captured_payment(local, remote)   # "run 1"
        payments.finalize_captured_payment(local, remote)   # "run 2" — the race

    assert len(captured_keys) == 2
    assert captured_keys[0] == captured_keys[1], (
        "both racing calls must produce the identical Razorpay idempotency key, "
        "or a real double refund becomes possible"
    )


# ── db.verify_order ──────────────────────────────────────────────────────────

def test_verify_order_unknown_order():
    with patch.object(payments, "get_payment_by_provider_order", return_value=None):
        result = payments.verify_order("order_missing")
    assert result == {"order_id": "order_missing", "error": "unknown order"}


def test_verify_order_already_captured_short_circuits_without_calling_razorpay():
    local = {"id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4()), "state": "captured"}
    with patch.object(payments, "get_payment_by_provider_order", return_value=local), \
         patch.object(payments, "fetch_razorpay_order_payments") as mocked_fetch:
        result = payments.verify_order("order_1")
    assert result == {"order_id": "order_1", "confirmed": True, "status": "already"}
    mocked_fetch.assert_not_called()


def test_verify_order_no_captured_payment_yet():
    local = {"id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4()), "state": "created"}
    with patch.object(payments, "get_payment_by_provider_order", return_value=local), \
         patch.object(payments, "fetch_razorpay_order_payments", return_value=[{"status": "authorized"}]):
        result = payments.verify_order("order_1")
    assert result == {"order_id": "order_1", "confirmed": False, "status": "authorized"}


def test_verify_order_captured_payment_finalizes():
    local = {"id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4()), "state": "created"}
    captured = {"id": "pay_1", "status": "captured", "amount": 5000}
    with patch.object(payments, "get_payment_by_provider_order", return_value=local), \
         patch.object(payments, "fetch_razorpay_order_payments", return_value=[captured]), \
         patch.object(payments, "finalize_captured_payment", return_value="confirmed") as mocked_finalize:
        result = payments.verify_order("order_1")
    assert result == {"order_id": "order_1", "confirmed": True, "status": "confirmed"}
    mocked_finalize.assert_called_once_with(local, captured)


# ── db.sweep_verify_payments ─────────────────────────────────────────────────

def test_sweep_verify_payments_processes_each_candidate_independently():
    rows = [{"provider_order_id": "order_1"}, {"provider_order_id": "order_2"}]

    def fake_verify(order_id):
        if order_id == "order_1":
            raise RuntimeError("razorpay timeout")
        return {"order_id": order_id, "confirmed": True, "status": "confirmed"}

    class FakeQuery:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        @property
        def not_(self): return self
        def is_(self, *a, **k): return self
        def gte(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self):
            class R:
                data = rows
            return R()

    with patch.object(payments, "_supabase") as mock_supabase, \
         patch.object(payments, "verify_order", side_effect=fake_verify) as mocked_verify:
        mock_supabase.table.return_value = FakeQuery()
        result = payments.sweep_verify_payments()

    assert result["swept"] == 2
    assert result["confirmed"] == 1
    assert mocked_verify.call_count == 2
    # The failure on order_1 must not stop order_2 from being processed.
    assert result["results"][0]["error"] == "razorpay timeout"
    assert result["results"][1]["confirmed"] is True


# ── POST /payments/verify (single-booking, request-driven) ─────────────────────

def test_verify_endpoint_by_order_id(client):
    with patch.object(db, "verify_order", return_value={"order_id": "order_1", "confirmed": True, "status": "confirmed"}) as mocked:
        resp = client.post("/payments/verify", json={"order_id": "order_1"})
    assert resp.status_code == 200
    assert resp.json()["confirmed"] is True
    mocked.assert_called_once_with("order_1")


def test_verify_endpoint_by_booking_id_resolves_order(client):
    booking_id = str(uuid.uuid4())
    with patch.object(db, "get_payment_by_booking", return_value={"provider_order_id": "order_9"}), \
         patch.object(db, "verify_order", return_value={"order_id": "order_9", "confirmed": False, "status": "none"}) as mocked:
        resp = client.post("/payments/verify", json={"booking_id": booking_id})
    assert resp.status_code == 200
    mocked.assert_called_once_with("order_9")


def test_verify_endpoint_no_order_maps_to_404(client):
    with patch.object(db, "get_payment_by_booking", return_value=None):
        resp = client.post("/payments/verify", json={"booking_id": str(uuid.uuid4())})
    assert resp.status_code == 404


# ── POST /admin/payments/verify-sweep ───────────────────────────────────────────

def test_admin_verify_sweep_requires_admin(client):
    resp = client.post("/admin/payments/verify-sweep")
    assert resp.status_code in (401, 403)


def test_admin_verify_sweep_happy_path(client):
    from core.auth import AuthUser, get_current_user
    admin_user = AuthUser(id=str(uuid.uuid4()), email="admin@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: admin_user
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "sweep_verify_payments", return_value={"ok": True, "swept": 3, "confirmed": 1, "results": []}) as mocked:
            resp = client.post("/admin/payments/verify-sweep")
        assert resp.status_code == 200
        assert resp.json()["swept"] == 3
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()
