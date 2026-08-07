import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

import config
import db

logger = logging.getLogger("immigroov.routers.pricing")

router = APIRouter(prefix="/pricing", tags=["pricing"])


def resolve_pricing_country(request: Request, fallback: Optional[str]) -> Optional[str]:
    """The country used for PPP pricing, from the single trusted source.

    The Vercel BFF geolocates the visitor at the edge (a value the browser can't
    forge) and forwards it as X-Geo-Country, signed with the shared
    INTERNAL_GEO_TOKEN. This is called for BOTH the display price (/convert) and
    the binding price (/quote), so what the user sees always equals what they pay.

    - Token configured: trust ONLY a validly-signed edge country. A direct/spoofed
      call with a fake ?country= gets None (no PPP discount) - fail closed.
    - Token empty (not set up yet): fall back to the client-supplied country, i.e.
      the pre-enforcement behaviour, so nothing breaks before the env vars are set.
    """
    token = config.INTERNAL_GEO_TOKEN
    if not token:
        return fallback
    cc = (request.headers.get("x-geo-country") or "").strip().upper()
    sig = request.headers.get("x-internal-geo-token") or ""
    if sig == token and len(cc) == 2 and cc.isalpha():
        return cc
    return None

# Per-quote FX freshness guard. The dispatcher refreshes rates every ~6h, but the
# price a user commits to should ride on a recent rate, so a quote whose newest
# rate is older than this triggers a synchronous refresh first. ECB (the free
# Frankfurter feed) only publishes ~once a business day, so anything tighter buys
# no extra accuracy — this just caps how stale the committed rate can be.
FX_QUOTE_MAX_AGE = timedelta(minutes=60)


# ── Binding quote ──────────────────────────────────────────────────────────────

# Fields that go back to the customer's browser. Markup model: the customer sees the full breakdown -
# session price (mentor_amount / subtotal) + platform fee (platform_fee / platform_fee_pct) + tax +
# total. The INTERNAL mentor commission (fee_amount, fee_pct, commission_amount, commission_pct,
# markup_pct) and the mentor's take (net_mentor / net_customer) are ADMIN-ONLY and stay redacted,
# along with the mentor's raw rate (set_price) and fx internals.
_PUBLIC_QUOTE_FIELDS = {
    "quote_id", "expires_at", "gross_customer", "customer_currency", "pricing_source",
    "service_id", "customer_country", "pricing_hash",
    "mentor_amount", "subtotal", "platform_fee", "platform_fee_pct", "tax_amount", "tax_pct",
}


def _public_quote(snap: dict) -> dict:
    return {k: v for k, v in (snap or {}).items() if k in _PUBLIC_QUOTE_FIELDS}


@router.get("/quote/{service_id}")
def get_quote(request: Request, service_id: str, country: Optional[str] = Query(None, min_length=2, max_length=2)):
    """Issue a binding 10-minute price quote for a service + customer country.
    Public — no auth required. The returned quote_id is a one-time-use token
    consumed by /payments/reserve."""
    # The binding country comes from the trusted edge geo (not the client's ?country=),
    # so the amount charged can't be spoofed to unlock a PPP discount.
    country = resolve_pricing_country(request, country)
    # Refresh FX before pricing if the cached rate is stale, so the amount the user
    # locks in reflects a current rate. Refresh only when stale (never block on a
    # healthy cache); if the refresh itself fails, fall through — get_booking_quote
    # still raises FX_UNAVAILABLE below if the existing rates are too old to use.
    try:
        if db.fx_rates_are_stale(FX_QUOTE_MAX_AGE):
            db.refresh_fx_rates()
    except Exception:
        logger.warning("get_quote: FX refresh-if-stale failed; pricing on existing rates", exc_info=True)
    try:
        return _public_quote(db.get_booking_quote(service_id, country))
    except Exception as e:
        msg = str(e)
        if "FX_UNAVAILABLE" in msg:
            raise HTTPException(status_code=503, detail="Pricing is temporarily unavailable — please try again shortly")
        if "not available" in msg.lower():
            raise HTTPException(status_code=404, detail="Service not available")
        logger.exception("get_quote failed service=%s country=%s", service_id, country)
        raise HTTPException(status_code=500, detail="Failed to compute price")


# ── Display pricing (browse/list pages) ────────────────────────────────────────

class ConvertItem(BaseModel):
    key: str
    amount: float
    from_: str = Field("USD", alias="from")  # "from" is a Python keyword
    is_ppp: bool = False
    mentor_country: Optional[str] = None     # for relative PPP (customer factor / mentor factor)
    mentor_id: Optional[str] = None          # for the per-mentor commission override

    model_config = {"populate_by_name": True}

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        if v < 0:
            raise ValueError("amount must be >= 0")
        return v


class ConvertPricesBody(BaseModel):
    country: Optional[str] = None
    items: list[ConvertItem]


@router.post("/convert")
def convert_prices(body: ConvertPricesBody, request: Request):
    """Soft, no-fee display conversion for browse/list pages — never fails on
    stale/missing FX (falls back to the mentor-currency price with fx_ok=false).
    The binding /pricing/quote/{service_id} endpoint is what enforces fresh FX."""
    # Same trusted edge country as the binding quote, so the DISPLAYED price and the
    # CHARGED price always agree (no showing an India price then charging US, or v.v.).
    country = resolve_pricing_country(request, body.country)
    items = [
        {"key": i.key, "amount": i.amount, "from": i.from_, "is_ppp": i.is_ppp,
         "mentor_country": i.mentor_country, "mentor_id": i.mentor_id}
        for i in body.items
    ]
    try:
        return {"prices": db.convert_prices(country, items)}
    except Exception:
        logger.exception("convert_prices failed country=%s", country)
        raise HTTPException(status_code=500, detail="Failed to convert prices")
