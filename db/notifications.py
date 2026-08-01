"""Reminder + attendance-nudge data layer (read + atomic claim).

Concurrency model: the dispatcher already holds a lease lock so only one tick runs at a
time; on top of that, each reminder is CLAIMED atomically before it is sent, using the
booking_reminders UNIQUE(booking_id, kind) constraint. A duplicate INSERT raises a
unique_violation, so a second tick (or a retry) can never double-send. This makes the
whole thing safe even if two dispatchers overlap.
"""
import logging
from datetime import datetime, timedelta, timezone

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.notifications")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def due_unreminded_bookings(
    kind: str, lo_min: int, hi_min: int, require_unconfirmed: bool = False,
) -> list[str]:
    """Booking ids whose slot_time falls in [now+lo_min, now+hi_min], are still active
    (confirmed/rescheduled), and have NOT yet had a reminder of `kind` sent. This is a
    best-effort pre-filter; claim_reminder() is the real guard against double-send.
    require_unconfirmed=True additionally keeps only sessions the mentor hasn't confirmed
    (for the attendance nudge)."""
    now = datetime.now(timezone.utc)
    lo = (now + timedelta(minutes=lo_min)).isoformat()
    hi = (now + timedelta(minutes=hi_min)).isoformat()
    try:
        rows = (
            _supabase.table("bookings")
            .select("id, mentor_confirmed_at")
            .in_("status", ["confirmed", "rescheduled"])
            .gte("slot_time", lo)
            .lte("slot_time", hi)
            .execute()
        ).data or []
    except Exception:
        logger.exception("due_unreminded_bookings query failed kind=%s", kind)
        return []
    ids = [r["id"] for r in rows if not (require_unconfirmed and r.get("mentor_confirmed_at"))]
    if not ids:
        return []
    try:
        seen = (
            _supabase.table("booking_reminders")
            .select("booking_id")
            .eq("kind", kind)
            .in_("booking_id", ids)
            .execute()
        ).data or []
    except Exception:
        seen = []
    seen_ids = {r["booking_id"] for r in seen}
    return [i for i in ids if i not in seen_ids]


def due_review_requests(
    reminder: bool = False, window_days: int = 14, reminder_after_days: int = 3,
) -> list[str]:
    """Booking ids eligible for a post-session review email: status 'completed', the mentee is a
    registered account (candidate_id) who actually joined (candidate_joined_at), the session ended
    within window_days, and there's no review yet. For reminder=True, only those whose initial
    'review_request' went out at least reminder_after_days ago and still have no review.
    claim_reminder() is the real double-send guard."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=window_days)).isoformat()
    try:
        rows = (
            _supabase.table("bookings")
            .select("id, candidate_id, candidate_joined_at")
            .eq("status", "completed")
            .gte("slot_end", since)
            .execute()
        ).data or []
    except Exception:
        logger.exception("due_review_requests query failed reminder=%s", reminder)
        return []
    ids = [r["id"] for r in rows if r.get("candidate_id") and r.get("candidate_joined_at")]
    if not ids:
        return []
    # Drop bookings that already have a review.
    try:
        reviewed = (_supabase.table("reviews").select("booking_id").in_("booking_id", ids).execute()).data or []
    except Exception:
        reviewed = []
    reviewed_ids = {r["booking_id"] for r in reviewed}
    ids = [i for i in ids if i not in reviewed_ids]
    if not ids:
        return []
    kind = "review_reminder" if reminder else "review_request"
    # Drop bookings already claimed for THIS kind.
    try:
        seen = (_supabase.table("booking_reminders").select("booking_id").eq("kind", kind).in_("booking_id", ids).execute()).data or []
    except Exception:
        seen = []
    seen_ids = {r["booking_id"] for r in seen}
    ids = [i for i in ids if i not in seen_ids]
    if not reminder or not ids:
        return ids
    # Reminder: only those whose initial request went out >= reminder_after_days ago.
    cutoff = (now - timedelta(days=reminder_after_days)).isoformat()
    try:
        reqs = (
            _supabase.table("booking_reminders")
            .select("booking_id")
            .eq("kind", "review_request")
            .in_("booking_id", ids)
            .lte("created_at", cutoff)
            .execute()
        ).data or []
    except Exception:
        reqs = []
    ready = {r["booking_id"] for r in reqs}
    return [i for i in ids if i in ready]


def claim_reminder(booking_id: str, kind: str) -> bool:
    """Atomically claim the right to send this (booking, kind) reminder. Returns True only
    if THIS caller inserted the row; False if another tick already claimed it (the
    UNIQUE(booking_id, kind) constraint rejects the duplicate). Reschedules delete these
    rows, so a moved session is reminded again for its new time."""
    try:
        res = _supabase.table("booking_reminders").insert(
            {"booking_id": booking_id, "kind": kind}
        ).execute()
        return bool(res.data)
    except Exception:
        # unique_violation (already claimed) or a transient error - either way, don't send.
        return False
