"""Platform timings and policy numbers, in ONE place.

These values appear twice over: once as behaviour (when a reminder actually fires, how long a hold
lasts) and once as a sentence in an email ("you'll get a reminder 24 hours and 30 minutes before").
When they lived in both places independently, changing the behaviour left the emails quietly lying to
people - the 1h reminder became 30min in BUG-094 while the confirmation email still promised the old
schedule. Everything here is imported by BOTH the code that acts on it and the templates that describe
it, so a change lands in one edit.

Deliberately dependency-free (no mailer, no notifications, no db) so anything can import it.
"""

# ── Session reminders ─────────────────────────────────────────────────────────
# Minutes before the slot, as (low, high) match windows. The dispatcher scans these; the templates
# describe them. Add or change a window here and both follow.
REMINDER_WINDOWS: dict[str, tuple[int, int]] = {
    "24h":   (23 * 60 + 45, 24 * 60 + 15),
    "30min": (20, 40),
}

# The mentor "are you available?" nudge, same ~1h-out window.
ATTENDANCE_WINDOW: tuple[int, int] = (45, 75)

# ── Meeting room ──────────────────────────────────────────────────────────────
MEETING_OPEN_BEFORE_MIN = 5      # room unlocks this long before the slot
MEETING_GRACE_AFTER_MIN = 30     # and stays open this long past the end

# ── Cancellation / reschedule ─────────────────────────────────────────────────
BUFFER_HOURS = 2.0               # inside this, cancelling is off the table entirely
LATE_CANCEL_FEE_PCT = 50         # kept when a mentor declines a late cancellation
MENTOR_NO_SHOW_PENALTY_PCT = 25  # payout penalty for a late mentor cancellation / no-show

# ── Money ─────────────────────────────────────────────────────────────────────
REFUND_BUSINESS_DAYS = "5-7"     # what a bank typically takes to show a refund
REFUND_CHASE_DAYS = 7            # after this, tell us it hasn't arrived
SUPPORT_REPLY_DAYS = "1-2"       # contact-form turnaround

# ── Auth links ────────────────────────────────────────────────────────────────
SIGNUP_LINK_EXPIRY = "24 hours"
RESET_LINK_EXPIRY = "1 hour"


def _humanise(minutes: int) -> str:
    """90 -> '1 hour 30 minutes', 1440 -> '24 hours', 30 -> '30 minutes'."""
    hours, mins = divmod(int(round(minutes)), 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return " ".join(parts) or "0 minutes"


def reminder_label(kind: str) -> str:
    """How far out one reminder is, in words: '24h' -> '24 hours', '30min' -> '30 minutes'.
    Taken from the CENTRE of the configured window, so the wording tracks the schedule."""
    lo, hi = REMINDER_WINDOWS.get(kind, (0, 0))
    return _humanise((lo + hi) / 2)


def reminder_notice() -> str:
    """Every reminder we send, listed for the confirmation email: '24 hours and 30 minutes'.
    Ordered furthest-out first, and it grows or shrinks automatically with REMINDER_WINDOWS."""
    labels = [reminder_label(k) for k, _ in
              sorted(REMINDER_WINDOWS.items(), key=lambda kv: -((kv[1][0] + kv[1][1]) / 2))]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"
