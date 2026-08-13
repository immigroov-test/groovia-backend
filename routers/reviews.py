import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import db
from core.auth import AuthUser, get_current_user, require_admin

logger = logging.getLogger("immigroov.routers.reviews")

router = APIRouter(prefix="/reviews", tags=["reviews"])


class SubmitReviewBody(BaseModel):
    booking_id: str
    rating: int = Field(..., ge=1, le=5)
    body: Optional[str] = Field(None, max_length=5000)          # sanitized rich-text HTML
    knowledge: Optional[int] = Field(None, ge=1, le=5)
    communication: Optional[int] = Field(None, ge=1, le=5)
    helpfulness: Optional[int] = Field(None, ge=1, le=5)


@router.post("")
def submit_review(body: SubmitReviewBody, user: AuthUser = Depends(get_current_user)):
    """A mentee rates + reviews a session they attended (once completed, within the review window).
    One per booking; enters admin moderation as 'pending'."""
    try:
        rid = db.submit_review(
            body.booking_id, user.id, body.rating, (body.body or "").strip() or None,
            body.knowledge, body.communication, body.helpfulness,
        )
        return {"id": rid}
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "Only the mentee" in msg:
            raise HTTPException(status_code=403, detail="Only the mentee on this booking can review it")
        if "after the session is completed" in msg:
            raise HTTPException(status_code=400, detail="You can review only after the session is completed")
        if "review window" in msg:
            raise HTTPException(status_code=400, detail="The review window for this session has closed")
        if "Rating must be" in msg:
            raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
        if "Booking not found" in msg:
            raise HTTPException(status_code=404, detail="Booking not found")
        logger.exception("submit_review failed")
        raise HTTPException(status_code=500, detail="Could not save your review")


@router.get("/mentor/{mentor_id}")
def mentor_reviews(mentor_id: str):
    """Public: a mentor's published reviews."""
    return db.mentor_reviews(mentor_id)


@router.get("/mentor/{mentor_id}/summary")
def mentor_summary(mentor_id: str):
    """Public: a mentor's rating summary (avg, count, distribution, sub-ratings)."""
    return db.mentor_rating_summary(mentor_id)


@router.get("/my/{booking_id}")
def my_review(booking_id: str, user: AuthUser = Depends(get_current_user)):
    """The caller's own review for a booking (to prefill the edit form). {} if none."""
    return db.my_review_for_booking(booking_id, user.id) or {}


@router.get("/admin")
def admin_reviews_list(user: AuthUser = Depends(require_admin)):
    """All recent reviews (including hidden) for moderation."""
    return db.admin_reviews()


class StatusBody(BaseModel):
    status: str = Field(..., pattern="^(pending|published|rejected)$")


@router.post("/admin/{review_id}/status")
def admin_status(review_id: str, body: StatusBody, user: AuthUser = Depends(require_admin)):
    """Publish (enable) / reject (disable) / reset a review for its mentor."""
    db.admin_set_review_status(review_id, body.status)
    return {"ok": True}
