import logging
from typing import Any, Optional

from supabase import Client, create_client

import config
from services import mailer

logger = logging.getLogger("immigroov.db.webinars")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


# ── Mentor self-service ────────────────────────────────────────────────────

def create_webinar(
    mentor_id: str, title: str, description: Optional[str], start: str,
    duration: int = 60, capacity: Optional[int] = None, visibility: str = "public",
) -> str:
    """Raises on: empty title, start time not in the future."""
    res = _supabase.rpc("create_webinar", {
        "p_mentor_id": mentor_id, "p_title": title, "p_description": description, "p_start": start,
        "p_duration": duration, "p_capacity": capacity, "p_visibility": visibility,
    }).execute()
    return res.data


def cancel_webinar(webinar_id: str) -> None:
    _supabase.rpc("cancel_webinar", {"p_webinar_id": webinar_id}).execute()


def mentor_webinars(mentor_id: str) -> list[dict[str, Any]]:
    res = _supabase.rpc("mentor_webinars", {"p_mentor_id": mentor_id}).execute()
    return res.data or []


def get_webinar_mentor_id(webinar_id: str) -> Optional[str]:
    """Used by the FastAPI layer to check ownership before cancel/registrants."""
    res = _supabase.table("webinars").select("mentor_id").eq("id", webinar_id).limit(1).execute()
    return res.data[0]["mentor_id"] if res.data else None


def webinar_registrants(webinar_id: str) -> list[dict[str, Any]]:
    res = _supabase.rpc("webinar_registrants", {"p_webinar_id": webinar_id}).execute()
    return res.data or []


# ── Public ─────────────────────────────────────────────────────────────────

def list_webinars() -> list[dict[str, Any]]:
    res = _supabase.rpc("list_webinars", {}).execute()
    return res.data or []


def webinar_public(webinar_id: str) -> Optional[dict[str, Any]]:
    res = _supabase.rpc("webinar_public", {"p_id": webinar_id}).execute()
    return res.data[0] if res.data else None


def register_webinar(webinar_id: str, email: str, name: Optional[str] = None) -> dict[str, Any]:
    """Raises on: invalid email, webinar not found/closed/started/full."""
    res = _supabase.rpc("register_webinar", {
        "p_webinar_id": webinar_id, "p_email": email, "p_name": name,
    }).execute()
    return res.data or {}


# ── Admin ──────────────────────────────────────────────────────────────────

def admin_webinars() -> list[dict[str, Any]]:
    res = _supabase.rpc("admin_webinars", {}).execute()
    return res.data or []


def claim_due_webinar_reminders() -> list[dict[str, Any]]:
    """Atomically claims every currently-due (webinar, stage) reminder pair —
    the claim (flipping reminded_1d/reminded_1h) happens inside this one RPC
    call, before any email is sent. Only rows this call actually claimed are
    returned; a concurrent call racing for the same row gets nothing back for
    it. See the SQL function's own comment for why this replaced a separate
    read-then-mark pair (DISPATCHER_SAFETY_CHECKLIST.md's send_webinar_reminders
    finding — the old shape could double-send under overlap)."""
    res = _supabase.rpc("claim_due_webinar_reminders", {}).execute()
    return res.data or []


def send_due_webinar_reminders() -> dict[str, Any]:
    """Claims + sends in one call — shared by POST /admin/webinars/send-reminders
    (manual trigger) and the dispatcher (scheduled trigger), so there's exactly
    one implementation of this loop, not two that could drift."""
    due = claim_due_webinar_reminders()
    sent = 0
    webinars_reminded: set[tuple[str, str]] = set()
    for row in due:
        webinars_reminded.add((row["webinar_id"], row["stage"]))
        try:
            mailer.send_transactional(row["registrant_email"], "webinar_reminder", {
                "recipient_name": row.get("registrant_name") or "",
                "webinar_title": row.get("title") or "",
                "start_time": str(row.get("start_time") or ""),
                "room_url": row.get("room_url") or "",
                "stage": row.get("stage"),
            })
            sent += 1
        except Exception:
            # The claim already happened — a send failure here does NOT get
            # retried by the next tick (the flag is already flipped). Logged
            # for ops follow-up; accepting a lost reminder is the tradeoff for
            # not re-sending to everyone else who already got theirs.
            logger.warning("webinar reminder email failed webinar=%s stage=%s", row.get("webinar_id"), row.get("stage"))
            continue
    return {"emails_sent": sent, "webinars_marked": len(webinars_reminded)}
