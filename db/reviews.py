import logging
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.reviews")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def get_review_token_info(token: str) -> dict[str, Any]:
    """Public token lookup for the /review/:token page. Raises on an unknown
    token — the RPC's own 'Invalid review link' message is preserved."""
    res = _supabase.rpc("get_review_token_info", {"p_token": token}).execute()
    return res.data or {}


def submit_review(token: str, rating: int, title: Optional[str], review: Optional[str]) -> dict[str, Any]:
    """Final on submit — there is no edit path (immigroov's own 0101 dropped
    it). Raises on: bad rating, invalid/expired/already-used token, or a
    booking that isn't completed yet."""
    res = _supabase.rpc("submit_review", {
        "p_token": token, "p_rating": rating, "p_title": title, "p_review": review,
    }).execute()
    return res.data or {}


def mentor_reviews_public(mentor_id: str, limit: int = 10, offset: int = 0) -> dict[str, Any]:
    res = _supabase.rpc("mentor_reviews_public", {
        "p_mentor_id": mentor_id, "p_limit": limit, "p_offset": offset,
    }).execute()
    return res.data or {"breakdown": {}, "reviews": []}


def admin_reviews_queue() -> list[dict[str, Any]]:
    """Reviews awaiting moderation (1-3 stars), oldest first."""
    res = _supabase.rpc("admin_reviews_queue", {}).execute()
    return res.data or []


def admin_moderate_review(review_id: str, decision: str, admin_user_id: Optional[str] = None) -> None:
    """decision: 'approve' | 'reject'. Raises if the review isn't pending."""
    _supabase.rpc("admin_moderate_review", {
        "p_review_id": review_id, "p_decision": decision, "p_admin_user_id": admin_user_id,
    }).execute()


def get_review_notify_info(booking_id: str) -> Optional[dict[str, Any]]:
    """mentor name/email for the 5-star review notification email."""
    try:
        res = (
            _supabase.table("bookings")
            .select("mentor_id, mentors(display_name, email, profile_id)")
            .eq("id", booking_id)
            .single()
            .execute()
        )
        b = res.data
        if not b:
            return None
        m = b.get("mentors") or {}
        mentor_email = m.get("email")
        if m.get("profile_id"):
            p = (_supabase.table("profiles").select("email").eq("id", m["profile_id"]).single().execute()).data or {}
            mentor_email = p.get("email") or mentor_email
        return {"mentor_name": m.get("display_name"), "mentor_email": mentor_email}
    except Exception:
        logger.exception("get_review_notify_info failed booking=%s", booking_id)
        return None
