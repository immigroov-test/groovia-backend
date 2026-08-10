import base64
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
        # Immigroov logo (served from the frontend's public/ - absolute URL so email clients can load it).
        f'<p style="margin:0 0 28px"><img src="{config.FRONTEND_URL.rstrip("/")}/Immigroov_Transparent_Logo.png"'
        ' alt="Immigroov" height="28" style="height:28px;width:auto;display:block;border:0"></p>'
        + content
        + '<hr style="border:none;border-top:1px solid #e8e8e8;margin:32px 0">'
        '<p style="margin:0;font-size:12px;color:#999">'
        "Immigroov - AI-powered career consultancy for international professionals.<br>"
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


def _detail_list(rows: list[tuple[str, str]]) -> str:
    """Airy label/value pairs with no box - matches the confirmed-booking page style."""
    inner = ""
    for label, value in rows:
        if not value:
            continue
        inner += (
            '<tr><td style="padding:0 0 16px">'
            f'<p style="margin:0 0 3px;font-size:11px;font-weight:600;color:#8a8a8a;text-transform:uppercase;letter-spacing:.7px">{_e(label)}</p>'
            f'<p style="margin:0;font-size:15px;font-weight:600;color:#0a0a0a;line-height:1.4">{_e(value)}</p>'
            "</td></tr>"
        )
    return (
        '<table cellpadding="0" cellspacing="0" role="presentation" style="width:100%;margin:12px 0 20px">'
        + inner + "</table>"
    )


# ── Templates ─────────────────────────────────────────────────────────────────

def _mentor_approved(d: dict) -> tuple[str, str]:
    name = _e(d.get("display_name", ""))
    hub = d.get("mentor_hub_url", config.FRONTEND_URL + "/mentor")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">You\'re approved - welcome to Immigroov!</h1>'
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
    return "You're approved - welcome to the Immigroov mentor team", _base(body)


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


def _mentor_changes_requested(d: dict) -> tuple[str, str]:
    name = _e(d.get("display_name", ""))
    reason = (d.get("reason") or "").strip()
    hub = d.get("mentor_hub_url", config.FRONTEND_URL + "/mentor")
    reason_html = _info_row("What to change", reason) if reason else ""
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">A quick update needed on your application</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        "Thanks for applying to mentor on Immigroov. We'd like to move forward, but our reviewer has asked "
        "for a few changes before we can approve your profile."
        "</p>"
        + reason_html
        + '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">'
        "Open your Mentor Hub, update your profile, and re-submit for approval."
        "</p>"
        + _btn(hub, "Update my profile")
    )
    return "A quick update needed on your Immigroov mentor application", _base(body)


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


def _admin_mentor_suspended(d: dict) -> tuple[str, str]:
    """Ops/admin copy confirming a mentor was suspended, so the team has a record."""
    name = _e(d.get("display_name", ""))
    email = _e(d.get("mentor_email", ""))
    by = _e(d.get("suspended_by", "an admin"))
    review_url = d.get("review_url", config.FRONTEND_URL + "/admin")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Mentor suspended</h1>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"<strong>{name}</strong>{f' ({email})' if email else ''} has been suspended by {by}. "
        "Their profile is now hidden from candidates and they can take no new bookings."
        "</p>"
        + _btn(review_url, "Open admin panel")
    )
    return f"[Immigroov] Mentor suspended: {name}", _base(body)


def _mentor_row(name: str, photo: str, subtitle: str = "") -> str:
    """A small mentor avatar + name block for booking emails."""
    photo = (photo or "").strip()
    avatar = (
        f'<img src="{_e(photo)}" alt="{_e(name)}" width="52" height="52"'
        ' style="width:52px;height:52px;border-radius:50%;object-fit:cover;display:block;border:0">'
    ) if photo else (
        '<div style="width:52px;height:52px;border-radius:50%;background:#e8ebff;color:#3a4bd0;'
        'text-align:center;line-height:52px;font-weight:700;font-size:18px">'
        f'{_e((name or "?")[:1].upper())}</div>'
    )
    sub = f'<p style="margin:2px 0 0;font-size:13px;color:#777">{_e(subtitle)}</p>' if subtitle else ""
    return (
        '<table cellpadding="0" cellspacing="0" role="presentation" style="margin:0 0 20px;width:100%">'
        f'<tr><td width="52" style="vertical-align:middle;padding-right:14px">{avatar}</td>'
        f'<td style="vertical-align:middle"><p style="margin:0;font-size:16px;font-weight:700;color:#0a0a0a">{_e(name)}</p>{sub}</td>'
        "</tr></table>"
    )


def _prep_block(notes: str, answers: list, heading: str) -> str:
    """The customer's free-text prep note + their answers to the mentor's intake questions (BUG-113).
    Returns '' when there's nothing to show."""
    rows = ""
    if notes:
        rows += f'<p style="margin:6px 0 0;font-size:14px;color:#444;line-height:1.6">{_e(notes)}</p>'
    for a in (answers or []):
        q = _e(str(a.get("question", "") or ""))
        ans = _e(str(a.get("answer", "") or ""))
        if not ans:
            continue
        rows += (f'<p style="margin:8px 0 0;font-size:14px;color:#444;line-height:1.6">'
                 f'<strong>{q}</strong><br>{ans}</p>')
    if not rows:
        return ""
    return (
        '<div style="margin:16px 0 0;padding:14px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px">'
        f'<p style="margin:0;font-size:13px;font-weight:600;color:#475569">{_e(heading)}</p>'
        + rows + '</div>'
    )


def _money(amount: float, currency: str) -> str:
    """Plain '€71.26' / 'INR 1,499.00'. No locale guessing: the symbol map covers what we price in and
    anything else falls back to the ISO code, which is never wrong, just less pretty."""
    symbols = {"EUR": "€", "USD": "$", "GBP": "£", "INR": "₹", "AUD": "A$", "CAD": "C$",
               "SGD": "S$", "AED": "AED ", "SAR": "SAR ", "CHF": "CHF ", "JPY": "¥"}
    sym = symbols.get((currency or "").upper())
    return f"{sym}{amount:,.2f}" if sym else f"{(currency or '').upper()} {amount:,.2f}"


def _invoice_block(inv: Optional[dict], booking_ref: str = "") -> str:
    """FEAT-019: the payment breakdown, so the confirmation doubles as the customer's invoice. Shows
    only what THEY paid (session, platform fee, tax, total) - commission and the mentor's payout are
    internal. Free sessions pass inv=None and get nothing."""
    if not inv:
        return ""
    ccy = inv.get("currency") or ""
    rows = [("Session", _money(float(inv.get("session") or 0), ccy))]
    if float(inv.get("platform_fee") or 0) > 0:
        rows.append(("Platform fee", _money(float(inv["platform_fee"]), ccy)))
    if float(inv.get("tax") or 0) > 0:
        rows.append(("Tax", _money(float(inv["tax"]), ccy)))

    line_html = "".join(
        '<tr>'
        f'<td style="padding:4px 0;font-size:14px;color:#555">{_e(label)}</td>'
        f'<td style="padding:4px 0;font-size:14px;color:#555;text-align:right">{_e(value)}</td>'
        '</tr>'
        for label, value in rows
    )
    total_html = (
        '<tr>'
        '<td style="padding:10px 0 0;border-top:1px solid #e5e5e5;font-size:15px;font-weight:700;color:#0a0a0a">Total paid</td>'
        f'<td style="padding:10px 0 0;border-top:1px solid #e5e5e5;font-size:15px;font-weight:700;color:#0a0a0a;text-align:right">'
        f'{_e(_money(float(inv.get("total") or 0), ccy))}</td>'
        '</tr>'
    )
    ref = (f'<p style="margin:10px 0 0;font-size:12px;color:#999">Booking reference {_e(booking_ref)}. '
           'Quote it if you contact support.</p>') if booking_ref else ""
    return (
        '<div style="margin:20px 0 0;padding:14px 16px;background:#fafafa;border:1px solid #ececec;border-radius:12px">'
        '<p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#999;text-transform:uppercase;letter-spacing:.6px">'
        'Payment summary</p>'
        f'<table style="width:100%;border-collapse:collapse">{line_html}{total_html}</table>'
        + ref +
        '</div>'
    )


def _booking_confirmed_candidate(d: dict) -> tuple[str, str]:
    candidate = _e(d.get("candidate_name", ""))
    mentor = d.get("mentor_name", "your mentor")
    photo = d.get("mentor_photo", "")
    service = d.get("service_title", "1-on-1 session")
    url = d.get("meeting_url", "")
    is_guest = d.get("is_guest")
    signup_url = d.get("signup_url", "")
    guest_block = (
        '<div style="margin:20px 0 0;padding:14px 16px;background:#fff7ed;border:1px solid #fed7aa;border-radius:12px">'
        '<p style="margin:0;font-size:14px;color:#7c2d12;line-height:1.6">'
        'You booked as a <strong>guest</strong>. Create a free account with <strong>this email</strong> '
        'to join your session and manage it (reschedule or cancel).</p>'
        + (_btn(signup_url, "Create your free account") if signup_url else "")
        + '</div>'
    )
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your session is confirmed ✓</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {candidate},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Your session with <strong>{_e(mentor)}</strong> is confirmed. Here are the details.</p>'
        + _mentor_row(mentor, photo, "Your mentor")
        + _detail_list([
            ("What", service),
            ("When (your time)", d.get("candidate_time", "")),
            ("When (mentor's time)", d.get("mentor_time", "")),
            ("Who", f"{mentor} (mentor) and you"),
            ("Where", "Video call - use the Join meeting button below"),
            ("Booking reference", d.get("booking_ref", "")),
        ])
        + _invoice_block(d.get("invoice"), d.get("booking_ref", ""))
        + _prep_block(d.get("notes", ""), d.get("answers"), "What you shared with your mentor")
        + '<p style="margin:16px 0 0;font-size:15px;color:#444;line-height:1.6">'
        "You'll receive a reminder 24 hours and 30 minutes before the session."
        "</p>"
        + (guest_block if is_guest else (
            (_btn(url, "Join meeting") if url else "")
            + '<p style="margin:20px 0 0;font-size:14px;color:#444;line-height:1.6">'
            f'Need to change it? <a href="{config.FRONTEND_URL}/account/sessions" style="color:#6b7fff">Reschedule or cancel</a>'
            " anytime from your account.</p>"
        ))
    )
    return f"Confirmed: your session with {mentor}", _base(body)


def _booking_confirmed_mentor(d: dict) -> tuple[str, str]:
    mentor = _e(d.get("mentor_name", ""))
    candidate = d.get("candidate_name", "a candidate")
    candidate_email = d.get("candidate_email", "")
    service = d.get("service_title", "1-on-1 session")
    url = d.get("meeting_url", "")
    notes_html = _prep_block(d.get("notes", ""), d.get("answers"), f"What to prepare (from {candidate})")
    who = f"{candidate}" + (f" ({candidate_email})" if candidate_email else "")
    body = (
        f'<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">New booking from {_e(candidate)}</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {mentor},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"<strong>{_e(candidate)}</strong> has booked a session with you. Here are the details."
        "</p>"
        + _detail_list([
            ("What", service),
            ("When (your time)", d.get("mentor_time", "")),
            ("When (candidate's time)", d.get("candidate_time", "")),
            ("Who", who),
            ("Where", "Video call - use the Join meeting button below"),
        ])
        + notes_html
        + (_btn(url, "Join meeting") if url else "")
        + (
            '<p style="margin:24px 0 0;font-size:13px;color:#888;line-height:1.6">'
            "Need to change this session? You can reschedule or cancel it from your "
            f'<a href="{config.FRONTEND_URL.rstrip("/")}/mentor" style="color:#6b7fff">mentor dashboard</a> '
            "(Sessions tab).</p>"
        )
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


def _session_reminder_30min(d: dict) -> tuple[str, str]:
    recipient = _e(d.get("recipient_name", ""))
    other = d.get("other_party_name", "your mentor")
    url = d.get("meeting_url", "")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your session starts in 30 minutes</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {recipient},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"Your session with <strong>{_e(other)}</strong> starts in about 30 minutes. Make sure you're ready!"
        "</p>"
        + (_btn(url, "Join meeting") if url else "")
    )
    return f"Your session with {other} starts in 30 minutes", _base(body)


def _mentor_attendance_check(d: dict) -> tuple[str, str]:
    mentor = _e(d.get("mentor_name", ""))
    candidate = d.get("candidate_name", "your attendee")
    url = config.FRONTEND_URL + "/account/sessions"
    when = d.get("session_time", "")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your session is in about 1 hour</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {mentor},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"Your session with <strong>{_e(candidate)}</strong> is coming up soon. Please confirm you'll be "
        "there so your attendee knows to expect you.</p>"
        + (_info_row("When", when) if when else "")
        + _btn(url, "View & confirm")
    )
    return f"Are you available for your session with {candidate}?", _base(body)


def _review_request(d: dict) -> tuple[str, str]:
    candidate = _e(d.get("candidate_name", ""))
    mentor = d.get("mentor_name", "your mentor")
    # Prefer a session-specific link (the review form lives on /session/<id>); fall back to /mentors.
    review_url = d.get("review_url") or (d.get("platform_url", config.FRONTEND_URL) + "/mentors")
    is_reminder = bool(d.get("is_reminder"))
    lead = ("Just a quick reminder to review your recent session with"
            if is_reminder else "We hope your session with")
    tail = "" if is_reminder else " was valuable"
    body = (
        f'<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">How was your session with {_e(mentor)}?</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {candidate},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"{lead} <strong>{_e(mentor)}</strong>{tail}! Your feedback helps other "
        "international professionals find the right mentor for their journey."
        "</p>"
        '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">It only takes a minute to leave a review.</p>'
        + _btn(review_url, "Leave a Review")
    )
    subject = (f"Reminder: review your session with {mentor}"
               if is_reminder else f"How was your session with {mentor}?")
    return subject, _base(body)


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
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Application received - we\'ll be in touch soon</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        "Thanks for applying to become a mentor on Immigroov. Our team will review your profile and "
        "get back to you within <strong>1-2 business days</strong>."
        "</p>"
        '<p style="margin:0;font-size:15px;color:#444;line-height:1.6">'
        "While you wait, you can set your weekly availability so it's ready the moment you're approved."
        "</p>"
        + _btn(avail_url, "Set my availability")
    )
    return "We've received your Immigroov mentor application", _base(body)


def _mentor_reinstated(d: dict) -> tuple[str, str]:
    name = _e(d.get("display_name", ""))
    hub = d.get("mentor_hub_url", config.FRONTEND_URL + "/mentor")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your mentor account is active again</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        "Good news - your Immigroov mentor account has been reinstated. Your profile is visible to "
        "candidates again and you can take new bookings."
        "</p>"
        + _btn(hub, "Go to Mentor Hub")
    )
    return "Your Immigroov mentor account is active again", _base(body)


def _admin_mentor_application(d: dict) -> tuple[str, str]:
    """Ops/admin copy when a new mentor applies, so the review queue is actioned."""
    name = d.get("display_name") or "-"
    email = d.get("mentor_email") or ""
    headline = d.get("headline") or ""
    review_url = d.get("review_url", config.FRONTEND_URL + "/admin")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">New mentor application</h1>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">A new mentor has applied and is awaiting review.</p>'
        + _info_row("Name", name)
        + (_info_row("Email", email) if email else "")
        + (_info_row("Headline", headline) if headline else "")
        + _btn(review_url, "Review application")
    )
    return f"[Admin] New mentor application: {name}", _base(body)


def _admin_mentor_change_request(d: dict) -> tuple[str, str]:
    """BUG-076/BUG-075: a live mentor changed a field that needs approval (name / country). The live
    profile is unchanged until an admin approves the staged change."""
    name = _e(d.get("mentor_name", ""))
    rows = [(c.get("label", ""), c.get("delta", "")) for c in (d.get("changes") or [])]
    review_url = d.get("review_url", config.FRONTEND_URL + "/admin")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Mentor changes need approval</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6"><strong>{name}</strong> submitted '
        "profile changes that need your approval before they go live. Their current profile is unchanged until you approve.</p>"
        + _detail_list(rows)
        + _btn(review_url, "Review in admin")
    )
    return f"[Admin] {name} submitted changes for approval", _base(body)


def _approval_change_row(c: dict) -> str:
    """One 'requires approval' row: label + old -> new. Photo renders as two thumbnails instead of
    raw URLs so the admin can actually see whether the new photo is acceptable."""
    label, old, new = c.get("label", ""), c.get("old", ""), c.get("new", "")
    if label == "Photo":
        def _thumb(url: str) -> str:
            if not url or url == "(empty)":
                return '<span style="font-size:13px;color:#999">No photo</span>'
            return (f'<img src="{_e(url)}" alt="" style="width:64px;height:64px;border-radius:8px;'
                     'object-fit:cover;vertical-align:middle;border:1px solid #e8e8e8">')
        value_html = f'{_thumb(old)}&nbsp;&nbsp;<span style="color:#999">→</span>&nbsp;&nbsp;{_thumb(new)}'
    else:
        value_html = f'{_e(old)} <span style="color:#999">→</span> {_e(new)}'
    return (
        '<tr><td style="padding:0 0 16px">'
        f'<p style="margin:0 0 3px;font-size:11px;font-weight:600;color:#8a8a8a;text-transform:uppercase;letter-spacing:.7px">{_e(label)}</p>'
        f'<p style="margin:0;font-size:15px;font-weight:600;color:#0a0a0a;line-height:1.4">{value_html}</p>'
        "</td></tr>"
    )


def _admin_mentor_changes_submitted(d: dict) -> tuple[str, str]:
    """Retest feedback (profile-approval optimization): ONE consolidated email per mentor submission
    - never one per field. Only sent when at least one approval-gated field (Name/Phone/Photo/Service
    Country) actually changed; any auto-approved (already-live) fields from the same submission are
    listed as information only, with no action needed."""
    name = _e(d.get("mentor_name", ""))
    approval_changes = d.get("approval_changes") or []
    info_changes = d.get("info_changes") or []
    review_url = d.get("review_url", config.FRONTEND_URL + "/admin")

    approval_html = (
        '<table cellpadding="0" cellspacing="0" role="presentation" style="width:100%;margin:12px 0 20px">'
        + "".join(_approval_change_row(c) for c in approval_changes)
        + "</table>"
    )
    info_html = ""
    if info_changes:
        items = "".join(
            f'<li style="margin:0 0 6px;font-size:14px;color:#555">{_e(c.get("label", ""))}: Changed</li>'
            for c in info_changes
        )
        info_html = (
            '<p style="margin:24px 0 8px;font-size:12px;font-weight:600;color:#999;text-transform:uppercase;letter-spacing:.6px">'
            "Information only - no approval required</p>"
            f'<ul style="margin:0 0 8px;padding-left:20px">{items}</ul>'
            '<p style="margin:0;font-size:13px;color:#999">These have already been automatically approved.</p>'
        )

    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Mentor profile changes awaiting approval</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6"><strong>{name}</strong> submitted '
        "profile changes for review. Their live profile is unchanged for these fields until you approve.</p>"
        '<p style="margin:24px 0 8px;font-size:12px;font-weight:600;color:#999;text-transform:uppercase;letter-spacing:.6px">'
        "Requires approval</p>"
        + approval_html + info_html
        + _btn(review_url, "Review in admin")
    )
    return f"[Admin] {name} submitted changes for approval", _base(body)


# ── Lifecycle v2 templates (cancel / reschedule / no-show) ──────────────────────

def _booking_cancelled(d: dict) -> tuple[str, str]:
    """BUG-124: one template, two audiences. The mentor used to receive the customer's copy ("book
    another session from the mentor directory"), which reads like someone else's mail. BUG-123 adds the
    reason, and the body now carries the same detail as the reschedule email (service, when it was
    scheduled in the RECIPIENT's own timezone, booking reference)."""
    name = _e(d.get("recipient_name", "there"))
    other = _e(d.get("other_name", "the other party"))
    is_mentor = d.get("audience") == "mentor"
    by_you = bool(d.get("cancelled_by_you"))
    reason = (d.get("reason") or "").strip()
    manage_url = d.get("manage_url") or ""

    if by_you:
        lead = f"You cancelled this session with <strong>{other}</strong>. We've let them know."
    elif is_mentor:
        lead = f"<strong>{other}</strong> cancelled their session with you. The slot is free again on your calendar."
    else:
        lead = f"<strong>{other}</strong> cancelled your session."

    rows = _info_row("Session", d.get("service_title", "")) if d.get("service_title") else ""
    rows += _info_row("Was scheduled for", d.get("session_time", ""))
    if d.get("booking_ref"):
        rows += _info_row("Booking reference", d["booking_ref"])
    if reason:
        rows += _info_row("Reason given", reason)

    tail = ("Your availability for that slot is open again."
            if is_mentor else
            "You can book another session any time from the mentor directory.")
    cta = _btn(manage_url, "Go to your dashboard" if is_mentor else "Find another mentor") if manage_url else ""

    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Session cancelled</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">{lead}</p>'
        + rows
        + f'<p style="margin:0;font-size:15px;color:#444;line-height:1.6">{tail}</p>'
        + cta
    )
    return "Your Immigroov session was cancelled", _base(body)


def _booking_rescheduled(d: dict) -> tuple[str, str]:
    # BUG-088: the rescheduled email now mirrors the booking-confirmation email - service, the
    # previous time AND the new time, and the join link - not just a bare "new time" line.
    name = _e(d.get("recipient_name", "there"))
    other = d.get("other_name", "the other party")
    service = d.get("service_title", "1-on-1 session")
    url = d.get("meeting_url", "")
    manage = d.get("manage_url", "")
    old_time = d.get("old_time", "")
    new_time = d.get("new_time", "") or d.get("session_time", "")
    rows: list[tuple[str, str]] = [("What", service)]
    if old_time:
        rows.append(("Previous time", old_time))
    rows.append(("New date & time", new_time))
    rows.append(("Where", "Video call - use the Join meeting button below"))
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your session was rescheduled ✓</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"Your session with <strong>{_e(other)}</strong> has moved to a new time. Here are the updated details.</p>"
        + _detail_list(rows)
        + (_btn(url, "Join meeting") if url else "")
        + (
            '<p style="margin:20px 0 0;font-size:14px;color:#444;line-height:1.6">'
            f'Need to change it again? <a href="{manage}" style="color:#6b7fff">Manage this session</a>.</p>'
            if manage else ""
        )
    )
    return f"Rescheduled: your session with {other}", _base(body)


def _reschedule_proposed(d: dict) -> tuple[str, str]:
    name = _e(d.get("recipient_name", "there"))
    mentor = d.get("other_name", "your mentor")
    # BUG-085: link straight to the session so the customer lands on the accept/counter/reject
    # controls, instead of a generic /account page where the proposal isn't visible.
    link = d.get("session_url") or (config.FRONTEND_URL + "/account/sessions")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your mentor proposed a new time</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"<strong>{_e(mentor)}</strong> proposed a new time range for your session. "
        "Open it to pick a time that works, ask for another date, or decline.</p>"
        + _btn(link, "Review & pick a time")
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
        "Approve or decline it from your Mentor Hub - if you don't respond in time it auto-approves.</p>"
        + _btn(config.FRONTEND_URL + "/mentor", "Review request")
    )
    return "A reschedule was requested", _base(body)


def _reschedule_counter(d: dict) -> tuple[str, str]:
    # Mentee used "Ask another date" - the mentor must PROPOSE times for that day (not approve/decline).
    mentor = _e(d.get("recipient_name", "there"))
    attendee = d.get("other_name", "An attendee")
    link = d.get("session_url") or (config.FRONTEND_URL + "/mentor")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Your attendee asked for a different day</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {mentor},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"<strong>{_e(attendee)}</strong> would like to reschedule but none of your proposed times worked. "
        "Open the session to propose a new date and time range that suits you.</p>"
        + _btn(link, "Propose new times")
    )
    return "Your attendee asked for a different day", _base(body)


def _cancel_requested(d: dict) -> tuple[str, str]:
    mentor = _e(d.get("recipient_name", "there"))
    attendee = d.get("other_name", "An attendee")
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">A cancellation was requested</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {mentor},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"<strong>{_e(attendee)}</strong> requested to cancel a session within 24 hours of the start. "
        "Approve or decline it from your Mentor Hub - if you don't respond in time it auto-approves.</p>"
        + _btn(config.FRONTEND_URL + "/mentor", "Review request")
    )
    return "A cancellation was requested", _base(body)


def _no_show_reported(d: dict) -> tuple[str, str]:
    """Audience-aware, like the cancellation email. Both parties used to get one generic message
    ("a session you were part of was reported") pointing at /account, which tells a mentor nothing and
    sends them to the wrong dashboard. Each side is now told who was reported, what they can do next,
    and where. Deliberately silent on the other party's money: the mentor is not told the customer's
    refund and the customer is not told the mentor's payout penalty."""
    name = _e(d.get("recipient_name", "there"))
    other = _e(d.get("other_name", "the other participant"))
    is_mentor = d.get("audience") == "mentor"
    about_you = bool(d.get("about_you"))          # True when the RECIPIENT is the one reported

    if is_mentor and about_you:
        lead = (f"<strong>{other}</strong> reported that you didn't join this session. "
                "If that's wrong, open the session and let us know so we can review it.")
        cta = ("Open your sessions", d.get("manage_url") or f"{config.FRONTEND_URL}/mentor")
    elif is_mentor:
        lead = (f"<strong>{other}</strong> didn't join this session. You can offer them a rebook or "
                "close the session from your dashboard.")
        cta = ("Resolve this session", d.get("manage_url") or f"{config.FRONTEND_URL}/mentor")
    elif about_you:
        lead = (f"<strong>{other}</strong> reported that you didn't join this session. They may offer "
                "you a rebook. If this is wrong, open the session and tell us.")
        cta = ("Open the session", d.get("manage_url") or f"{config.FRONTEND_URL}/account")
    else:
        lead = (f"<strong>{other}</strong> didn't join this session. You can rebook with them, try a "
                "different mentor, or request a refund, all from the session page.")
        cta = ("Choose how to resolve it", d.get("manage_url") or f"{config.FRONTEND_URL}/account")

    rows = _info_row("Session", d.get("service_title", "")) if d.get("service_title") else ""
    rows += _info_row("Scheduled for", d.get("session_time", ""))
    if d.get("booking_ref"):
        rows += _info_row("Booking reference", d["booking_ref"])

    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Session marked as a no-show</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">{lead}</p>'
        + rows
        + _btn(cta[1], cta[0])
    )
    return "A session was marked as a no-show", _base(body)


def _cancel_request_sent(d: dict) -> tuple[str, str]:
    """BUG-124 audit: the customer who asks for a late cancellation used to get NOTHING back - only the
    mentor was emailed - so they had no record that the request existed or what happens next."""
    name = _e(d.get("recipient_name", "there"))
    other = _e(d.get("other_name", "your mentor"))
    rows = _info_row("Session", d.get("service_title", "")) if d.get("service_title") else ""
    rows += _info_row("Scheduled for", d.get("session_time", ""))
    if d.get("booking_ref"):
        rows += _info_row("Booking reference", d["booking_ref"])
    if d.get("reason"):
        rows += _info_row("Reason you gave", d["reason"])
    body = (
        '<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">Cancellation request sent</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Hi {name},</p>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">'
        f"We've asked <strong>{other}</strong> to approve your cancellation. This session is close enough "
        "to its start time that it needs their approval. We'll email you as soon as they respond, and any "
        "refund follows our refund policy.</p>"
        + rows
        + _btn(d.get("manage_url") or f"{config.FRONTEND_URL}/account", "View the session")
    )
    return "We've sent your cancellation request", _base(body)


def _booking_admin_notice(d: dict) -> tuple[str, str]:
    """Ops/admin copy for every booking, reschedule, and cancellation."""
    event = d.get("event", "updated")
    titles = {
        "booked": "New booking",
        "cancelled": "Booking cancelled",
        "rescheduled": "Booking rescheduled",
        "no_show": "Session no-show",
    }
    title = titles.get(event, "Booking update")
    mentor = d.get("mentor_name") or "-"
    candidate = d.get("candidate_name") or "-"
    candidate_email = d.get("candidate_email") or ""
    rows: list[tuple[str, str]] = []
    # A booking reference first: it is what support and the customer quote at each other, and what
    # every other admin surface is searchable by. Without it an ops mail was unactionable.
    if d.get("booking_ref"):
        rows.append(("Booking reference", d["booking_ref"]))
    if d.get("service_title"):
        rows.append(("Session", d["service_title"]))
    rows += [
        ("Mentor", mentor),
        ("Candidate", f"{candidate} ({candidate_email})" if candidate_email else candidate),
    ]
    # New bookings carry both parties' times; lifecycle events carry a single session_time.
    if d.get("mentor_time") or d.get("candidate_time"):
        rows.append(("When (mentor's time)", d.get("mentor_time") or "-"))
        rows.append(("When (candidate's time)", d.get("candidate_time") or "-"))
    else:
        rows.append(("When", d.get("session_time", "-")))
    # Money is ADMIN-ONLY and is exactly what makes this mail actionable: what the customer paid, and
    # the split. Neither party's email ever carries the other side's figures.
    inv = d.get("invoice")
    if inv:
        ccy = inv.get("currency") or ""
        rows.append(("Customer paid", _money(float(inv.get("total") or 0), ccy)))
        rows.append(("Of which platform fee", _money(float(inv.get("platform_fee") or 0), ccy)))
        if float(inv.get("tax") or 0) > 0:
            rows.append(("Tax", _money(float(inv["tax"]), ccy)))
    if d.get("reason"):
        rows.append(("Reason given", d["reason"]))
    body = (
        f'<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">{_e(title)}</h1>'
        '<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">Admin notification - a booking event occurred.</p>'
        + _detail_list(rows)
    )
    return f"[Admin] {title}: {candidate} × {mentor}", _base(body)


def _auth_link_email(title: str, subject: str, lead: str, btn_label: str, expiry_note: str, d: dict) -> tuple[str, str]:
    url = d.get("action_url", "")
    body = (
        f'<h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#0a0a0a">{_e(title)}</h1>'
        f'<p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.6">{lead}</p>'
        + _btn(url, btn_label)
        + f'<p style="margin:20px 0 0;font-size:13px;color:#999;line-height:1.5">{expiry_note} '
          f'If the button doesn\'t work, copy and paste this link: <br><span style="word-break:break-all">{_e(url)}</span></p>'
    )
    return subject, _base(body)


# BUG-026: these back the Supabase Auth "Send Email" hook (routers/auth.py:auth_email_hook), so
# sign-in/signup/recovery emails go out over the same Resend pipeline (and EMAIL_FROM address) as
# every other Immigroov email, instead of Supabase's own default sender.
def _auth_signup_confirm(d: dict) -> tuple[str, str]:
    return _auth_link_email(
        "Confirm your Immigroov account", "Confirm your Immigroov account",
        "Click below to confirm your email and finish setting up your account.",
        "Confirm my account", "This link expires in 24 hours.", d,
    )


def _auth_magic_link(d: dict) -> tuple[str, str]:
    return _auth_link_email(
        "Sign in to Immigroov", "Your Immigroov sign-in link",
        "Click below to sign in. This link is only valid for this device/browser.",
        "Sign in", "This link expires shortly and can only be used once.", d,
    )


def _auth_recovery(d: dict) -> tuple[str, str]:
    return _auth_link_email(
        "Reset your Immigroov password", "Reset your Immigroov password",
        "Click below to choose a new password. If you didn't request this, you can ignore this email.",
        "Reset password", "This link expires in 1 hour.", d,
    )


def _auth_generic(d: dict) -> tuple[str, str]:
    """Fallback for any Supabase auth email type without its own copy (email change, invite,
    reauthentication, ...) - still branded and sent via Resend rather than silently dropped."""
    return _auth_link_email(
        "Confirm this action", "Confirm your Immigroov account action",
        "Click below to confirm this action on your Immigroov account.",
        "Confirm", "This link will expire soon.", d,
    )


def _contact_form(d: dict) -> tuple[str, str]:
    """Contact-page submission, delivered to the support inbox."""
    name = _e(f"{d.get('first_name', '')} {d.get('last_name', '')}".strip() or "-")
    email = _e(d.get("email", ""))
    topic = _e(d.get("topic") or "General")
    msg = _e(d.get("message", "")).replace("\n", "<br>")
    body = (
        '<h1 style="margin:0 0 12px;font-size:20px;font-weight:700;color:#0a0a0a">New contact message</h1>'
        + _details_card([("From", name), ("Email", email), ("Topic", topic)])
        + f'<p style="margin:16px 0 0;font-size:15px;color:#444;line-height:1.6">{msg}</p>'
    )
    return f"Contact form: {topic}", _base(body)


_TEMPLATES = {
    "booking_admin_notice": _booking_admin_notice,
    "contact_form": _contact_form,
    "mentor_application_received": _mentor_application_received,
    "admin_mentor_application": _admin_mentor_application,
    "admin_mentor_change_request": _admin_mentor_change_request,
    "admin_mentor_changes_submitted": _admin_mentor_changes_submitted,
    "mentor_approved": _mentor_approved,
    "mentor_rejected": _mentor_rejected,
    "mentor_changes_requested": _mentor_changes_requested,
    "mentor_suspended": _mentor_suspended,
    "admin_mentor_suspended": _admin_mentor_suspended,
    "mentor_reinstated": _mentor_reinstated,
    "booking_confirmed_candidate": _booking_confirmed_candidate,
    "booking_confirmed_mentor": _booking_confirmed_mentor,
    "session_reminder_24h": _session_reminder_24h,
    "session_reminder_1h": _session_reminder_1h,
    "session_reminder_30min": _session_reminder_30min,
    "mentor_attendance_check": _mentor_attendance_check,
    "review_request": _review_request,
    "welcome_candidate": _welcome_candidate,
    "welcome_mentor": _welcome_mentor,
    "booking_cancelled": _booking_cancelled,
    "booking_rescheduled": _booking_rescheduled,
    "reschedule_proposed": _reschedule_proposed,
    "reschedule_requested": _reschedule_requested,
    "reschedule_counter": _reschedule_counter,
    "cancel_requested": _cancel_requested,
    "cancel_request_sent": _cancel_request_sent,
    "no_show_reported": _no_show_reported,
    "auth_signup_confirm": _auth_signup_confirm,
    "auth_magic_link": _auth_magic_link,
    "auth_recovery": _auth_recovery,
    "auth_generic": _auth_generic,
}


def send_transactional(
    to: str,
    template: str,
    data: dict,
    scheduled_at: Optional[datetime] = None,
    attachments: Optional[list[dict]] = None,
) -> None:
    """Send one transactional email via Resend. Raises on network error or non-2xx.
    Dispatch via FastAPI BackgroundTasks so the request path is never blocked.
    When MOCK_SERVICES=true, writes to _dev_emails.jsonl instead of calling Resend.
    `attachments` is a list of {"filename": str, "content": str} (raw text, e.g. an .ics);
    the content is base64-encoded here for Resend."""
    fn = _TEMPLATES.get(template)
    if fn is None:
        logger.error("Unknown email template %r", template)
        return
    subject, html = fn(data)

    if config.MOCK_SERVICES:
        _log_dev_email(to, template, subject, data, scheduled_at)
        return

    if not config.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set - skipping %s to %s", template, to)
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
    if attachments:
        payload["attachments"] = [
            {"filename": a["filename"],
             "content": base64.b64encode(a["content"].encode("utf-8")).decode("ascii")}
            for a in attachments
        ]
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
                "Resend rejected %s to %s (from=%r): HTTP %s - %s",
                template, recipient, config.EMAIL_FROM, resp.status_code, resp.text,
            )
        resp.raise_for_status()
        logger.info("Sent %s to %s", template, recipient)
    except Exception:
        logger.exception("Resend API call failed (template=%s, to=%s)", template, recipient)
        raise
