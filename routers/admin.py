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


@router.get("/mentors/revisions")
def list_mentor_revisions(user: AuthUser = Depends(require_admin)):
    """Approved mentors with staged profile edits awaiting review. Must be declared
    before /mentors/{mentor_id} so the literal path isn't swallowed by the param route."""
    return db.list_pending_revisions()


@router.post("/mentors/{mentor_id}/revision/approve")
def approve_revision(mentor_id: str, background_tasks: BackgroundTasks, user: AuthUser = Depends(require_admin)):
    """Apply an approved mentor's staged profile changes to their live profile."""
    try:
        display_name, mentor_email = db.get_mentor_email(mentor_id)
        result = db.apply_pending_changes(mentor_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Mentor not found")
    if mentor_email:
        background_tasks.add_task(
            mailer.send_transactional,
            mentor_email,
            "mentor_approved",
            {"display_name": display_name or "", "mentor_hub_url": config.FRONTEND_URL + "/mentor"},
        )
    return result


@router.post("/mentors/{mentor_id}/revision/request-changes")
def request_revision_changes(mentor_id: str, background_tasks: BackgroundTasks, body: RejectBody = RejectBody(),
                             user: AuthUser = Depends(require_admin)):
    """Reject an approved mentor's staged changes: discard them (the live profile keeps
    serving) and record a reviewer note the mentor sees on their dashboard."""
    reason = (body.reason or "").strip() or None
    try:
        display_name, mentor_email = db.get_mentor_email(mentor_id)
        result = db.discard_pending_changes(mentor_id, reason=reason)
    except ValueError:
        raise HTTPException(status_code=404, detail="Mentor not found")
    if mentor_email:
        background_tasks.add_task(
            mailer.send_transactional,
            mentor_email,
            "mentor_changes_requested",
            {
                "display_name": display_name or "",
                "reason": reason or "",
                "mentor_hub_url": config.FRONTEND_URL + "/mentor/profile",
            },
        )
    return result


@router.get("/mentors/{mentor_id}")
def get_mentor_detail(mentor_id: str, user: AuthUser = Depends(require_admin)):
    """Full mentor profile for admin review: all fields, profile email, availability slots.
    Must come after all literal /mentors/<word> routes to avoid shadowing them."""
    mentor = db.get_mentor_full_details(mentor_id)
    if not mentor:
        raise HTTPException(status_code=404, detail="Mentor not found")
    # Masked payout details (holder, bank, country, last 4). Full numbers only via the reveal route.
    mentor["bank"] = db.get_mentor_bank_masked(mentor_id) or {"has_details": False}
    return mentor


@router.post("/mentors/{mentor_id}/bank/reveal")
def reveal_mentor_bank(mentor_id: str, user: AuthUser = Depends(require_admin)):
    """Decrypt and return a mentor's full payout details for the founder to pay them. A deliberate
    admin action (POST, not part of the detail payload) so full numbers aren't shown by default."""
    try:
        revealed = db.get_mentor_bank_revealed(mentor_id)
    except RuntimeError as e:
        logger.error("Bank reveal failed for mentor %s: %s", mentor_id, e)
        raise HTTPException(status_code=503, detail="Could not decrypt bank details (check BANK_ENC_KEY).")
    if not revealed:
        raise HTTPException(status_code=404, detail="No bank details on file for this mentor")
    logger.info("admin %s revealed bank details for mentor %s", user.id, mentor_id)
    return revealed


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


@router.post("/mentors/{mentor_id}/request-changes")
def request_changes(mentor_id: str, background_tasks: BackgroundTasks, body: RejectBody = RejectBody(),
                    user: AuthUser = Depends(require_admin)):
    """Ask a pending applicant to revise their profile. Sets status to changes_requested
    (editable + resubmittable) and stores the reviewer note shown in their dashboard."""
    reason = (body.reason or "").strip() or None
    try:
        display_name, mentor_email = db.get_mentor_email(mentor_id)
        result = db.set_mentor_status(mentor_id, "changes_requested", reason=reason)
    except ValueError:
        raise HTTPException(status_code=404, detail="Mentor not found")
    if mentor_email:
        background_tasks.add_task(
            mailer.send_transactional,
            mentor_email,
            "mentor_changes_requested",
            {
                "display_name": display_name or "",
                "reason": reason or "",
                "mentor_hub_url": config.FRONTEND_URL + "/mentor",
            },
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
def reinstate_mentor(mentor_id: str, background_tasks: BackgroundTasks, user: AuthUser = Depends(require_admin)):
    try:
        display_name, mentor_email = db.get_mentor_email(mentor_id)
        result = db.set_mentor_status(mentor_id, "approved")
    except ValueError:
        raise HTTPException(status_code=404, detail="Mentor not found")
    if mentor_email:
        background_tasks.add_task(
            mailer.send_transactional,
            mentor_email,
            "mentor_reinstated",
            {"display_name": display_name or "", "mentor_hub_url": config.FRONTEND_URL + "/mentor"},
        )
    return result


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
    """Mentors with accrued no-show strikes - the ops queue."""
    return db.list_mentors_with_strikes()


@router.post("/mentors/{mentor_id}/reset-strikes")
def reset_strikes(mentor_id: str, user: AuthUser = Depends(require_admin)):
    try:
        return db.reset_mentor_strikes(mentor_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Mentor not found")


# ── Payments + payouts (financials) ─────────────────────────────────────────

@router.get("/payments")
def admin_payments(user: AuthUser = Depends(require_admin)):
    """Recent customer payments. `configured` is false when the payments
    migration hasn't been applied yet (tables absent)."""
    rows = db.admin_list_payments()
    return {"configured": rows is not None, "payments": rows or []}


@router.get("/payouts")
def admin_payouts(state: Optional[str] = None, user: AuthUser = Depends(require_admin)):
    """Mentor payouts (optionally filtered by payout_state). `configured` is
    false when the payments migration hasn't been applied yet."""
    rows = db.admin_list_payouts(state=state)
    return {"configured": rows is not None, "payouts": rows or []}


class PayoutPaidBody(BaseModel):
    reference: Optional[str] = None


@router.post("/payouts/{booking_id}/mark-paid")
def mark_payout_paid(booking_id: str, body: PayoutPaidBody = PayoutPaidBody(), user: AuthUser = Depends(require_admin)):
    """Record a manual payout as paid. The RPC rejects bookings that aren't
    'completed' (nothing to pay out yet) and payouts already void/blocked."""
    try:
        db.mark_payout_paid(booking_id, (body.reference or "").strip() or "manual")
    except Exception as e:
        msg = str(e)
        if "not completed" in msg.lower():
            raise HTTPException(status_code=409, detail="This booking isn't completed yet, so there's nothing to pay out.")
        logger.exception("mark_payout_paid failed booking=%s", booking_id)
        raise HTTPException(status_code=500, detail="Could not mark the payout as paid.")
    return {"ok": True}


@router.post("/payouts/{booking_id}/block")
def block_payout(booking_id: str, body: RejectBody = RejectBody(), user: AuthUser = Depends(require_admin)):
    """Hold a payout (dispute, compliance). Never overrides an already-paid payout."""
    try:
        db.set_payout_blocked(booking_id, (body.reason or "").strip() or None)
    except Exception:
        logger.exception("block_payout failed booking=%s", booking_id)
        raise HTTPException(status_code=500, detail="Could not block the payout.")
    return {"ok": True}


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
