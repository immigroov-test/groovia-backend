# tests/unit/test_referrals_router.py
# HTTP-layer acceptance tests for routers/referrals.py + the referral-adjacent
# endpoints added to routers/admin.py and routers/payments.py. The SQL business
# rules (attribution precedence, commission splits, fraud thresholds) live in
# the RPCs ported in the Referral migration commits and are NOT re-verified
# here (no live Postgres in this environment, consistent with every prior
# module) — these tests cover request validation, routing, and
# RPC-error -> HTTP-status mapping, with db.* mocked.
import uuid
from unittest.mock import patch

import db
from core.auth import AuthUser, get_current_user


def _as_admin(client):
    admin_user = AuthUser(id=str(uuid.uuid4()), email="admin@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: admin_user
    return admin_user


def _as_user(client, user_id=None, email="affiliate@example.com"):
    user = AuthUser(id=user_id or str(uuid.uuid4()), email=email, role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: user
    return user


# ── Public: click tracking + code validation ─────────────────────────────────

def test_log_click_happy_path(client):
    with patch.object(db, "log_referral_click", return_value=None) as mocked:
        resp = client.post("/referrals/click", json={"slug": "aya-k", "session_token": "tok123"})
    assert resp.status_code == 200
    assert resp.json() == {"logged": True}
    mocked.assert_called_once_with("aya-k", "tok123")


def test_validate_code_happy_path(client):
    with patch.object(db, "validate_referral_code", return_value={"valid": True, "discount_pct": 10}):
        resp = client.get("/referrals/validate/SAVE10")
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "discount_pct": 10}


def test_validate_code_invalid_still_200(client):
    """An unknown/expired code is a normal negative result, not an error."""
    with patch.object(db, "validate_referral_code", return_value={"valid": False, "discount_pct": 0}):
        resp = client.get("/referrals/validate/NOPE")
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


# ── Affiliate dashboard ────────────────────────────────────────────────────────

def test_dashboard_requires_auth(client):
    resp = client.get("/referrals/dashboard")
    assert resp.status_code == 401


def test_dashboard_happy_path(client):
    user = _as_user(client)
    try:
        fake = {"affiliate": {"id": "x"}, "tier": "starter"}
        with patch.object(db, "affiliate_dashboard_summary", return_value=fake) as mocked:
            resp = client.get("/referrals/dashboard")
        assert resp.status_code == 200
        assert resp.json() == fake
        mocked.assert_called_once_with(user.id)
    finally:
        client.app.dependency_overrides.clear()


def test_dashboard_not_an_affiliate_maps_to_403(client):
    _as_user(client)
    try:
        with patch.object(db, "affiliate_dashboard_summary", side_effect=Exception("Not an affiliate account")):
            resp = client.get("/referrals/dashboard")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


# ── Admin: onboarding ──────────────────────────────────────────────────────────

def test_onboard_affiliate_requires_admin(client):
    _as_user(client)
    try:
        with patch.object(db, "get_profile_role", return_value="candidate"):
            resp = client.post("/admin/referrals/onboard", json={
                "email": "aff@example.com", "type": "non_mentor", "slug": "aff-slug",
                "code": "AFF10", "redemption_cap": 100, "expires_at": "2027-01-01T00:00:00Z",
                "discount_pct": 10,
            })
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


def test_onboard_affiliate_happy_path(client):
    _as_admin(client)
    try:
        fake = {"affiliate_id": "a1", "link_id": "l1", "code_id": "c1"}
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_onboard_affiliate", return_value=fake) as mocked:
            resp = client.post("/admin/referrals/onboard", json={
                "email": "aff@example.com", "type": "non_mentor", "slug": "aff-slug",
                "code": "AFF10", "redemption_cap": 100, "expires_at": "2027-01-01T00:00:00Z",
                "discount_pct": 10,
            })
        assert resp.status_code == 200
        assert resp.json() == fake
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_onboard_affiliate_validation_error_maps_to_400(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_onboard_affiliate", side_effect=Exception("Discount percent must be between 0 and 100")):
            resp = client.post("/admin/referrals/onboard", json={
                "email": "aff@example.com", "type": "non_mentor", "slug": "aff-slug",
                "code": "AFF10", "redemption_cap": 100, "expires_at": "2027-01-01T00:00:00Z",
                "discount_pct": 999,
            })
        assert resp.status_code == 400
    finally:
        client.app.dependency_overrides.clear()


# ── Admin: review + reporting ─────────────────────────────────────────────────

def test_affiliates_overview_requires_admin(client):
    _as_user(client)
    try:
        with patch.object(db, "get_profile_role", return_value="candidate"):
            resp = client.get("/admin/referrals/affiliates")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


def test_affiliates_overview_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_affiliates_overview", return_value=[{"affiliate_id": "a1"}]) as mocked:
            resp = client.get("/admin/referrals/affiliates")
        assert resp.status_code == 200
        assert resp.json() == [{"affiliate_id": "a1"}]
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_referral_bookings_overview_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_referral_bookings_overview", return_value=[]):
            resp = client.get("/admin/referrals/bookings")
        assert resp.status_code == 200
    finally:
        client.app.dependency_overrides.clear()


def test_referral_review_queue_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_referral_review_queue", return_value=[{"flag_id": "f1"}]):
            resp = client.get("/admin/referrals/queue")
        assert resp.status_code == 200
        assert resp.json() == [{"flag_id": "f1"}]
    finally:
        client.app.dependency_overrides.clear()


def test_mentor_steering_report_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_mentor_steering_report", return_value=[]):
            resp = client.get("/admin/referrals/steering-report")
        assert resp.status_code == 200
    finally:
        client.app.dependency_overrides.clear()


def test_resolve_fraud_flag_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_resolve_fraud_flag") as mocked:
            resp = client.post(f"/admin/referrals/flags/{uuid.uuid4()}/resolve", json={"decision": "approve"})
        assert resp.status_code == 200
        assert resp.json() == {"resolved": True}
        assert mocked.call_args.args[1] == "approve"
    finally:
        client.app.dependency_overrides.clear()


def test_resolve_fraud_flag_not_found_maps_to_404(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_resolve_fraud_flag", side_effect=Exception("Flag not found")):
            resp = client.post(f"/admin/referrals/flags/{uuid.uuid4()}/resolve", json={"decision": "approve"})
        assert resp.status_code == 404
    finally:
        client.app.dependency_overrides.clear()


def test_freeze_affiliate_requires_note(client):
    """Body validation is enforced by the RPC; the router just relays the error as 400."""
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_freeze_affiliate", side_effect=Exception("A note is required to freeze an affiliate")):
            resp = client.post(f"/admin/referrals/affiliates/{uuid.uuid4()}/freeze", json={"note": ""})
        assert resp.status_code == 400
    finally:
        client.app.dependency_overrides.clear()


def test_freeze_affiliate_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_freeze_affiliate") as mocked:
            resp = client.post(f"/admin/referrals/affiliates/{uuid.uuid4()}/freeze", json={"note": "suspicious volume spike"})
        assert resp.status_code == 200
        assert resp.json() == {"frozen": True}
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_unfreeze_affiliate_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_unfreeze_affiliate") as mocked:
            resp = client.post(f"/admin/referrals/affiliates/{uuid.uuid4()}/unfreeze", json={"note": "cleared review"})
        assert resp.status_code == 200
        assert resp.json() == {"frozen": False}
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_void_ledger_entry_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_void_commission_ledger_entry") as mocked:
            resp = client.post(f"/admin/referrals/ledger/{uuid.uuid4()}/void", json={"note": "duplicate entry"})
        assert resp.status_code == 200
        assert resp.json() == {"voided": True}
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


# ── Admin: payout batching ────────────────────────────────────────────────────

def test_payout_batch_preview_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_payout_batch_preview", return_value=[{"commission_ledger_id": "l1"}]) as mocked:
            resp = client.get("/admin/referrals/payout-preview?batch_date=2026-07-10")
        assert resp.status_code == 200
        assert resp.json() == [{"commission_ledger_id": "l1"}]
        mocked.assert_called_once_with("2026-07-10")
    finally:
        client.app.dependency_overrides.clear()


def test_finalize_payout_batch_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_finalize_payout_batch", return_value="batch-1") as mocked:
            resp = client.post("/admin/referrals/payout-finalize", json={"batch_date": "2026-07-10"})
        assert resp.status_code == 200
        assert resp.json() == {"batch_id": "batch-1"}
        mocked.assert_called_once_with("2026-07-10")
    finally:
        client.app.dependency_overrides.clear()


# ── /payments/reserve: referral params pass through ──────────────────────────

def test_reserve_passes_referral_params_through(client):
    fake_result = {"booking_id": str(uuid.uuid4()), "amount": 100, "currency": "USD",
                    "hold_expires_at": "2026-07-08T00:00:00Z"}
    with patch.object(db, "reserve_booking", return_value=fake_result) as mocked:
        resp = client.post("/payments/reserve", json={
            "quote_id": str(uuid.uuid4()), "mentor_id": str(uuid.uuid4()), "service_id": str(uuid.uuid4()),
            "slot_time": "2026-07-15T10:00:00Z", "email": "guest@example.com",
            "referral_session_token": "tok-abc", "referral_code": "SAVE10",
        })
    assert resp.status_code == 200
    assert mocked.call_args.kwargs["referral_session_token"] == "tok-abc"
    assert mocked.call_args.kwargs["referral_code"] == "SAVE10"


def test_reserve_referral_params_default_to_none(client):
    fake_result = {"booking_id": str(uuid.uuid4()), "amount": 100, "currency": "USD",
                    "hold_expires_at": "2026-07-08T00:00:00Z"}
    with patch.object(db, "reserve_booking", return_value=fake_result) as mocked:
        resp = client.post("/payments/reserve", json={
            "quote_id": str(uuid.uuid4()), "mentor_id": str(uuid.uuid4()), "service_id": str(uuid.uuid4()),
            "slot_time": "2026-07-15T10:00:00Z", "email": "guest@example.com",
        })
    assert resp.status_code == 200
    assert mocked.call_args.kwargs["referral_session_token"] is None
    assert mocked.call_args.kwargs["referral_code"] is None
