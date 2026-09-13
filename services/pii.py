"""Redact obvious personal identifiers before a chat turn is stored (FEAT-033).

We keep conversations to understand what people ask about, not to hold their contact details. A
message like "email me at x@y.com, my passport is J8462213" is useful for analytics as
"email me at [email], my passport is [number]": the intent survives, the identifier does not.

Deliberately narrow. This targets patterns that are unambiguous, and does NOT attempt to strip names
or addresses:

  * Reliable name detection needs a NER model. It would be another dependency, another model call per
    turn, and it would still be wrong often enough to be untrustworthy.
  * Over-redaction destroys the thing we are keeping the text for. "I am moving to Frankfurt" must
    survive, and a name-stripper that catches Frankfurt has made the data useless.

So this reduces exposure rather than eliminating it, and the retention window is what actually bounds
the risk. Both are needed; neither is sufficient alone. Say so plainly in the privacy policy rather
than claiming chats are anonymised, because they are not.

Applied to what we STORE. The agent still sees the real text at request time, or it could not answer.
"""
from __future__ import annotations

import re

# name@host.tld, tolerating +tags and subdomains.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")

# International and national phone shapes: optional +, then 7-15 digits with spaces, dots, dashes or
# brackets between them. Requires at least 7 digits so it cannot eat "10 years" or a year like 2026.
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{6,18}\d(?![\w.])")

# Passport, national ID, card and reference numbers. Two shapes, with different floors:
#   letter-prefixed (J8462213, AB123456) needs only 6 digits, because the letter already marks it as
#     an identifier rather than a quantity
#   bare digits need 8, because shorter runs are usually years, salaries, fees or durations, and
#     redacting "65000 EUR" or "2026" would destroy exactly what makes the message worth keeping
_LONG_ID = re.compile(r"\b(?:[A-Z]{1,2}\d{6,}|\d{8,})\b", re.IGNORECASE)

# IBAN: two letters, two check digits, then 11-30 alphanumerics.
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")


def redact(text: str | None) -> str | None:
    """Return `text` with obvious identifiers replaced by a label.

    Order matters: IBAN and email are matched before the phone rule, which is the greediest and would
    otherwise swallow the digits inside them.
    """
    if not text:
        return text
    out = _IBAN.sub("[iban]", text)
    out = _EMAIL.sub("[email]", out)
    out = _LONG_ID.sub("[id-number]", out)
    out = _PHONE.sub("[phone]", out)
    return out
