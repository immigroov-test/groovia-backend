import logging
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.direct_booking")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


# ── Slots ──────────────────────────────────────────────────────────────────────

def get_available_slots(
    mentor_id: str,
    service_id: str,
    p_from: str,
    p_to: str,
) -> list[dict[str, Any]]:
    """Call the get_available_slots PostgreSQL RPC and return results as a list of dicts."""
    res = _supabase.rpc("get_available_slots", {
        "p_mentor_id":  mentor_id,
        "p_service_id": service_id,
        "p_from":       p_from,
        "p_to":         p_to,
    }).execute()
    if not res.data:
        return []
    # RPC may raise an exception on error; Supabase SDK converts it to a PostgRESTError.
    return [{"slot_start": r["slot_start"], "slot_end": r["slot_end"]} for r in res.data]


# ── Booking ────────────────────────────────────────────────────────────────────

def book_session(
    *,
    mentor_id: str,
    service_id: str,
    slot_time: str,
    email: str,
    name: Optional[str] = None,
    timezone: str = "UTC",
    answers: list[dict] = [],
    specific_availability_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    res = _supabase.rpc("book_session", {
        "p_mentor_id":                mentor_id,
        "p_service_id":               service_id,
        "p_slot_time":                slot_time,
        "p_email":                    email,
        "p_name":                     name,
        "p_timezone":                 timezone,
        "p_answers":                  answers,
        "p_specific_availability_id": specific_availability_id,
        "p_candidate_id":             candidate_id,
    }).execute()
    return res.data or []


def get_booking_by_idempotency_key(key: str) -> Optional[dict[str, Any]]:
    """Return an existing booking created with this idempotency key, if any - so a
    retried/duplicated request returns the same booking instead of creating another."""
    if not key:
        return None
    try:
        res = (
            _supabase.table("bookings")
            .select("id, status")
            .eq("idempotency_key", key)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception:
        logger.exception("idempotency lookup failed")
        return None


def set_booking_idempotency_key(booking_id: str, key: str) -> None:
    """Stamp a freshly-created booking with its idempotency key (best-effort)."""
    if not booking_id or not key:
        return
    try:
        _supabase.table("bookings").update({"idempotency_key": key}).eq("id", booking_id).execute()
    except Exception:
        logger.exception("idempotency stamp failed for booking %s", booking_id)


def get_booking_principals(booking_id: str) -> Optional[dict[str, Any]]:
    """Return the candidate_id and mentor_id for a booking (for auth checks)."""
    try:
        res = (
            _supabase.table("bookings")
            .select("candidate_id, mentor_id")
            .eq("id", booking_id)
            .single()
            .execute()
        )
        return res.data or None
    except Exception:
        return None


def get_booking_reschedule_target(booking_id: str) -> Optional[dict[str, Any]]:
    """mentor_id + service_id + candidate_id + current slot_time for a booking - used
    to load the reschedule slot picker for its owner."""
    try:
        res = (
            _supabase.table("bookings")
            .select("candidate_id, mentor_id, service_id, slot_time")
            .eq("id", booking_id)
            .single()
            .execute()
        )
        return res.data or None
    except Exception:
        return None


def get_booking_notify_info(booking_id: str) -> Optional[dict[str, Any]]:
    """Everything needed to email both parties about a booking lifecycle event:
    mentor name/email, candidate name/email, service title, and the slot time."""
    try:
        res = (
            _supabase.table("bookings")
            .select("mentor_id, candidate_id, candidate_email, candidate_name, slot_time, "
                    "service_id, services(title), mentors(display_name)")
            .eq("id", booking_id)
            .single()
            .execute()
        )
        b = res.data
        if not b:
            return None
        from .mentors import get_mentor_email
        mentor_name, mentor_email = get_mentor_email(b["mentor_id"])
        cand_name = b.get("candidate_name")
        cand_email = b.get("candidate_email")
        if b.get("candidate_id") and not cand_email:
            prof = (_supabase.table("profiles")
                    .select("email, display_name, full_name")
                    .eq("id", b["candidate_id"]).single().execute()).data or {}
            cand_email = cand_email or prof.get("email")
            cand_name = cand_name or prof.get("display_name") or prof.get("full_name")
        svc = b.get("services") or {}
        return {
            "mentor_name":     mentor_name,
            "mentor_email":    mentor_email,
            "candidate_name":  cand_name,
            "candidate_email": cand_email,
            "service_title":   svc.get("title") if isinstance(svc, dict) else None,
            "slot_time":       b.get("slot_time"),
        }
    except Exception:
        logger.exception("get_booking_notify_info failed booking=%s", booking_id)
        return None


def get_service_mentor_id(service_id: str) -> Optional[str]:
    """Return the mentor_id that owns a service (for ownership checks)."""
    try:
        res = _supabase.table("services").select("mentor_id").eq("id", service_id).single().execute()
        return res.data["mentor_id"] if res.data else None
    except Exception:
        return None


def get_question_mentor_id(question_id: str) -> Optional[str]:
    """Return the mentor_id that owns a question (via its parent service)."""
    try:
        res = (_supabase.table("service_questions")
               .select("service_id").eq("id", question_id).single().execute())
        if not res.data:
            return None
        return get_service_mentor_id(res.data["service_id"])
    except Exception:
        return None


def get_offer_booking_principals(offer_id: str) -> Optional[dict[str, Any]]:
    """Return {candidate_id, mentor_id} for the booking referenced by a reschedule offer."""
    try:
        res = (_supabase.table("reschedule_offers")
               .select("booking_id").eq("id", offer_id).single().execute())
        if not res.data:
            return None
        return get_booking_principals(res.data["booking_id"])
    except Exception:
        return None


def cancel_booking(booking_id: str, cancelled_by: str = "user") -> dict:
    res = _supabase.rpc("cancel_booking", {
        "p_booking_id":   booking_id,
        "p_cancelled_by": cancelled_by,
    }).execute()
    if not res.data:
        raise ValueError("Booking not found or already cancelled")
    return res.data


def get_booking_times_display(booking_id: str) -> Optional[dict]:
    res = _supabase.rpc("booking_times_display", {"p_booking_id": booking_id}).execute()
    return res.data[0] if res.data else None


# ── Reschedule negotiation ─────────────────────────────────────────────────────

def mentor_propose_reschedule(booking_id: str, offer_date: str, range_start: str, range_end: str) -> str:
    res = _supabase.rpc("mentor_propose_reschedule", {
        "p_booking_id": booking_id,
        "p_date":       offer_date,
        "p_start":      range_start,
        "p_end":        range_end,
    }).execute()
    return res.data


def mentee_accept_reschedule(offer_id: str, slot_time: str) -> dict:
    res = _supabase.rpc("mentee_accept_reschedule", {
        "p_offer_id":  offer_id,
        "p_slot_time": slot_time,
    }).execute()
    return res.data


def mentee_request_other_date(booking_id: str, requested_date: str) -> str:
    res = _supabase.rpc("mentee_request_other_date", {
        "p_booking_id": booking_id,
        "p_date":       requested_date,
    }).execute()
    return res.data


def mentor_confirm_reschedule(offer_id: str) -> dict:
    res = _supabase.rpc("mentor_confirm_reschedule", {"p_offer_id": offer_id}).execute()
    return res.data


def mentee_reject_reschedule(offer_id: str) -> dict:
    res = _supabase.rpc("mentee_reject_reschedule", {"p_offer_id": offer_id}).execute()
    return res.data


# ── Lifecycle v2: requests, customer-side reschedule, no-show ───────────────────

def respond_booking_request(request_id: str, accept: bool) -> None:
    """Mentor approves/rejects a pending cancel or reschedule request."""
    _supabase.rpc("respond_booking_request", {
        "p_request_id": request_id,
        "p_accept":     accept,
    }).execute()


def customer_reschedule(booking_id: str, slot_time: str) -> str:
    """User reschedules directly (free window, or after mentor approval).
    Returns 'rescheduled' or 'autocancelled'."""
    res = _supabase.rpc("customer_reschedule", {
        "p_booking_id": booking_id,
        "p_slot_time":  slot_time,
    }).execute()
    return res.data


def request_reschedule(booking_id: str) -> Optional[str]:
    """User asks the mentor to approve a late reschedule. Returns the request id."""
    res = _supabase.rpc("request_reschedule", {"p_booking_id": booking_id}).execute()
    return res.data


def flag_no_show(booking_id: str, no_show_party: str) -> dict:
    """Report the other party as a no-show (allowed 10 min after start)."""
    res = _supabase.rpc("flag_no_show", {
        "p_booking_id":    booking_id,
        "p_no_show_party": no_show_party,
    }).execute()
    return res.data


def resolve_mentor_no_show(booking_id: str, choice: str) -> dict:
    """User resolves a mentor no-show: 'rebook_same' | 'rebook_different' | 'refund'."""
    res = _supabase.rpc("resolve_mentor_no_show", {
        "p_booking_id": booking_id,
        "p_choice":     choice,
    }).execute()
    return res.data


def resolve_customer_no_show(booking_id: str, choice: str) -> dict:
    """Mentor resolves a user no-show: 'accept_rebook' | 'reject'."""
    res = _supabase.rpc("resolve_customer_no_show", {
        "p_booking_id": booking_id,
        "p_choice":     choice,
    }).execute()
    return res.data


def get_request_booking_principals(request_id: str) -> Optional[dict[str, Any]]:
    """Return {candidate_id, mentor_id} for the booking behind a booking_request."""
    try:
        res = (_supabase.table("booking_requests")
               .select("booking_id").eq("id", request_id).single().execute())
        if not res.data:
            return None
        return get_booking_principals(res.data["booking_id"])
    except Exception:
        return None


def list_booking_requests(booking_id: str) -> list[dict]:
    """Open + resolved cancel/reschedule requests for a booking (newest first)."""
    res = (_supabase.table("booking_requests")
           .select("id, kind, initiated_by, status, respond_by, note, created_at, resolved_at")
           .eq("booking_id", booking_id)
           .order("created_at", desc=True)
           .execute())
    return res.data or []


def mentor_confirm_attendance(booking_id: str) -> None:
    _supabase.rpc("mentor_confirm_attendance", {"p_booking_id": booking_id}).execute()


# ── Services ───────────────────────────────────────────────────────────────────

_SERVICE_COLS = ("id, title, description, type, duration, category, set_price, "
                 "set_currency, platform_fee, is_active, is_ppp, status, tags")


def list_services(mentor_id: str, active_only: bool = False) -> list[dict]:
    q = (_supabase.table("services")
         .select(_SERVICE_COLS)
         .eq("mentor_id", mentor_id))
    if active_only:
        # Public/bookable: active AND admin-approved only.
        q = q.eq("is_active", True).eq("status", "approved")
    return q.order("created_at").execute().data or []


def approve_pending_services(mentor_id: str) -> int:
    """Approve all of a mentor's pending services (called when the mentor is approved -
    the services they set up during review go live with them)."""
    try:
        res = (_supabase.table("services")
               .update({"status": "approved"})
               .eq("mentor_id", mentor_id)
               .eq("status", "pending")
               .execute())
        return len(res.data or [])
    except Exception:
        logger.exception("approve_pending_services failed for mentor %s", mentor_id)
        return 0


def list_pending_services() -> list[dict]:
    """All services awaiting admin approval, with mentor name - the admin service queue."""
    try:
        res = (_supabase.table("services")
               .select("id, title, description, type, duration, set_price, set_currency, created_at, mentor_id")
               .eq("status", "pending")
               .order("created_at", desc=True)
               .execute())
        rows = res.data or []
        ids = list({r["mentor_id"] for r in rows if r.get("mentor_id")})
        names: dict = {}
        if ids:
            m = _supabase.table("mentors").select("id, display_name, status").in_("id", ids).execute()
            names = {x["id"]: x for x in (m.data or [])}
        for r in rows:
            info = names.get(r.get("mentor_id")) or {}
            r["mentor_name"] = info.get("display_name")
            r["mentor_status"] = info.get("status")
        # Only surface services whose mentor is already approved (pre-approval services
        # ride along with the mentor's own approval, so they don't need a separate review).
        return [r for r in rows if r.get("mentor_status") == "approved"]
    except Exception:
        logger.exception("list_pending_services failed")
        return []


def set_service_status(service_id: str, status: str) -> dict:
    """Admin approve/reject a single service."""
    res = _supabase.table("services").update({"status": status}).eq("id", service_id).execute()
    if not res.data:
        raise ValueError("Service not found")
    return res.data[0]


MAX_SERVICE_TAGS = 5   # industry-standard cap for search/matching keywords


def create_service(
    *,
    mentor_id: str,
    title: str,
    description: Optional[str] = None,
    type: str = "video",
    duration: int = 30,
    category: Optional[str] = None,
    set_price: float = 0,
    is_active: bool = True,
    is_ppp: bool = False,
    tags: Optional[list[str]] = None,
) -> str:
    res = _supabase.rpc("service_create", {
        "p_mentor_id":   mentor_id,
        "p_title":       title,
        "p_description": description,
        "p_type":        type,
        "p_duration":    duration,
        "p_category":    category,
        "p_set_price":   set_price,
        "p_active":      is_active,
        "p_ppp":         is_ppp,
    }).execute()
    service_id = res.data
    # tags live on the services row directly (not a service_create param, to keep that
    # RPC's signature stable). Store the deduped, capped keyword list.
    if tags and service_id:
        clean: list[str] = []
        for t in tags:
            t = (t or "").strip()
            if t and t.lower() not in {c.lower() for c in clean}:
                clean.append(t)
        if clean:
            _supabase.table("services").update({"tags": clean[:MAX_SERVICE_TAGS]}).eq("id", service_id).execute()
    return service_id


def set_service_tags(service_id: str, tags: list[str]) -> None:
    """Replace a service's search tags (deduped, capped)."""
    clean: list[str] = []
    for t in tags or []:
        t = (t or "").strip()
        if t and t.lower() not in {c.lower() for c in clean}:
            clean.append(t)
    _supabase.table("services").update({"tags": clean[:MAX_SERVICE_TAGS]}).eq("id", service_id).execute()


def set_service_active(service_id: str, is_active: bool) -> None:
    _supabase.rpc("service_set_active", {"p_id": service_id, "p_active": is_active}).execute()


def delete_service(service_id: str) -> None:
    _supabase.rpc("service_delete", {"p_id": service_id}).execute()


# ── Questions ──────────────────────────────────────────────────────────────────

def list_questions(service_id: str) -> list[dict]:
    res = _supabase.rpc("question_list", {"p_service_id": service_id}).execute()
    return res.data or []


def add_question(service_id: str, text: str, required: bool = False, q_type: str = "text") -> str:
    res = _supabase.rpc("question_add", {
        "p_service_id": service_id,
        "p_text":       text,
        "p_required":   required,
        "p_type":       q_type,
    }).execute()
    return res.data


def delete_question(question_id: str) -> None:
    _supabase.rpc("question_remove", {"p_id": question_id}).execute()


# ── Availability management ────────────────────────────────────────────────────

def get_availability_rules(mentor_id: str) -> dict:
    res = _supabase.rpc("avail_get_rules", {"p_mentor_id": mentor_id}).execute()
    return res.data[0] if res.data else {}


def set_availability_rules(
    *,
    mentor_id: str,
    days_ahead: int,
    min_notice_hours: float,
    cancel_hours: Optional[int] = None,
) -> None:
    _supabase.rpc("avail_set_rules", {
        "p_mentor_id":         mentor_id,
        "p_days_ahead":        days_ahead,
        "p_min_notice_hours":  min_notice_hours,
        "p_cancel_hours":      cancel_hours,
    }).execute()


def list_weekly_availability(mentor_id: str) -> list[dict]:
    res = _supabase.rpc("avail_list_weekly", {"p_mentor_id": mentor_id}).execute()
    return res.data or []


def add_weekly_availability(
    *,
    mentor_id: str,
    weekday: str,
    start_time: str,
    end_time: str,
) -> None:
    _supabase.rpc("avail_add_weekly", {
        "p_mentor_id": mentor_id,
        "p_day":       weekday,
        "p_start":     start_time,
        "p_end":       end_time,
    }).execute()


def remove_weekly_availability(slot_id: str) -> None:
    _supabase.rpc("avail_remove_weekly", {"p_id": slot_id}).execute()


def block_date(*, mentor_id: str, slot_date: str) -> None:
    _supabase.rpc("avail_block_date", {
        "p_mentor_id": mentor_id,
        "p_date":      slot_date,
    }).execute()


def override_date(*, mentor_id: str, slot_date: str, start_time: str, end_time: str) -> None:
    _supabase.rpc("avail_override_date", {
        "p_mentor_id": mentor_id,
        "p_date":      slot_date,
        "p_start":     start_time,
        "p_end":       end_time,
    }).execute()


def list_specific_availability(mentor_id: str) -> list[dict]:
    res = _supabase.rpc("avail_list_specific", {"p_mentor_id": mentor_id}).execute()
    return res.data or []


def remove_specific_availability(entry_id: str) -> None:
    _supabase.rpc("avail_remove_specific", {"p_id": entry_id}).execute()


def list_mentor_sessions(mentor_id: str) -> list[dict]:
    res = _supabase.rpc("mentor_sessions", {"p_mentor_id": mentor_id}).execute()
    return res.data or []


def list_candidate_bookings(candidate_id: str) -> list[dict]:
    """All of a mentee's bookings with full lifecycle state for the manager UI."""
    res = _supabase.rpc("my_bookings", {"p_candidate_id": candidate_id}).execute()
    return res.data or []


def set_booking_phone(booking_id: str, phone: str) -> None:
    """Store the mentee's contact phone on the booking (collected at checkout)."""
    _supabase.table("bookings").update({"candidate_phone": (phone or "").strip()}).eq("id", booking_id).execute()


def set_profile_phone_if_empty(profile_id: str, phone: str) -> None:
    """Save the phone entered at booking to the mentee's profile, without ever
    overwriting a number they already set."""
    if not profile_id or not (phone or "").strip():
        return
    try:
        cur = _supabase.table("profiles").select("phone").eq("id", profile_id).limit(1).execute()
        existing = (cur.data[0].get("phone") if cur.data else None) or ""
        if not existing.strip():
            _supabase.table("profiles").update({"phone": phone.strip()}).eq("id", profile_id).execute()
    except Exception:
        logger.warning("set_profile_phone_if_empty failed for %s", profile_id)
