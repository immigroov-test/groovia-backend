import logging
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
    # BUG-087: FX is frozen per UTC day. refresh_fx_rates() self-gates - it fetches once per day and
    # is a no-op the rest of the day - so the quote rides the SAME rate the browse/booking pages
    # already showed, and a price never moves within a day. It only fetches if today's rate is
    # missing; if that fetch fails, get_booking_quote still raises FX_UNAVAILABLE below.
    try:
        db.refresh_fx_rates()
    except Exception:
        logger.warning("get_quote: daily FX refresh failed; pricing on existing rates", exc_info=True)
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


class DisplayPricesBody(BaseModel):
    country: Optional[str] = None
    service_ids: list[str]


@router.post("/display")
def display_prices(body: DisplayPricesBody, request: Request):
    """Session-price-only display pricing for the cards + booking page. Uses the SAME per-service
    engine as the binding quote (explicit currency + PPP), so the session price shown equals the
    checkout session line and the only difference at checkout is the disclosed platform fee + tax
    (BUG-077). Public; soft FX (falls back to the mentor currency, never fails)."""
    # Same trusted edge country as the binding quote, so display and charge agree.
    country = resolve_pricing_country(request, body.country)
    ids = [s for s in (body.service_ids or []) if s][:200]
    if not ids:
        return {"prices": []}
    try:
        return {"prices": db.display_service_prices(country, ids)}
    except Exception:
        logger.exception("display_prices failed country=%s", country)
        raise HTTPException(status_code=500, detail="Failed to compute display prices")


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


class PricePreviewBody(BaseModel):
    amount: float
    currency: str = "INR"
    smart_pricing: bool = True


@router.post("/preview")
def price_preview(body: PricePreviewBody):
    """BUG-62: mentor-facing preview of what a customer in each key market (India, Australia, Singapore,
    Saudi, UAE, US) would see for a base rate. Pure FX + PPP math on the mentor's own inputs (no stored
    data), so it's public. PPP is anchored to the base currency and only applied when smart_pricing."""
    try:
        amt = round(max(0.0, float(body.amount or 0)), 2)
        return {"markets": db.pricing_preview(amt, (body.currency or "INR").strip().upper(), bool(body.smart_pricing))}
    except Exception:
        logger.exception("price_preview failed currency=%s", body.currency)
        raise HTTPException(status_code=500, detail="Failed to compute preview")
