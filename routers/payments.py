import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

import config
import db
from core.auth import AuthUser, get_current_user_optional
from services import mailer

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


@router.post("/reserve")
def reserve(body: ReserveBody, user: Optional[AuthUser] = Depends(get_current_user_optional)):
    """Consume a binding price quote into a 10-minute payment-hold booking.
    Works for both authenticated users and guests, same as the old direct-book
    endpoint. The caller must follow up with /payments/razorpay/create-order
    (or, in MOCK_SERVICES mode, /payments/confirm-mock) before the hold expires."""
    answers_json = [a.model_dump() for a in body.answers]
    candidate_id = user.id if user else None
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
    """Cheap read-only status poll for the checkout page while it waits for
    the webhook to land. Mirrors immigroov's booking_status RPC."""
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
        logger.exception("razorpay create-order failed booking=%s", body.booking_id)
        raise HTTPException(status_code=502, detail=f"Could not create payment order: {e}")
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
    Always returns 200 once the signature checks out (even on a handler error)
    so Razorpay doesn't hammer retries on our own bugs — the failure is logged
    to payment_events.error for follow-up instead."""
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    # MOCK_SERVICES=true skips signature verification for local dev/tests —
    # documented in config.py's own MOCK_SERVICES comment ("webhook sig checks").
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
        try:
            result = db.confirm_booking_payment(local["booking_id"], razorpay_payment_id)
        except Exception as e:
            if "HOLD_EXPIRED" in str(e):
                # The 10-minute hold lapsed before the payment landed — the
                # money arrived for a slot we can no longer honour. Issue a
                # full refund rather than silently keeping the charge.
                db.issue_razorpay_refund(
                    payment_id=razorpay_payment_id,
                    amount_minor=remote.get("amount", 0),
                    booking_id=local["booking_id"],
                )
                db.set_payment_state(local["id"], "refunded")
                return
            raise
        db.record_provider_payload(local["id"], remote)
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
    """Best-effort — mirrors the existing booking.py _send_booking_confirmation
    background task, reused here so a real-payment confirmation emails exactly
    like a mock-mode one does."""
    try:
        info = db.get_booking_notify_info(booking_id) or {}
        candidate_email = info.get("candidate_email")
        if not candidate_email:
            return
        meeting_url = f"{config.FRONTEND_URL}/meeting/{booking_id}"
        mailer.send_transactional(candidate_email, "booking_confirmed_candidate", {
            "candidate_name": info.get("candidate_name") or "there",
            "mentor_name": info.get("mentor_name") or "your mentor",
            "service_title": info.get("service_title") or "1-on-1 session",
            "session_time": str(info.get("slot_time") or ""),
            "meeting_url": meeting_url,
        })
        if info.get("mentor_email"):
            mailer.send_transactional(info["mentor_email"], "booking_confirmed_mentor", {
                "mentor_name": info.get("mentor_name") or "there",
                "candidate_name": info.get("candidate_name") or "A candidate",
                "candidate_email": candidate_email,
                "service_title": info.get("service_title") or "1-on-1 session",
                "session_time": str(info.get("slot_time") or ""),
                "notes": "",
                "meeting_url": meeting_url,
            })
    except Exception:
        logger.warning("payment confirmation email failed booking=%s", booking_id)


# ── Mock confirm (local dev without a real Razorpay sandbox) ───────────────────

class MockConfirmBody(BaseModel):
    booking_id: str


if config.MOCK_SERVICES:
    @router.post("/confirm-mock")
    def confirm_mock(body: MockConfirmBody, background_tasks: BackgroundTasks):
        """Only mounted when MOCK_SERVICES=true. Skips Razorpay entirely and
        confirms the hold directly — mirrors immigroov's payments_enabled=false
        mock mode, for local dev without real Razorpay credentials."""
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
