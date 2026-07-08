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
