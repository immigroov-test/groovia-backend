import logging
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.referrals")

# The version of the programme rules a mentor agrees to when joining. Bump when the rates or
# rules change, so an older agreement is distinguishable from the current one.
TERMS_VERSION = "v2-2026-09"

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def validate_referral_code(code: str, service_id: Optional[str] = None, email: Optional[str] = None) -> dict[str, Any]:
    """Backend-authoritative code check for checkout. Returns
    {valid, reason, discount_pct, code_id?, affiliate_id?, code?, service_id?}.
    service_id lets a code scoped to one session type be refused for another; email lets a
    customer who already used this code be told so before paying."""
    res = _supabase.rpc("validate_referral_code", {
        "p_code": code, "p_service_id": service_id, "p_email": email,
    }).execute()
    return res.data or {"valid": False, "discount_pct": 0, "reason": "error"}


def generate_referral_code(
    *,
    mentor_id: str,
    discount_pct: float = 0,
    redemption_cap: Optional[int] = None,
    expires_at: Optional[str] = None,
    service_id: Optional[str] = None,
) -> str:
    """Create a system-generated code for the mentor's affiliate (auto-created on first use).
    Returns the new code string. The RPC caps the discount and always applies a finite usage cap +
    expiry; raises its message on bad input."""
    res = _supabase.rpc("generate_referral_code", {
        "p_mentor_id": mentor_id,
        "p_discount_pct": discount_pct,
        "p_redemption_cap": redemption_cap,
        "p_expires_at": expires_at,
        "p_service_id": service_id,
    }).execute()
    return res.data


def join_referral_program(mentor_id: str, terms_version: str = TERMS_VERSION) -> dict[str, Any]:
    """The mentor joins the referral programme after seeing its terms. Recorded with the
    version of the rules shown, which is the consent trail for the rates."""
    res = _supabase.rpc("referral_join_program", {
        "p_mentor_id": mentor_id, "p_terms_version": terms_version,
    }).execute()
    return res.data or {}


def leave_referral_program(mentor_id: str) -> dict[str, Any]:
    """Leaving ends every open attribution and deactivates the mentor's codes at once."""
    res = _supabase.rpc("referral_leave_program", {"p_mentor_id": mentor_id}).execute()
    return res.data or {}


def mentor_referral_link_slug(mentor_id: str) -> Optional[str]:
    """The link slug for a mentor who is in the programme, else None. Read by the public profile
    so a visit arriving from outside the site can be credited to them."""
    aff = (_supabase.table("affiliates").select("id, status, enrolled_at, left_at")
           .eq("mentor_id", mentor_id).limit(1).execute()).data
    if not aff:
        return None
    a = aff[0]
    if a.get("status") != "active" or not a.get("enrolled_at") or a.get("left_at"):
        return None
    link = _supabase.table("affiliate_links").select("slug").eq("affiliate_id", a["id"]).limit(1).execute().data
    return link[0]["slug"] if link else None


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


def attribute_booking_referral(booking_id: str, code: str, service_id: Optional[str] = None,
                               email: Optional[str] = None) -> None:
    """Best-effort: validate a code and record it on an already-created booking (the mock/free
    path, where reserve_booking didn't run). No charge + no pricing rows, so no commission is
    generated; this just captures the attribution."""
    if not code:
        return
    try:
        v = validate_referral_code(code, service_id, email)
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


def record_referral_click(slug: str, session_token: str) -> dict[str, Any]:
    """Log a click on a promoter's link against a browser session token. Returns
    {ok, affiliate_id?, reason?}. An unknown slug is a normal outcome, not an error: old links
    stay in circulation long after a code is retired."""
    res = _supabase.rpc("record_referral_click", {
        "p_slug": slug, "p_session_token": session_token,
    }).execute()
    return res.data or {"ok": False, "reason": "error"}


def resolve_booking_attribution(booking_id: str, session_token: Optional[str] = None) -> dict[str, Any]:
    """Turn a link click and/or a code into the customer's attribution record, then stamp the
    booking so the commission engine can see it. Best-effort by design: attribution must never
    fail a booking the customer has already paid for."""
    try:
        res = _supabase.rpc("resolve_booking_attribution", {
            "p_booking_id": booking_id, "p_session_token": session_token,
        }).execute()
        return res.data or {}
    except Exception:
        logger.exception("resolve_booking_attribution failed booking=%s", booking_id)
        return {}


def admin_fraud_queue(include_resolved: bool = False) -> list[dict[str, Any]]:
    """Open fraud flags with the affiliate history and booking attached (admin review queue)."""
    res = _supabase.rpc("admin_fraud_queue", {"p_include_resolved": include_resolved}).execute()
    return res.data or []


def admin_resolve_fraud_flag(
    flag_id: str, decision: str, note: Optional[str] = None, admin_id: Optional[str] = None
) -> dict[str, Any]:
    """Record one of the three review decisions. Also settles the commission: reject_and_hold
    rejects it, either approval releases it once no other flag on it is still open."""
    res = _supabase.rpc("admin_resolve_fraud_flag", {
        "p_flag_id": flag_id, "p_decision": decision, "p_note": note, "p_admin": admin_id,
    }).execute()
    return res.data or {}


def admin_payout_batches() -> list[dict[str, Any]]:
    """Payout batches with their commission count and total."""
    res = _supabase.rpc("admin_payout_batches", {}).execute()
    return res.data or []


def build_payout_batch(batch_date: str) -> dict[str, Any]:
    """Sweep approved, eligible, unflagged commissions into the batch for this date.
    The RPC rejects any date that is not the 1st or the 15th."""
    res = _supabase.rpc("build_payout_batch", {"p_batch_date": batch_date}).execute()
    return res.data or {}


def admin_onboard_affiliate(
    *, display_name: str, email: str, audience_corridor: Optional[str] = None,
    is_house_channel: bool = False, discount_pct: Optional[float] = None,
    redemption_cap: Optional[int] = None, code_expires_at: Optional[str] = None,
    admin_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a non-mentor affiliate (Track B influencer) with a link, and a code when a discount
    is given. Returns {affiliate_id, link_slug, code}. The RPC raises its own message on a
    duplicate email or bad input."""
    res = _supabase.rpc("admin_onboard_affiliate", {
        "p_display_name": display_name, "p_email": email,
        "p_audience_corridor": audience_corridor, "p_is_house_channel": is_house_channel,
        "p_discount_pct": discount_pct, "p_redemption_cap": redemption_cap,
        "p_code_expires_at": code_expires_at, "p_admin": admin_id,
    }).execute()
    return res.data or {}


def admin_generate_affiliate_code(
    affiliate_id: str, *, discount_pct: float = 0,
    redemption_cap: Optional[int] = None, expires_at: Optional[str] = None,
    service_id: Optional[str] = None,
) -> str:
    """Issue a code for any affiliate, mentor or not. Same caps and expiry as the mentor path."""
    res = _supabase.rpc("generate_affiliate_code", {
        "p_affiliate_id": affiliate_id, "p_discount_pct": discount_pct,
        "p_redemption_cap": redemption_cap, "p_expires_at": expires_at,
        "p_service_id": service_id,
    }).execute()
    return res.data


def admin_set_affiliate_status(
    affiliate_id: str, status: str, admin_id: Optional[str] = None, note: Optional[str] = None
) -> dict[str, Any]:
    """Freeze or reactivate an affiliate's referral channel. Earned commissions are untouched."""
    res = _supabase.rpc("admin_set_affiliate_status", {
        "p_affiliate_id": affiliate_id, "p_status": status, "p_admin": admin_id, "p_note": note,
    }).execute()
    return res.data or {}


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
