import html as _html
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

import config

logger = logging.getLogger("immigroov.mailer")

_RESEND_URL = "https://api.resend.com/emails"
_DEV_EMAIL_LOG = Path(__file__).parent.parent / "_dev_emails.jsonl"


def _log_dev_email(
    to: str,
    template: str,
    subject: str,
    data: dict,
    scheduled_at: Optional[datetime],
) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "to": to,
        "template": template,
        "subject": subject,
        "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        "data": data,
    }
    logger.info("[MOCK EMAIL] to=%s template=%s subject=%r scheduled_at=%s", to, template, subject, record["scheduled_at"])
    try:
        with _DEV_EMAIL_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + os.linesep)
    except Exception:
        logger.warning("Could not write dev email log to %s", _DEV_EMAIL_LOG)


def read_dev_emails(limit: int = 50) -> list[dict]:
    """Return the last `limit` dev email records (newest first)."""
    if not _DEV_EMAIL_LOG.exists():
        return []
    lines = _DEV_EMAIL_LOG.read_text(encoding="utf-8").splitlines()
    records = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(records) >= limit:
            break
    return records


def clear_dev_emails() -> int:
    """Truncate the dev email log. Returns number of records deleted."""
    if not _DEV_EMAIL_LOG.exists():
        return 0
    lines = [l for l in _DEV_EMAIL_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    count = len(lines)
    _DEV_EMAIL_LOG.write_text("", encoding="utf-8")
    return count


def _e(s: object) -> str:
    return _html.escape(str(s or ""))


def _base(content: str) -> str:
    return (
        '<!DOCTYPE html><html lang="en">'
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;padding:0;background:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Helvetica,Arial,sans-serif">'
        '<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="padding:40px 16px">'
        '<tr><td align="center">'
        '<table width="560" cellpadding="0" cellspacing="0" role="presentation"'
        ' style="background:#ffffff;border-radius:10px;padding:40px 48px;max-width:560px">'
        "<tr><td>"
        # Immigroov logo (served from the frontend's public/ — absolute URL so email clients can load it).
        f'<p style="margin:0 0 28px"><img src="{config.FRONTEND_URL.rstrip("/")}/Immigroov_Transparent_Logo.png"'
        ' alt="Immigroov" height="28" style="height:28px;width:auto;display:block;border:0"></p>'
        + content
        + '<hr style="border:none;border-top:1px solid #e8e8e8;margin:32px 0">'
        '<p style="margin:0;font-size:12px;color:#999">'
        "Immigroov — AI-powered career consultancy for international professionals.<br>"
        'Questions? <a href="mailto:support@immigroov.com" style="color:#6b7fff">support@immigroov.com</a>'
        "</p>"
        "</td></tr></table>"
        "</td></tr></table>"
        "</body></html>"
    )


def _btn(url: str, label: str) -> str:
    return (
        f'<p style="margin:24px 0 0">'
        f'<a href="{_e(url)}" style="display:inline-block;background:#6b7fff;color:#fff;'
        f'text-decoration:none;padding:12px 24px;border-radius:6px;font-size:14px;font-weight:600">'
        f"{_e(label)}</a></p>"
    )


def _info_row(label: str, value: str) -> str:
    return (
        '<table cellpadding="0" cellspacing="0" role="presentation"'
        ' style="background:#f8f8fc;border-radius:6px;padding:16px 20px;margin-bottom:20px;width:100%">'
        "<tr><td>"
        f'<p style="margin:0 0 4px;font-size:12px;font-weight:600;color:#999;text-transform:uppercase;letter-spacing:.6px">{_e(label)}</p>'
        f'<p style="margin:0;font-size:16px;font-weight:600;color:#0a0a0a">{_e(value)}</p>'
        "</td></tr></table>"
    )


def _details_card(rows: list[tuple[str, str]]) -> str:
    """A single cal.com-style card with What / When / Who / Where label+value rows."""
    inner = ""
    for label, value in rows:
        if not value:
            continue
        inner += (
            f'<p style="margin:0 0 4px;font-size:12px;font-weight:600;color:#999;text-transform:uppercase;letter-spacing:.6px">{_e(label)}</p>'
            f'<p style="margin:0 0 18px;font-size:15px;font-weight:600;color:#0a0a0a;line-height:1.4">{_e(value)}</p>'
        )
    return (
        '<table cellpadding="0" cellspacing="0" role="presentation"'
        ' style="background:#f8f8fc;border-radius:8px;padding:22px 24px 6px;margin:8px 0 20px;width:100%">'
        "<tr><td>" + inner + "</td></tr></table>"
    )


# ── Templates ─────────────────────────────────────────────────────────────────

def _mentor_approved(d: dict) -> tuple[str, str]:
    name = _e(d.get("display_name", ""))
    hub = d.get("mentor_hub_url", config.FRONTEND_URL + "/mentor")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">You\'re approved — welcome to Immigroov!</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        "Your application to become a mentor on Immigroov has been approved. Candidates looking for visa "
        "and career guidance in your area of expertise can now discover and book sessions with you."
        "</p>"
        '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">'
        "Head to your Mentor Hub to set your availability and go live."
        "</p>"
        + _btn(hub, "Go to Mentor Hub")
    )
    return "You're approved — welcome to the Immigroov mentor team", _base(body)


def _mentor_rejected(d: dict) -> tuple[str, str]:
    name = _e(d.get("display_name", ""))
    reason = (d.get("reason") or "").strip()
    reason_html = _info_row("Reviewer note", reason) if reason else ""
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your Immigroov mentor application</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        "Thank you for your interest in becoming a mentor on Immigroov. After reviewing your application, "
        "we're unable to approve it at this time."
        "</p>"
        + reason_html
        + '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">'
        "If you have additional credentials or context to share, you're welcome to update your profile and "
        "re-apply. We appreciate your understanding."
        "</p>"
    )
    return "Your Immigroov mentor application", _base(body)


def _mentor_suspended(d: dict) -> tuple[str, str]:
    name = _e(d.get("display_name", ""))
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your mentor account has been suspended</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        "Your mentor account on Immigroov has been temporarily suspended. Your profile will not be visible "
        "to candidates and no new bookings can be made during this time."
        "</p>"
        '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">'
        'If you believe this is in error, please contact <a href="mailto:support@immigroov.com" style="color:#6b7fff">support@immigroov.com</a>.'
        "</p>"
    )
    return "Your Immigroov mentor account has been suspended", _base(body)


def _booking_confirmed_candidate(d: dict) -> tuple[str, str]:
    candidate = _e(d.get("candidate_name", ""))
    mentor = d.get("mentor_name", "your mentor")
    service = d.get("service_title", "1-on-1 session")
    url = d.get("meeting_url", "")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your session is confirmed ✓</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {candidate},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Your session with <strong>{_e(mentor)}</strong> is confirmed. Here are the details.</p>'
        + _details_card([
            ("What", service),
            ("When", d.get("session_time", "")),
            ("Who", f"{mentor} (mentor) and you"),
            ("Where", "Video call — use the Join meeting button below"),
        ])
        + '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">'
        "You'll receive a reminder 24 hours and 1 hour before the session."
        "</p>"
        + (_btn(url, "Join meeting") if url else "")
        + '<p style="margin:20px 0 0;font-size:14px;color:#444;line-height:1.6">'
        f'Need to change it? <a href="{config.FRONTEND_URL}/account/sessions" style="color:#6b7fff">Reschedule or cancel</a>'
        " anytime from your account.</p>"
    )
    return f"Confirmed: your session with {mentor}", _base(body)


def _booking_confirmed_mentor(d: dict) -> tuple[str, str]:
    mentor = _e(d.get("mentor_name", ""))
    candidate = d.get("candidate_name", "a candidate")
    candidate_email = d.get("candidate_email", "")
    service = d.get("service_title", "1-on-1 session")
    url = d.get("meeting_url", "")
    notes = d.get("notes", "")
    notes_html = (
        f'<p style="margin:16px 0 0;font-size:14px;color:#444;line-height:1.6">'
        f"<strong>What to prepare (from {_e(candidate)}):</strong><br>{_e(notes)}</p>"
    ) if notes else ""
    who = f"{candidate}" + (f" ({candidate_email})" if candidate_email else "")
    body = (
        f'<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">New booking from {_e(candidate)}</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {mentor},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"<strong>{_e(candidate)}</strong> has booked a session with you. Here are the details."
        "</p>"
        + _details_card([
            ("What", service),
            ("When", d.get("session_time", "")),
            ("Who", who),
            ("Where", "Video call — use the Join meeting button below"),
        ])
        + notes_html
        + (_btn(url, "Join meeting") if url else "")
    )
    return f"New booking: {candidate}", _base(body)


def _session_reminder_24h(d: dict) -> tuple[str, str]:
    recipient = _e(d.get("recipient_name", ""))
    other = d.get("other_party_name", "your mentor")
    url = d.get("meeting_url", "")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your session is tomorrow</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {recipient},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"Just a reminder that your session with <strong>{_e(other)}</strong> is tomorrow."
        "</p>"
        + _info_row("Date & Time", d.get("session_time", ""))
        + (_btn(url, "Join meeting") if url else "")
    )
    return f"Reminder: your session with {other} is tomorrow", _base(body)


def _session_reminder_1h(d: dict) -> tuple[str, str]:
    recipient = _e(d.get("recipient_name", ""))
    other = d.get("other_party_name", "your mentor")
    url = d.get("meeting_url", "")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your session starts in 1 hour</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {recipient},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"Your session with <strong>{_e(other)}</strong> starts in 1 hour. Make sure you're ready!"
        "</p>"
        + (_btn(url, "Join meeting") if url else "")
    )
    return f"Your session with {other} starts in 1 hour", _base(body)


def _session_reminder_soon(d: dict) -> tuple[str, str]:
    """Mentor-only 10-minute-out nudge (immigroov's mentor_10m). No 24h/1h
    equivalent exists for this stage — see the founder's call in
    0083_mentor_reminders.sql (mentors get closer-to-start reminders only,
    since attendance depends on both parties joining)."""
    recipient = _e(d.get("recipient_name", ""))
    other = d.get("other_party_name", "your mentee")
    url = d.get("meeting_url", "")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your session starts in about 10 minutes</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {recipient},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"Your session with <strong>{_e(other)}</strong> starts in about 10 minutes."
        "</p>"
        + (_btn(url, "Join meeting") if url else "")
    )
    return f"Your session with {other} starts in about 10 minutes", _base(body)


def _review_request(d: dict) -> tuple[str, str]:
    candidate = _e(d.get("candidate_name", ""))
    mentor = d.get("mentor_name", "your mentor")
    review_url = d.get("platform_url", config.FRONTEND_URL) + "/mentors"
    body = (
        f'<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">How was your session with {_e(mentor)}?</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {candidate},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"We hope your session with <strong>{_e(mentor)}</strong> was valuable! Your feedback helps other "
        "international professionals find the right mentor for their journey."
        "</p>"
        '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">It only takes a minute to leave a review.</p>'
        + _btn(review_url, "Leave a Review")
    )
    return f"How was your session with {mentor}?", _base(body)


def _mentor_five_star_review(d: dict) -> tuple[str, str]:
    mentor = _e(d.get("mentor_name", ""))
    title = d.get("review_title", "")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">You received a new 5★ review</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Congrats{", " + mentor if mentor else ""}!'
        " You just received a new 5-star review"
        + (f': "{_e(title)}"' if title else ".")
        + "</p>"
    )
    return "You received a new 5★ review", _base(body)


def _welcome_candidate(d: dict) -> tuple[str, str]:
    name = _e(d.get("candidate_name", ""))
    platform = d.get("platform_url", config.FRONTEND_URL)
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Welcome to Immigroov</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        "Welcome! You've taken your first step towards building your international career with expert guidance."
        "</p>"
        '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">'
        "Explore our mentor directory to find professionals who've navigated the visa and career journey you're on."
        "</p>"
        + _btn(platform + "/mentors", "Browse Mentors")
    )
    return "Welcome to Immigroov", _base(body)


def _welcome_mentor(d: dict) -> tuple[str, str]:
    name = _e(d.get("display_name", ""))
    hub = d.get("mentor_hub_url", config.FRONTEND_URL + "/mentor")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your mentor profile is live</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        "Your Immigroov mentor profile is now live. Candidates can discover and book sessions with you through the platform."
        "</p>"
        '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">'
        "Head to your Mentor Hub to check your upcoming bookings, manage your availability, and update your profile."
        "</p>"
        + _btn(hub, "Go to Mentor Hub")
    )
    return "Your Immigroov mentor profile is live", _base(body)


def _mentor_application_received(d: dict) -> tuple[str, str]:
    name = _e(d.get("display_name", ""))
    avail_url = d.get("availability_url", config.FRONTEND_URL + "/mentor/availability")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Application received — we\'ll be in touch soon</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        "Thanks for applying to become a mentor on Immigroov. Our team will review your profile and "
        "get back to you within <strong>1–2 business days</strong>."
        "</p>"
        '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">'
        "While you wait, you can set your weekly availability so it's ready the moment you're approved."
        "</p>"
        + _btn(avail_url, "Set my availability")
    )
    return "We've received your Immigroov mentor application", _base(body)


# ── Lifecycle v2 templates (cancel / reschedule / no-show) ──────────────────────

def _booking_cancelled(d: dict) -> tuple[str, str]:
    name = _e(d.get("recipient_name", "there"))
    other = d.get("other_name", "the other party")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your session was cancelled</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"Your session with <strong>{_e(other)}</strong> has been cancelled.</p>"
        + _info_row("Was scheduled for", d.get("session_time", ""))
        + '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">'
        "You can book another session any time from the mentor directory.</p>"
    )
    return "Your Immigroov session was cancelled", _base(body)


def _booking_rescheduled(d: dict) -> tuple[str, str]:
    name = _e(d.get("recipient_name", "there"))
    other = d.get("other_name", "the other party")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your session was rescheduled ✓</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"Your session with <strong>{_e(other)}</strong> has a new time.</p>"
        + _info_row("New date & time", d.get("session_time", ""))
    )
    return f"Rescheduled: your session with {other}", _base(body)


def _reschedule_proposed(d: dict) -> tuple[str, str]:
    name = _e(d.get("recipient_name", "there"))
    mentor = d.get("other_name", "your mentor")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your mentor proposed a new time</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"<strong>{_e(mentor)}</strong> proposed a new time range for your session. "
        "Open your sessions to pick a slot that works, or decline.</p>"
        + _btn(config.FRONTEND_URL + "/account", "Review proposal")
    )
    return f"{mentor} proposed a new time for your session", _base(body)


def _reschedule_requested(d: dict) -> tuple[str, str]:
    mentor = _e(d.get("recipient_name", "there"))
    attendee = d.get("other_name", "An attendee")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">A reschedule was requested</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {mentor},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"<strong>{_e(attendee)}</strong> requested to reschedule a session within 24 hours of the start. "
        "Approve or decline it from your Mentor Hub — if you don't respond in time it auto-approves.</p>"
        + _btn(config.FRONTEND_URL + "/mentor", "Review request")
    )
    return "A reschedule was requested", _base(body)


def _cancel_requested(d: dict) -> tuple[str, str]:
    mentor = _e(d.get("recipient_name", "there"))
    attendee = d.get("other_name", "An attendee")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">A cancellation was requested</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {mentor},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"<strong>{_e(attendee)}</strong> requested to cancel a session within 24 hours of the start. "
        "Approve or decline it from your Mentor Hub — if you don't respond in time it auto-approves.</p>"
        + _btn(config.FRONTEND_URL + "/mentor", "Review request")
    )
    return "A cancellation was requested", _base(body)


def _no_show_reported(d: dict) -> tuple[str, str]:
    name = _e(d.get("recipient_name", "there"))
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Session marked as a no-show</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        "A session you were part of was reported as a no-show. Open your sessions to see the options for resolving it.</p>"
        + _btn(config.FRONTEND_URL + "/account", "View session")
    )
    return "A session was marked as a no-show", _base(body)


def _booking_admin_notice(d: dict) -> tuple[str, str]:
    """Ops/admin copy for every booking, reschedule, and cancellation."""
    event = d.get("event", "updated")
    titles = {"booked": "New booking", "cancelled": "Booking cancelled", "rescheduled": "Booking rescheduled"}
    title = titles.get(event, "Booking update")
    mentor = d.get("mentor_name") or "—"
    candidate = d.get("candidate_name") or "—"
    candidate_email = d.get("candidate_email") or ""
    body = (
        f'<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">{_e(title)}</h1>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Admin notification — a booking event occurred.</p>'
        + _info_row("Mentor", mentor)
        + _info_row("Candidate", f"{candidate} ({candidate_email})" if candidate_email else candidate)
        + _info_row("When", d.get("session_time", "—"))
    )
    return f"[Admin] {title}: {candidate} × {mentor}", _base(body)


def _webinar_registered(d: dict) -> tuple[str, str]:
    title = d.get("webinar_title", "")
    name = _e(d.get("recipient_name", "") or "there")
    url = d.get("room_url", "")
    body = (
        f'<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">You\'re registered: {_e(title)}</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"You're registered for <strong>{_e(title)}</strong>. We'll send reminders 1 day and 1 hour before it starts."
        "</p>"
        + _info_row("Date & Time", d.get("start_time", ""))
        + (_btn(url, "Join link") if url else "")
    )
    return f"You're registered: {title}", _base(body)


def _webinar_reminder(d: dict) -> tuple[str, str]:
    title = d.get("webinar_title", "")
    stage = d.get("stage", "1h")
    name = _e(d.get("recipient_name", "") or "there")
    url = d.get("room_url", "")
    when = "tomorrow" if stage == "1d" else "in about an hour"
    heading = f"Tomorrow: {title}" if stage == "1d" else f"Starting soon: {title}"
    body = (
        f'<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">{_e(heading)}</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"Your webinar <strong>{_e(title)}</strong> is {when}."
        "</p>"
        + _info_row("Date & Time", d.get("start_time", ""))
        + (_btn(url, "Join link") if url else "")
    )
    return heading, _base(body)


_TEMPLATES = {
    "booking_admin_notice": _booking_admin_notice,
    "mentor_application_received": _mentor_application_received,
    "mentor_approved": _mentor_approved,
    "mentor_rejected": _mentor_rejected,
    "mentor_suspended": _mentor_suspended,
    "booking_confirmed_candidate": _booking_confirmed_candidate,
    "booking_confirmed_mentor": _booking_confirmed_mentor,
    "session_reminder_24h": _session_reminder_24h,
    "session_reminder_1h": _session_reminder_1h,
    "session_reminder_soon": _session_reminder_soon,
    "review_request": _review_request,
    "mentor_five_star_review": _mentor_five_star_review,
    "welcome_candidate": _welcome_candidate,
    "welcome_mentor": _welcome_mentor,
    "booking_cancelled": _booking_cancelled,
    "booking_rescheduled": _booking_rescheduled,
    "reschedule_proposed": _reschedule_proposed,
    "reschedule_requested": _reschedule_requested,
    "cancel_requested": _cancel_requested,
    "no_show_reported": _no_show_reported,
    "webinar_registered": _webinar_registered,
    "webinar_reminder": _webinar_reminder,
}


def send_transactional(
    to: str,
    template: str,
    data: dict,
    scheduled_at: Optional[datetime] = None,
) -> None:
    """Send one transactional email via Resend. Raises on network error or non-2xx.
    Dispatch via FastAPI BackgroundTasks so the request path is never blocked.
    When MOCK_SERVICES=true, writes to _dev_emails.jsonl instead of calling Resend."""
    fn = _TEMPLATES.get(template)
    if fn is None:
        logger.error("Unknown email template %r", template)
        return
    subject, html = fn(data)

    if config.MOCK_SERVICES:
        _log_dev_email(to, template, subject, data, scheduled_at)
        return

    if not config.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping %s to %s", template, to)
        return

    # Testing without a verified domain: route everything to one inbox, tagged with the
    # intended recipient so it's clear who each email was really for.
    recipient = to
    if config.EMAIL_TEST_REDIRECT:
        subject = f"[to: {to}] {subject}"
        recipient = config.EMAIL_TEST_REDIRECT

    payload: dict = {
        "from": config.EMAIL_FROM,
        "to": [recipient],
        "subject": subject,
        "html": html,
    }
    if scheduled_at:
        payload["scheduled_at"] = scheduled_at.isoformat()
    try:
        resp = httpx.post(
            _RESEND_URL,
            headers={
                "Authorization": f"Bearer {config.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if resp.status_code >= 400:
            # Surface Resend's actual reason (unverified domain, sandbox recipient, etc.)
            logger.error(
                "Resend rejected %s to %s (from=%r): HTTP %s — %s",
                template, recipient, config.EMAIL_FROM, resp.status_code, resp.text,
            )
        resp.raise_for_status()
        logger.info("Sent %s to %s", template, recipient)
    except Exception:
        logger.exception("Resend API call failed (template=%s, to=%s)", template, recipient)
        raise
