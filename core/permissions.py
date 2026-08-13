# core/permissions.py
# Centralized authorization policies (who-can-do-what), as reusable FastAPI
# dependencies. Authentication lives in core/auth.py; this layer adds role and
# ownership checks so routers don't each re-implement them.
#
# Roles in the system:
#   - admin   : profiles.role = 'admin'           -> require_admin (in core/auth.py)
#   - mentor  : has a row in mentors(profile_id)   -> require_mentor / require_active_mentor
#   - mentee  : any authenticated user             -> get_current_user (in core/auth.py)
#               ownership of a booking is checked with authorize_booking_party().
import logging
from typing import Any, Optional

from fastapi import Depends, HTTPException, status

from core.auth import AuthUser, get_current_user

logger = logging.getLogger("immigroov.permissions")


def require_mentor(user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Require the caller to own a mentor profile that is allowed to set up
    services/availability. That is every state except 'suspended' — including
    'changes_requested' and 'rejected', where the mentor must be able to edit
    their services/availability to address the reviewer's notes and resubmit.
    Returns the mentor row so the endpoint doesn't refetch it."""
    import db as _db
    mentor = _db.get_mentor_by_profile_id(user.id)
    if not mentor:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Mentor access required")
    if mentor.get("status") == "suspended":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Mentor profile not active")
    return mentor


def require_active_mentor(user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Stricter variant: only an APPROVED mentor (e.g. live/booking-facing actions)."""
    import db as _db
    mentor = _db.get_mentor_by_profile_id(user.id)
    if not mentor or mentor.get("status") != "approved":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Approved mentor access required")
    return mentor


def authorize_booking_party(
    principals: Optional[dict[str, Any]],
    user: AuthUser,
    *,
    allow: str = "both",  # 'both' | 'mentor' | 'candidate'
) -> dict[str, str]:
    """Authorize the caller as a party to a booking. `principals` is
    {candidate_id, mentor_id} (from db.get_booking_principals / *_offer_* / *_request_*).
    Returns {'role': 'mentor'|'candidate'} or raises 404/403. Centralizes the
    booking-party checks that the booking router would otherwise repeat per endpoint."""
    if not principals:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    import db as _db
    is_candidate = principals.get("candidate_id") == user.id
    mentor = _db.get_mentor_by_profile_id(user.id)
    is_mentor = bool(mentor and mentor.get("id") == principals.get("mentor_id"))

    if allow == "mentor" and not is_mentor:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the booking's mentor can do this")
    if allow == "candidate" and not is_candidate:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the booking's owner can do this")
    if allow == "both" and not (is_mentor or is_candidate):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this booking")
    return {"role": "mentor" if is_mentor else "candidate"}
