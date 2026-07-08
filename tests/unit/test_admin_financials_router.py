# tests/unit/test_admin_financials_router.py
# HTTP-layer acceptance tests for the Admin Financials endpoints added to
# routers/admin.py (admin_payouts/admin_ledger/admin_booking_detail read RPCs,
# plus the mark-paid/block write RPCs that already existed from the Payments
# module). The SQL business rules (fee_pct fallback, ledger bucket sums,
# timeline reconstruction) live in the RPCs ported in this module's SQL
# commit and are NOT re-verified here (no live Postgres in this environment,
# consistent with every prior module) — these tests cover request routing,
# admin-gating, and RPC-error -> HTTP-status mapping, with db.* mocked.
import uuid
from unittest.mock import patch

import db
from core.auth import AuthUser, get_current_user


def _as_admin(client):
    admin_user = AuthUser(id=str(uuid.uuid4()), email="admin@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: admin_user
    return admin_user


def _as_user(client):
    user = AuthUser(id=str(uuid.uuid4()), email="candidate@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: user
    return user


# ── GET /admin/payouts ────────────────────────────────────────────────────────

def test_list_payouts_requires_admin(client):
    _as_user(client)
    try:
        with patch.object(db, "get_profile_role", return_value="candidate"):
            resp = client.get("/admin/payouts")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


def test_list_payouts_happy_path(client):
    _as_admin(client)
    try:
        fake = [{"booking_id": str(uuid.uuid4()), "payout_status": "pending"}]
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_payouts", return_value=fake) as mocked:
            resp = client.get("/admin/payouts")
        assert resp.status_code == 200
        assert resp.json() == fake
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


# ── GET /admin/ledger ──────────────────────────────────────────────────────────

def test_list_ledger_requires_admin(client):
    _as_user(client)
    try:
        with patch.object(db, "get_profile_role", return_value="candidate"):
            resp = client.get("/admin/ledger")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


def test_list_ledger_happy_path(client):
    _as_admin(client)
    try:
        fake = [{"id": str(uuid.uuid4()), "kind": "refund"}]
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_ledger", return_value=fake) as mocked:
            resp = client.get("/admin/ledger")
        assert resp.status_code == 200
        assert resp.json() == fake
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


# ── GET /admin/bookings/{id}/financials ───────────────────────────────────────

def test_booking_financial_detail_happy_path(client):
    _as_admin(client)
    booking_id = str(uuid.uuid4())
    try:
        fake = {"booking": {"id": booking_id}, "timeline": []}
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_booking_detail", return_value=fake) as mocked:
            resp = client.get(f"/admin/bookings/{booking_id}/financials")
        assert resp.status_code == 200
        assert resp.json() == fake
        mocked.assert_called_once_with(booking_id)
    finally:
        client.app.dependency_overrides.clear()


def test_booking_financial_detail_not_found_maps_to_404(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_booking_detail", return_value=None):
            resp = client.get(f"/admin/bookings/{uuid.uuid4()}/financials")
        assert resp.status_code == 404
    finally:
        client.app.dependency_overrides.clear()


def test_booking_financial_detail_requires_admin(client):
    _as_user(client)
    try:
        with patch.object(db, "get_profile_role", return_value="candidate"):
            resp = client.get(f"/admin/bookings/{uuid.uuid4()}/financials")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


# ── POST /admin/payouts/{id}/mark-paid ────────────────────────────────────────

def test_mark_payout_paid_happy_path(client):
    _as_admin(client)
    booking_id = str(uuid.uuid4())
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "mark_payout_paid") as mocked:
            resp = client.post(f"/admin/payouts/{booking_id}/mark-paid", json={"reference": "UTR123"})
        assert resp.status_code == 200
        assert resp.json() == {"paid": True}
        mocked.assert_called_once_with(booking_id, "UTR123")
    finally:
        client.app.dependency_overrides.clear()


def test_mark_payout_paid_not_completed_maps_to_409(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "mark_payout_paid", side_effect=Exception("Booking x is not completed — cannot mark payout paid")):
            resp = client.post(f"/admin/payouts/{uuid.uuid4()}/mark-paid", json={"reference": "UTR123"})
        assert resp.status_code == 409
    finally:
        client.app.dependency_overrides.clear()


def test_mark_payout_paid_requires_admin(client):
    _as_user(client)
    try:
        with patch.object(db, "get_profile_role", return_value="candidate"):
            resp = client.post(f"/admin/payouts/{uuid.uuid4()}/mark-paid", json={"reference": "UTR123"})
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


# ── POST /admin/payouts/{id}/block ────────────────────────────────────────────

def test_block_payout_happy_path(client):
    _as_admin(client)
    booking_id = str(uuid.uuid4())
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "set_payout_blocked") as mocked:
            resp = client.post(f"/admin/payouts/{booking_id}/block", json={"reason": "compliance hold"})
        assert resp.status_code == 200
        assert resp.json() == {"blocked": True}
        mocked.assert_called_once_with(booking_id, "compliance hold")
    finally:
        client.app.dependency_overrides.clear()


def test_block_payout_reason_optional(client):
    _as_admin(client)
    booking_id = str(uuid.uuid4())
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "set_payout_blocked") as mocked:
            resp = client.post(f"/admin/payouts/{booking_id}/block", json={})
        assert resp.status_code == 200
        mocked.assert_called_once_with(booking_id, None)
    finally:
        client.app.dependency_overrides.clear()
