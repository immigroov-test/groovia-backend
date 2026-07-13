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
    """Public mentor browse - returns approved + active mentors with optional filters."""
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


@router.get("/countries")
def supported_countries():
    """ISO-2 country codes we currently have mentors in (for the 'find a mentor' dropdown).
    Declared before /{slug} so it isn't captured by the slug route."""
    try:
        return {"countries": db.list_supported_countries()}
    except Exception:
        logger.exception("supported_countries failed")
        raise HTTPException(status_code=500, detail="Failed to load supported countries")


@router.get("/facets")
def mentor_facets():
    """Topic + country facets for the find-a-mentor dropdowns (dependent + auto-expanding).
    Declared before /{slug} so it isn't captured by the slug route."""
    try:
        return db.list_mentor_facets()
    except Exception:
        logger.exception("mentor_facets failed")
        raise HTTPException(status_code=500, detail="Failed to load mentor facets")


@router.get("/{slug}")
def get_mentor(slug: str):
    """Public mentor profile by slug."""
    mentor = db.get_mentor_by_slug(slug)
    if not mentor:
        raise HTTPException(status_code=404, detail="Mentor not found")
    public = {k: v for k, v in mentor.items() if k not in _PRIVATE_FIELDS}
    return public
