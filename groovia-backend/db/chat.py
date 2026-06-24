import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def upsert_chat_thread(
    *,
    thread_id: str,
    user_id: Optional[str],
    user_intent: Optional[str] = None,
    track: Optional[str] = None,
    title_seed: Optional[str] = None,
) -> None:
    """Create or update the metadata row for a LangGraph thread.
    Called from /chat after every successful turn.
    `title_seed` (first ~60 chars of the user's first real message) is only written
    when the row's title is still empty."""
    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "id": thread_id,
        "user_id": user_id,
        "last_message_at": now,
    }
    if user_intent is not None:
        payload["user_intent"] = user_intent
    if track is not None:
        payload["track"] = track

    try:
        _supabase.table("chat_threads").upsert(payload, on_conflict="id").execute()
        if title_seed:
            _supabase.table("chat_threads").update({"title": title_seed[:60]}).eq("id", thread_id).is_("title", "null").execute()
    except Exception:
        logger.exception("Failed to upsert chat_thread %s", thread_id)


def claim_thread(thread_id: str, user_id: str) -> bool:
    """Link a guest thread (user_id IS NULL) to the now-authenticated user.
    Idempotent: returns True if it linked or if it was already owned by this user."""
    try:
        res = (
            _supabase.table("chat_threads")
            .update({"user_id": user_id})
            .eq("id", thread_id)
            .is_("user_id", "null")
            .execute()
        )
        if res.data:
            return True
        owner = get_thread_owner(thread_id)
        return owner == user_id
    except Exception:
        logger.exception("Failed to claim chat_thread %s for %s", thread_id, user_id)
        return False


def list_user_threads(user_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    res = (
        _supabase.table("chat_threads")
        .select("id, title, user_intent, track, last_message_at, message_count")
        .eq("user_id", user_id)
        .eq("is_archived", False)
        .order("last_message_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def get_thread_owner(thread_id: str) -> Optional[str]:
    """Return the user_id that owns this thread, or None if no row / guest thread."""
    res = (
        _supabase.table("chat_threads")
        .select("user_id")
        .eq("id", thread_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return res.data[0].get("user_id")


def upsert_ai_event(
    *,
    thread_id: str,
    intent: Optional[str] = None,
    revision_count: int = 0,
    tool_calls: int = 0,
    latency_ms: Optional[int] = None,
    model: Optional[str] = None,
    quality_failure: bool = False,
) -> None:
    try:
        _supabase.table("ai_events").insert({
            "thread_id": thread_id,
            "intent": intent,
            "revision_count": revision_count,
            "tool_calls": tool_calls,
            "latency_ms": latency_ms,
            "model": model,
            "quality_failure": quality_failure,
        }).execute()
    except Exception:
        logger.exception("Failed to log ai_event for thread %s", thread_id)
