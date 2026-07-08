import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def list_active_mentors(
    *,
    country_code: Optional[str] = None,
    category: Optional[str] = None,
    profile_keyword: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Public mentor browse query.
    All filters are AND-ed. country_code is an ISO 3166-1 alpha-2 code (e.g. 'NL')."""
    q = (
        _supabase.table("mentors")
        .select("id, slug, display_name, headline, bio, photo_url, "
                "expertise_country_codes, expertise_categories, languages, "
                "professional_domains, booking_url, years_lived_experience")
        .eq("status", "approved")
        .eq("is_active", True)
        .limit(limit)
    )
    if country_code:
        q = q.contains("expertise_country_codes", [country_code.upper()])
    if category:
        q = q.contains("expertise_categories", [category])
    if profile_keyword:
        q = q.ilike("headline", f"%{profile_keyword}%")
    return q.execute().data or []


def list_mentors_grouped_by_country(limit_per_country: int = 2) -> dict[str, list[dict[str, Any]]]:
    """Return all approved+active mentors, grouped by every ISO-2 country in their expertise.
    Used by the report flow so the LLM gets real mentor data in-prompt and never has to
    call retrieve_matching_mentors per country.
    Default is 2 - the report ends with a Mentor Directory link so users can browse more."""
    rows = (
        _supabase.table("mentors")
        .select("display_name, headline, slug, expertise_country_codes")
        .eq("status", "approved")
        .eq("is_active", True)
        .execute()
        .data or []
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        url = f"{config.FRONTEND_URL}/mentors/{r['slug']}"
        for code in (r.get("expertise_country_codes") or []):
            bucket = grouped.setdefault(code, [])
            if len(bucket) < limit_per_country:
                bucket.append({
                    "name": r["display_name"],
                    "headline": r.get("headline") or "",
                    "booking_url": url,
                })
    return grouped


def mentors_available_for_country(country_code: str) -> bool:
    """Cheap existence check - does the mentors table have any approved+active mentor
    whose expertise covers this ISO-2 country code?"""
    if not country_code:
        return False
    res = (
        _supabase.table("mentors")
        .select("id")
        .eq("status", "approved")
        .eq("is_active", True)
        .contains("expertise_country_codes", [country_code.upper()])
        .limit(1)
        .execute()
    )
    return bool(res.data)


def get_mentor_by_slug(slug: str) -> Optional[dict[str, Any]]:
    res = (
        _supabase.table("mentors")
        .select("*")
        .eq("slug", slug)
        .eq("status", "approved")
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_mentor_by_profile_id(profile_id: str) -> Optional[dict[str, Any]]:
    """Resolve the mentor row linked to a logged-in user's profile, if any."""
    if not profile_id:
        return None
    res = (
        _supabase.table("mentors")
        .select("id, slug, display_name, status, session_duration_minutes, rejection_reason")
        .eq("profile_id", profile_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def link_mentor_by_email(profile_id: str, email: str) -> Optional[dict[str, Any]]:
    """Link a pre-approved mentor (created by an admin, matched by email, not yet
    attached to any account) to this user's profile, and grant the mentor role.
    Idempotent: if the user is already a mentor, returns that row. Returns None when
    there is nothing to link. Safe by design - email ownership is proven by the login."""
    if not profile_id or not email:
        return None
    email = email.strip().lower()
    try:
        already = (
            _supabase.table("mentors")
            .select("id, slug, display_name, status, profile_id")
            .eq("profile_id", profile_id)
            .limit(1)
            .execute()
        )
        if already.data:
            return already.data[0]

        candidates = (
            _supabase.table("mentors")
            .select("id, slug, display_name, status, profile_id")
            .ilike("email", email)
            .execute()
        )
        mentor = next((m for m in (candidates.data or []) if not m.get("profile_id")), None)
        if not mentor:
            return None

        _supabase.table("mentors").update({"profile_id": profile_id}).eq("id", mentor["id"]).execute()
        _supabase.table("profiles").update({"role": "mentor"}).eq("id", profile_id).execute()
        mentor["profile_id"] = profile_id
        logger.info("Linked pre-approved mentor %s to profile %s by email match", mentor["id"], profile_id)
        return mentor
    except Exception:
        logger.exception("link_mentor_by_email failed (profile=%s)", profile_id)
        return None


def get_mentor_by_id(mentor_id: str) -> Optional[dict[str, Any]]:
    """Fetch a mentor row by primary key - used internally (never exposed to browser)."""
    if not mentor_id:
        return None
    res = (
        _supabase.table("mentors")
        .select("id, slug, display_name, status, session_duration_minutes, timezone")
        .eq("id", mentor_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def create_mentor_signup(
    profile_id: str,
    *,
    display_name: str,
    headline: Optional[str],
    timezone_name: str,
    expertise_country_codes: list[str] | None = None,
    languages: list[str] | None = None,
    professional_domains: list[str] | None = None,
    years_lived_experience: Optional[int] = None,
    bio: Optional[str] = None,
    photo_url: Optional[str] = None,
    phone: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    social_links: list[dict[str, str]] | None = None,
    public_notes: Optional[str] = None,
    session_duration_minutes: int = 60,
) -> dict[str, Any]:
    """Creates a new mentor row for a self-service signup, pending admin review."""
    base_slug = _SLUG_RE.sub("-", display_name.lower()).strip("-") or "mentor"
    slug = base_slug
    for _ in range(5):
        exists = _supabase.table("mentors").select("id").eq("slug", slug).limit(1).execute()
        if not exists.data:
            break
        slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"

    res = _supabase.table("mentors").insert({
        "profile_id": profile_id,
        "slug": slug,
        "display_name": display_name,
        "headline": headline,
        "timezone": timezone_name,
        "status": "pending_review",
        "submission_count": 1,
        "expertise_country_codes": expertise_country_codes or [],
        "languages": languages or [],
        "professional_domains": professional_domains or [],
        "years_lived_experience": years_lived_experience,
        "bio": bio,
        "photo_url": photo_url,
        "phone": phone,
        "city": city,
        "country": country,
        "social_links": social_links or [],
        "public_notes": public_notes,
        "session_duration_minutes": session_duration_minutes,
    }).execute()

    if not res.data:
        raise RuntimeError("Mentor insert returned no data")
    mentor_row = res.data[0]

    try:
        _supabase.table("profiles").update({"role": "mentor"}).eq("id", profile_id).execute()
    except Exception:
        _supabase.table("mentors").delete().eq("id", mentor_row["id"]).execute()
        logger.exception("Profile role update failed; mentor row rolled back for profile %s", profile_id)
        raise RuntimeError("Failed to update profile role")

    try:
        _supabase.table("consent_log").insert({
            "user_id": profile_id,
            "consent_type": "mentor_agreement",
            "version": "v1",
        }).execute()
    except Exception:
        logger.warning("Consent log insert failed for profile %s (non-fatal)", profile_id)

    return mentor_row


# Non-critical: presentation-only fields - no re-approval needed.
_NON_CRITICAL_MENTOR_FIELDS = {
    "display_name", "headline", "bio", "photo_url",
    "phone", "city", "country", "social_links", "public_notes",
    "languages", "timezone", "session_duration_minutes",
}

# Critical: expertise claims - changes flip status back to pending_review.
_CRITICAL_MENTOR_FIELDS = {
    "expertise_country_codes", "years_lived_experience", "professional_domains",
}


def update_mentor_profile(mentor_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Update non-critical mentor fields. No re-approval triggered."""
    safe = {k: v for k, v in fields.items() if k in _NON_CRITICAL_MENTOR_FIELDS}
    if not safe:
        raise ValueError("No valid non-critical fields to update")
    safe["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _supabase.table("mentors").update(safe).eq("id", mentor_id).execute()
    if not res.data:
        raise ValueError(f"Mentor {mentor_id!r} not found")
    return res.data[0]


def update_mentor_critical_fields(mentor_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Update expertise/credibility fields - flips status to pending_review and increments submission_count."""
    safe = {k: v for k, v in fields.items() if k in _CRITICAL_MENTOR_FIELDS}
    if not safe:
        raise ValueError("No valid critical fields to update")
    safe["status"] = "pending_review"
    safe["rejection_reason"] = None   # a fresh re-application clears the old reviewer note
    safe["updated_at"] = datetime.now(timezone.utc).isoformat()
    current = (
        _supabase.table("mentors").select("submission_count").eq("id", mentor_id).limit(1).execute()
    )
    count = (current.data[0].get("submission_count") or 1) if current.data else 1
    safe["submission_count"] = count + 1
    res = _supabase.table("mentors").update(safe).eq("id", mentor_id).execute()
    if not res.data:
        raise ValueError(f"Mentor {mentor_id!r} not found")
    return res.data[0]


def get_mentor_availability(mentor_id: str) -> list[dict[str, Any]]:
    """Return all weekly availability slots for a mentor, ordered by day and time."""
    res = (
        _supabase.table("mentor_availability")
        .select("id, day_of_week, start_time, end_time")
        .eq("mentor_id", mentor_id)
        .order("day_of_week")
        .order("start_time")
        .execute()
    )
    return res.data or []


def set_mentor_availability(
    mentor_id: str,
    slots: list[dict[str, Any]],
    session_duration_minutes: int,
    availability_type: str = "manual",
) -> list[dict[str, Any]]:
    """Replace a mentor's weekly availability slots atomically and update their session duration."""
    _supabase.table("mentor_availability").delete().eq("mentor_id", mentor_id).execute()
    inserted: list[dict[str, Any]] = []
    if slots:
        rows = [
            {
                "mentor_id": mentor_id,
                "day_of_week": s["day_of_week"],
                "start_time": s["start_time"],
                "end_time": s["end_time"],
            }
            for s in slots
        ]
        res = _supabase.table("mentor_availability").insert(rows).execute()
        inserted = res.data or []
    _supabase.table("mentors").update({
        "session_duration_minutes": session_duration_minutes,
        "availability_type": availability_type,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", mentor_id).execute()
    return inserted


def list_mentors_by_status(status: str, limit: int = 100) -> list[dict[str, Any]]:
    """Return mentor rows with the given status, enriched with profile email/full_name."""
    rows = (
        _supabase.table("mentors")
        .select("id, slug, display_name, headline, timezone, status, submission_count, created_at, profile_id")
        .eq("status", status)
        .order("created_at")
        .limit(limit)
        .execute()
        .data or []
    )
    profile_ids = [r["profile_id"] for r in rows if r.get("profile_id")]
    if profile_ids:
        profiles = (
            _supabase.table("profiles")
            .select("id, email, full_name")
            .in_("id", profile_ids)
            .execute()
            .data or []
        )
        profile_map = {p["id"]: p for p in profiles}
        for row in rows:
            p = profile_map.get(row.get("profile_id") or "")
            row["email"] = p["email"] if p else None
            row["full_name"] = p["full_name"] if p else None
    return rows


def set_mentor_status(mentor_id: str, status: str, reason: Optional[str] = None) -> dict[str, Any]:
    """Update a mentor's status and return the updated row. is_active is synced here.
    On reject, stores `reason` (shown to the mentor); clears it on any other status."""
    payload: dict[str, Any] = {
        "status": status,
        "is_active": (status == "approved"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload["rejection_reason"] = (reason or "").strip() or None if status == "rejected" else None
    res = (
        _supabase.table("mentors")
        .update(payload)
        .eq("id", mentor_id)
        .execute()
    )
    if not res.data:
        raise ValueError(f"Mentor {mentor_id!r} not found")
    return res.data[0]


def get_profile_id_by_email(email: str) -> Optional[str]:
    if not email:
        return None
    try:
        res = (
            _supabase.table("profiles")
            .select("id")
            .eq("email", email.lower())
            .is_("deleted_at", "null")  # a soft-deleted account must not count as existing
            .limit(1)
            .execute()
        )
        return res.data[0]["id"] if res.data else None
    except Exception:
        logger.exception("Profile lookup by email failed")
        return None


def set_profile_role_guest(profile_id: str) -> None:
    """Mark a just-verified passwordless account as a guest - only if still a plain
    candidate (never downgrade a mentor/admin)."""
    if not profile_id:
        return
    try:
        _supabase.table("profiles").update({"role": "guest"}).eq("id", profile_id).eq("role", "candidate").execute()
    except Exception:
        logger.exception("set_profile_role_guest failed for %s", profile_id)


def get_email_account_status(email: str) -> dict:
    """Return {exists, has_password} for an email by inspecting auth.users. Lets the
    popup route unconfirmed / passwordless accounts (signInWithOtp creates the row with
    no password) to verify+setup instead of the password login screen."""
    email = (email or "").strip().lower()
    if not email:
        return {"exists": False, "has_password": False}
    try:
        res = _supabase.rpc("email_account_status", {"p_email": email}).execute()
        if not res.data:
            return {"exists": False, "has_password": False}
        return {"exists": True, "has_password": bool(res.data[0].get("has_password"))}
    except Exception:
        logger.exception("email_account_status RPC failed; falling back to profiles")
        pid = get_profile_id_by_email(email)
        return {"exists": pid is not None, "has_password": pid is not None}


def get_profile_role(profile_id: str) -> Optional[str]:
    """Return the app-level role for a profile (candidate|mentor|admin), or None if not found."""
    if not profile_id:
        return None
    res = (
        _supabase.table("profiles")
        .select("role")
        .eq("id", profile_id)
        .limit(1)
        .execute()
    )
    return res.data[0]["role"] if res.data else None


def save_profile_summary_if_empty(user_id: str, summary: str) -> None:
    """Persist the agent-extracted resume summary to the user's profile, but only
    when the field is still NULL - a manual edit on the account page wins."""
    if not summary:
        return
    try:
        (
            _supabase.table("profiles")
            .update({"profile_summary": summary[:2000]})
            .eq("id", user_id)
            .is_("profile_summary", "null")
            .execute()
        )
    except Exception:
        logger.exception("Failed to save profile summary for %s", user_id)


def get_mentor_email(mentor_id: str) -> tuple[Optional[str], Optional[str]]:
    """Return (display_name, email) for a mentor, joining through profiles.
    Used by admin endpoints to address transactional emails after status changes."""
    res = (
        _supabase.table("mentors")
        .select("display_name, profile_id, email")
        .eq("id", mentor_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None, None
    row = res.data[0]
    display_name: Optional[str] = row.get("display_name")
    profile_id: Optional[str] = row.get("profile_id")
    fallback_email: Optional[str] = row.get("email")  # seed/dummy mentors have no profile
    if not profile_id:
        return display_name, fallback_email
    res2 = (
        _supabase.table("profiles")
        .select("email")
        .eq("id", profile_id)
        .limit(1)
        .execute()
    )
    email: Optional[str] = res2.data[0].get("email") if res2.data else None
    return display_name, email or fallback_email


def get_booking_candidate_info(external_id: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (candidate_email, candidate_name, mentor_display_name) for a booking.
    Used to send the post-session review request after MEETING_ENDED."""
    res = (
        _supabase.table("bookings")
        .select("candidate_email, candidate_name, mentor_id")
        .eq("external_id", external_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None, None, None
    row = res.data[0]
    candidate_email: Optional[str] = row.get("candidate_email")
    candidate_name: Optional[str] = row.get("candidate_name")
    mentor_id: Optional[str] = row.get("mentor_id")
    mentor_name: Optional[str] = None
    if mentor_id:
        m = (
            _supabase.table("mentors")
            .select("display_name")
            .eq("id", mentor_id)
            .limit(1)
            .execute()
        )
        mentor_name = m.data[0].get("display_name") if m.data else None
    return candidate_email, candidate_name, mentor_name


def get_mentor_full_details(mentor_id: str) -> Optional[dict[str, Any]]:
    """All mentor fields + profile email + availability slots. Used by admin review."""
    res = (
        _supabase.table("mentors")
        .select("*")
        .eq("id", mentor_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    mentor = res.data[0]
    if mentor.get("profile_id"):
        p = (
            _supabase.table("profiles")
            .select("email, full_name")
            .eq("id", mentor["profile_id"])
            .limit(1)
            .execute()
        )
        if p.data:
            mentor["email"] = p.data[0].get("email")
            mentor["full_name"] = p.data[0].get("full_name")
    mentor["availability_slots"] = get_mentor_availability(mentor_id)
    return mentor


def get_admin_stats() -> dict[str, int]:
    """Return platform-level counters for the admin overview card."""
    try:
        pending = _supabase.table("mentors").select("id", count="exact").eq("status", "pending_review").execute()
        approved = _supabase.table("mentors").select("id", count="exact").eq("status", "approved").execute()
        bookings = _supabase.table("bookings").select("id", count="exact").execute()
        return {
            "pending_mentor_count": pending.count or 0,
            "approved_mentor_count": approved.count or 0,
            "total_bookings": bookings.count or 0,
        }
    except Exception:
        logger.exception("Failed to fetch admin stats")
        return {"pending_mentor_count": 0, "approved_mentor_count": 0, "total_bookings": 0}


# ── Admin: booking oversight + no-show ops ──────────────────────────────────

def list_all_bookings(
    status: Optional[str] = None,
    mentor_id: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """All bookings for the admin oversight table (newest first) with the mentor name
    attached. Optional filters: status, mentor, and a search over candidate email/name."""
    try:
        query = (
            _supabase.table("bookings")
            .select("id, status, slot_time, candidate_name, candidate_email, "
                    "reschedule_count, no_show_by, created_at, mentor_id")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status:
            query = query.eq("status", status)
        if mentor_id:
            query = query.eq("mentor_id", mentor_id)
        if q:
            safe = re.sub(r"[(),%*]", "", q).strip()
            if safe:
                query = query.or_(f"candidate_email.ilike.*{safe}*,candidate_name.ilike.*{safe}*")
        rows = query.execute().data or []
        ids = list({r["mentor_id"] for r in rows if r.get("mentor_id")})
        names: dict[str, Any] = {}
        if ids:
            mres = _supabase.table("mentors").select("id, display_name").in_("id", ids).execute()
            names = {m["id"]: m.get("display_name") for m in (mres.data or [])}
        for r in rows:
            r["mentor_name"] = names.get(r.get("mentor_id"))
        return rows
    except Exception:
        logger.exception("list_all_bookings failed")
        return []


def get_booking_admin_detail(booking_id: str) -> Optional[dict[str, Any]]:
    """One booking + mentor name + its request/offer history for the admin detail view."""
    try:
        bres = _supabase.table("bookings").select("*").eq("id", booking_id).limit(1).execute()
        if not bres.data:
            return None
        b = bres.data[0]
        if b.get("mentor_id"):
            m = _supabase.table("mentors").select("display_name, email").eq("id", b["mentor_id"]).limit(1).execute()
            if m.data:
                b["mentor_name"] = m.data[0].get("display_name")
                b["mentor_email"] = m.data[0].get("email")
        for tbl, key in (("booking_requests", "requests"), ("reschedule_offers", "offers")):
            try:
                r = _supabase.table(tbl).select("*").eq("booking_id", booking_id).order("created_at").execute()
                b[key] = r.data or []
            except Exception:
                b[key] = []
        return b
    except Exception:
        logger.exception("get_booking_admin_detail failed")
        return None


def list_mentors_with_strikes() -> list[dict[str, Any]]:
    """Mentors who have accrued no-show strikes - the admin ops queue."""
    try:
        res = (
            _supabase.table("mentors")
            .select("id, display_name, slug, status, no_show_strikes, last_no_show_at")
            .gt("no_show_strikes", 0)
            .order("no_show_strikes", desc=True)
            .execute()
        )
        return res.data or []
    except Exception:
        logger.exception("list_mentors_with_strikes failed")
        return []


def reset_mentor_strikes(mentor_id: str) -> dict[str, Any]:
    """Admin clears a mentor's no-show strikes (a dispute resolved in the mentor's favour)."""
    res = _supabase.table("mentors").update({"no_show_strikes": 0}).eq("id", mentor_id).execute()
    if not res.data:
        raise ValueError("Mentor not found")
    return {"id": mentor_id, "no_show_strikes": 0}


def upsert_ai_event(
    *,
    thread_id: Optional[str],
    intent: Optional[str],
    revision_count: int,
    tool_calls: int,
    latency_ms: int,
    model: str,
    quality_failure: bool,
) -> None:
    """Append one row to ai_events for offline LLM quality analysis."""
    try:
        _supabase.table("ai_events").insert({
            "thread_id": thread_id,
            "intent": intent,
            "revision_count": revision_count,
            "tool_calls": tool_calls,
            "latency_ms": latency_ms,
            "model": model,
            "quality_failure": quality_failure,
        }).execute()
    except Exception:
        logger.exception("Failed to log ai_event (thread=%s)", thread_id)


