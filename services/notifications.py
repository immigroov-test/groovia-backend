"""Scheduled booking notifications: 24h/30min session reminders (to the candidate) and the
T-60 "are you available?" nudge (to the mentor). Invoked by the dispatcher each tick.

Each item is claimed atomically (db.claim_reminder) before sending, so overlapping ticks
never double-send. Sending failures are swallowed per-item so one bad email never blocks
the rest of the batch.
"""
import logging
from datetime import datetime

import config
import db
from services import mailer, policy

logger = logging.getLogger("immigroov.services.notifications")

# Window each reminder fires in, in minutes from now. Kept ~wider than the ~5-min dispatcher
# tick so a session is never skipped between ticks; the per-(booking,kind) claim prevents
# repeats within the window.
# The windows live in services/policy so the schedule and the sentences describing it in emails can
# never drift apart (BUG-094 changed 1h -> 30min and the confirmation email kept promising the old one).
_REMINDER_WINDOWS = policy.REMINDER_WINDOWS
_ATTEND_WINDOW = policy.ATTENDANCE_WINDOW   # mentor nudge, same ~1h-out window


def _fmt_time(times: dict | None, local_key: str, tz_key: str) -> str:
    """Format a party's local session time as 'Sat, Jul 18, 2026 at 2:00 PM (Berlin)'."""
    if not times:
        return ""
    local = times.get(local_key)
    tz = times.get(tz_key) or "UTC"
    if not local:
        return ""
    try:
        dt = datetime.fromisoformat(str(local).replace("Z", ""))
        stamp = dt.strftime("%a, %b %d, %Y at %I:%M %p").replace(" 0", " ")
    except Exception:
        stamp = str(local)
    city = tz.split("/")[-1].replace("_", " ")
    return f"{stamp} ({city})"


def send_session_reminders() -> dict:
    """24h + 30min reminders to BOTH the candidate and the mentor (BUG-094).

    One claim_reminder() per (booking, kind) still gates the whole pair - that's the
    existing atomic dedup guarantee and stays untouched, so a retried/overlapping tick
    still can't double-send. Each recipient's send is its own try/except so a failure
    on one side (e.g. a bad mentor email) never blocks the other party's reminder.
    """
    sent = 0
    for kind, (lo, hi) in _REMINDER_WINDOWS.items():
        for bid in db.due_unreminded_bookings(kind, lo, hi):
            if not db.claim_reminder(bid, kind):
                continue
            info = db.get_booking_notify_info(bid) or {}
            times = db.get_booking_times_display(bid)
            meeting_url = f"{config.FRONTEND_URL}/meeting/{bid}"
            recipients = (
                (info.get("candidate_email"), info.get("candidate_name"),
                 info.get("mentor_name") or "your mentor", "customer_local", "customer_tz"),
                (info.get("mentor_email"), info.get("mentor_name"),
                 info.get("candidate_name") or "your attendee", "mentor_local", "mentor_tz"),
            )
            for to, recipient_name, other_name, local_key, tz_key in recipients:
                if not to:
                    continue
                try:
                    mailer.send_transactional(to, f"session_reminder_{kind}", {
                        "recipient_name": recipient_name or "there",
                        "other_party_name": other_name,
                        "meeting_url": meeting_url,
                        "session_time": _fmt_time(times, local_key, tz_key),
                    })
                    sent += 1
                except Exception:
                    logger.warning("reminder send failed booking=%s kind=%s to=%s", bid, kind, to)
    return {"reminders_sent": sent}


def send_review_requests() -> dict:
    """Post-session 'leave a review' email to the mentee (attended, registered accounts only),
    then one reminder ~3 days later if they still haven't reviewed. Links to the session page,
    where the review form lives. Claim-then-send, so no double emails."""
    sent = 0
    for reminder in (False, True):
        kind = "review_reminder" if reminder else "review_request"
        for bid in db.due_review_requests(reminder=reminder):
            if not db.claim_reminder(bid, kind):
                continue
            try:
                info = db.get_booking_notify_info(bid) or {}
                to = info.get("candidate_email")
                if not to:
                    continue
                # Name the session and when it was: a review ask that only says "your recent
                # session" leaves the person guessing which one, days later. Best-effort - a missing
                # timestamp must never cost the whole email, which is the point of the message.
                try:
                    times = db.get_booking_times_display(bid) or {}
                except Exception:
                    times = {}
                mailer.send_transactional(to, "review_request", {
                    "candidate_name": info.get("candidate_name") or "there",
                    "mentor_name": info.get("mentor_name") or "your mentor",
                    "service_title": info.get("service_title") or "",
                    "session_time": _fmt_time(times, "customer_local", "customer_tz"),
                    "booking_ref": f"#{str(bid).split('-')[0]}",
                    "review_url": f"{config.FRONTEND_URL}/session/{bid}",
                    "is_reminder": reminder,
                })
                sent += 1
            except Exception:
                logger.warning("review request send failed booking=%s kind=%s", bid, kind)
    return {"review_requests_sent": sent}


def send_attendance_checks() -> dict:
    """T-60 'are you available?' nudge to the mentor for still-unconfirmed sessions."""
    lo, hi = _ATTEND_WINDOW
    sent = 0
    for bid in db.due_unreminded_bookings("attend_check", lo, hi, require_unconfirmed=True):
        if not db.claim_reminder(bid, "attend_check"):
            continue
        try:
            info = db.get_booking_notify_info(bid) or {}
            to = info.get("mentor_email")
            if not to:
                continue
            times = db.get_booking_times_display(bid)
            mailer.send_transactional(to, "mentor_attendance_check", {
                "mentor_name": info.get("mentor_name") or "there",
                "candidate_name": info.get("candidate_name") or "your attendee",
                "session_time": _fmt_time(times, "mentor_local", "mentor_tz"),
            })
            sent += 1
        except Exception:
            logger.warning("attendance nudge send failed booking=%s", bid)
    return {"attend_checks_sent": sent}
