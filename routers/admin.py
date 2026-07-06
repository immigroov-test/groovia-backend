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
