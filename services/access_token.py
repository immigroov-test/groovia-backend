"""Signed, single-booking access tokens, so a guest can use their own booking without an account.

The problem this solves: a guest pays, gets a confirmation email, and every link in it lands on a
page that redirects to /login. They have no account, so they cannot open the session they just paid
for. Telling them to sign up first is the wrong trade for something already purchased.

A token is what the email itself proves: it was sent to one address, for one booking, for one side of
it. So it carries `party` as well as `booking_id`, which makes attendance tracking *better* than a
login: the mentor's link stamps the mentor, the customer's link stamps the customer, regardless of who
happens to be signed in on that browser. That was a real confusion with the old links.

Trust model is the same as a password-reset link: whoever holds the email may act on it. That is the
intention, not a weakness.

Deliberately not a JWT: this needs three fields and no algorithm negotiation, and the whole class of
JWT algorithm-confusion bugs is avoided by having exactly one signing method.

Signed with SUPABASE_JWT_SECRET, which is guaranteed present (config exits at boot without it), so
there is no new secret to lose or forget to set per environment.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Literal, Optional

import config

logger = logging.getLogger("immigroov.access_token")

Party = Literal["candidate", "mentor"]

# Long enough to cover a booking made weeks ahead plus the session itself; short enough that a
# forwarded email does not grant access forever. Checked against the booking's own window too, so
# this is only the outer bound.
DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 120   # 120 days


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _secret() -> bytes:
    return (config.SUPABASE_JWT_SECRET or "").encode("utf-8")


def issue(booking_id: str, party: Party, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """A URL-safe token granting access to ONE booking as ONE party."""
    body = _b64(json.dumps(
        {"b": booking_id, "p": party, "e": int(time.time()) + ttl_seconds},
        separators=(",", ":"),
    ).encode("utf-8"))
    sig = _b64(hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify(token: Optional[str], booking_id: str) -> Optional[Party]:
    """The party this token authorises for THIS booking, or None.

    Returns None for anything suspect rather than raising, so callers can fall back to normal auth.
    The booking id is checked against the token's own, so a token for booking A cannot open booking B
    even though both are validly signed.
    """
    if not token or not _secret():
        return None
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64(hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).digest())
    # Timing-safe: a plain == leaks how much of the signature matched.
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        claims = json.loads(_unb64(body))
    except Exception:
        return None
    if claims.get("b") != booking_id:
        return None
    if int(claims.get("e", 0)) < int(time.time()):
        return None
    party = claims.get("p")
    return party if party in ("candidate", "mentor") else None


def session_url(booking_id: str, party: Party) -> str:
    """The session page link to put in an email: works for a guest, and for a signed-in user."""
    return (f"{config.FRONTEND_URL.rstrip('/')}/session/{booking_id}"
            f"?t={issue(booking_id, party)}")
