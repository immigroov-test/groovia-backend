"""Minimal RFC 5545 iCalendar (.ics) builder for booking-confirmation attachments.

One VEVENT per session. Times are emitted in UTC (the 'Z' form), which every calendar
client localizes for the viewer, so we don't need per-recipient timezone handling here.
"""
from datetime import datetime, timezone
from typing import Optional


def _fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(text: str) -> str:
    # RFC 5545 TEXT escaping: backslash, semicolon, comma, newline.
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(
    *, uid: str, start: datetime, end: datetime, summary: str,
    description: str = "", location: str = "", organizer_email: Optional[str] = None,
) -> str:
    """Return a single-event VCALENDAR string. `start`/`end` must be aware datetimes."""
    now = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Immigroov//Booking//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}@immigroov",
        f"DTSTAMP:{_fmt_utc(now)}",
        f"DTSTART:{_fmt_utc(start)}",
        f"DTEND:{_fmt_utc(end)}",
        f"SUMMARY:{_esc(summary)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_esc(description)}")
    if location:
        lines.append(f"LOCATION:{_esc(location)}")
    if organizer_email:
        lines.append(f"ORGANIZER:mailto:{organizer_email}")
    lines += ["STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"]
    # CRLF line endings per spec.
    return "\r\n".join(lines) + "\r\n"
