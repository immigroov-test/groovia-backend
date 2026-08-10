"""Booking-rule limits, in one place so signup, the profile edit and the availability page can't drift.

BUG-045: the ranges were previously enforced loosely (days_ahead accepted 1, notice accepted 0), which
let a mentor save rules that make them effectively unbookable or unable to be cancelled on. The limits
below are the product rules; the client validates against the same numbers as you type, and the server
re-checks because a client is never the authority on this.
"""
from typing import Optional

DAYS_AHEAD_MIN, DAYS_AHEAD_MAX = 30, 90
MIN_NOTICE_MIN, MIN_NOTICE_MAX = 2.0, 24.0
CANCEL_MIN, CANCEL_MAX = 2, 48

# Test-only mentor profiles that may keep out-of-range rules (0 notice, so a slot can be booked and
# joined immediately while testing). These are dummy profiles and never migrate to production.
EXEMPT_MENTOR_SLUGS = {"yokesh-dhanabal"}


def limits() -> dict:
    """The limits, so the client can render them without hardcoding a second copy."""
    return {
        "days_ahead": {"min": DAYS_AHEAD_MIN, "max": DAYS_AHEAD_MAX},
        "min_notice_hours": {"min": MIN_NOTICE_MIN, "max": MIN_NOTICE_MAX},
        "cancel_hours": {"min": CANCEL_MIN, "max": CANCEL_MAX},
    }


def validate(
    *,
    days_ahead: Optional[int],
    min_notice_hours: Optional[float],
    cancel_hours: Optional[int],
    mentor_slug: Optional[str] = None,
) -> None:
    """Raise ValueError with a mentor-readable message if any rule is out of range.
    Exempt test profiles are skipped entirely."""
    if mentor_slug and mentor_slug in EXEMPT_MENTOR_SLUGS:
        return
    if days_ahead is not None and not (DAYS_AHEAD_MIN <= days_ahead <= DAYS_AHEAD_MAX):
        raise ValueError(f"Mentees must be able to book between {DAYS_AHEAD_MIN} and {DAYS_AHEAD_MAX} days ahead")
    if min_notice_hours is not None and not (MIN_NOTICE_MIN <= min_notice_hours <= MIN_NOTICE_MAX):
        raise ValueError(f"Minimum booking notice must be between {MIN_NOTICE_MIN:g} and {MIN_NOTICE_MAX:g} hours")
    if cancel_hours is not None and not (CANCEL_MIN <= cancel_hours <= CANCEL_MAX):
        raise ValueError(f"Cancellation / rescheduling notice must be between {CANCEL_MIN} and {CANCEL_MAX} hours")
