import hashlib
import hmac
import logging
from typing import Any, Optional

import httpx
from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.payments")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)

_RAZORPAY_BASE = "https://api.razorpay.com/v1"
_ZERO_DECIMAL = {"JPY", "KRW", "VND"}


def to_minor(amount_major: float, currency: str) -> int:
    """Convert a decimal amount to Razorpay's minor-unit representation
    (paise/cents). A handful of currencies have no minor unit at all."""
    if currency.upper() in _ZERO_DECIMAL:
        return round(amount_major)
    return round(amount_major * 100)


def from_minor(amount_minor: int, currency: str) -> float:
    if currency.upper() in _ZERO_DECIMAL:
        return float(amount_minor)
    return amount_minor / 100


# ── RPC wrappers ─────────────────────────────────────────────────────────────

def reserve_booking(
    *,
    quote_id: str,
    mentor_id: str,
    service_id: str,
    slot_time: str,
    email: str,
    name: Optional[str] = None,
    timezone: str = "UTC",
    answers: list[dict] = [],
    specific_availability_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
) -> dict[str, Any]:
    """Consume a binding price quote into a 'pending' (payment-hold) booking.
    Raises on QUOTE_EXPIRED / slot-taken — the RPC's own error message is
    preserved so the router can map it to the right HTTP status."""
    res = _supabase.rpc("reserve_booking", {
        "p_quote_id":                quote_id,
        "p_mentor_id":               mentor_id,
        "p_service_id":              service_id,
        "p_slot_time":               slot_time,
        "p_email":                   email,
        "p_name":                    name,
        "p_timezone":                timezone,
        "p_answers":                 answers,
        "p_specific_availability_id": specific_availability_id,
        "p_candidate_id":            candidate_id,
    }).execute()
    if not res.data:
        raise RuntimeError("reserve_booking returned no data")
    return res.data[0]


def confirm_booking_payment(booking_id: str, provider_ref: str) -> str:
    """Finalize a payment hold into a confirmed booking. Returns 'confirmed'
    or 'already_confirmed'. Raises HOLD_EXPIRED if the 10-minute window passed."""
    res = _supabase.rpc("confirm_booking_payment", {
        "p_booking_id":   booking_id,
        "p_provider_ref": provider_ref,
    }).execute()
    return res.data


def set_provider_order(booking_id: str, order_id: str) -> None:
    _supabase.rpc("set_provider_order", {"p_booking_id": booking_id, "p_order_id": order_id}).execute()


def set_payment_state(payment_id: str, new_state: str) -> None:
    _supabase.rpc("set_payment_state", {"p_payment_id": payment_id, "p_new_state": new_state}).execute()


def refund_owed_minor(booking_id: str) -> int:
    res = _supabase.rpc("refund_owed_minor", {"p_booking_id": booking_id}).execute()
    return res.data or 0


def booking_status(booking_id: str) -> Optional[str]:
    res = _supabase.rpc("booking_status", {"p_booking_id": booking_id}).execute()
    return res.data


def get_payment_by_booking(booking_id: str) -> Optional[dict[str, Any]]:
    res = (
        _supabase.table("customer_payments")
        .select("id, booking_id, amount, currency, state, provider_order_id, provider_payment_id")
        .eq("booking_id", booking_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_payment_by_provider_order(provider_order_id: str) -> Optional[dict[str, Any]]:
    res = (
        _supabase.table("customer_payments")
        .select("id, booking_id, amount, currency, state, provider_order_id, provider_payment_id")
        .eq("provider_order_id", provider_order_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_payment_by_provider_payment_id(provider_payment_id: str) -> Optional[dict[str, Any]]:
    res = (
        _supabase.table("customer_payments")
        .select("id, booking_id, amount, currency, state")
        .eq("provider_payment_id", provider_payment_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def record_provider_payload(payment_id: str, payload: dict) -> None:
    _supabase.table("customer_payments").update({"provider_payload": payload}).eq("id", payment_id).execute()


def record_payment_error(payment_id: str, code: Optional[str], description: Optional[str]) -> None:
    _supabase.table("customer_payments").update({
        "provider_error_code": code, "provider_error_description": description,
    }).eq("id", payment_id).execute()


# ── Razorpay webhook idempotency (payment_events) ───────────────────────────

def payment_event_seen(event_id: str) -> bool:
    """True if this event_id has already been processed (dedup on replay)."""
    res = (
        _supabase.table("payment_events")
        .select("processed_at")
        .eq("event_id", event_id)
        .limit(1)
        .execute()
    )
    return bool(res.data and res.data[0].get("processed_at"))


def upsert_payment_event(event_id: str, event_type: str, payload: dict, signature: str) -> None:
    """First-writer-wins log of the raw webhook, BEFORE processing it."""
    _supabase.table("payment_events").upsert({
        "event_id": event_id, "type": event_type, "payload": payload, "signature": signature,
    }, on_conflict="event_id", ignore_duplicates=True).execute()


def mark_payment_event_processed(event_id: str, error: Optional[str] = None) -> None:
    from datetime import datetime, timezone as _tz
    _supabase.table("payment_events").update({
        "processed_at": None if error else datetime.now(_tz.utc).isoformat(),
        "error": error,
    }).eq("event_id", event_id).execute()


def record_refund(
    *, payment_id: str, booking_id: str, amount_minor: int, currency: str,
    status: str = "created", provider_refund_id: Optional[str] = None,
) -> None:
    _supabase.table("payment_refunds").insert({
        "payment_id": payment_id, "booking_id": booking_id, "amount_minor": amount_minor,
        "currency": currency, "status": status, "provider_refund_id": provider_refund_id,
    }).execute()


def update_refund_status(provider_refund_id: str, status: str) -> None:
    _supabase.table("payment_refunds").update({"status": status}).eq("provider_refund_id", provider_refund_id).execute()


# ── Razorpay HTTP client ─────────────────────────────────────────────────────

def _auth() -> tuple[str, str]:
    return (config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET)


def create_razorpay_order(*, booking_id: str, amount_major: float, currency: str) -> dict[str, Any]:
    """POST /orders. Idempotency-keyed on the booking id, so a retried request
    (e.g. after a dropped response) returns the same order instead of a second one."""
    amount_minor = to_minor(amount_major, currency)
    resp = httpx.post(
        f"{_RAZORPAY_BASE}/orders",
        auth=_auth(),
        headers={"X-Razorpay-Idempotency-Key": f"booking-{booking_id}"},
        json={
            "amount": amount_minor,
            "currency": currency.upper(),
            "receipt": f"booking-{booking_id}",
            "notes": {"booking_id": booking_id},
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def issue_razorpay_refund(*, payment_id: str, amount_minor: int, booking_id: str) -> dict[str, Any]:
    """POST /payments/{id}/refund. payment_id here is the RAZORPAY payment id
    (provider_payment_id), not our customer_payments.id."""
    resp = httpx.post(
        f"{_RAZORPAY_BASE}/payments/{payment_id}/refund",
        auth=_auth(),
        headers={"X-Razorpay-Idempotency-Key": f"refund-{booking_id}-{amount_minor}"},
        json={"amount": amount_minor, "notes": {"booking_id": booking_id}},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_razorpay_payment(payment_id: str) -> dict[str, Any]:
    resp = httpx.get(f"{_RAZORPAY_BASE}/payments/{payment_id}", auth=_auth(), timeout=15.0)
    resp.raise_for_status()
    return resp.json()


def verify_webhook_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """HMAC-SHA256(raw body, RAZORPAY_WEBHOOK_SECRET), timing-safe compare.
    Matches immigroov's razorpay.ts verifyWebhook() exactly."""
    if not signature or not config.RAZORPAY_WEBHOOK_SECRET:
        return False
    expected = hmac.new(
        config.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
