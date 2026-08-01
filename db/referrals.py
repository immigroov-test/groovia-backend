import logging
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.referrals")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def validate_referral_code(code: str) -> dict[str, Any]:
    """Backend-authoritative code check for checkout. Returns
    {valid, reason, discount_pct, code_id?, affiliate_id?, code?}."""
    res = _supabase.rpc("validate_referral_code", {"p_code": code}).execute()
    return res.data or {"valid": False, "discount_pct": 0, "reason": "error"}


def generate_referral_code(
    *,
    mentor_id: str,
    discount_pct: float = 0,
    redemption_cap: Optional[int] = None,
    expires_at: Optional[str] = None,
    code: Optional[str] = None,
) -> str:
    """Create a code for the mentor's affiliate (auto-created on first use).
    Returns the new code string. Raises the RPC's message on a taken code / bad input."""
    res = _supabase.rpc("generate_referral_code", {
        "p_mentor_id": mentor_id,
        "p_discount_pct": discount_pct,
        "p_redemption_cap": redemption_cap,
        "p_expires_at": expires_at,
        "p_code": code,
    }).execute()
    return res.data


def mentor_referral_overview(mentor_id: str) -> dict[str, Any]:
    """This mentor's own codes + promoter earnings (for the mentor dashboard)."""
    res = _supabase.rpc("mentor_referral_overview", {"p_mentor_id": mentor_id}).execute()
    return res.data or {}


def set_code_active(code_id: str, is_active: bool, mentor_id: str) -> bool:
    """Activate/deactivate a code, but only if it belongs to this mentor's affiliate.
    Returns True if a row was updated."""
    code = _supabase.table("referral_codes").select("id, affiliate_id").eq("id", code_id).limit(1).execute()
    if not code.data:
        return False
    aff = _supabase.table("affiliates").select("mentor_id").eq("id", code.data[0]["affiliate_id"]).limit(1).execute()
    if not aff.data or aff.data[0].get("mentor_id") != mentor_id:
        return False
    _supabase.table("referral_codes").update({"is_active": is_active}).eq("id", code_id).execute()
    return True


def attribute_booking_referral(booking_id: str, code: str) -> None:
    """Best-effort: validate a code and record it on an already-created booking (the mock/free
    path, where reserve_booking didn't run). No charge + no pricing rows, so no commission is
    generated; this just captures the attribution."""
    if not code:
        return
    try:
        v = validate_referral_code(code)
        if not v.get("valid"):
            return
        _supabase.table("bookings").update({
            "referral_code": v.get("code"),
            "referral_code_id": v.get("code_id"),
            "referral_affiliate_id": v.get("affiliate_id"),
            "referral_discount_applied_pct": v.get("discount_pct") or None,
        }).eq("id", booking_id).execute()
    except Exception:
        logger.exception("attribute_booking_referral failed booking=%s", booking_id)


def admin_referrals_overview() -> list[dict[str, Any]]:
    """One row per affiliate with code + referral + money aggregates (admin Referrals tab)."""
    res = _supabase.rpc("admin_referrals_overview", {}).execute()
    return res.data or []


def admin_referral_bookings(affiliate_id: Optional[str] = None) -> list[dict[str, Any]]:
    """One row per referred (commission) booking: who gave the code, customer, service,
    discount, split, final amount + commission (admin drill-in + payouts)."""
    res = _supabase.rpc("admin_referral_bookings", {"p_affiliate_id": affiliate_id}).execute()
    return res.data or []


def admin_set_commission_status(
    ledger_id: str, status: str, admin_id: Optional[str] = None, note: Optional[str] = None
) -> None:
    """Approve / reject / mark-paid / void a commission (admin review)."""
    _supabase.rpc("admin_set_commission_status", {
        "p_ledger_id": ledger_id, "p_status": status, "p_admin": admin_id, "p_note": note,
    }).execute()
