import json
import logging
import re
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

import config
import db
from core.auth import AuthUser, get_current_user, get_current_user_optional

logger = logging.getLogger("immigroov.routers.payments")

router = APIRouter(prefix="/payments", tags=["payments"])


# ── Reserve (create a payment-hold booking from a quote) ───────────────────────

class BookingAnswerItem(BaseModel):
    question_id: str
    answer_text: str


class ReserveBody(BaseModel):
    quote_id: str
    mentor_id: str
    service_id: str
    slot_time: datetime
    email: str
    phone: str
    name: Optional[str] = None
    notes: Optional[str] = None
    timezone: str = "UTC"
    answers: list[BookingAnswerItem] = []
    specific_availability_id: Optional[str] = None
    referral_code: Optional[str] = None

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email address")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        v = (v or "").strip()
        if len(re.sub(r"\D", "", v)) < 7:
            raise ValueError("A valid phone number is required")
        return v


@router.post("/reserve")
def reserve(body: ReserveBody, user: Optional[AuthUser] = Depends(get_current_user_optional)):
    """Consume a binding price quote into a 10-minute payment-hold booking. Guest-allowed
    (flight-style checkout): a signed-in caller attaches candidate_id; a guest books with
    candidate_id NULL and their identity lives in candidate_email/name/phone, to be claimed
    when they later sign up with that email. The caller must follow up with
    /payments/razorpay/create-order (or, when payments are disabled, /payments/confirm-mock)
    before the hold expires."""
    answers_json = [a.model_dump() for a in body.answers]
    candidate_id = user.id if user else None
    # A mentor must never pay for their own session (same account or same email). They can still test
    # the flow as a normal guest with a DIFFERENT email.
    if db.is_self_booking(body.mentor_id, candidate_id, body.email):
        raise HTTPException(status_code=403,
                            detail="You can't book your own session. To test the booking flow, use a different email address.")
    try:
        result = db.reserve_booking(
            quote_id=body.quote_id,
            mentor_id=body.mentor_id,
            service_id=body.service_id,
            slot_time=body.slot_time.isoformat(),
            email=body.email,
            name=body.name,
            timezone=body.timezone,
            answers=answers_json,
            specific_availability_id=body.specific_availability_id,
            candidate_id=candidate_id,
            referral_code=body.referral_code,
        )
        try:
            db.set_booking_phone(result["booking_id"], body.phone)
            db.set_booking_notes(result["booking_id"], body.notes)   # BUG-113
            if candidate_id:
                db.set_profile_phone_if_empty(candidate_id, body.phone)
        except Exception:
            logger.warning("could not save phone/notes for reserved booking %s", result.get("booking_id"))
        return result
    except Exception as e:
        msg = str(e)
        if "QUOTE_EXPIRED" in msg:
            raise HTTPException(status_code=409, detail=msg)
        if "not available" in msg.lower() or "just taken" in msg.lower():
            # The slot may be blocked by the caller's OWN un-paid hold (they cancelled the
            # popup and are retrying). Reuse it instead of failing, so retry always works;
            # only a hold held by SOMEONE ELSE actually returns 409. Guests are matched by email.
            own = db.get_own_pending_hold(candidate_id, body.mentor_id, body.slot_time.isoformat(),
                                          candidate_email=body.email)
            if own:
                try:
                    db.set_booking_phone(own["booking_id"], body.phone)
                except Exception:
                    pass
                return own
            raise HTTPException(status_code=409, detail=msg)
        logger.exception("reserve failed mentor=%s service=%s", body.mentor_id, body.service_id)
        raise HTTPException(status_code=500, detail="Reservation failed — please try again")


# ── Status poll (webhook-independent checkout UX) ──────────────────────────────

@router.get("/status/{booking_id}")
def status(booking_id: str):
    """Cheap read-only status poll for the checkout page while it waits for the
    webhook to land."""
    result = db.booking_status(booking_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return {"status": result}


# ── Razorpay: create order ─────────────────────────────────────────────────────

class CreateOrderBody(BaseModel):
    booking_id: str


@router.post("/razorpay/create-order")
def create_order(body: CreateOrderBody):
    payment = db.get_payment_by_booking(body.booking_id)
    if not payment:
        raise HTTPException(status_code=404, detail="No payment record for this booking")
    if payment.get("state") != "created":
        raise HTTPException(status_code=409, detail=f"Payment is already {payment.get('state')}")
    try:
        order = db.create_razorpay_order(
            booking_id=body.booking_id, amount_major=payment["amount"], currency=payment["currency"],
        )
    except httpx.HTTPStatusError as e:
        # Surface Razorpay's actual reason (e.g. unsupported currency) instead of a bare 400.
        reason = ""
        try:
            reason = ((e.response.json().get("error") or {}).get("description") or "").strip()
        except Exception:
            reason = (getattr(e.response, "text", "") or "")[:200]
        status_code = getattr(e.response, "status_code", "?")
        logger.error("razorpay create-order failed booking=%s status=%s reason=%s currency=%s",
                     body.booking_id, status_code, reason, payment.get("currency"))
        # Free the held slot so the user can retry (or pick another time) right away.
        try:
            db.abandon_hold(body.booking_id)
        except Exception:
            logger.warning("could not release hold after failed order booking=%s", body.booking_id)
        raise HTTPException(status_code=502, detail=f"Could not create payment order: {reason or e}")
    db.set_provider_order(body.booking_id, order["id"])
    db.record_provider_payload(payment["id"], order)
    return {
        "order_id": order["id"],
        "key_id": config.RAZORPAY_KEY_ID,
        "amount": order["amount"],
        "currency": order["currency"],
        "booking_id": body.booking_id,
    }


# ── Razorpay: webhook ───────────────────────────────────────────────────────────

@router.post("/razorpay/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    """Verifies the HMAC-SHA256 signature, dedupes on event_id, then handles
    payment.captured/order.paid, payment.failed, refund.created/refund.processed.
    Always returns 200 once the signature checks out (even on a handler error) so
    Razorpay doesn't hammer retries on our own bugs — the failure is logged to
    payment_events.error for follow-up instead."""
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    sig_ok = config.MOCK_SERVICES or db.verify_webhook_signature(raw_body, signature)

    # Log EVERY delivery, verified or not, BEFORE deciding what to do with it. A rejected webhook used
    # to be discarded with no record in any table, which made "Razorpay never called us" and "we
    # rejected everything Razorpay sent" indistinguishable from the database. That cost hours: the
    # live webhook had no secret configured, so ~50 payment.captured events were 400'd and thrown
    # away, and one customer's captured payment was cancelled as an abandoned hold.
    try:
        preview = json.loads(raw_body or b"{}")
    except Exception:
        preview = {"unparseable_body": True}
    intake_id = db.log_webhook_event(
        provider="razorpay",
        event_type=preview.get("event") or "unknown",
        external_id=request.headers.get("x-razorpay-event-id"),
        signature_ok=bool(sig_ok),
        payload=preview,
    )

    if not sig_ok:
        # One False hides three different faults with three different fixes. Naming which one turns
        # the next occurrence into a one-line diagnosis instead of a day's work.
        if not config.RAZORPAY_WEBHOOK_SECRET:
            reason = ("OUR secret is missing: RAZORPAY_WEBHOOK_SECRET is not set on this service. "
                      "Set it on Render and redeploy.")
        elif not signature:
            reason = ("Razorpay sent NO signature, which means the webhook in Razorpay has no Secret "
                      "configured. Note Razorpay keeps SEPARATE webhooks for Test and Live mode: check "
                      "you are editing the LIVE one.")
        else:
            reason = ("Signature present but does not match. The Secret on Razorpay's LIVE webhook and "
                      "RAZORPAY_WEBHOOK_SECRET on this service are different values.")
        # Loud, because this means money can move without a booking being confirmed. The signature
        # itself is never logged: it derives from the shared secret.
        logger.error("RAZORPAY WEBHOOK REJECTED. event=%s cause=%s "
                     "Consequence: payments may be captured without their bookings being confirmed.",
                     preview.get("event") or "unknown", reason)
        db.mark_webhook_processed(intake_id, error=f"signature rejected: {reason}")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    db.mark_webhook_processed(intake_id)

    payload = await request.json()
    event_type = payload.get("event", "")
    event_id = request.headers.get("x-razorpay-event-id") or payload.get("payload", {}).get(
        "payment", {}).get("entity", {}).get("id") or f"{event_type}-{raw_body[:32].hex()}"

    if db.payment_event_seen(event_id):
        return {"status": "ok", "dedup": True}
    db.upsert_payment_event(event_id, event_type, payload, signature)

    try:
        _handle_webhook_event(event_type, payload, background_tasks)
        db.mark_payment_event_processed(event_id)
    except Exception as e:
        logger.exception("webhook handler failed event=%s type=%s", event_id, event_type)
        db.mark_payment_event_processed(event_id, error=str(e))
    return {"status": "ok"}


def _handle_webhook_event(event_type: str, payload: dict, background_tasks: BackgroundTasks) -> None:
    entity = payload.get("payload", {})

    if event_type in ("payment.captured", "order.paid"):
        razorpay_payment = entity.get("payment", {}).get("entity", {})
        provider_order_id = razorpay_payment.get("order_id")
        razorpay_payment_id = razorpay_payment.get("id")
        if not provider_order_id or not razorpay_payment_id:
            return
        local = db.get_payment_by_provider_order(provider_order_id)
        if not local:
            logger.warning("webhook payment.captured: no local payment for order=%s", provider_order_id)
            return
        # Re-fetch from Razorpay rather than trusting the webhook body verbatim.
        remote = db.fetch_razorpay_payment(razorpay_payment_id)
        result = db.finalize_captured_payment(local, remote)
        if result == "confirmed":
            background_tasks.add_task(_send_confirmation_email, local["booking_id"])

    elif event_type == "payment.failed":
        razorpay_payment = entity.get("payment", {}).get("entity", {})
        provider_order_id = razorpay_payment.get("order_id")
        local = db.get_payment_by_provider_order(provider_order_id) if provider_order_id else None
        if not local:
            return
        db.set_payment_state(local["id"], "failed")
        db.record_payment_error(
            local["id"], razorpay_payment.get("error_code"), razorpay_payment.get("error_description"),
        )
        background_tasks.add_task(
            _send_payment_failed_email, local["booking_id"],
            razorpay_payment.get("error_code"), razorpay_payment.get("error_description"),
        )

    elif event_type in ("refund.created", "refund.processed"):
        refund_entity = entity.get("refund", {}).get("entity", {})
        provider_refund_id = refund_entity.get("id")
        razorpay_payment_id = refund_entity.get("payment_id")
        if not provider_refund_id or not razorpay_payment_id:
            return
        status_ = "processed" if event_type == "refund.processed" else "created"
        db.update_refund_status(provider_refund_id, status_)
        local = db.get_payment_by_provider_payment_id(razorpay_payment_id)
        if not local:
            return
        remaining = db.refund_owed_minor(local["booking_id"])
        new_state = "refunded" if remaining <= 0 else "partially_refunded"
        db.set_payment_state(local["id"], new_state)
        # Only on 'processed': 'created' means the provider has accepted it, not that money moved.
        if status_ == "processed":
            background_tasks.add_task(
                _send_refund_email, local["booking_id"],
                int(refund_entity.get("amount") or 0), refund_entity.get("currency") or local.get("currency") or "",
            )


_ZERO_DECIMAL = {"JPY", "KRW", "VND", "IDR"}   # Razorpay/most PSPs quote these without minor units


def _major(amount_minor: int, currency: str) -> str:
    """Minor units -> a display string. payment_refunds stores minor units; most of our currencies are
    2-decimal, but a zero-decimal currency must NOT be divided or we'd understate a refund 100x."""
    ccy = (currency or "").upper()
    value = float(amount_minor) if ccy in _ZERO_DECIMAL else float(amount_minor) / 100.0
    return mailer.format_money(value, ccy)


def _payment_context(booking_id: str) -> dict:
    """Shared recipient/context lookup for the money emails."""
    info = db.get_booking_notify_info(booking_id) or {}
    return {
        "booking_ref": f"#{str(booking_id).split('-')[0]}",
        "service_title": info.get("service_title") or "1-on-1 session",
        "mentor_name": info.get("mentor_name") or "",
        "mentor_email": info.get("mentor_email") or "",
        "candidate_name": info.get("candidate_name") or "there",
        "candidate_email": info.get("candidate_email") or "",
    }


def _send_payment_failed_email(booking_id: str, error_code: Optional[str], error_desc: Optional[str]) -> None:
    """A failed payment used to be silent: the customer was left believing they had a booking. Tell
    them it did NOT happen, and give ops the provider's error. The mentor is deliberately NOT emailed -
    no booking exists for them to act on."""
    try:
        ctx = _payment_context(booking_id)
        inv = db.get_booking_invoice(booking_id)
        amount = mailer.format_money(float(inv["total"]), inv["currency"]) if inv else ""
        reason = (error_desc or error_code or "").strip()
        if ctx["candidate_email"]:
            mailer.send_transactional(ctx["candidate_email"], "payment_failed", {
                "recipient_name": ctx["candidate_name"], "mentor_name": ctx["mentor_name"],
                "service_title": ctx["service_title"], "amount": amount, "failure_reason": reason,
                "retry_url": f"{config.FRONTEND_URL}/session/{booking_id}",
            })
        for admin_email in db.admin_notify_emails():
            mailer.send_transactional(admin_email, "payment_admin_notice", {
                "kind": "failed", **ctx, "amount": amount, "failure_reason": reason,
            })
    except Exception:
        logger.warning("payment failed email skipped booking=%s", booking_id)


def _send_refund_email(booking_id: str, amount_minor: int, currency: str) -> None:
    """Sent only once the provider reports the refund PROCESSED, so we never tell someone their money
    is back before it is."""
    try:
        ctx = _payment_context(booking_id)
        amount = _major(amount_minor, currency)
        if ctx["candidate_email"]:
            mailer.send_transactional(ctx["candidate_email"], "refund_issued", {
                "recipient_name": ctx["candidate_name"], "service_title": ctx["service_title"],
                "booking_ref": ctx["booking_ref"], "amount": amount,
            })
        for admin_email in db.admin_notify_emails():
            mailer.send_transactional(admin_email, "payment_admin_notice", {
                "kind": "refund", **ctx, "amount": amount,
            })
    except Exception:
        logger.warning("refund email skipped booking=%s", booking_id)


def _send_confirmation_email(booking_id: str) -> None:
    """Delegates to booking.py's _send_booking_confirmation (richer template:
    per-party local-timezone formatting, admin copy). mentor_id is resolved
    inside that function from booking_id, so passing an empty string is fine."""
    from routers.booking import _send_booking_confirmation
    try:
        info = db.get_booking_notify_info(booking_id) or {}
        candidate_email = info.get("candidate_email")
        if not candidate_email:
            return
        _send_booking_confirmation(booking_id, "", candidate_email, info.get("candidate_name"))
    except Exception:
        logger.warning("payment confirmation email failed booking=%s", booking_id)


# ── Webhook-independent verify (post-Checkout backstop, one booking at a time) ──

class VerifyBody(BaseModel):
    order_id: Optional[str] = None
    booking_id: Optional[str] = None


@router.post("/verify")
def verify(body: VerifyBody, background_tasks: BackgroundTasks):
    order_id = body.order_id
    if not order_id and body.booking_id:
        payment = db.get_payment_by_booking(body.booking_id)
        order_id = payment.get("provider_order_id") if payment else None
    if not order_id:
        raise HTTPException(status_code=404, detail="No order found for this booking")
    try:
        result = db.verify_order(order_id)
    except httpx.HTTPStatusError as e:
        logger.exception("verify_order failed order=%s", order_id)
        raise HTTPException(status_code=502, detail=f"Could not verify payment: {e}")
    # BUG-024: this is the webhook-independent backstop path (fires whenever Razorpay's
    # webhook hasn't landed - e.g. no public HTTPS URL registered, common in local/staging
    # dev). Unlike _handle_webhook_event, this endpoint used to finalize the payment
    # without ever sending the candidate their confirmation email. Only fire on a FRESH
    # confirmation (status == "confirmed"), not "already"/"already_confirmed", so a
    # repeated client-side poll never double-sends.
    if result.get("status") == "confirmed":
        local = db.get_payment_by_provider_order(order_id)
        if local:
            background_tasks.add_task(_send_confirmation_email, local["booking_id"])
    return result


# ── Checkout config + mock confirm ──────────────────────────────────────────────
# payments_enabled (platform_settings, admin-configurable) is the BUSINESS toggle
# deciding mock-instant-confirm vs real Razorpay checkout — distinct from the
# MOCK_SERVICES env flag. The frontend needs this to pick a checkout flow in any
# environment, so it's a runtime check here rather than a route-mount-time gate.

@router.get("/config")
def payments_config():
    """Public — tells the frontend which checkout flow to use."""
    return {"payments_enabled": db.payments_enabled()}


@router.post("/run-dispatcher")
def run_dispatcher(request: Request, background_tasks: BackgroundTasks):
    """Protected trigger for the money-correctness dispatcher (expire stale holds,
    verify-sweep dropped webhooks, refresh FX, process refunds, reconcile). Call it on
    a schedule via Supabase pg_cron + net.http_post (or any external cron) with the
    DISPATCHER_TOKEN header — no paid Render Cron Job needed. Runs in the background so
    the trigger returns immediately; the dispatcher's own lease lock prevents overlap."""
    token = config.DISPATCHER_TOKEN
    if not token or request.headers.get("x-dispatcher-token") != token:
        raise HTTPException(status_code=403, detail="Forbidden")
    from jobs.run_due import run_due
    background_tasks.add_task(run_due)
    return {"ok": True, "scheduled": True}


class MockConfirmBody(BaseModel):
    booking_id: str


@router.post("/confirm-mock")
def confirm_mock(body: MockConfirmBody, background_tasks: BackgroundTasks):
    """Skips Razorpay entirely and confirms the hold directly (payments_enabled=
    false mock mode). Rejected once real payments are switched on."""
    if db.payments_enabled():
        raise HTTPException(status_code=403, detail="Real payments are enabled — use the Razorpay checkout flow")
    try:
        result = db.confirm_booking_payment(body.booking_id, f"mock_{body.booking_id}")
    except Exception as e:
        msg = str(e)
        if "HOLD_EXPIRED" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=500, detail="Mock confirmation failed")
    if result == "confirmed":
        background_tasks.add_task(_send_confirmation_email, body.booking_id)
    return {"result": result}
