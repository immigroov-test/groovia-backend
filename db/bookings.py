import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def log_webhook_event(
    *,
    provider: str,
    event_type: str,
    external_id: Optional[str],
    signature_ok: bool,
    payload: dict,
) -> Optional[str]:
    """Append the raw webhook to the intake log BEFORE processing.
    Returns the event row id, or None if even logging failed."""
    try:
        res = _supabase.table("webhook_events").insert({
            "provider": provider,
            "event_type": event_type,
            "external_id": external_id,
            "signature_ok": signature_ok,
            "payload": payload,
        }).execute()
        return res.data[0]["id"] if res.data else None
    except Exception:
        logger.exception("Failed to log webhook event (%s %s)", provider, event_type)
        return None


def mark_webhook_processed(event_id: Optional[str], error: Optional[str] = None) -> None:
    if not event_id:
        return
    try:
        _supabase.table("webhook_events").update({
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        }).eq("id", event_id).execute()
    except Exception:
        logger.exception("Failed to mark webhook event %s processed", event_id)


def update_booking_status(external_id: str, status: str) -> bool:
    """Status-only update for lifecycle-tail events (meeting ended, no-show).
    Returns False when no booking row exists for this uid yet."""
    res = (
        _supabase.table("bookings")
        .update({"status": status})
        .eq("external_id", external_id)
        .execute()
    )
    return bool(res.data)


def upsert_booking(fields: dict[str, Any], *, insert_only: bool = False) -> None:
    """Idempotent write keyed on external_id (the Cal booking uid).
    insert_only=True (BOOKING_CREATED) never overwrites an existing row - guards
    against out-of-order webhooks downgrading a cancelled/completed booking back
    to confirmed. Raises on failure so the caller records it in webhook_events."""
    _supabase.table("bookings").upsert(
        fields,
        on_conflict="external_id",
        ignore_duplicates=insert_only,
    ).execute()


def recent_rejected_webhooks(since: datetime) -> list[dict]:
    """Webhook deliveries rejected for a bad signature since `since`, newest first.

    Feeds the dispatcher's alert. A rejection means the payment likely succeeded at the provider while
    we never recorded it, so the booking gets cancelled as an abandoned hold."""
    try:
        res = (_supabase.table("webhook_events")
               .select("id, event_type, error, received_at")
               .eq("signature_ok", False)
               .gte("received_at", since.isoformat())
               .order("received_at", desc=True).limit(200).execute())
        return res.data or []
    except Exception:
        logger.exception("recent_rejected_webhooks failed")
        return []
