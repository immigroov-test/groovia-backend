import logging
import re
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

import config
import db
from core.auth import AuthUser, get_current_user

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
def reserve(body: ReserveBody, user: AuthUser = Depends(get_current_user)):
    """Consume a binding price quote into a 10-minute payment-hold booking.
    BUG-025: requires a real account - guest booking was removed, so every booking
    is attached to a candidate_id from here on. The caller must follow up with
    /payments/razorpay/create-order (or, when payments are disabled,
    /payments/confirm-mock) before the hold expires."""
    answers_json = [a.model_dump() for a in body.answers]
    candidate_id = user.id
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
        )
        try:
            db.set_booking_phone(result["booking_id"], body.phone)
            if candidate_id:
                db.set_profile_phone_if_empty(candidate_id, body.phone)
        except Exception:
            logger.warning("could not save phone for reserved booking %s", result.get("booking_id"))
        return result
    except Exception as e:
        msg = str(e)
        if "QUOTE_EXPIRED" in msg:
            raise HTTPException(status_code=409, detail=msg)
        if "not available" in msg.lower() or "just taken" in msg.lower():
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
    # MOCK_SERVICES=true skips signature verification for local dev/tests.
    if not config.MOCK_SERVICES and not db.verify_webhook_signature(raw_body, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

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
