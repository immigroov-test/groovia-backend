"""Validate + normalize mentor multi-currency pricing input.

Two shapes:
  - currency_rates (on the mentor): additional-currency base rates [{currency, hourly_rate}].
    The PRIMARY currency + rate live in mentors.currency / mentors.hourly_rate.
  - currency_prices (on a service): explicit per-currency prices [{currency, base_price, offer_price?}]
    (usually derived from the rates by duration on the frontend).
"""
import re
from typing import Any, Optional

_CCY_RE = re.compile(r"^[A-Z]{3}$")


def _ccy(v: Any) -> str:
    c = str(v or "").strip().upper()
    if not _CCY_RE.match(c):
        raise ValueError(f"Invalid currency code: {v!r}")
    return c


def _amount(v: Any, label: str) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if not (0 <= n <= 1_000_000):
        raise ValueError(f"{label} must be between 0 and 1,000,000")
    return round(n, 2)


def validate_currency_rates(primary_currency: Optional[str], rates: Optional[list[dict]]) -> list[dict]:
    """Additional-currency hourly rates. Each must differ from the primary and from each other; > 0."""
    primary = _ccy(primary_currency) if primary_currency else None
    out: list[dict] = []
    seen: set[str] = set()
    for r in rates or []:
        c = _ccy(r.get("currency"))
        if c == primary:
            raise ValueError(f"{c} is already your primary currency")
        if c in seen:
            raise ValueError(f"Duplicate currency: {c}")
        rate = _amount(r.get("hourly_rate"), f"{c} rate")
        if rate <= 0:
            raise ValueError(f"Enter a rate for {c}")
        seen.add(c)
        out.append({"currency": c, "hourly_rate": rate})
    return out


def validate_currency_prices(primary_currency: Optional[str], prices: Optional[list[dict]]) -> list[dict]:
    """Explicit per-currency service prices. Skips the primary (charged from set_price) + dups; keeps
    a positive base_price and an offer_price only when it's a real discount (0 < offer < base)."""
    primary = _ccy(primary_currency) if primary_currency else None
    out: list[dict] = []
    seen: set[str] = set()
    for p in prices or []:
        c = _ccy(p.get("currency"))
        if c == primary or c in seen:
            continue
        base = _amount(p.get("base_price"), f"{c} price")
        if base <= 0:
            continue
        row: dict[str, Any] = {"currency": c, "base_price": base}
        if p.get("offer_price") not in (None, ""):
            offer = _amount(p.get("offer_price"), f"{c} offer")
            if 0 < offer < base:
                row["offer_price"] = offer
        seen.add(c)
        out.append(row)
    return out
