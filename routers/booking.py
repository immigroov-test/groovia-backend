import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from postgrest.exceptions import APIError
from pydantic import BaseModel, EmailStr, field_validator

import config
import db
from core.auth import AuthUser, get_current_user, get_current_user_optional
from services import mailer, policy
from services.ics import build_ics

logger = logging.getLogger("immigroov.routers.booking")

router = APIRouter(prefix="/booking", tags=["booking"])


def _raise_booking_error(e: Exception, fallback: str) -> None:
    """BUG-085: a plain `RAISE EXCEPTION 'text'` in plpgsql (no explicit errcode) defaults to
    SQLSTATE P0001 - every reschedule/cancel guard in the SQL layer ("Please pick a time inside the
    proposed range", "This proposal is no longer open", ...) is exactly that: an intentional,
    user-facing message, not an internal to hide. Surface it as a 400 instead of the generic 500
    these endpoints used to always return, which left the customer with no idea why an accept/
    propose/cancel failed. Anything else (constraint violation, connection error) still falls back
    to `fallback`."""
    if isinstance(e, APIError) and getattr(e, "code", None) == "P0001":
        raise HTTPException(status_code=400, detail=getattr(e, "message", None) or str(e))
    raise HTTPException(status_code=500, detail=fallback)

# Jitsi 1:1 video. The room is revealed only inside [start - 5min, end + 30min], and only to the
# booking's candidate or mentor. The server comes from config (BUG-120): the default demo server cuts
# an EMBEDDED call off after 5 minutes, so production needs JaaS or a self-hosted domain.
JITSI_DOMAIN = config.JITSI_DOMAIN
MEETING_OPEN_BEFORE = timedelta(minutes=policy.MEETING_OPEN_BEFORE_MIN)
MEETING_GRACE_AFTER = timedelta(minutes=policy.MEETING_GRACE_AFTER_MIN)

# Hard floor before the session where cancelling is off the table entirely (no refund path left).
# Mirrors booking_deadline_state() in SQL; surfaced to the client so copy can state it (BUG-119).
BUFFER_HOURS = policy.BUFFER_HOURS


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _meeting_party(meeting: dict, user_id: str) -> Optional[str]:
    """'candidate' | 'mentor' | None - which side of the booking this user is."""
    if meeting.get("candidate_id") and meeting["candidate_id"] == user_id:
        return "candidate"
    if meeting.get("mentor_profile_id") and meeting["mentor_profile_id"] == user_id:
        return "mentor"
    return None


def _deadline_state(slot: Optional[datetime], now: datetime, cancel_notice_hours) -> tuple[Optional[str], float]:
    """(deadline_state, free_hours) - buffer < BUFFER_HOURS, then 'late' until THIS mentor's own
    cancel/reschedule notice window, else 'free'. Single source of truth for both booking_detail()
    and reschedule_slots() (BUG-119) - they used to compute this separately and reschedule_slots()
    had its own hardcoded 24h floor, so the reschedule page could contradict the session detail
    page for any mentor whose configured notice wasn't exactly 24h. Mirrors booking_deadline_state()
    in SQL."""
    free_hours = max(float(cancel_notice_hours or 24), BUFFER_HOURS)
    if not slot:
        return None, free_hours
    hours = (slot - now).total_seconds() / 3600
    state = "buffer" if hours < BUFFER_HOURS else "late" if hours < free_hours else "free"
    return state, free_hours

def _jaas_token(room: str, display_name: str, is_moderator: bool) -> Optional[str]:
    """Signed room token for Jitsi as a Service, or None on the demo server (which takes no token).

    Only JaaS/self-hosted lifts the 5-minute embed cut-off (BUG-120), and JaaS authenticates every
    join with an RS256 JWT scoped to one room. Failing to sign must never block the call, so a bad key
    logs and degrades to no token rather than 500-ing someone out of their session."""
    if not config.JITSI_JAAS_READY:
        return None
    try:
        import jwt as _jwt
        now = int(datetime.now(timezone.utc).timestamp())
        payload = {
            "aud": "jitsi", "iss": "chat", "sub": config.JITSI_APP_ID,
            "room": room, "exp": now + 4 * 3600, "nbf": now - 60,
            "context": {"user": {"name": display_name or "Guest", "moderator": is_moderator}},
        }
        return _jwt.encode(payload, config.JITSI_PRIVATE_KEY, algorithm="RS256",
                           headers={"kid": config.JITSI_KID})
    except Exception:
        logger.exception("jaas token signing failed; joining without one")
        return None


@router.get("/{booking_id}/room")
def meeting_room(booking_id: str, user: AuthUser = Depends(get_current_user)):
    """Reveal the Jitsi room for a session - only to its candidate/mentor, and only
    within the join window. Returns {open:false, opens_at} before the window so the
    page can show a countdown without ever exposing the room early."""
    m = db.get_booking_meeting(booking_id)
    if not m:
        raise HTTPException(status_code=404, detail="Session not found")
    party = _meeting_party(m, user.id)
    if not party:
        raise HTTPException(status_code=403, detail="You are not a participant of this session")
    if m.get("status") in ("cancelled", "no_show"):
        raise HTTPException(status_code=409, detail="This session is no longer active")

    slot = _parse_ts(m.get("slot_time"))
    if not slot:
        raise HTTPException(status_code=409, detail="This session has no scheduled time yet")
    end = _parse_ts(m.get("slot_end")) or (slot + timedelta(minutes=30))
    opens_at = slot - MEETING_OPEN_BEFORE
    closes_at = end + MEETING_GRACE_AFTER
    now = datetime.now(timezone.utc)

    display_name = (m.get("candidate_name") if party == "candidate" else m.get("mentor_name")) or ""
    other_name = (m.get("mentor_name") if party == "candidate" else m.get("candidate_name")) or "the other participant"
    # i_joined gates the post-call actions on the meeting page: clicking Join is what proves a
    # party actually attended, so only then may they leave a review or report the other side as a
    # no-show. they_joined lets the page say who was missing instead of asking.
    i_joined   = bool(m.get("candidate_joined_at") if party == "candidate" else m.get("mentor_joined_at"))
    they_joined = bool(m.get("mentor_joined_at") if party == "candidate" else m.get("candidate_joined_at"))
    base = {"party": party, "display_name": display_name, "other_name": other_name,
            "i_joined": i_joined, "they_joined": they_joined,
            "service_title": m.get("service_title"), "duration": m.get("duration"),
            "slot_time": m.get("slot_time"), "opens_at": opens_at.isoformat(), "closes_at": closes_at.isoformat()}

    if now < opens_at:
        return {"open": False, "reason": "early", **base}
    if now > closes_at:
        return {"open": False, "reason": "ended", **base}

    room = db.ensure_meeting_room(booking_id)
    if not room:
        raise HTTPException(status_code=500, detail="Could not prepare the meeting room")
    # JaaS rooms are namespaced by the AppID and need a signed token; the demo server takes neither.
    full_room = f"{config.JITSI_APP_ID}/{room}" if config.JITSI_JAAS_READY else room
    token = _jaas_token(room, display_name, party == "mentor")
    # embed=False on the public server: it cuts an EMBEDDED call at 5 minutes, so the client opens the
    # room in its own tab instead (same server, no cap). Once JaaS/self-hosted is configured the call
    # goes back inside the page with no client change.
    join_url = f"https://{JITSI_DOMAIN}/{full_room}" + (f"?jwt={token}" if token else "")
    return {"open": True, "domain": JITSI_DOMAIN, "room": full_room, "jwt": token,
            "embed": config.JITSI_JAAS_READY, "join_url": join_url, **base}


class AttendanceBody(BaseModel):
    event: str   # 'joined' | 'left'


@router.post("/{booking_id}/attendance")
def meeting_attendance(booking_id: str, body: AttendanceBody, user: AuthUser = Depends(get_current_user)):
    """Record that the caller joined/left the call, for no-show detection. Best-effort
    (client-reported), attributed to the caller's side of the booking."""
    if body.event not in ("joined", "left"):
        raise HTTPException(status_code=400, detail="event must be 'joined' or 'left'")
    m = db.get_booking_meeting(booking_id)
    if not m:
        raise HTTPException(status_code=404, detail="Session not found")
    party = _meeting_party(m, user.id)
    if not party:
        raise HTTPException(status_code=403, detail="You are not a participant of this session")
    db.record_meeting_attendance(booking_id, party, body.event)
    return {"ok": True}


# ── Unified session detail (candidate / mentor / admin) ─────────────────────────

@router.get("/{booking_id}/detail")
def booking_detail(booking_id: str, user: AuthUser = Depends(get_current_user)):
    """Role-aware detail for the session page. Returns the confirmation-style fields plus
    capability flags and the join window (identical to the /room gate, so the page and the
    room agree on when 'Join' is live). Candidate/mentor/admin each see only what they should."""
    d = db.get_booking_full_detail(booking_id)
    if not d:
        raise HTTPException(status_code=404, detail="Session not found")

    is_candidate = bool(d.get("candidate_id") and d["candidate_id"] == user.id)
    is_mentor = bool(d.get("mentor_profile_id") and d["mentor_profile_id"] == user.id)
    is_admin = (not is_candidate and not is_mentor) and db.get_profile_role(user.id) == "admin"
    if not (is_candidate or is_mentor or is_admin):
        raise HTTPException(status_code=403, detail="You are not a participant of this session")
    role = "candidate" if is_candidate else "mentor" if is_mentor else "admin"

    status = d.get("status")
    active = status in ("confirmed", "rescheduled")
    unpaid_hold = status == "pending"
    slot = _parse_ts(d.get("slot_time"))
    dur = d.get("service_duration") or 30
    end = _parse_ts(d.get("slot_end")) or (slot + timedelta(minutes=dur) if slot else None)
    now = datetime.now(timezone.utc)
    is_past = bool(slot and slot < now)

    # Join window == the /room gate: open only within [slot - 5min, end + 30min].
    opens_at = (slot - MEETING_OPEN_BEFORE) if slot else None
    closes_at = (end + MEETING_GRACE_AFTER) if end else None
    join_open = bool(active and slot and closes_at and opens_at <= now <= closes_at)

    # Deadline state: buffer < 2h, then 'late' until the mentor's cancel/reschedule notice, else
    # 'free'. Mirrors booking_deadline_state() in SQL so the page and the DB agree on penalties.
    deadline_state, free_hours = _deadline_state(slot, now, d.get("cancel_notice_hours"))

    can_pay = bool(is_candidate and unpaid_hold and not is_past)
    can_join = bool((is_candidate or is_mentor) and join_open)
    # BUG-084: inside the 2h buffer, cancel_booking hard-rejects (no refund path left) but
    # request_reschedule no longer does - a reschedule REQUEST (mentor approval, not an instant
    # pick) is still possible, so offer that instead of a cancel button that would just error out.
    can_reschedule = bool((is_candidate or is_mentor) and active and not is_past)
    can_cancel = bool((is_candidate or is_mentor) and active and not is_past and deadline_state != "buffer")
    # BUG-099: the no-show button is live only in a sensible window - from 10 min after the start until
    # 24h after the end - and never once a no-show is already recorded or the session isn't active.
    no_show_reported = bool(d.get("no_show_by"))
    no_show_open = bool(slot and end and (slot + timedelta(minutes=10)) < now < (end + timedelta(hours=24)))
    can_report_no_show = bool((is_candidate or is_mentor) and active and not no_show_reported and no_show_open)

    out: dict = {
        "id": d["id"],
        "role": role,
        "status": status,
        "service_title": d.get("service_title") or "Session",
        "service_duration": d.get("service_duration"),
        "service_type": d.get("service_type"),
        "slot_time": d.get("slot_time"),
        "slot_end": d.get("slot_end"),
        "is_past": is_past,
        "paid": active,               # confirmed/rescheduled == paid (or free/mock)
        "unpaid_hold": unpaid_hold,
        "reschedule_count": d.get("reschedule_count") or 0,
        "no_show_by": d.get("no_show_by"),
        "deadline_state": deadline_state,
        # BUG-119: the real windows, so the page states this mentor's actual notice period instead of
        # a hardcoded "2 hours" / "24 hours" that contradicts whatever they set in their booking rules.
        "cancel_notice_hours": free_hours,
        "buffer_hours": BUFFER_HOURS,
        # The page states these percentages in its cancellation copy; sending them means the wording
        # follows services/policy instead of being retyped in the client (BUG-147).
        "late_cancel_fee_pct": policy.LATE_CANCEL_FEE_PCT,
        "mentor_penalty_pct": policy.MENTOR_NO_SHOW_PENALTY_PCT,
        "opens_at": opens_at.isoformat() if opens_at else None,
        "closes_at": closes_at.isoformat() if closes_at else None,   # BUG-105: joinable until the END (+grace)
        "join_open": join_open,
        "offer": d.get("offer"),
        "request": d.get("request"),
        "candidate_tz": d.get("attendee_tz"),
        "mentor_name": d.get("mentor_name"),
        "mentor_photo": d.get("mentor_photo"),
        "mentor_slug": d.get("mentor_slug"),
        "mentor_tz": d.get("mentor_tz"),
        "mentor_country": d.get("mentor_country"),
        "can_pay": can_pay,
        "can_join": can_join,
        "can_reschedule": can_reschedule,
        "can_cancel": can_cancel,
        "can_report_no_show": can_report_no_show,
        "notes": d.get("notes"),                       # BUG-113: customer's "what to prepare" note
        "answers": db.get_booking_answers(d["id"]),    # BUG-113: intake-question answers
    }

    # Candidate's contact details are for the mentor + admin only.
    if is_mentor or is_admin:
        out["candidate_name"] = d.get("candidate_name")
        out["candidate_email"] = d.get("candidate_email")
        out["candidate_phone"] = d.get("candidate_phone")

    # Payment amount is the CUSTOMER's transaction - it bakes in the platform commission + tax,
    # which are admin-only. The candidate sees their own; admin sees all. The MENTOR only needs to
    # know it's paid (the `paid` flag); their own net earning lives in their payouts view, so we do
    # NOT expose the gross customer amount to them.
    pay = d.get("payment")
    if pay and (is_candidate or is_admin):
        out["payment"] = {
            "state": pay.get("state"),
            "amount": pay.get("amount"),
            "currency": pay.get("currency"),
        }

    # What the candidate needs to re-enter checkout on an unpaid hold.
    if can_pay:
        out["pay_context"] = {
            "mentor_id": d.get("mentor_id"),
            "service_id": d.get("service_id"),
            "mentor_slug": d.get("mentor_slug"),
            "phone": d.get("candidate_phone") or "",
        }
    return out


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
    from datetime import date as _date

    target = db.get_booking_reschedule_target(booking_id)
    if not target:
        raise HTTPException(status_code=404, detail="Booking not found")
    if target.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Only the booking owner can reschedule")

    today = _date.today()
    p_from = from_date or today
    p_to = to_date or (today + timedelta(days=30))

    slot_iso = target.get("slot_time")
    # BUG-119: same helper (and same source - this mentor's own cancel_notice_hours) as
    # booking_detail(), so this page can never disagree with the session detail page about
    # whether a reschedule this close to the session needs the mentor's approval.
    deadline, free_hours = _deadline_state(_parse_ts(slot_iso), datetime.now(timezone.utc), target.get("cancel_notice_hours"))
    # If the MENTOR proposed a reschedule, the page defaults to their proposed time frame (with a
    # "see all my available times" option). offer = {id, range_start, range_end} or None.
    offer = db.get_active_mentor_proposal(booking_id)
    try:
        slots = db.get_available_slots(target["mentor_id"], target["service_id"], str(p_from), str(p_to))
        return {
            "slots": slots, "current_slot": slot_iso, "deadline_state": deadline, "offer": offer,
            "cancel_notice_hours": free_hours,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        logger.exception("reschedule_slots failed booking=%s", booking_id)
        raise HTTPException(status_code=500, detail="Failed to fetch slots")


@router.get("/{booking_id}/proposal-slots")
def proposal_slots(booking_id: str, user: AuthUser = Depends(get_current_user)):
    """Real, bookable slots inside the mentor's proposed reschedule range (BUG-085). The old UI
    let the customer type any time, which almost always failed the availability check on accept.
    Owner-only. Returns [] when there is no open mentor proposal."""
    target = db.get_booking_reschedule_target(booking_id)
    if not target:
        raise HTTPException(status_code=404, detail="Booking not found")
    if target.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Only the booking owner can view this")

    offer = db.get_active_mentor_proposal(booking_id)
    if not offer or not offer.get("range_start") or not offer.get("range_end"):
        return {"slots": [], "offer_id": offer.get("id") if offer else None}

    r_start = _parse_ts(offer["range_start"])
    r_end = _parse_ts(offer["range_end"])
    try:
        all_slots = db.get_available_slots(
            target["mentor_id"], target["service_id"], str(r_start.date()), str(r_end.date()),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        logger.exception("proposal_slots failed booking=%s", booking_id)
        raise HTTPException(status_code=500, detail="Failed to fetch slots")

    # Keep only slots whose start falls inside [range_start, range_end) - these are the times the
    # mentee can actually accept (mentee_accept_reschedule enforces the same window + availability).
    in_range = []
    for s in all_slots:
        st = _parse_ts(s.get("slot_start"))
        if st and r_start <= st < r_end:
            in_range.append(s)
    return {"slots": in_range, "offer_id": offer.get("id"),
            "range_start": offer["range_start"], "range_end": offer["range_end"]}


# ── Book a session ─────────────────────────────────────────────────────────────

class BookingAnswerItem(BaseModel):
    question_id: str
    answer_text: str


def _validate_phone(v: str) -> str:
    """Mandatory contact phone. The frontend collects it with a country code; here
    we just require enough digits to be a real number (E.164 is 8-15 digits incl.
    country code, but keep the floor lenient for short national formats)."""
    v = (v or "").strip()
    if len(re.sub(r"\D", "", v)) < 7:
        raise ValueError("A valid phone number is required")
    return v


class BookSessionBody(BaseModel):
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
    idempotency_key: Optional[str] = None
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
        return _validate_phone(v)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: Optional[str]) -> Optional[str]:
        return (v or "").strip() or None

    @field_validator("notes")
    @classmethod
    def strip_notes(cls, v: Optional[str]) -> Optional[str]:
        return (v or "").strip()[:500] or None


@router.post("")
def book_session(
    body: BookSessionBody,
    background_tasks: BackgroundTasks,
    user: Optional[AuthUser] = Depends(get_current_user_optional),
):
    """Book a direct session slot (free / mock-confirm path). Guest-allowed (flight-style):
    a signed-in caller attaches candidate_id; a guest books with candidate_id NULL and their
    identity lives in candidate_email/name/phone, claimed when they later sign up with that
    email. The quote/reserve/confirm flow in routers/payments.py is the paid equivalent."""
    # Idempotency: a retried/duplicated request (e.g. after a dropped network response)
    # returns the original booking instead of creating a second one.
    if body.idempotency_key:
        existing = db.get_booking_by_idempotency_key(body.idempotency_key)
        if existing:
            return {"booking_id": existing["id"], "status": existing.get("status", "confirmed")}

    answers_json = [a.model_dump() for a in body.answers]
    candidate_id = user.id if user else None
    # A mentor must never book their own session (same account or same email); they can test with a
    # DIFFERENT email as a normal guest.
    if db.is_self_booking(body.mentor_id, candidate_id, body.email):
        raise HTTPException(status_code=403,
                            detail="You can't book your own session. To test the booking flow, use a different email address.")
    try:
        result = db.book_session(
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
        if result and result[0]:
            booking_id = result[0]["booking_id"]
            if body.idempotency_key:
                db.set_booking_idempotency_key(booking_id, body.idempotency_key)
            if body.referral_code:
                # Mock/free path: no charge + no pricing rows, so this records attribution only
                # (no commission is generated). The paid path applies the discount in reserve.
                db.attribute_booking_referral(booking_id, body.referral_code)
            db.set_booking_phone(booking_id, body.phone)
            db.set_booking_notes(booking_id, body.notes)   # BUG-113: persist for email + dashboard
            if candidate_id:
                db.set_profile_phone_if_empty(candidate_id, body.phone)
            background_tasks.add_task(
                _send_booking_confirmation, booking_id, body.mentor_id, body.email, body.name
            )
        return result[0] if result else {"booking_id": None, "status": "unknown"}
    except Exception as e:
        msg = str(e)
        if "not available" in msg.lower():
            raise HTTPException(status_code=409, detail=msg)
        logger.exception("book_session failed mentor=%s service=%s", body.mentor_id, body.service_id)
        raise HTTPException(status_code=500, detail="Booking failed - please try again")


# ── Cancellation ───────────────────────────────────────────────────────────────

class CancelBody(BaseModel):
    booking_id: str
    cancelled_by: str = "user"
    # BUG-123: mandatory for both sides. It goes to the other party's email and is kept for any refund
    # review, so it is REQUIRED (no default - a default would skip the validator entirely and let an
    # omitted field through) and an empty or one-character "reason" is rejected.
    reason: str

    @field_validator("cancelled_by")
    @classmethod
    def validate_by(cls, v: str) -> str:
        if v not in ("user", "mentor"):
            raise ValueError("cancelled_by must be 'user' or 'mentor'")
        return v

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 3:
            raise ValueError("Please give a reason for the cancellation")
        if len(v) > 1000:
            raise ValueError("Please keep the reason under 1000 characters")
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
        # Store the reason BEFORE the state change so the notification (which reads the booking) always
        # sees it, and so a failed cancel doesn't leave a reason for a session that is still live.
        db.set_booking_cancellation_reason(body.booking_id, body.reason)
        result = db.cancel_booking(body.booking_id, body.cancelled_by)
        status = result.get("status") if isinstance(result, dict) else None
        background_tasks.add_task(
            _notify_parties, body.booking_id,
            "cancelled" if status == "cancelled" else "cancel_requested",
            None, body.cancelled_by,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("cancel_booking failed id=%s", body.booking_id)
        _raise_booking_error(e, "Cancellation failed")


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
    except Exception as e:
        logger.exception("propose_reschedule failed booking=%s", body.booking_id)
        _raise_booking_error(e, "Failed to propose reschedule")


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
    old_slot = (db.get_offer_booking(body.offer_id) or {}).get("slot_time")
    try:
        booking = db.mentee_accept_reschedule(body.offer_id, body.slot_time.isoformat())
        if isinstance(booking, dict) and booking.get("id"):
            background_tasks.add_task(_notify_parties, booking["id"], "rescheduled", old_slot)
        return booking
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("accept_reschedule failed offer=%s", body.offer_id)
        _raise_booking_error(e, "Failed to accept reschedule")


class RequestOtherDateBody(BaseModel):
    booking_id: str
    requested_date: date


@router.post("/reschedule/request-date")
def request_other_date(body: RequestOtherDateBody, background_tasks: BackgroundTasks, user: AuthUser = Depends(get_current_user)):
    """Mentee counter-proposes a different date (the 'Ask another date' branch of Diagram 3).
    The mentor is notified to re-propose times for that day."""
    principals = db.get_booking_principals(body.booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")
    if principals.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Only the session's mentee can request a different date")
    try:
        offer_id = db.mentee_request_other_date(body.booking_id, str(body.requested_date))
        background_tasks.add_task(_notify_parties, body.booking_id, "counter_proposed")
        return {"offer_id": offer_id}
    except Exception as e:
        logger.exception("request_other_date failed booking=%s", body.booking_id)
        _raise_booking_error(e, "Failed to submit date request")


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
    except Exception as e:
        logger.exception("confirm_reschedule failed offer=%s", body.offer_id)
        _raise_booking_error(e, "Failed to confirm reschedule")


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
    old_slot = (db.get_booking_reschedule_target(body.booking_id) or {}).get("slot_time")
    try:
        result = db.customer_reschedule(body.booking_id, body.slot_time.isoformat())
        if result == "rescheduled":
            background_tasks.add_task(_notify_parties, body.booking_id, "rescheduled", old_slot)
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
    """Mentee rejects the mentor's proposal - per policy (Diagram 3) this cancels the booking
    (late: refund 100% + 25% mentor penalty; within: credit 100%). A customer who wants to keep
    the session but change the time uses 'Ask another date' (request-date), not this."""
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
    no_show_party: str  # 'mentor' | 'user' - the party who DIDN'T show

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


def _notify_refund_request(booking_id: str) -> None:
    """Tell the admins a customer has asked for a refund after a mentor no-show, so someone can review
    it. Deliberately admin-only: the customer already sees "under review" in the app."""
    try:
        info = db.get_booking_notify_info(booking_id) or {}
        inv = db.get_booking_invoice(booking_id)
        for admin_email in db.admin_notify_emails():
            mailer.send_transactional(admin_email, "payment_admin_notice", {
                "kind": "refund_requested",
                "booking_ref": _booking_ref(booking_id),
                "service_title": info.get("service_title") or "",
                "mentor_name": info.get("mentor_name") or "",
                "candidate_name": info.get("candidate_name") or "",
                "candidate_email": info.get("candidate_email") or "",
                "amount": (mailer.format_money(float(inv["total"]), inv["currency"]) if inv else ""),
                "failure_reason": "Mentor no-show - customer requested a refund. Needs manual review.",
            })
    except Exception:
        logger.warning("refund-request admin email skipped booking=%s", booking_id)


@router.post("/no-show/resolve-mentor")
def resolve_mentor_no_show(body: ResolveNoShowBody, background_tasks: BackgroundTasks,
                           user: AuthUser = Depends(get_current_user)):
    """User chooses how to resolve a mentor no-show: rebook_same | rebook_different | refund."""
    principals = db.get_booking_principals(body.booking_id)
    if not principals:
        raise HTTPException(status_code=404, detail="Booking not found")
    if principals.get("candidate_id") != user.id:
        raise HTTPException(status_code=403, detail="Only the attendee can resolve a mentor no-show")
    try:
        result = db.resolve_mentor_no_show(body.booking_id, body.choice)
        # BUG-121: a refund after a no-show is REVIEWED by a person, not paid automatically, so the
        # request has to reach an admin. The customer is told it is under review, never that it is done.
        if body.choice == "refund":
            background_tasks.add_task(_notify_refund_request, body.booking_id)
        return result
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

def _send_one(to: str, template: str, data: dict, attachments=None) -> bool:
    """Send one email, isolated. Returns True on success.

    Every confirmation used to sit in a single try block, sequentially: customer, then mentor, then
    admins. One failure aborted the rest, so a bounced customer address silently cost the mentor their
    notification too, and the log said only "booking confirmation email failed" without naming who.
    Each recipient is now independent and named in the log.
    """
    if not to:
        logger.warning("email skipped: no address for template=%s", template)
        return False
    try:
        mailer.send_transactional(to, template, data, attachments=attachments)
        logger.info("email sent template=%s to=%s", template, to)
        return True
    except Exception:
        logger.exception("EMAIL FAILED template=%s to=%s", template, to)
        return False


def _send_booking_confirmation(
    booking_id: str, mentor_id: str, candidate_email: str, candidate_name: Optional[str],
):
    """Notify both parties that a session was booked. Runs in a BackgroundTask so a
    mailer failure never affects the booking response."""
    try:
        times = db.get_booking_times_display(booking_id)
        info = db.get_booking_notify_info(booking_id) or {}
        # BUG-113: the customer's prep note + question answers, read from the DB so they reach BOTH the
        # instant-book path AND the paid path (whose email fires later at payment-confirm, not booking).
        notes = info.get("notes")
        answers = info.get("answers") or []
        mentor_name = info.get("mentor_name")
        mentor_email = info.get("mentor_email")
        mentor_photo = info.get("mentor_photo")
        service_title = info.get("service_title") or "1-on-1 session"
        meeting_url = f"{config.FRONTEND_URL}/meeting/{booking_id}"

        # booking_times_display returns a per-party local timestamp + IANA tz name.
        # Format both parties' times so every email can show "your time" AND "their time".
        def _fmt(local_key: str, tz_key: str) -> str:
            if not times:
                return ""
            local = times.get(local_key)
            tz = times.get(tz_key) or "UTC"
            if not local:
                return ""
            try:
                dt = datetime.fromisoformat(str(local).replace("Z", ""))
                stamp = dt.strftime("%a, %b %d, %Y at %I:%M %p").replace(" 0", " ")
            except Exception:
                stamp = str(local)
            city = tz.split("/")[-1].replace("_", " ")
            return f"{stamp} ({city})"

        candidate_time = _fmt("customer_local", "customer_tz")
        mentor_time = _fmt("mentor_local", "mentor_tz")

        # Guest booking (candidate_id NULL): tell them to create an account with THIS email to
        # join + manage (that signup auto-links the booking + payment via /auth/sync).
        is_guest = not info.get("candidate_id")
        signup_url = f"{config.FRONTEND_URL}/home?auth=open&email={quote(candidate_email or '')}"

        # Calendar (.ics) so both parties can one-tap add the session to their calendar.
        ics_att = None
        try:
            m = db.get_booking_meeting(booking_id) or {}
            start = _parse_ts(m.get("slot_time"))
            end = _parse_ts(m.get("slot_end")) or (start + timedelta(minutes=30) if start else None)
            if start and end:
                ics = build_ics(
                    uid=booking_id, start=start, end=end,
                    summary=f"Immigroov: {service_title}",
                    description=f"Your 1-on-1 with {mentor_name or 'your mentor'}. Join here: {meeting_url}",
                    location=meeting_url,
                )
                ics_att = [{"filename": "session.ics", "content": ics}]
        except Exception:
            ics_att = None

        sent_ok = _send_one(
            candidate_email,
            "booking_confirmed_candidate",
            {
                "candidate_name": candidate_name or "there",
                "mentor_name": mentor_name or "your mentor",
                "mentor_photo": mentor_photo or "",
                "service_title": service_title,
                "candidate_time": candidate_time,
                "mentor_time": mentor_time,
                "meeting_url": meeting_url,
                "is_guest": is_guest,
                "signup_url": signup_url,
                "notes": notes or "",
                "answers": answers,
                # FEAT-019: booking reference + what they were charged, so this email doubles as the
                # customer's invoice. Absent for free sessions (no charge, so nothing to itemise).
                "booking_ref": _booking_ref(booking_id),
                "invoice": db.get_booking_invoice(booking_id),
            },
            attachments=ics_att,
        )
        if not sent_ok:
            # Loud: the customer has paid and has no record of what they booked or how to join.
            logger.error("CUSTOMER CONFIRMATION NOT SENT booking=%s to=%s - they have paid and have "
                         "no session details. Resend manually.", booking_id, candidate_email)

        if mentor_email:
            _send_one(
                mentor_email,
                "booking_confirmed_mentor",
                {
                    "mentor_name": mentor_name or "there",
                    "candidate_name": candidate_name or "A candidate",
                    "candidate_email": candidate_email,
                    "service_title": service_title,
                    "candidate_time": candidate_time,
                    "mentor_time": mentor_time,
                    "notes": notes or "",
                    "answers": answers,
                    "meeting_url": meeting_url,
                    "booking_ref": _booking_ref(booking_id),
                },
                attachments=ics_att,
            )

        for admin_email in db.admin_notify_emails():
            _send_one(
                admin_email,
                "booking_admin_notice",
                {
                    "event": "booked",
                    "booking_ref": _booking_ref(booking_id),
                    "invoice": db.get_booking_invoice(booking_id),
                    "mentor_name": mentor_name or "",
                    "candidate_name": candidate_name or "",
                    "candidate_email": candidate_email,
                    "candidate_time": candidate_time,
                    "mentor_time": mentor_time,
                    "service_title": service_title,
                },
            )
    except Exception:
        logger.warning("booking confirmation email failed booking=%s", booking_id)


def _fmt_iso_in_tz(iso: Optional[str], tz_name: Optional[str]) -> str:
    """Format a UTC timestamp in a party's own IANA timezone, e.g. 'Mon, Aug 11, 2025 at 3:00 PM
    (Kolkata)'. Used to show the OLD time in the rescheduled email in each recipient's timezone."""
    dt = _parse_ts(iso)
    if not dt:
        return ""
    try:
        from zoneinfo import ZoneInfo
        dt = dt.astimezone(ZoneInfo(tz_name or "UTC"))
    except Exception:
        pass
    stamp = dt.strftime("%a, %b %d, %Y at %I:%M %p").replace(" 0", " ")
    city = (tz_name or "UTC").split("/")[-1].replace("_", " ")
    return f"{stamp} ({city})"


def _booking_ref(booking_id: str) -> str:
    """Short human reference for a booking, matching what the session page shows (#8b6826f8), so a
    customer quoting it to support is quoting the same string we can search on."""
    return f"#{str(booking_id).split('-')[0]}"


def _local_stamp(times: dict, local_key: str, tz_key: str) -> str:
    """'Mon, Aug 10, 2026 at 3:45 PM (Kolkata)' from booking_times_display, in the RECIPIENT's zone."""
    local = times.get(local_key)
    if not local:
        return ""
    tz = times.get(tz_key) or "UTC"
    try:
        dt = datetime.fromisoformat(str(local).replace("Z", ""))
        stamp = dt.strftime("%a, %b %d, %Y at %I:%M %p").replace(" 0", " ")
    except Exception:
        stamp = str(local)
    return f"{stamp} ({tz.split('/')[-1].replace('_', ' ')})"


def _notify_parties(booking_id: str, event: str, old_slot: Optional[str] = None,
                    cancelled_by: Optional[str] = None):
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
        service_title = info.get("service_title") or "1-on-1 session"
        meeting_url = f"{config.FRONTEND_URL}/meeting/{booking_id}"
        # The customer session-detail page is /session/{id} (there is NO /account/sessions/{id} page,
        # only the reschedule sub-route), so linking to the latter 404'd (BUG-096 + the BUG-085 email).
        session_url = f"{config.FRONTEND_URL}/session/{booking_id}"
        mentor_hub = f"{config.FRONTEND_URL}/mentor"

        def send(to, template, data):
            if not to:
                return
            mailer.send_transactional(to, template, data)

        if event == "cancelled":
            # BUG-124: both sides used to get the SAME customer-worded email ("book another session
            # from the mentor directory"), which reads as someone else's mail when it lands in a
            # mentor's inbox. Each side now gets its own wording, and the body carries the same detail
            # as the reschedule email: service, when it was scheduled IN THE RECIPIENT'S OWN timezone
            # (BUG-126), the booking reference, and the reason it was cancelled (BUG-123).
            times = db.get_booking_times_display(booking_id) or {}
            reason = info.get("cancel_reason") or ""
            common = {
                "service_title": service_title,
                "booking_ref": _booking_ref(booking_id),
                "reason": reason,
            }
            send(c_email, "booking_cancelled", {
                **common, "audience": "customer", "recipient_name": c_name, "other_name": m_name,
                "session_time": _local_stamp(times, "customer_local", "customer_tz") or session_time,
                "cancelled_by_you": cancelled_by == "user",
                "manage_url": f"{config.FRONTEND_URL}/mentors",
            })
            send(m_email, "booking_cancelled", {
                **common, "audience": "mentor", "recipient_name": m_name, "other_name": c_name,
                "session_time": _local_stamp(times, "mentor_local", "mentor_tz") or session_time,
                "cancelled_by_you": cancelled_by == "mentor",
                "manage_url": mentor_hub,
            })
        elif event == "rescheduled":
            # BUG-088: the rescheduled email now carries the SAME detail as the booking email -
            # service, old + new time (each in the recipient's own tz), and the join link.
            times = db.get_booking_times_display(booking_id) or {}

            def _new(local_key: str, tz_key: str) -> str:
                local = times.get(local_key)
                tz = times.get(tz_key) or "UTC"
                if not local:
                    return session_time
                try:
                    dt = datetime.fromisoformat(str(local).replace("Z", ""))
                    stamp = dt.strftime("%a, %b %d, %Y at %I:%M %p").replace(" 0", " ")
                except Exception:
                    stamp = str(local)
                return f"{stamp} ({tz.split('/')[-1].replace('_', ' ')})"

            cust_new, mentor_new = _new("customer_local", "customer_tz"), _new("mentor_local", "mentor_tz")
            send(c_email, "booking_rescheduled", {
                "recipient_name": c_name, "other_name": m_name, "service_title": service_title,
                "booking_ref": _booking_ref(booking_id),
                "old_time": _fmt_iso_in_tz(old_slot, times.get("customer_tz")), "new_time": cust_new,
                "meeting_url": meeting_url, "manage_url": session_url,
            })
            send(m_email, "booking_rescheduled", {
                "recipient_name": m_name, "other_name": c_name, "service_title": service_title,
                "booking_ref": _booking_ref(booking_id),
                "old_time": _fmt_iso_in_tz(old_slot, times.get("mentor_tz")), "new_time": mentor_new,
                "meeting_url": meeting_url, "manage_url": mentor_hub,
            })
        elif event == "proposed":
            send(c_email, "reschedule_proposed", {"recipient_name": c_name, "other_name": m_name,
                                                  "service_title": service_title,
                                                  "booking_ref": _booking_ref(booking_id),
                                                  "session_time": session_time, "session_url": session_url})
        elif event == "counter_proposed":
            # Mentee asked for another date (counter-offer). Mentor must re-propose times for that day.
            send(m_email, "reschedule_counter", {"recipient_name": m_name, "other_name": c_name,
                                                 "service_title": service_title,
                                                 "booking_ref": _booking_ref(booking_id),
                                                 "session_time": session_time, "session_url": mentor_hub})
        elif event == "reschedule_requested":
            times = db.get_booking_times_display(booking_id) or {}
            send(m_email, "reschedule_requested", {
                "recipient_name": m_name, "other_name": c_name, "service_title": service_title,
                "session_time": _local_stamp(times, "mentor_local", "mentor_tz") or session_time,
                "booking_ref": _booking_ref(booking_id), "manage_url": mentor_hub,
            })
        elif event == "cancel_requested":
            times = db.get_booking_times_display(booking_id) or {}
            reason = info.get("cancel_reason") or ""
            send(m_email, "cancel_requested", {
                "recipient_name": m_name, "other_name": c_name, "service_title": service_title,
                "session_time": _local_stamp(times, "mentor_local", "mentor_tz") or session_time,
                "booking_ref": _booking_ref(booking_id), "reason": reason, "manage_url": mentor_hub,
            })
            # The requester used to get nothing back, so they had no record the request existed.
            send(c_email, "cancel_request_sent", {
                "recipient_name": c_name, "other_name": m_name, "service_title": service_title,
                "session_time": _local_stamp(times, "customer_local", "customer_tz") or session_time,
                "booking_ref": _booking_ref(booking_id), "reason": reason, "manage_url": session_url,
            })
        elif event == "no_show":
            # no_show_by names WHO failed to attend, so each side is told whether it is about them.
            times = db.get_booking_times_display(booking_id) or {}
            no_show_by = (info.get("no_show_by") or "").lower()
            common = {"service_title": service_title, "booking_ref": _booking_ref(booking_id)}
            send(c_email, "no_show_reported", {
                **common, "audience": "customer", "recipient_name": c_name, "other_name": m_name,
                "session_time": _local_stamp(times, "customer_local", "customer_tz") or session_time,
                "about_you": no_show_by == "user", "manage_url": session_url,
            })
            send(m_email, "no_show_reported", {
                **common, "audience": "mentor", "recipient_name": m_name, "other_name": c_name,
                "session_time": _local_stamp(times, "mentor_local", "mentor_tz") or session_time,
                "about_you": no_show_by == "mentor", "manage_url": mentor_hub,
            })

        # Admin/ops copy on the money-relevant lifecycle events (cancel, reschedule, no-show).
        if event in ("cancelled", "rescheduled", "no_show"):
            for admin_email in db.admin_notify_emails():
                mailer.send_transactional(admin_email, "booking_admin_notice", {
                    "event": event,
                    "booking_ref": _booking_ref(booking_id),
                    "service_title": service_title,
                    "mentor_name": info.get("mentor_name") or "",
                    "candidate_name": info.get("candidate_name") or "",
                    "candidate_email": c_email or "",
                    "session_time": session_time,
                    # Admin-only: what the customer paid and the split, so ops can act without
                    # opening the dashboard. Never included in either party's own email.
                    "invoice": db.get_booking_invoice(booking_id),
                    "reason": info.get("cancel_reason") or "",
                })
    except Exception:
        logger.warning("lifecycle email dispatch failed booking=%s event=%s", booking_id, event)
