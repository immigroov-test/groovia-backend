import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, field_validator

import db
from services import mailer

logger = logging.getLogger("immigroov.routers.reviews")

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/token/{token}")
def token_info(token: str):
    """Public — feeds the /review/:token page. Also used to block a resubmit
    (already_submitted) and show the expiry state."""
    try:
        return db.get_review_token_info(token)
    except Exception as e:
        msg = str(e)
        if "Invalid review link" in msg:
            raise HTTPException(status_code=404, detail=msg)
        logger.exception("token_info failed")
        raise HTTPException(status_code=500, detail="Failed to load review link")


class SubmitReviewBody(BaseModel):
    token: str
    rating: int
    title: Optional[str] = None
    review: Optional[str] = None

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError("rating must be between 1 and 5")
        return v

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: Optional[str]) -> Optional[str]:
        return (v or "").strip()[:200] or None

    @field_validator("review")
    @classmethod
    def strip_review(cls, v: Optional[str]) -> Optional[str]:
        return (v or "").strip()[:2000] or None


@router.post("/submit")
def submit(body: SubmitReviewBody, background_tasks: BackgroundTasks):
    """Final on submit — there is no edit path. A 5-star review notifies the
    mentor by email (BackgroundTask, best-effort)."""
    try:
        result = db.submit_review(body.token, body.rating, body.title, body.review)
    except Exception as e:
        msg = str(e)
        if "Invalid review link" in msg or "expired" in msg.lower() or "already been submitted" in msg.lower() \
                or "already submitted" in msg.lower():
            raise HTTPException(status_code=409, detail=msg)
        if "completed session" in msg.lower():
            raise HTTPException(status_code=400, detail=msg)
        logger.exception("submit_review failed")
        raise HTTPException(status_code=500, detail="Failed to submit review")

    if result.get("rating") == 5:
        background_tasks.add_task(_notify_five_star, body.token, body.title)
    return result


def _notify_five_star(token: str, title: Optional[str]) -> None:
    """Best-effort — a mailer failure never affects the submit response."""
    try:
        info = db.get_review_token_info(token)
        booking_id = info.get("booking_id")
        if not booking_id:
            return
        notify = db.get_review_notify_info(booking_id) or {}
        mentor_email = notify.get("mentor_email")
        if not mentor_email:
            return
        mailer.send_transactional(mentor_email, "mentor_five_star_review", {
            "mentor_name": notify.get("mentor_name") or "",
            "review_title": title or "",
        })
    except Exception:
        logger.warning("5-star review notification failed token=%s", token)
