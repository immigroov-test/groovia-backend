import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, field_validator

import config
import db
from core.auth import AuthUser, get_current_user
from services import mailer

logger = logging.getLogger("immigroov.routers.booking")

router = APIRouter(prefix="/booking", tags=["booking"])


# ── Slot availability ──────────────────────────────────────────────────────────

@router.get("/slots/{mentor_id}/{service_id}")
def get_slots(
    mentor_id: str,
    service_id: str,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
):
    """Return available booking slots for a mentor + service combination."""
    from datetime import date as _date, timedelta
    today = _date.today()
    p_from = from_date or today
    p_to   = to_date   or (today + timedelta(days=30))
    if p_to < p_from:
        raise HTTPException(status_code=400, detail="to_date must be >= from_date")
    if (p_to - p_from).days > 60:
        raise HTTPException(status_code=400, detail="Date range must be 60 days or fewer")
    try:
        slots = db.get_available_slots(mentor_id, service_id, str(p_from), str(p_to))
        return {"slots": slots, "count": len(slots)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        logger.exception("get_slots failed mentor=%s service=%s", mentor_id, service_id)
        raise HTTPException(status_code=500, detail="Failed to fetch slots")


# ── Reschedule slot picker (booking owner) ─────────────────────────────────────

@router.get("/{booking_id}/reschedule-slots")
def reschedule_slots(
    booking_id: str,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    user: AuthUser = Depends(get_current_user),
):
    """Available slots for rescheduling a booking (its own mentor + service), plus the
    current slot and deadline state. Owner-only."""
    from datetime import date as _date, timedelta, timezone as _tz
    target = db.get_booking_reschedule_target(booking_id)
    if not target:
        raise HTTPException(status_code=404, detail="Booking not found")
    if target.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Only the booking owner can reschedule")

    today = _date.today()
    p_from = from_date or today
    p_to = to_date or (today + timedelta(days=30))

    slot_iso = target.get("slot_time")
    deadline: Optional[str] = None
    if slot_iso:
        try:
            st = datetime.fromisoformat(str(slot_iso).replace("Z", "+00:00"))
            hours = (st - datetime.now(_tz.utc)).total_seconds() / 3600
            deadline = "buffer" if hours < 2 else "late" if hours < 24 else "free"
        except Exception:
            deadline = None
    try:
        slots = db.get_available_slots(target["mentor_id"], target["service_id"], str(p_from), str(p_to))
        return {"slots": slots, "current_slot": slot_iso, "deadline_state": deadline}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        logger.exception("reschedule_slots failed booking=%s", booking_id)
        raise HTTPException(status_code=500, detail="Failed to fetch slots")


# ── Book a session ─────────────────────────────────────────────────────────────
# RETIRED (Payments module cutover): booking creation now goes through the
# quote-based flow — POST /pricing/quote/{service_id} -> POST /payments/reserve
# -> POST /payments/confirm-mock or the real Razorpay checkout (see
# routers/payments.py). The old POST /booking here booked straight to
# 'confirmed' with zero pricing/payment involved; keeping it live would leave
# an unprotected zero-money booking path once real money matters.
#
# db.book_session (the underlying RPC) is deliberately NOT dropped from the
# database — same caution immigroov itself took with its own superseded
# creation RPCs (see the frozen spec's own comment on book_session_guest).


# ── Cancellation ───────────────────────────────────────────────────────────────

class CancelBody(BaseModel):
    booking_id: str
    cancelled_by: str = "user"

    @field_validator("cancelled_by")
    @classmethod
    def validate_by(cls, v: str) -> str:
        if v not in ("user", "mentor"):
            raise ValueError("cancelled_by must be 'user' or 'mentor'")
        return v


@router.post("/cancel")
def cancel_booking(body: CancelBody, background_tasks: BackgroundTasks, user: AuthUser = Depends(get_current_user)):
    principals = db.get_booking_principals(body.booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")

    is_candidate = principals.get("candidate_id") == user.id
    mentor = db.get_mentor_by_profile_id(user.id)
    is_mentor = bool(mentor and mentor.get("id") == principals.get("mentor_id"))

    if not is_candidate and not is_mentor:
        raise HTTPException(status_code=403, detail="You are not authorized to cancel this booking")

    try:
        result = db.cancel_booking(body.booking_id, body.cancelled_by)
        status = result.get("status") if isinstance(result, dict) else None
        background_tasks.add_task(
            _notify_parties, body.booking_id,
            "cancelled" if status == "cancelled" else "cancel_requested",
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("cancel_booking failed id=%s", body.booking_id)
        raise HTTPException(status_code=500, detail="Cancellation failed")


# ── Reschedule negotiation ─────────────────────────────────────────────────────

class ProposeRescheduleBody(BaseModel):
    booking_id: str
    offer_date: date
    range_start: datetime
    range_end: datetime


@router.post("/reschedule/propose")
def propose_reschedule(body: ProposeRescheduleBody, background_tasks: BackgroundTasks, user: AuthUser = Depends(get_current_user)):
    """Mentor proposes a new day + free time range."""
    if body.range_end <= body.range_start:
        raise HTTPException(status_code=400, detail="range_end must be after range_start")
    principals = db.get_booking_principals(body.booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor or mentor.get("id") != principals.get("mentor_id"):
        raise HTTPException(status_code=403, detail="Only the booking's mentor can propose a reschedule")
    try:
        offer_id = db.mentor_propose_reschedule(
            booking_id=body.booking_id,
            offer_date=str(body.offer_date),
            range_start=body.range_start.isoformat(),
            range_end=body.range_end.isoformat(),
        )
        background_tasks.add_task(
            _notify_parties, body.booking_id, "proposed" if offer_id else "cancelled"
        )
        return {"offer_id": offer_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("propose_reschedule failed booking=%s", body.booking_id)
        raise HTTPException(status_code=500, detail="Failed to propose reschedule")


class AcceptRescheduleBody(BaseModel):
    offer_id: str
    slot_time: datetime


@router.post("/reschedule/accept")
def accept_reschedule(body: AcceptRescheduleBody, background_tasks: BackgroundTasks, user: AuthUser = Depends(get_current_user)):
    """Mentee picks a time inside the mentor's proposed range."""
    principals = db.get_offer_booking_principals(body.offer_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Offer not found")
    if principals.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Only the session's mentee can accept a reschedule offer")
    try:
        booking = db.mentee_accept_reschedule(body.offer_id, body.slot_time.isoformat())
        if isinstance(booking, dict) and booking.get("id"):
            background_tasks.add_task(_notify_parties, booking["id"], "rescheduled")
        return booking
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("accept_reschedule failed offer=%s", body.offer_id)
        raise HTTPException(status_code=500, detail="Failed to accept reschedule")


class RequestOtherDateBody(BaseModel):
    booking_id: str
    requested_date: date


@router.post("/reschedule/request-date")
def request_other_date(body: RequestOtherDateBody, user: AuthUser = Depends(get_current_user)):
    """Mentee counter-proposes a different date."""
    principals = db.get_booking_principals(body.booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")
    if principals.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Only the session's mentee can request a different date")
    try:
        offer_id = db.mentee_request_other_date(body.booking_id, str(body.requested_date))
        return {"offer_id": offer_id}
    except Exception:
        logger.exception("request_other_date failed booking=%s", body.booking_id)
        raise HTTPException(status_code=500, detail="Failed to submit date request")


class ConfirmRescheduleBody(BaseModel):
    offer_id: str


@router.post("/reschedule/confirm")
def confirm_reschedule(body: ConfirmRescheduleBody, user: AuthUser = Depends(get_current_user)):
    """Mentor confirms the time the mentee selected."""
    principals = db.get_offer_booking_principals(body.offer_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Offer not found")
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor or mentor.get("id") != principals.get("mentor_id"):
        raise HTTPException(status_code=403, detail="Only the booking's mentor can confirm a reschedule")
    try:
        booking = db.mentor_confirm_reschedule(body.offer_id)
        return booking
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("confirm_reschedule failed offer=%s", body.offer_id)
        raise HTTPException(status_code=500, detail="Failed to confirm reschedule")


@router.post("/confirm-attendance/{booking_id}")
def confirm_attendance(booking_id: str, user: AuthUser = Depends(get_current_user)):
    """Mentor confirms they will attend (called ~1h before session)."""
    principals = db.get_booking_principals(booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor or mentor.get("id") != principals.get("mentor_id"):
        raise HTTPException(status_code=403, detail="Only the booking's mentor can confirm attendance")
    try:
        db.mentor_confirm_attendance(booking_id)
        return {"confirmed": True}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to confirm attendance")


# ── My bookings (mentee) ───────────────────────────────────────────────────────

@router.get("/my")
def my_bookings(user: AuthUser = Depends(get_current_user)):
    """The signed-in user's bookings with full lifecycle state for the manager UI."""
    return {"bookings": db.list_candidate_bookings(user.id)}


# ── Lifecycle v2: late-cancel / reschedule requests ────────────────────────────

class RespondRequestBody(BaseModel):
    request_id: str
    accept: bool


@router.post("/request/respond")
def respond_request(body: RespondRequestBody, user: AuthUser = Depends(get_current_user)):
    """Mentor approves/rejects a user's pending cancel or reschedule request."""
    principals = db.get_request_booking_principals(body.request_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Request not found")
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor or mentor.get("id") != principals.get("mentor_id"):
        raise HTTPException(status_code=403, detail="Only the booking's mentor can respond to this request")
    try:
        db.respond_booking_request(body.request_id, body.accept)
        return {"ok": True}
    except Exception as e:
        if "no longer open" in str(e).lower():
            raise HTTPException(status_code=409, detail=str(e))
        logger.exception("respond_request failed id=%s", body.request_id)
        raise HTTPException(status_code=500, detail="Failed to respond to request")


class CustomerRescheduleBody(BaseModel):
    booking_id: str
    slot_time: datetime


@router.post("/reschedule/customer")
def customer_reschedule(body: CustomerRescheduleBody, background_tasks: BackgroundTasks, user: AuthUser = Depends(get_current_user)):
    """User reschedules to a new slot (free window, or after mentor approval)."""
    principals = db.get_booking_principals(body.booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")
    if principals.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Only the booking's owner can reschedule")
    try:
        result = db.customer_reschedule(body.booking_id, body.slot_time.isoformat())
        if result == "rescheduled":
            background_tasks.add_task(_notify_parties, body.booking_id, "rescheduled")
        return {"result": result}
    except Exception as e:
        msg = str(e)
        if any(k in msg.lower() for k in ("not available", "approval", "2 hours")):
            raise HTTPException(status_code=409, detail=msg)
        logger.exception("customer_reschedule failed id=%s", body.booking_id)
        raise HTTPException(status_code=500, detail="Failed to reschedule")


class BookingIdBody(BaseModel):
    booking_id: str


@router.post("/reschedule/request")
def request_reschedule(body: BookingIdBody, background_tasks: BackgroundTasks, user: AuthUser = Depends(get_current_user)):
    """User requests a late reschedule that needs mentor approval first."""
    principals = db.get_booking_principals(body.booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")
    if principals.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Only the booking's owner can request a reschedule")
    try:
        req_id = db.request_reschedule(body.booking_id)
        background_tasks.add_task(_notify_parties, body.booking_id, "reschedule_requested")
        return {"request_id": req_id}
    except Exception as e:
        if "2 hours" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        logger.exception("request_reschedule failed id=%s", body.booking_id)
        raise HTTPException(status_code=500, detail="Failed to request reschedule")


class RejectRescheduleBody(BaseModel):
    offer_id: str


@router.post("/reschedule/reject")
def reject_reschedule(body: RejectRescheduleBody, background_tasks: BackgroundTasks, user: AuthUser = Depends(get_current_user)):
    """Mentee rejects the mentor's proposal — the booking is cancelled."""
    principals = db.get_offer_booking_principals(body.offer_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Offer not found")
    if principals.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Only the session's mentee can reject this proposal")
    try:
        booking = db.mentee_reject_reschedule(body.offer_id)
        if isinstance(booking, dict) and booking.get("id"):
            background_tasks.add_task(_notify_parties, booking["id"], "cancelled")
        return booking
    except Exception as e:
        if "no longer open" in str(e).lower():
            raise HTTPException(status_code=409, detail=str(e))
        logger.exception("reject_reschedule failed offer=%s", body.offer_id)
        raise HTTPException(status_code=500, detail="Failed to reject reschedule")


# ── Lifecycle v2: no-show ──────────────────────────────────────────────────────

class FlagNoShowBody(BaseModel):
    booking_id: str
    no_show_party: str  # 'mentor' | 'user' — the party who DIDN'T show

    @field_validator("no_show_party")
    @classmethod
    def validate_party(cls, v: str) -> str:
        if v not in ("mentor", "user"):
            raise ValueError("no_show_party must be 'mentor' or 'user'")
        return v


@router.post("/no-show/flag")
def flag_no_show(body: FlagNoShowBody, background_tasks: BackgroundTasks, user: AuthUser = Depends(get_current_user)):
    """Report the OTHER party as a no-show. Reporter must be the counterpart."""
    principals = db.get_booking_principals(body.booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")
    mentor = db.get_mentor_by_profile_id(user.id)
    is_mentor = bool(mentor and mentor.get("id") == principals.get("mentor_id"))
    is_candidate = principals.get("candidate_id") == user.id
    if body.no_show_party == "mentor" and not is_candidate:
        raise HTTPException(status_code=403, detail="Only the attendee can report a mentor no-show")
    if body.no_show_party == "user" and not is_mentor:
        raise HTTPException(status_code=403, detail="Only the mentor can report an attendee no-show")
    try:
        booking = db.flag_no_show(body.booking_id, body.no_show_party)
        background_tasks.add_task(_notify_parties, body.booking_id, "no_show")
        return booking
    except Exception as e:
        msg = str(e)
        if "10 minutes" in msg or "active session" in msg:
            raise HTTPException(status_code=409, detail=msg)
        logger.exception("flag_no_show failed id=%s", body.booking_id)
        raise HTTPException(status_code=500, detail="Failed to report no-show")


class ResolveNoShowBody(BaseModel):
    booking_id: str
    choice: str


@router.post("/no-show/resolve-mentor")
def resolve_mentor_no_show(body: ResolveNoShowBody, user: AuthUser = Depends(get_current_user)):
    """User chooses how to resolve a mentor no-show: rebook_same | rebook_different | refund."""
    principals = db.get_booking_principals(body.booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")
    if principals.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Only the attendee can resolve a mentor no-show")
    try:
        return db.resolve_mentor_no_show(body.booking_id, body.choice)
    except Exception as e:
        msg = str(e)
        if "no-show" in msg.lower() or "Unknown choice" in msg:
            raise HTTPException(status_code=409, detail=msg)
        logger.exception("resolve_mentor_no_show failed id=%s", body.booking_id)
        raise HTTPException(status_code=500, detail="Failed to resolve no-show")


@router.post("/no-show/resolve-customer")
def resolve_customer_no_show(body: ResolveNoShowBody, user: AuthUser = Depends(get_current_user)):
    """Mentor chooses how to resolve a user no-show: accept_rebook | reject."""
    principals = db.get_booking_principals(body.booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor or mentor.get("id") != principals.get("mentor_id"):
        raise HTTPException(status_code=403, detail="Only the mentor can resolve an attendee no-show")
    try:
        return db.resolve_customer_no_show(body.booking_id, body.choice)
    except Exception as e:
        msg = str(e)
        if "no-show" in msg.lower() or "Unknown choice" in msg:
            raise HTTPException(status_code=409, detail=msg)
        logger.exception("resolve_customer_no_show failed id=%s", body.booking_id)
        raise HTTPException(status_code=500, detail="Failed to resolve no-show")


@router.get("/{booking_id}/requests")
def list_requests(booking_id: str, user: AuthUser = Depends(get_current_user)):
    """List cancel/reschedule requests for a booking (visible to either party)."""
    principals = db.get_booking_principals(booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")
    mentor = db.get_mentor_by_profile_id(user.id)
    is_mentor = bool(mentor and mentor.get("id") == principals.get("mentor_id"))
    if not is_mentor and principals.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"requests": db.list_booking_requests(booking_id)}


# ── Background helpers ─────────────────────────────────────────────────────────
# _send_booking_confirmation is also used by routers/payments.py — it's the
# richer of the two email senders (per-party local-timezone formatting via
# booking_times_display, admin copy), so payments reuses it rather than
# keeping a second, weaker duplicate.

def _send_booking_confirmation(
    booking_id: str, mentor_id: str, candidate_email: str, candidate_name: Optional[str],
    notes: Optional[str] = None,
):
    """Notify both parties that a session was booked. Runs in a BackgroundTask so a
    mailer failure never affects the booking response."""
    try:
        times = db.get_booking_times_display(booking_id)
        info = db.get_booking_notify_info(booking_id) or {}
        mentor_name = info.get("mentor_name")
        mentor_email = info.get("mentor_email")
        service_title = info.get("service_title") or "1-on-1 session"
        meeting_url = f"{config.FRONTEND_URL}/meeting/{booking_id}"

        # booking_times_display returns a per-party local timestamp + IANA tz name.
        def _fmt(local_key: str, tz_key: str) -> str:
            if not times:
                return ""
            local = times.get(local_key)
            tz = times.get(tz_key) or "UTC"
            return f"{local} ({tz})" if local else ""

        mailer.send_transactional(
            candidate_email,
            "booking_confirmed_candidate",
            {
                "candidate_name": candidate_name or "there",
                "mentor_name": mentor_name or "your mentor",
                "service_title": service_title,
                "session_time": _fmt("customer_local", "customer_tz"),
                "meeting_url": meeting_url,
            },
        )

        if mentor_email:
            mailer.send_transactional(
                mentor_email,
                "booking_confirmed_mentor",
                {
                    "mentor_name": mentor_name or "there",
                    "candidate_name": candidate_name or "A candidate",
                    "candidate_email": candidate_email,
                    "service_title": service_title,
                    "session_time": _fmt("mentor_local", "mentor_tz"),
                    "notes": notes or "",
                    "meeting_url": meeting_url,
                },
            )

        if config.ADMIN_EMAIL:
            mailer.send_transactional(
                config.ADMIN_EMAIL,
                "booking_admin_notice",
                {
                    "event": "booked",
                    "mentor_name": mentor_name or "",
                    "candidate_name": candidate_name or "",
                    "candidate_email": candidate_email,
                    "session_time": _fmt("mentor_local", "mentor_tz"),
                    "service_title": service_title,
                },
            )
    except Exception:
        logger.warning("booking confirmation email failed booking=%s", booking_id)


def _notify_parties(booking_id: str, event: str):
    """Dispatch the lifecycle email(s) for a booking event. Runs in a BackgroundTask;
    a mailer failure never affects the action's response. Events mirror the DB's
    notify_booking_event outbox so behaviour stays consistent."""
    try:
        info = db.get_booking_notify_info(booking_id)
        if not info:
            return
        session_time = info.get("slot_time") or ""
        m_email, m_name = info.get("mentor_email"), info.get("mentor_name") or "there"
        c_email, c_name = info.get("candidate_email"), info.get("candidate_name") or "there"

        def send(to, template, recipient_name, other_name):
            if not to:
                return
            mailer.send_transactional(to, template, {
                "recipient_name": recipient_name,
                "other_name": other_name,
                "session_time": session_time,
            })

        if event == "cancelled":
            send(c_email, "booking_cancelled", c_name, m_name)
            send(m_email, "booking_cancelled", m_name, c_name)
        elif event == "rescheduled":
            send(c_email, "booking_rescheduled", c_name, m_name)
            send(m_email, "booking_rescheduled", m_name, c_name)
        elif event == "proposed":
            send(c_email, "reschedule_proposed", c_name, m_name)
        elif event == "reschedule_requested":
            send(m_email, "reschedule_requested", m_name, c_name)
        elif event == "cancel_requested":
            send(m_email, "cancel_requested", m_name, c_name)
        elif event == "no_show":
            send(c_email, "no_show_reported", c_name, m_name)
            send(m_email, "no_show_reported", m_name, c_name)

        # Admin/ops copy on the money-relevant lifecycle events.
        if event in ("cancelled", "rescheduled") and config.ADMIN_EMAIL:
            mailer.send_transactional(config.ADMIN_EMAIL, "booking_admin_notice", {
                "event": event,
                "mentor_name": info.get("mentor_name") or "",
                "candidate_name": info.get("candidate_name") or "",
                "candidate_email": c_email or "",
                "session_time": session_time,
            })
    except Exception:
        logger.warning("lifecycle email dispatch failed booking=%s event=%s", booking_id, event)
