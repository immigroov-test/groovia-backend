import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
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
    referral_code: Optional[str] = None,
) -> dict[str, Any]:
    """Consume a binding price quote into a 'pending' (payment-hold) booking.
    Raises on QUOTE_EXPIRED / slot-taken — the RPC's own error message is
    preserved so the router can map it to the right HTTP status.
    referral_code (optional) is validated server-side; a valid code applies its
    discount to what the customer pays and records the attribution on the booking."""
    res = _supabase.rpc("reserve_booking", {
        "p_quote_id":                 quote_id,
        "p_mentor_id":                mentor_id,
        "p_service_id":               service_id,
        "p_slot_time":                slot_time,
        "p_email":                    email,
        "p_name":                     name,
        "p_timezone":                 timezone,
        "p_answers":                  answers,
        "p_specific_availability_id": specific_availability_id,
        "p_candidate_id":             candidate_id,
        "p_referral_code":            referral_code,
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


def expire_stale_holds() -> int:
    """Release payment holds whose 10-min window lapsed (bookings 'pending' ->
    'cancelled', customer_payments 'created' -> 'failed'). Service-role only.
    Returns rows affected."""
    res = _supabase.rpc("expire_stale_holds", {}).execute()
    return res.data or 0


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


def payments_enabled() -> bool:
    """Business-level toggle (platform_settings.payments_enabled) deciding
    mock-instant-confirm vs real Razorpay checkout. Distinct from the
    MOCK_SERVICES env flag, which is a broader local-dev/test switch."""
    res = _supabase.rpc("public_setting", {"p_key": "payments_enabled"}).execute()
    return str(res.data).strip().lower() == "true"


def abandon_hold(booking_id: str) -> None:
    """Release a payment-hold booking whose order could not be created, so the slot
    frees immediately instead of staying blocked until expire_stale_holds runs (which
    needs the dispatcher cron). Only touches a still-'pending' hold and its unpaid
    'created' payment, so it can't disturb an already-confirmed booking."""
    _supabase.table("bookings").update(
        {"status": "cancelled", "payment_hold_expires_at": None}
    ).eq("id", booking_id).eq("status", "pending").execute()
    _supabase.table("customer_payments").update(
        {"state": "failed"}
    ).eq("booking_id", booking_id).eq("state", "created").execute()


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


def get_own_pending_hold(candidate_id: Optional[str], mentor_id: str, slot_time: str,
                         candidate_email: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The caller's own un-expired 'pending' payment hold on this exact slot, if any.
    Lets a retry after a cancelled/closed Razorpay popup REUSE the same hold instead of
    hitting 'that time is not available' on their own reservation. Refreshes the 10-min
    window, and only reuses a hold whose payment is still 'created' (not failed/captured).
    A signed-in caller is matched by candidate_id; a guest (candidate_id NULL) by the email
    they booked with, so guest retries work too."""
    now = datetime.now(timezone.utc)
    try:
        q = (
            _supabase.table("bookings")
            .select("id")
            .eq("mentor_id", mentor_id)
            .eq("slot_time", slot_time)
            .eq("status", "pending")
            .gt("payment_hold_expires_at", now.isoformat())
        )
        if candidate_id:
            q = q.eq("candidate_id", candidate_id)
        elif candidate_email:
            q = q.is_("candidate_id", "null").eq("candidate_email", candidate_email.strip().lower())
        else:
            return None
        res = q.order("created_at", desc=True).limit(1).execute()
        if not res.data:
            return None
        booking_id = res.data[0]["id"]
        pay = get_payment_by_booking(booking_id)
        if not pay or pay.get("state") != "created":
            return None
        new_exp = (now + timedelta(minutes=10)).isoformat()
        _supabase.table("bookings").update({"payment_hold_expires_at": new_exp}).eq("id", booking_id).execute()
        return {"booking_id": booking_id, "amount": pay["amount"], "currency": pay["currency"], "hold_expires_at": new_exp}
    except Exception:
        logger.exception("get_own_pending_hold failed candidate=%s mentor=%s", candidate_id, mentor_id)
        return None


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
            # Razorpay caps `receipt` at 40 chars; "booking-" + a 36-char UUID is 44 and
            # is rejected with a 400. The bare booking id (36 chars) is unique and fits.
            "receipt": booking_id[:40],
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


def fetch_razorpay_order_payments(order_id: str) -> list[dict[str, Any]]:
    resp = httpx.get(f"{_RAZORPAY_BASE}/orders/{order_id}/payments", auth=_auth(), timeout=15.0)
    resp.raise_for_status()
    return resp.json().get("items", [])


# ── Webhook-independent verification ───────────────────────────────────────────
# The webhook can be delayed or dropped (closed tab, missed delivery, especially
# in test mode). finalize_captured_payment is the ONE place that decides what to
# do with a captured Razorpay payment — shared by the webhook handler and
# verify_order/sweep_verify_payments, so the race-sensitive HOLD_EXPIRED branch
# is implemented exactly once.
#
# Concurrency: confirm_booking_payment takes a row lock and is idempotent
# (returns 'already_confirmed' once confirmed) — safe to call twice concurrently.
# The HOLD_EXPIRED branch is safe because issue_razorpay_refund's idempotency key
# is deterministic (booking_id + amount_minor), so two concurrent callers both
# send Razorpay the identical key and get back the SAME refund — no double refund.
def finalize_captured_payment(local_payment: dict[str, Any], remote_payment: dict[str, Any]) -> str:
    """local_payment: a customer_payments row (needs 'id', 'booking_id').
    remote_payment: a Razorpay payment object (needs 'id', 'amount') already
    fetched from Razorpay directly — never trust a webhook body verbatim.
    Returns 'confirmed' | 'already_confirmed' | 'refunded'."""
    razorpay_payment_id = remote_payment["id"]
    try:
        result = confirm_booking_payment(local_payment["booking_id"], razorpay_payment_id)
    except Exception as e:
        if "HOLD_EXPIRED" not in str(e):
            raise
        # The 10-minute hold lapsed before the payment landed — the money arrived
        # for a slot we can no longer honour. Issue a full refund. Money REALLY
        # was captured by Razorpay before we refunded it, so the state machine
        # must visit 'captured' first — 'created' -> 'refunded' is not a legal
        # transition (set_payment_state's CHECK).
        issue_razorpay_refund(
            payment_id=razorpay_payment_id,
            amount_minor=remote_payment.get("amount", 0),
            booking_id=local_payment["booking_id"],
        )
        set_payment_state(local_payment["id"], "captured")
        set_payment_state(local_payment["id"], "refunded")
        return "refunded"
    record_provider_payload(local_payment["id"], remote_payment)
    return result


def verify_order(order_id: str) -> dict[str, Any]:
    """Single-order, webhook-independent verify — the frontend calls this right
    after Checkout succeeds, in case the webhook hasn't landed yet. Idempotent:
    a payment already resolved (captured/refunded/partially_refunded) short-
    circuits without calling Razorpay again."""
    local = get_payment_by_provider_order(order_id)
    if not local:
        return {"order_id": order_id, "error": "unknown order"}
    if local.get("state") in ("captured", "refunded", "partially_refunded"):
        return {"order_id": order_id, "confirmed": local["state"] == "captured", "status": "already"}

    items = fetch_razorpay_order_payments(order_id)
    captured = next((p for p in items if p.get("status") == "captured"), None)
    if not captured:
        return {"order_id": order_id, "confirmed": False, "status": items[0]["status"] if items else "none"}

    result = finalize_captured_payment(local, captured)
    return {"order_id": order_id, "confirmed": result in ("confirmed", "already_confirmed"), "status": result}


def process_refunds(limit: int = 200) -> dict[str, Any]:
    """Finds captured/partially-refunded payments that still owe a refund (per
    refund_owed_minor) and issues Razorpay refunds against the original payment.
    Supports multiple partial refunds per payment (ledger_version increments per
    call). Upsert on provider_refund_id makes a losing concurrent run a no-op."""
    rows = (
        _supabase.table("customer_payments")
        .select("id, booking_id, provider_payment_id, currency")
        .not_.is_("provider_payment_id", "null")
        .in_("state", ["captured", "partially_refunded"])
        .limit(limit)
        .execute()
    )

    issued = 0
    failed = 0
    for p in rows.data or []:
        owed_minor = refund_owed_minor(p["booking_id"])
        if owed_minor <= 0:
            continue

        count_res = (
            _supabase.table("payment_refunds")
            .select("id", count="exact")
            .eq("booking_id", p["booking_id"])
            .execute()
        )
        version = count_res.count or 0

        try:
            refund = issue_razorpay_refund(
                payment_id=p["provider_payment_id"], amount_minor=owed_minor, booking_id=p["booking_id"],
            )
            status = "processed" if refund.get("status") == "processed" else "created"
            (
                _supabase.table("payment_refunds")
                .upsert(
                    {
                        "payment_id": p["id"], "booking_id": p["booking_id"], "amount_minor": owed_minor,
                        "currency": p["currency"], "status": status, "provider_refund_id": refund.get("id"),
                        "ledger_version": version,
                    },
                    on_conflict="provider_refund_id", ignore_duplicates=True,
                )
                .execute()
            )
            issued += 1
        except Exception as e:
            logger.exception("process_refunds: refund failed booking=%s", p["booking_id"])
            _supabase.table("payment_refunds").insert({
                "payment_id": p["id"], "booking_id": p["booking_id"], "amount_minor": owed_minor,
                "currency": p["currency"], "status": "failed", "ledger_version": version,
                "provider_error_description": str(e),
            }).execute()
            failed += 1

    return {"ok": True, "issued": issued, "failed": failed}


def reconcile_payments(window_hours: int = 36, limit: int = 500) -> dict[str, Any]:
    """Cross-checks local customer_payments against Razorpay's record and logs
    any mismatch (amount/currency/status/fetch failure) to
    payment_reconciliation_log for ops review. Read-only against
    customer_payments/bookings — never mutates payment state."""
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    rows = (
        _supabase.table("customer_payments")
        .select("id, booking_id, amount, currency, state, provider_payment_id")
        .not_.is_("provider_payment_id", "null")
        .gte("created_at", since)
        .limit(limit)
        .execute()
    )

    mismatches = 0
    for p in rows.data or []:
        try:
            remote = fetch_razorpay_payment(p["provider_payment_id"])
        except Exception as e:
            mismatches += 1
            _supabase.table("payment_reconciliation_log").insert({
                "kind": "fetch_failed", "provider_payment_id": p["provider_payment_id"],
                "booking_id": p["booking_id"], "detail": {"error": str(e)},
            }).execute()
            continue

        local_minor = to_minor(float(p["amount"]), p["currency"])
        problems: dict[str, Any] = {}
        if remote.get("amount") != local_minor:
            problems["amount"] = {"local": local_minor, "provider": remote.get("amount")}
        if str(remote.get("currency", "")).upper() != str(p["currency"]).upper():
            problems["currency"] = {"local": p["currency"], "provider": remote.get("currency")}
        if p["state"] == "captured" and remote.get("status") not in ("captured", "refunded"):
            problems["status"] = {"local": p["state"], "provider": remote.get("status")}

        if problems:
            mismatches += 1
            _supabase.table("payment_reconciliation_log").insert({
                "kind": "mismatch", "provider_payment_id": p["provider_payment_id"],
                "booking_id": p["booking_id"], "detail": problems,
            }).execute()

    return {"ok": True, "checked": len(rows.data or []), "mismatches": mismatches}


def sweep_verify_payments(window_minutes: int = 60, limit: int = 100) -> dict[str, Any]:
    """Cron backstop for missed/delayed webhooks — verifies every recent still-
    'created' payment that has an order id. Each order is verified independently
    so one failure doesn't stop the rest of the sweep."""
    since = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
    rows = (
        _supabase.table("customer_payments")
        .select("provider_order_id")
        .eq("state", "created")
        .not_.is_("provider_order_id", "null")
        .gte("created_at", since)
        .limit(limit)
        .execute()
    )
    results = []
    for r in rows.data or []:
        order_id = r["provider_order_id"]
        try:
            results.append(verify_order(order_id))
        except Exception as e:
            logger.exception("sweep_verify_payments failed order=%s", order_id)
            results.append({"order_id": order_id, "error": str(e)})
    confirmed = sum(1 for x in results if x.get("confirmed"))
    return {"ok": True, "swept": len(results), "confirmed": confirmed, "results": results}


# ── Admin financials (read) ────────────────────────────────────────────────────
# Both return None (not []) when the payment tables don't exist yet, i.e. the
# payments_setup.sql migration hasn't been applied. The admin router maps None to
# a "not configured" flag so the panel shows a setup hint instead of an error.

def admin_list_payments(limit: int = 200) -> Optional[list[dict[str, Any]]]:
    """Recent customer payments with booking + mentor context, newest first."""
    try:
        res = (
            _supabase.table("customer_payments")
            .select(
                "id, booking_id, amount, currency, state, provider_payment_id, created_at, "
                "bookings(candidate_name, candidate_email, status, slot_time, mentors(display_name))"
            )
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception:
        logger.warning("admin_list_payments unavailable (payments migration not applied?)")
        return None


def admin_list_payouts(state: Optional[str] = None, limit: int = 200) -> Optional[list[dict[str, Any]]]:
    """Mentor payouts with mentor + booking context, newest first. Optional
    payout_state filter (pending / paid / void / blocked)."""
    try:
        q = (
            _supabase.table("mentor_payouts")
            .select(
                "id, booking_id, mentor_id, amount, net_amount_mentor_currency, mentor_currency, "
                "gross_amount, customer_currency, payout_state, method, payout_reference, paid_date, created_at, "
                "mentors(display_name), bookings(status, slot_time, candidate_name, candidate_email)"
            )
            .order("created_at", desc=True)
            .limit(limit)
        )
        if state:
            q = q.eq("payout_state", state)
        return q.execute().data or []
    except Exception:
        logger.warning("admin_list_payouts unavailable (payments migration not applied?)")
        return None


# ── Payout admin ops (manual payouts) ──────────────────────────────────────────

def mark_payout_paid(booking_id: str, reference: str) -> None:
    _supabase.rpc("mark_payout_paid", {"p_booking_id": booking_id, "p_reference": reference}).execute()


def set_payout_blocked(booking_id: str, reason: Optional[str] = None) -> None:
    _supabase.rpc("set_payout_blocked", {"p_booking_id": booking_id, "p_reason": reason}).execute()


def verify_webhook_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """HMAC-SHA256(raw body, RAZORPAY_WEBHOOK_SECRET), timing-safe compare."""
    if not signature or not config.RAZORPAY_WEBHOOK_SECRET:
        return False
    expected = hmac.new(
        config.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
