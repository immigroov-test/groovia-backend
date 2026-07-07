import logging
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.pricing")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


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
