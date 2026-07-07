# routers/mentors.py
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import db
from core.auth import AuthUser, get_current_user_optional

logger = logging.getLogger("immigroov.routers.mentors")

router = APIRouter(prefix="/mentors", tags=["mentors"])

# Fields that must never leave the backend in a public response.
_PRIVATE_FIELDS = {"profile_id"}


@router.get("")
def list_mentors(
    country: Optional[str] = Query(None, min_length=2, max_length=2, description="ISO 3166-1 alpha-2"),
    category: Optional[str] = None,
    q: Optional[str] = Query(None, description="Free-text keyword to match against headline"),
    limit: int = Query(50, ge=1, le=100),
):
    """Public mentor browse — returns approved + active mentors with optional filters."""
    try:
        rows = db.list_active_mentors(
            country_code=country,
            category=category,
            profile_keyword=q,
            limit=limit,
        )
        return {"mentors": rows, "count": len(rows)}
    except Exception:
        logger.exception("list_mentors failed")
        raise HTTPException(status_code=500, detail="Failed to load mentors")


@router.get("/{slug}")
def get_mentor(slug: str):
    """Public mentor profile by slug."""
    mentor = db.get_mentor_by_slug(slug)
    if not mentor:
        raise HTTPException(status_code=404, detail="Mentor not found")
    public = {k: v for k, v in mentor.items() if k not in _PRIVATE_FIELDS}
    return public


@router.get("/{slug}/reviews")
def get_mentor_reviews(
    slug: str,
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    """Public — published reviews + rating breakdown for a mentor's profile page."""
    mentor = db.get_mentor_by_slug(slug)
    if not mentor:
        raise HTTPException(status_code=404, detail="Mentor not found")
    try:
        return db.mentor_reviews_public(mentor["id"], limit=limit, offset=offset)
    except Exception:
        logger.exception("get_mentor_reviews failed slug=%s", slug)
        raise HTTPException(status_code=500, detail="Failed to load reviews")
