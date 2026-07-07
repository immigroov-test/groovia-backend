import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

import db

logger = logging.getLogger("immigroov.routers.pricing")

router = APIRouter(prefix="/pricing", tags=["pricing"])


# ── Binding quote ──────────────────────────────────────────────────────────────

@router.get("/quote/{service_id}")
def get_quote(service_id: str, country: Optional[str] = Query(None, min_length=2, max_length=2)):
    """Issue a binding 10-minute price quote for a service + customer country.
    Public — no auth required, mirrors immigroov's anon-grantable get_booking_quote.
    The returned quote_id is a one-time-use token consumed by booking creation
    (not wired up yet — that's Payments-phase work)."""
    try:
        return db.get_booking_quote(service_id, country)
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
def convert_prices(body: ConvertPricesBody):
    """Soft, no-fee display conversion for browse/list pages — never fails on
    stale/missing FX (falls back to the mentor-currency price with fx_ok=false).
    The binding /pricing/quote/{service_id} endpoint is what enforces fresh FX."""
    items = [
        {"key": i.key, "amount": i.amount, "from": i.from_, "is_ppp": i.is_ppp}
        for i in body.items
    ]
    try:
        return {"prices": db.convert_prices(body.country, items)}
    except Exception:
        logger.exception("convert_prices failed country=%s", body.country)
        raise HTTPException(status_code=500, detail="Failed to convert prices")
