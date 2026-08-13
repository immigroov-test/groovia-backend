import logging
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.reviews")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def submit_review(
    booking_id: str, reviewer_id: str, rating: int, body: Optional[str] = None,
    knowledge: Optional[int] = None, communication: Optional[int] = None, helpfulness: Optional[int] = None,
) -> str:
    """Mentee submits/edits their review for a completed booking. Returns the review id. The RPC
    enforces: caller is the booking's mentee, the booking is completed, and it's within the review
    window; the review enters/returns to 'pending' moderation. body is sanitized rich-text HTML."""
    res = _supabase.rpc("submit_review", {
        "p_booking_id": booking_id, "p_reviewer": reviewer_id, "p_rating": rating, "p_body": body,
        "p_knowledge": knowledge, "p_communication": communication, "p_helpfulness": helpfulness,
    }).execute()
    return res.data


def mentor_reviews(mentor_id: str, include_hidden: bool = False) -> list[dict[str, Any]]:
    res = _supabase.rpc("mentor_reviews", {"p_mentor_id": mentor_id, "p_include_hidden": include_hidden}).execute()
    return res.data or []


def mentor_rating_summary(mentor_id: str) -> dict[str, Any]:
    """Avg, count, 5..1 distribution, and sub-rating averages (published reviews only)."""
    res = _supabase.rpc("mentor_rating_summary", {"p_mentor_id": mentor_id}).execute()
    return res.data or {}


def my_review_for_booking(booking_id: str, reviewer_id: str) -> Optional[dict[str, Any]]:
    res = _supabase.rpc("my_review_for_booking", {"p_booking_id": booking_id, "p_reviewer": reviewer_id}).execute()
    return res.data or None


def admin_reviews(limit: int = 100) -> list[dict[str, Any]]:
    res = _supabase.rpc("admin_reviews", {"p_limit": limit}).execute()
    return res.data or []


def admin_set_review_status(review_id: str, status: str) -> None:
    """Publish (enable) / reject (disable) / reset a review. Re-syncs the mentor's rating."""
    _supabase.rpc("admin_set_review_status", {"p_review_id": review_id, "p_status": status}).execute()
