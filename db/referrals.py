import logging
from typing import Any, Optional

from supabase import Client, create_client

import config
from services import mailer

logger = logging.getLogger("immigroov.db.referrals")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


# ── Identity linking (mirrors mentors.link_mentor_by_email) ─────────────────

def link_affiliate_by_email(profile_id: str, email: str) -> Optional[dict[str, Any]]:
    """Link a pre-onboarded affiliate (created by an admin, matched by email, not
    yet attached to any account) to this user's profile. Idempotent: if the user
    is already linked, returns that row. Returns None when there is nothing to
    link. Safe by design — email ownership is proven by the login."""
    if not profile_id or not email:
        return None
    email = email.strip().lower()
    try:
        already = (
            _supabase.table("affiliates")
            .select("id, type, status, profile_id")
            .eq("profile_id", profile_id)
            .limit(1)
            .execute()
        )
        if already.data:
            return already.data[0]

        candidates = (
            _supabase.table("affiliates")
            .select("id, type, status, profile_id")
            .ilike("email", email)
            .execute()
        )
        affiliate = next((a for a in (candidates.data or []) if not a.get("profile_id")), None)
        if not affiliate:
            return None

        _supabase.table("affiliates").update({"profile_id": profile_id}).eq("id", affiliate["id"]).execute()
        affiliate["profile_id"] = profile_id
        logger.info("Linked pre-onboarded affiliate %s to profile %s by email match", affiliate["id"], profile_id)
        return affiliate
    except Exception:
        logger.exception("link_affiliate_by_email failed (profile=%s)", profile_id)
        return None


# ── Public: click tracking + code validation ─────────────────────────────────

def log_referral_click(slug: str, session_token: str) -> None:
    """Fails silently on an unknown slug (RPC itself no-ops) — a visitor's page
    load must never break because of a bad/stale referral link."""
    _supabase.rpc("log_referral_click", {"p_slug": slug, "p_session_token": session_token}).execute()


def validate_referral_code(code: str) -> dict[str, Any]:
    res = _supabase.rpc("validate_referral_code", {"p_code": code}).execute()
    return res.data or {"valid": False, "discount_pct": 0}


# ── Affiliate-facing dashboard ────────────────────────────────────────────────

def affiliate_dashboard_summary(profile_id: str) -> dict[str, Any]:
    """Raises 'Not an affiliate account' if this profile has no linked affiliate row."""
    res = _supabase.rpc("affiliate_dashboard_summary", {"p_profile_id": profile_id}).execute()
    return res.data or {}


# ── Admin: onboarding ──────────────────────────────────────────────────────────

def admin_onboard_affiliate(
    *,
    email: str,
    type_: str,
    slug: str,
    code: str,
    redemption_cap: int,
    expires_at: str,
    discount_pct: float,
    mentor_id: Optional[str] = None,
    payout_details: Optional[dict] = None,
    audience_corridor: Optional[str] = None,
    is_house_channel: bool = False,
) -> dict[str, Any]:
    res = _supabase.rpc("admin_onboard_affiliate", {
        "p_email":              email,
        "p_type":               type_,
        "p_slug":               slug,
        "p_code":               code,
        "p_redemption_cap":     redemption_cap,
        "p_expires_at":         expires_at,
        "p_discount_pct":       discount_pct,
        "p_mentor_id":          mentor_id,
        "p_payout_details":     payout_details,
        "p_audience_corridor":  audience_corridor,
        "p_is_house_channel":   is_house_channel,
    }).execute()
    return res.data or {}


# ── Admin: review + reporting ─────────────────────────────────────────────────

def admin_affiliates_overview() -> list[dict[str, Any]]:
    res = _supabase.rpc("admin_affiliates_overview", {}).execute()
    return res.data or []


def admin_referral_bookings_overview() -> list[dict[str, Any]]:
    res = _supabase.rpc("admin_referral_bookings_overview", {}).execute()
    return res.data or []


def admin_referral_review_queue() -> list[dict[str, Any]]:
    res = _supabase.rpc("admin_referral_review_queue", {}).execute()
    return res.data or []


def admin_mentor_steering_report() -> list[dict[str, Any]]:
    res = _supabase.rpc("admin_mentor_steering_report", {}).execute()
    return res.data or []


def admin_resolve_fraud_flag(flag_id: str, decision: str, note: Optional[str] = None) -> None:
    """decision: 'approve' | 'approve_with_note' | 'reject_and_hold'."""
    _supabase.rpc("admin_resolve_fraud_flag", {
        "p_flag_id": flag_id, "p_decision": decision, "p_note": note,
    }).execute()


def admin_freeze_affiliate(affiliate_id: str, note: str) -> None:
    _supabase.rpc("admin_freeze_affiliate", {"p_affiliate_id": affiliate_id, "p_note": note}).execute()


def admin_unfreeze_affiliate(affiliate_id: str, note: str) -> None:
    _supabase.rpc("admin_unfreeze_affiliate", {"p_affiliate_id": affiliate_id, "p_note": note}).execute()


def admin_void_commission_ledger_entry(ledger_id: str, note: str) -> None:
    _supabase.rpc("admin_void_commission_ledger_entry", {"p_ledger_id": ledger_id, "p_note": note}).execute()


# ── Admin: payout batching ────────────────────────────────────────────────────

def admin_payout_batch_preview(batch_date: str) -> list[dict[str, Any]]:
    res = _supabase.rpc("admin_payout_batch_preview", {"p_batch_date": batch_date}).execute()
    return res.data or []


def admin_finalize_payout_batch(batch_date: str) -> str:
    res = _supabase.rpc("admin_finalize_payout_batch", {"p_batch_date": batch_date}).execute()
    return res.data


def get_referral_tracked_notify_info(booking_id: str) -> Optional[dict[str, Any]]:
    """Lookup for the "referral tracked" affiliate notification - called once,
    synchronously, as a BackgroundTask right after confirm_booking_payment
    (routers/payments.py), never polled. Returns None if the booking carried
    no referral info or attribution didn't resolve to an affiliate."""
    res = _supabase.rpc("get_referral_tracked_notify_info", {"p_booking_id": booking_id}).execute()
    rows = res.data or []
    return rows[0] if rows else None


# ── Dispatcher job: "commission approved" email ─────────────────────────────

def send_pending_commission_emails() -> dict[str, Any]:
    """claim_approved_commissions() (SQL) is the atomic claim, covering both
    paths that flip commission_ledger.status to 'approved' - the cron-driven
    auto-approve in run_referral_fraud_checks and the admin-triggered
    admin_resolve_fraud_flag - uniformly. Same claim-then-send pattern as
    db/reminders.py and db/reviews.send_due_review_requests: a send failure
    isn't retried by the next tick, isolated per row."""
    res = _supabase.rpc("claim_approved_commissions", {}).execute()
    rows = res.data or []
    sent = 0
    for row in rows:
        try:
            mailer.send_transactional(row["email"], "commission_approved", {
                "affiliate_name": row.get("affiliate_name") or "",
                "commission_amount_inr": row.get("commission_amount_inr"),
            })
            sent += 1
        except Exception:
            logger.warning("commission_approved email failed ledger=%s", row.get("ledger_id"))
    return {"claimed": len(rows), "emails_sent": sent}
