import logging
from typing import Any

from supabase import Client, create_client

import config
from services import mailer

logger = logging.getLogger("immigroov.db.reminders")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)

# Ports immigroov's process_due_reminders/process_mentor_reminders (4 pg_cron
# jobs: reminders-24h, reminders-1h, mentor-reminders-1h, mentor-reminders-10m).
# Built claim-then-send from day one — see the SQL functions' own comments in
# migrations/production_db_setup.sql and DISPATCHER_SAFETY_CHECKLIST.md for
# why (the claim, not a post-send mark, is what makes this safe under overlap).


def _claim_customer_reminders(kind: str, lo_minutes: int, hi_minutes: int) -> list[dict[str, Any]]:
    res = _supabase.rpc("claim_due_customer_reminders", {
        "p_kind": kind, "p_lo_minutes": lo_minutes, "p_hi_minutes": hi_minutes,
    }).execute()
    return res.data or []


def _claim_mentor_reminders(kind: str, lo_minutes: int, hi_minutes: int) -> list[dict[str, Any]]:
    res = _supabase.rpc("claim_due_mentor_reminders", {
        "p_kind": kind, "p_lo_minutes": lo_minutes, "p_hi_minutes": hi_minutes,
    }).execute()
    return res.data or []


def _reminder_link(row: dict[str, Any]) -> str:
    """Prefer the attendance-tracking /join/[token] link (records
    mentor_joined/candidate_joined - see routers/booking.py's join
    endpoints) over the plain meeting_url. Falls back to meeting_url for
    'dm' services, which have no join_token (set_meeting_url's trigger only
    assigns join tokens are unconditional per-party, but there's nothing to
    join for a message-only service) or if join_token is ever absent."""
    token = row.get("join_token")
    if token:
        return f"{config.FRONTEND_URL.rstrip('/')}/join/{token}"
    return row.get("meeting_url") or ""


def _send_claimed_reminders(rows: list[dict[str, Any]], template: str) -> dict[str, Any]:
    """Sends one email per already-claimed row. A send failure here does NOT
    get retried by the next tick — the claim already happened (see the SQL
    functions' comments). Logged for ops follow-up; each row's failure is
    isolated so one bad address doesn't stop the rest of the batch."""
    sent = 0
    for row in rows:
        try:
            mailer.send_transactional(row["email"], template, {
                "recipient_name": row.get("first_name") or "",
                "other_party_name": row.get("other_party_name") or "",
                "session_time": str(row.get("slot_utc") or ""),
                "meeting_url": _reminder_link(row),
            })
            sent += 1
        except Exception:
            logger.warning(
                "reminder email failed template=%s booking=%s", template, row.get("booking_id"),
            )
    return {"claimed": len(rows), "emails_sent": sent}


def send_customer_reminders_24h() -> dict[str, Any]:
    rows = _claim_customer_reminders("reminder_24h", 23 * 60, 25 * 60)
    return _send_claimed_reminders(rows, "session_reminder_24h")


def send_customer_reminders_1h() -> dict[str, Any]:
    rows = _claim_customer_reminders("reminder_1h", 30, 90)
    return _send_claimed_reminders(rows, "session_reminder_1h")


def send_mentor_reminders_1h() -> dict[str, Any]:
    rows = _claim_mentor_reminders("mentor_1h", 30, 90)
    return _send_claimed_reminders(rows, "session_reminder_1h")


def send_mentor_reminders_10m() -> dict[str, Any]:
    # DISPATCHER_SAFETY_CHECKLIST.md §3a: widened from immigroov's 5-15 min to
    # 3-15 min. That window (10 min wide) was only 2x a 5-minute dispatcher
    # tick — the narrowest margin of any reminder job on the list, meaning a
    # single missed tick could drop this reminder entirely, not just delay
    # it. Widening restores the same multi-tick safety margin every other
    # reminder job already has, without needing a faster second dispatcher.
    rows = _claim_mentor_reminders("mentor_10m", 3, 15)
    return _send_claimed_reminders(rows, "session_reminder_soon")
