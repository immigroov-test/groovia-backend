import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

import config
import db
from services import mailer
from core.auth import AuthUser, require_admin


class RejectBody(BaseModel):
    reason: Optional[str] = None

logger = logging.getLogger("immigroov.routers.admin")

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats")
def admin_stats(user: AuthUser = Depends(require_admin)):
    """Platform-level counters: pending mentors, approved mentors, total bookings."""
    return db.get_admin_stats()


@router.get("/mentors/pending")
def list_pending_mentors(user: AuthUser = Depends(require_admin)):
    return db.list_mentors_by_status("pending_review")


@router.get("/mentors/approved")
def list_approved_mentors(user: AuthUser = Depends(require_admin)):
    return db.list_mentors_by_status("approved")


@router.get("/mentors/suspended")
def list_suspended_mentors(user: AuthUser = Depends(require_admin)):
    return db.list_mentors_by_status("suspended")


@router.get("/mentors/{mentor_id}")
def get_mentor_detail(mentor_id: str, user: AuthUser = Depends(require_admin)):
    """Full mentor profile for admin review: all fields, profile email, availability slots.
    Must come after all literal /mentors/<word> routes to avoid shadowing them."""
    mentor = db.get_mentor_full_details(mentor_id)
    if not mentor:
        raise HTTPException(status_code=404, detail="Mentor not found")
    return mentor


@router.post("/mentors/{mentor_id}/approve")
def approve_mentor(mentor_id: str, background_tasks: BackgroundTasks, user: AuthUser = Depends(require_admin)):
    try:
        display_name, mentor_email = db.get_mentor_email(mentor_id)
        result = db.set_mentor_status(mentor_id, "approved")
        db.approve_pending_services(mentor_id)   # services set up during review go live too
    except ValueError:
        raise HTTPException(status_code=404, detail="Mentor not found")
    if mentor_email:
        data = {
            "display_name": display_name or "",
            "mentor_hub_url": config.FRONTEND_URL + "/mentor",
        }
        background_tasks.add_task(mailer.send_transactional, mentor_email, "mentor_approved", data)
        background_tasks.add_task(mailer.send_transactional, mentor_email, "welcome_mentor", data)
    return result


@router.post("/mentors/{mentor_id}/reject")
def reject_mentor(mentor_id: str, background_tasks: BackgroundTasks, body: RejectBody = RejectBody(),
                  user: AuthUser = Depends(require_admin)):
    reason = (body.reason or "").strip() or None
    try:
        display_name, mentor_email = db.get_mentor_email(mentor_id)
        result = db.set_mentor_status(mentor_id, "rejected", reason=reason)
    except ValueError:
        raise HTTPException(status_code=404, detail="Mentor not found")
    if mentor_email:
        background_tasks.add_task(
            mailer.send_transactional,
            mentor_email,
            "mentor_rejected",
            {"display_name": display_name or "", "reason": reason or ""},
        )
    return result


@router.post("/mentors/{mentor_id}/suspend")
def suspend_mentor(mentor_id: str, background_tasks: BackgroundTasks, user: AuthUser = Depends(require_admin)):
    try:
        display_name, mentor_email = db.get_mentor_email(mentor_id)
        result = db.set_mentor_status(mentor_id, "suspended")
    except ValueError:
        raise HTTPException(status_code=404, detail="Mentor not found")
    if mentor_email:
        background_tasks.add_task(
            mailer.send_transactional,
            mentor_email,
            "mentor_suspended",
            {"display_name": display_name or ""},
        )
    return result


@router.post("/mentors/{mentor_id}/reinstate")
def reinstate_mentor(mentor_id: str, user: AuthUser = Depends(require_admin)):
    try:
        return db.set_mentor_status(mentor_id, "approved")
    except ValueError:
        raise HTTPException(status_code=404, detail="Mentor not found")


# ── Booking oversight + no-show ops ─────────────────────────────────────────

@router.get("/bookings")
def list_bookings(
    status: Optional[str] = None,
    mentor_id: Optional[str] = None,
    q: Optional[str] = None,
    user: AuthUser = Depends(require_admin),
):
    """All bookings, newest first, with optional status / mentor / search filters."""
    return db.list_all_bookings(status=status, mentor_id=mentor_id, q=q)


@router.get("/bookings/{booking_id}")
def booking_detail(booking_id: str, user: AuthUser = Depends(require_admin)):
    detail = db.get_booking_admin_detail(booking_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Booking not found")
    return detail


@router.get("/no-show-strikes")
def no_show_strikes(user: AuthUser = Depends(require_admin)):
    """Mentors with accrued no-show strikes — the ops queue."""
    return db.list_mentors_with_strikes()


@router.post("/mentors/{mentor_id}/reset-strikes")
def reset_strikes(mentor_id: str, user: AuthUser = Depends(require_admin)):
    try:
        return db.reset_mentor_strikes(mentor_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Mentor not found")


# ── Service approval (services added by an already-approved mentor) ──────────

@router.get("/services/pending")
def pending_services(user: AuthUser = Depends(require_admin)):
    return db.list_pending_services()


@router.post("/services/{service_id}/approve")
def approve_service(service_id: str, user: AuthUser = Depends(require_admin)):
    try:
        return db.set_service_status(service_id, "approved")
    except ValueError:
        raise HTTPException(status_code=404, detail="Service not found")


@router.post("/services/{service_id}/reject")
def reject_service(service_id: str, user: AuthUser = Depends(require_admin)):
    try:
        return db.set_service_status(service_id, "rejected")
    except ValueError:
        raise HTTPException(status_code=404, detail="Service not found")


# ── Review moderation (1-3* holds) ────────────────────────────────────────────

@router.get("/reviews/pending")
def pending_reviews(user: AuthUser = Depends(require_admin)):
    return db.admin_reviews_queue()


@router.post("/reviews/{review_id}/approve")
def approve_review(review_id: str, user: AuthUser = Depends(require_admin)):
    try:
        db.admin_moderate_review(review_id, "approve", user.id)
        return {"approved": True}
    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        if "not awaiting moderation" in msg.lower():
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=500, detail="Failed to approve review")


@router.post("/reviews/{review_id}/reject")
def reject_review(review_id: str, user: AuthUser = Depends(require_admin)):
    try:
        db.admin_moderate_review(review_id, "reject", user.id)
        return {"rejected": True}
    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        if "not awaiting moderation" in msg.lower():
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=500, detail="Failed to reject review")


# ── Referral/affiliate program ────────────────────────────────────────────────

class OnboardAffiliateBody(BaseModel):
    email: str
    type: str
    slug: str
    code: str
    redemption_cap: int
    expires_at: str
    discount_pct: float
    mentor_id: Optional[str] = None
    payout_details: Optional[dict] = None
    audience_corridor: Optional[str] = None
    is_house_channel: bool = False


@router.post("/referrals/onboard")
def onboard_affiliate(body: OnboardAffiliateBody, user: AuthUser = Depends(require_admin)):
    """Creates the affiliate + their one link + their one code in a single call."""
    try:
        return db.admin_onboard_affiliate(
            email=body.email, type_=body.type, slug=body.slug, code=body.code,
            redemption_cap=body.redemption_cap, expires_at=body.expires_at, discount_pct=body.discount_pct,
            mentor_id=body.mentor_id, payout_details=body.payout_details,
            audience_corridor=body.audience_corridor, is_house_channel=body.is_house_channel,
        )
    except Exception as e:
        msg = str(e)
        if any(s in msg for s in ("must be", "required", "check", "Discount", "Redemption", "Expiry")):
            raise HTTPException(status_code=400, detail=msg)
        logger.exception("admin_onboard_affiliate failed")
        raise HTTPException(status_code=500, detail="Failed to onboard affiliate")


@router.get("/referrals/affiliates")
def affiliates_overview(user: AuthUser = Depends(require_admin)):
    return db.admin_affiliates_overview()


@router.get("/referrals/bookings")
def referral_bookings_overview(user: AuthUser = Depends(require_admin)):
    return db.admin_referral_bookings_overview()


@router.get("/referrals/queue")
def referral_review_queue(user: AuthUser = Depends(require_admin)):
    """Escalated fraud flags awaiting a human decision, oldest first."""
    return db.admin_referral_review_queue()


@router.get("/referrals/steering-report")
def mentor_steering_report(user: AuthUser = Depends(require_admin)):
    """Informational-only: which mentors each affiliate concentrates referrals
    toward this month. No auto-escalation — a founder-level read, not a gate."""
    return db.admin_mentor_steering_report()


class ResolveFraudFlagBody(BaseModel):
    decision: str  # approve | approve_with_note | reject_and_hold
    note: Optional[str] = None


@router.post("/referrals/flags/{flag_id}/resolve")
def resolve_fraud_flag(flag_id: str, body: ResolveFraudFlagBody, user: AuthUser = Depends(require_admin)):
    try:
        db.admin_resolve_fraud_flag(flag_id, body.decision, body.note)
        return {"resolved": True}
    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        if "must be" in msg.lower():
            raise HTTPException(status_code=400, detail=msg)
        raise HTTPException(status_code=500, detail="Failed to resolve fraud flag")


class AdminNoteBody(BaseModel):
    note: str


@router.post("/referrals/affiliates/{affiliate_id}/freeze")
def freeze_affiliate(affiliate_id: str, body: AdminNoteBody, user: AuthUser = Depends(require_admin)):
    try:
        db.admin_freeze_affiliate(affiliate_id, body.note)
        return {"frozen": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/referrals/affiliates/{affiliate_id}/unfreeze")
def unfreeze_affiliate(affiliate_id: str, body: AdminNoteBody, user: AuthUser = Depends(require_admin)):
    try:
        db.admin_unfreeze_affiliate(affiliate_id, body.note)
        return {"frozen": False}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/referrals/ledger/{ledger_id}/void")
def void_commission_ledger_entry(ledger_id: str, body: AdminNoteBody, user: AuthUser = Depends(require_admin)):
    try:
        db.admin_void_commission_ledger_entry(ledger_id, body.note)
        return {"voided": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/referrals/payout-preview")
def payout_batch_preview(batch_date: str, user: AuthUser = Depends(require_admin)):
    """batch_date: ISO date (YYYY-MM-DD). Preview only — nothing is finalized."""
    return db.admin_payout_batch_preview(batch_date)


class FinalizePayoutBatchBody(BaseModel):
    batch_date: str


@router.post("/referrals/payout-finalize")
def finalize_payout_batch(body: FinalizePayoutBatchBody, user: AuthUser = Depends(require_admin)):
    """Sweeps every eligible commission_ledger entry into a finalized batch and
    marks them 'paid'. Does NOT move any money — matches immigroov's V1 scope
    (eligibility tracking + 'paid' flagging only; the actual transfer is manual)."""
    batch_id = db.admin_finalize_payout_batch(body.batch_date)
    return {"batch_id": batch_id}


# ── Financials: mentor payouts, ledger, booking money detail ────────────────
# Unlike immigroov (which left admin_payouts/admin_ledger/admin_booking_detail
# GRANTed to anon+authenticated — an acknowledged, never-fixed gap in the
# source), these are gated by require_admin here, same as every other admin_*
# endpoint in this file.

@router.get("/payouts")
def list_payouts(user: AuthUser = Depends(require_admin)):
    """Every payout-actionable booking (confirmed/rescheduled/completed/no_show),
    newest first. Client filters (mentor/status/date) client-side, matching
    immigroov's own admin UI — no server-side filtering in the source either."""
    return db.admin_payouts()


@router.get("/ledger")
def list_ledger(user: AuthUser = Depends(require_admin)):
    """Flat, unfiltered read of every booking_ledger row, newest first."""
    return db.admin_ledger()


@router.get("/bookings/{booking_id}/financials")
def booking_financial_detail(booking_id: str, user: AuthUser = Depends(require_admin)):
    """Booking/payment/payout facts + money totals + reconstructed timeline —
    the admin drill-down behind the Payouts/Ledger tabs. Distinct from
    GET /admin/bookings/{id} (booking oversight: requests/offers, no money
    summary or timeline) — this is the financials-focused detail view."""
    detail = db.admin_booking_detail(booking_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Booking not found")
    return detail


class MarkPayoutPaidBody(BaseModel):
    reference: str


@router.post("/payouts/{booking_id}/mark-paid")
def mark_payout_paid(booking_id: str, body: MarkPayoutPaidBody, user: AuthUser = Depends(require_admin)):
    try:
        db.mark_payout_paid(booking_id, body.reference)
        return {"paid": True}
    except Exception as e:
        msg = str(e)
        if "not completed" in msg.lower():
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=500, detail="Failed to mark payout paid")


class BlockPayoutBody(BaseModel):
    reason: Optional[str] = None


@router.post("/payouts/{booking_id}/block")
def block_payout(booking_id: str, body: BlockPayoutBody, user: AuthUser = Depends(require_admin)):
    """No reference UI in immigroov calls this (confirmed absent from
    AdminManager.tsx) — ported faithfully anyway since it's part of the RPC
    surface; a future admin UI can wire it up."""
    db.set_payout_blocked(booking_id, body.reason)
    return {"blocked": True}


# ── Webinars ───────────────────────────────────────────────────────────────────

@router.get("/webinars")
def admin_webinars(user: AuthUser = Depends(require_admin)):
    """Cross-mentor platform view — every webinar, any status/visibility."""
    return db.admin_webinars()


@router.get("/webinars/{webinar_id}/registrants")
def admin_webinar_registrants(webinar_id: str, user: AuthUser = Depends(require_admin)):
    """Same underlying RPC as the mentor's own registrant view — admin has no
    ownership restriction here, matching immigroov's admin console."""
    return db.webinar_registrants(webinar_id)


@router.post("/webinars/send-reminders")
def send_webinar_reminders(user: AuthUser = Depends(require_admin)):
    """Admin-triggered stand-in for immigroov's pg_cron `webinar-reminders`
    job (every 10 min, no request context — impossible to reproduce as a
    true cron job without SQL-side HTTP or a scheduler groovia doesn't have
    yet). Finds due (1-day/1-hour) reminders, sends one email per registrant,
    and marks each (webinar, stage) as sent so it never re-fires. Wiring
    this to an actual timer (cron/cloud scheduler hitting this endpoint) is
    explicitly out of scope for this migration pass — see the SQL comment on
    due_webinar_reminders."""
    due = db.due_webinar_reminders()
    sent = 0
    marked: set[tuple[str, str]] = set()
    for row in due:
        try:
            mailer.send_transactional(row["registrant_email"], "webinar_reminder", {
                "recipient_name": row.get("registrant_name") or "",
                "webinar_title": row.get("title") or "",
                "start_time": str(row.get("start_time") or ""),
                "room_url": row.get("room_url") or "",
                "stage": row.get("stage"),
            })
            sent += 1
        except Exception:
            logger.warning("webinar reminder email failed webinar=%s stage=%s", row.get("webinar_id"), row.get("stage"))
            continue
        key = (row["webinar_id"], row["stage"])
        if key not in marked:
            db.mark_webinar_reminded(row["webinar_id"], row["stage"])
            marked.add(key)
    return {"emails_sent": sent, "webinars_marked": len(marked)}


@router.post("/payments/expire-holds")
def expire_payment_holds(user: AuthUser = Depends(require_admin)):
    """Manual "expire now" ops tool. The PRIMARY trigger is a real pg_cron
    entry (`expire-payment-holds`, every minute, calling SQL `expire_stale_holds()`
    directly — see migrations/production_db_setup.sql) per
    INFRASTRUCTURE_ARCHITECTURE_PLAN.md: pure SQL, zero external I/O, 1-minute
    cadence is exactly the case for staying DB-native rather than waking the
    application. This endpoint just gives an admin a way to force a run."""
    try:
        count = db.expire_stale_holds()
        return {"holds_expired": count}
    except Exception as e:
        logger.exception("expire_stale_holds failed")
        raise HTTPException(status_code=502, detail=f"Failed to expire stale holds: {e}")


@router.post("/fx/refresh")
def refresh_fx_rates(user: AuthUser = Depends(require_admin)):
    """Admin-triggered stand-in for immigroov's pg_cron `fx-refresh-6h` job.
    compute_booking_price hard-fails once fx_rates is >24h stale
    (platform_settings.fx_max_age_minutes), so this must run at least every
    ~6h in production or pricing starts failing. Wiring a real scheduler
    (cron/cloud scheduler hitting this endpoint every 6h) is the remaining
    infra step — this endpoint is the logic, same pattern as
    /admin/webinars/send-reminders above."""
    try:
        return db.refresh_fx_rates()
    except Exception as e:
        logger.exception("fx refresh failed")
        raise HTTPException(status_code=502, detail=f"FX refresh failed: {e}")
