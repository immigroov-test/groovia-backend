import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.pricing")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)

# Currencies we price in (must be Frankfurter/ECB-supported).
_FX_BASE_SYMBOLS = [
    "USD", "GBP", "INR", "CAD", "AUD", "NZD", "SGD", "HKD", "JPY", "KRW", "CNY",
    "MXN", "BRL", "ZAR", "CHF", "SEK", "NOK", "DKK", "PLN", "RON", "CZK", "HUF",
    "BGN", "ILS", "IDR", "PHP", "MYR", "THB", "TRY",
]
_FX_BACKOFF = [1.0, 3.0, 10.0]  # seconds, 3 attempts escalating

# USD-pegged currencies ECB/Frankfurter omits. If neither live provider returns them we DERIVE the
# EUR-pivot from the live EUR->USD rate times the official peg (never a hardcoded EUR rate, which goes
# stale the moment EUR/USD moves - that class of stale value is exactly what caused the mispricing).
_USD_PEGGED = {"AED": 3.6725, "SAR": 3.75}


def get_booking_quote(service_id: str, customer_country: Optional[str]) -> dict[str, Any]:
    """Issue a binding 10-minute price quote (compute_booking_price + a
    pricing_quotes row). Raises on the RPC's own errors (e.g. Service not
    available, FX_UNAVAILABLE) — the PostgREST client surfaces these as
    exceptions with the raised message in them."""
    res = _supabase.rpc("get_booking_quote", {
        "p_service_id": service_id,
        "p_customer_country": customer_country,
    }).execute()
    return res.data or {}


def convert_prices(customer_country: Optional[str], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read-only display pricing (soft FX fallback, no quote row, no fee) —
    for browse/list pages that show a price before a specific service quote
    is requested."""
    res = _supabase.rpc("convert_prices", {
        "p_customer_country": customer_country,
        "p_items": items,
    }).execute()
    return res.data or []


def display_service_prices(customer_country: Optional[str], service_ids: list[str]) -> list[dict[str, Any]]:
    """Session-price-only display pricing that uses the SAME per-service engine as the binding quote
    (explicit currency + PPP), so the session price shown on cards/booking equals the checkout session
    line (BUG-077). Soft FX (falls back to the mentor currency). Returns [{key, you, you0,
    customer_currency, fx_ok}] keyed by service id."""
    res = _supabase.rpc("display_service_prices", {
        "p_customer_country": customer_country,
        "p_service_ids": service_ids,
    }).execute()
    return res.data or []


def pricing_preview(amount: float, currency: str, smart_pricing: bool) -> list[dict[str, Any]]:
    """BUG-62 mentor-facing preview: what a customer in each key market sees for a base amount in the
    mentor's currency (FX, plus PPP anchored to the pricing currency when smart pricing is on). Returns
    [{country_code, currency, price, fx_ok}], one per market except the base currency's own."""
    res = _supabase.rpc("pricing_preview", {
        "p_amount": amount,
        "p_currency": currency,
        "p_smart_pricing": smart_pricing,
    }).execute()
    return res.data or []


# ── FX refresh (fetches EUR-pivot rates from Frankfurter) ────────────────────
# compute_booking_price hard-fails once fx_rates is >24h stale (fx_max_age_minutes),
# so a scheduler (jobs/run_due.py) must call refresh_fx_rates periodically.

def _erapi_rates() -> tuple[dict[str, float], Optional[str]]:
    """open.er-api.com: free, no key, daily, and covers EVERY currency we price (incl. the AED/SAR/
    LKR/PKR that ECB omits). Returns (EUR-pivot rate map, as_of)."""
    resp = httpx.get("https://open.er-api.com/v6/latest/EUR", timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "success" or not isinstance(data.get("rates"), dict):
        raise ValueError(f"open.er-api: {data.get('error-type', 'no rates in payload')}")
    return ({str(k).upper(): float(v) for k, v in data["rates"].items()},
            data.get("time_last_update_utc"))


def _frankfurter_rates(symbols: list[str]) -> tuple[dict[str, float], Optional[str]]:
    """ECB via Frankfurter. Authoritative but MAJORS ONLY, so it's our fallback / gap-filler."""
    resp = httpx.get("https://api.frankfurter.dev/v2/rates",
                     params={"base": "EUR", "quotes": ",".join(symbols)}, timeout=15.0)
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, list):
        return ({str(r["quote"]).upper(): float(r["rate"]) for r in payload},
                payload[0]["date"] if payload else None)
    if isinstance(payload, dict) and isinstance(payload.get("rates"), dict):
        return ({str(q).upper(): float(r) for q, r in payload["rates"].items()}, payload.get("date"))
    raise ValueError("Frankfurter: unexpected payload")


def _fetch_fx_rates_with_retry(symbols: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Latest EUR-pivot rates for `symbols`, newest source wins. open.er-api.com is primary (covers every
    currency we price); Frankfurter (ECB) fills anything still missing; USD-pegged currencies are derived
    from the live EUR->USD if both providers omit them. NO hardcoded rates are ever returned - on total
    provider failure this raises and the caller keeps the previously stored (last-day) rates. Returns
    ([{base,quote,rate,as_of}], provider_label)."""
    rates: dict[str, float] = {}
    as_of: Optional[str] = None
    provider = ""
    errs: list[str] = []
    for attempt in range(len(_FX_BACKOFF)):          # primary, comprehensive
        try:
            rates, as_of = _erapi_rates()
            provider = "open.er-api.com"
            break
        except Exception as e:  # noqa: BLE001
            errs.append(f"erapi:{e}")
            if attempt < len(_FX_BACKOFF) - 1:
                time.sleep(_FX_BACKOFF[attempt])
    missing = [s for s in symbols if s.upper() not in rates and s.upper() != "EUR"]
    if missing:                                       # fallback / gap-fill with ECB majors
        try:
            fr, fr_as_of = _frankfurter_rates(missing)
            for k, v in fr.items():
                rates.setdefault(k, v)
            as_of = as_of or fr_as_of
            provider = provider or "frankfurter"
        except Exception as e:  # noqa: BLE001
            errs.append(f"frankfurter:{e}")
    usd = rates.get("USD")                            # derive USD-pegged from the live peg if still gone
    if usd:
        for ccy, peg in _USD_PEGGED.items():
            rates.setdefault(ccy, peg * usd)
    rows = [{"base": "EUR", "quote": q.upper(), "rate": rates[q.upper()], "as_of": as_of}
            for q in symbols if q.upper() in rates and q.upper() != "EUR"]
    if not rows:
        raise RuntimeError("all FX providers failed: " + "; ".join(errs))
    if errs:
        logger.warning("refresh_fx_rates: provider issues (%s); used %s", "; ".join(errs), provider)
    return rows, provider


def fx_rates_are_stale(max_age: timedelta) -> bool:
    """Used by the dispatcher to self-gate refresh_fx_rates — no rows at all
    counts as stale (first-ever run)."""
    res = _supabase.table("fx_rates").select("fetched_at").order("fetched_at", desc=True).limit(1).execute()
    if not res.data:
        return True
    fetched_at = datetime.fromisoformat(res.data[0]["fetched_at"])
    return datetime.now(timezone.utc) - fetched_at >= max_age


def _fx_already_refreshed_today() -> bool:
    """True if fx_rates was already refreshed on the current UTC calendar day. Underpins the
    per-day price freeze (BUG-087): once today's rate is stored, it is not re-fetched until the
    next UTC day, so a mentor's displayed/charged price cannot drift within a day."""
    try:
        res = (_supabase.table("fx_rates").select("fetched_at")
               .order("fetched_at", desc=True).limit(1).execute())
        if not res.data:
            return False
        fetched = datetime.fromisoformat(res.data[0]["fetched_at"])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return fetched.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date()
    except Exception:
        logger.warning("refresh_fx_rates: daily-freeze check failed; will refresh", exc_info=True)
        return False


def refresh_fx_rates(force: bool = False) -> dict[str, Any]:
    """Fetch fresh EUR-pivot rates and upsert into fx_rates; log every run to
    fx_refresh_log. Symbols = the static supported set union every distinct
    active service's set_currency, plus INR.

    FROZEN PER UTC DAY (BUG-087): unless force=True, this is a no-op when rates were already
    fetched today, so every page and the checkout ride the SAME rate all day; it only moves at the
    UTC-day rollover. ECB (the free Frankfurter feed) only publishes ~once a business day anyway."""
    if not force and _fx_already_refreshed_today():
        return {"ok": True, "skipped": "already refreshed today (frozen per day)"}
    symbols = set(_FX_BASE_SYMBOLS)
    symbols.add("INR")
    try:
        res = _supabase.table("services").select("set_currency, currency_prices").eq("is_active", True).execute()
        for row in res.data or []:
            ccy = (row.get("set_currency") or "").upper()
            if ccy and ccy != "EUR":
                symbols.add(ccy)
            for p in (row.get("currency_prices") or []):          # explicit per-currency prices
                c = (p.get("currency") or "").upper()
                if c and c != "EUR":
                    symbols.add(c)
    except Exception:
        logger.exception("refresh_fx_rates: failed to load active service currencies, using static set only")

    try:
        rows, provider = _fetch_fx_rates_with_retry(sorted(symbols))
        now = datetime.now(timezone.utc).isoformat()
        upsert_rows = [{**r, "fetched_at": now} for r in rows]
        as_of = rows[0]["as_of"] if rows else None

        _supabase.table("fx_rates").upsert(upsert_rows, on_conflict="base,quote").execute()
        _supabase.table("fx_refresh_log").insert({
            "provider": provider, "as_of": as_of, "raw_json": rows, "success": True, "error": None,
        }).execute()
        return {"ok": True, "as_of": as_of, "count": len(upsert_rows), "provider": provider}
    except Exception as e:
        logger.exception("refresh_fx_rates failed")
        _supabase.table("fx_refresh_log").insert({
            "provider": "none", "as_of": None, "raw_json": None, "success": False, "error": str(e),
        }).execute()
        raise
