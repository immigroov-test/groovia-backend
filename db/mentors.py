import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def list_admin_emails() -> list[str]:
    """Emails of all admin accounts (profiles.role = 'admin', not soft-deleted)."""
    try:
        res = _supabase.table("profiles").select("email, deleted_at").eq("role", "admin").execute()
        return [r["email"] for r in (res.data or []) if r.get("email") and not r.get("deleted_at")]
    except Exception:
        logger.exception("list_admin_emails failed")
        return []


def admin_notify_emails() -> list[str]:
    """Recipients for admin/ops notifications: every admin account in the DB, plus the
    optional ADMIN_EMAIL env override. Deduped case-insensitively, so admins are driven
    by profiles.role='admin' and no Render env var is required."""
    emails = list_admin_emails()
    if config.ADMIN_EMAIL:
        emails.append(config.ADMIN_EMAIL)
    seen: set[str] = set()
    out: list[str] = []
    for e in emails:
        k = (e or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(e.strip())
    return out


def list_active_mentors(
    *,
    country_code: Optional[str] = None,
    category: Optional[str] = None,
    profile_keyword: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Public mentor browse query. Also backs the agent's mentor lookup.
    All filters are AND-ed. country_code is an ISO 3166-1 alpha-2 code (e.g. 'NL')."""
    base_cols = ("id, slug, display_name, headline, bio, photo_url, "
                 "expertise_country_codes, expertise_categories, specializations, languages, "
                 "professional_domains, booking_url, years_lived_experience, "
                 "years_professional_experience, home_country_code, "
                 "avg_rating, review_count, currency, city, country, smart_pricing, ")

    # Fetch more rows than the caller's display `limit`, because the serviceless filter below
    # drops some: if we capped the DB read at `limit`, a page could shrink well under `limit`
    # and hide later mentors entirely (the "I don't see Vinoth" bug). The table is small
    # (tens-to-low-hundreds), so over-fetching a fixed buffer is cheap. Revisit with a
    # has-active-service flag + keyset pagination if the roster ever grows into the thousands.
    fetch_cap = limit + 100

    def build(svc_cols: str):
        q = (
            _supabase.table("mentors")
            .select(base_cols + f"services({svc_cols})")
            .eq("status", "approved")
            .eq("is_active", True)
            .limit(fetch_cap)
        )
        if country_code:
            q = q.contains("expertise_country_codes", [country_code.upper()])
        if category:
            q = q.contains("expertise_categories", [category])
        if profile_keyword:
            # Match the mentor's name OR headline (commas would break PostgREST's or() syntax).
            kw = profile_keyword.replace(",", " ").strip()
            q = q.or_(f"display_name.ilike.%{kw}%,headline.ilike.%{kw}%")
        return q.execute().data or []

    # services.status isn't present in every environment; fall back without it so the
    # query (and the agent's mentor lookup) never crashes on a missing column.
    try:
        rows = build("id, set_price, set_currency, is_active, status, category")
        has_status = True
    except Exception:
        logger.warning("list_active_mentors: services.status missing; retrying without it")
        rows = build("id, set_price, set_currency, is_active, category")
        has_status = False

    # BUG-057 (+ Go-Live retest follow-up): services can legitimately sit in different currencies (a
    # migrated mentor's per-service currency comes from legacy data; see scripts/migrate_mentors.py),
    # so comparing raw set_price numbers picks whichever currency happens to have the smallest face
    # value - e.g. AUD 45 < INR 2699 by raw number, even though INR 2699 (~USD 28) is actually
    # cheaper than AUD 45 (~USD 32) once converted. Normalize every paid service to a common pivot
    # (USD) via the EUR-pivot fx_rates table before comparing; the DISPLAYED price still uses the
    # winning service's own set_price/set_currency, only the comparison is normalized.
    try:
        fx_rows = _supabase.table("fx_rates").select("quote, rate").eq("base", "EUR").execute().data or []
        fx_eur = {row["quote"].upper(): float(row["rate"]) for row in fx_rows if row.get("rate")}
    except Exception:
        logger.exception("list_active_mentors: failed to load fx_rates for price normalization")
        fx_eur = {}

    _unnormalizable_currencies_warned: set[str] = set()

    def _normalize_to_usd(amount: float, currency: str) -> Optional[float]:
        """USD-equivalent of `amount` (in `currency`), for MINIMUM-PRICE COMPARISON only - never for
        the displayed price, which always uses the winning service's own raw price/currency.

        Returns None when the currency can't be reliably normalized. Callers MUST exclude such
        services from the minimum comparison rather than falling back to comparing raw, unconverted
        face values - silently treating an unconverted amount as if it were USD is exactly the bug
        this normalization exists to prevent (AUD 45 read as "45", beating INR 2699 read as "2699").
        fx_rates is EUR-pivoted and may have no explicit EUR self-row, so EUR is handled explicitly
        (1:1 to itself) rather than falling through to a "rate not found" case."""
        ccy = (currency or "").upper()
        if not ccy:
            return None
        if ccy == "USD":
            return amount
        usd_per_eur = fx_eur.get("USD")
        if not usd_per_eur:
            if "USD" not in _unnormalizable_currencies_warned:
                _unnormalizable_currencies_warned.add("USD")
                logger.warning("list_active_mentors: no EUR->USD fx_rate available; cannot normalize any non-USD price")
            return None
        if ccy == "EUR":
            return amount * usd_per_eur
        rate = fx_eur.get(ccy)
        if not rate:
            if ccy not in _unnormalizable_currencies_warned:
                _unnormalizable_currencies_warned.add(ccy)
                logger.warning("list_active_mentors: no EUR->%s fx_rate available; can't normalize services priced in %s", ccy, ccy)
            return None
        return (amount / rate) * usd_per_eur

    # Collapse the mentor's bookable services into a single "starting from" price for the
    # card (cheapest active, admin-approved when status exists), then drop the raw list.
    # A mentor with no bookable service is a dead-end profile (nothing a mentee can book),
    # so hide them from browse - this is what the legacy portal did, and it drops the
    # migrated-but-serviceless mentors (Jasna, etc.) rather than showing empty profiles.
    out: list[dict[str, Any]] = []
    for r in rows:
        svcs = [s for s in (r.pop("services", None) or [])
                if s.get("is_active")
                and (not has_status or s.get("status") == "approved")
                and s.get("set_price") is not None]
        if not svcs:
            continue
        # "Starting from" = cheapest PAID session; a free intro session must never become the headline
        # price (it would always win as the minimum). Track a free session separately so the card can
        # show it as a small badge below the price.
        paid = [s for s in svcs if float(s["set_price"]) > 0]
        r["has_free_session"] = any(float(s["set_price"]) == 0 for s in svcs)
        cheapest: Optional[dict[str, Any]] = svcs[0] if svcs else None
        if paid:
            # Only compare services whose currency actually normalized (see _normalize_to_usd) - an
            # un-normalizable one is excluded from the comparison, never treated as its raw face
            # value. Tie-break on service id so equal normalized prices are deterministic and never
            # depend on database/array ordering.
            comparable = [
                (s, usd) for s in paid
                if (usd := _normalize_to_usd(float(s["set_price"]), s.get("set_currency"))) is not None
            ]
            if comparable:
                cheapest = min(comparable, key=lambda pair: (pair[1], pair[0]["id"]))[0]
            else:
                # Every paid service failed to normalize (e.g. a total fx_rates outage) - there is no
                # currency-safe way to pick a "cheapest" service right now. Fall back to a stable,
                # price-blind tie-break (service id) rather than ever comparing raw face values.
                logger.error(
                    "list_active_mentors: mentor=%s could not normalize ANY paid service's currency; "
                    "falling back to a non-price tie-break", r.get("id"),
                )
                cheapest = min(paid, key=lambda s: s["id"])
        r["min_price"] = float(cheapest["set_price"]) if paid and cheapest else 0.0
        r["price_currency"] = (cheapest.get("set_currency") if cheapest else None) or r.get("currency") or "USD"
        # The cheapest PAID service's id, so the card can localize its price through the SAME engine
        # as checkout (display_service_prices) and never show a figure that jumps at checkout (BUG-077).
        r["min_price_service_id"] = cheapest.get("id") if paid and cheapest else None
        # What the mentor actually helps with = the categories of the sessions they configured. This
        # is the user-facing filter facet (not the self-declared expertise_categories), so browse
        # filters/tags reflect what's really bookable.
        svc_cats: list[str] = []
        for s in svcs:
            c = (s.get("category") or "").strip()
            if c and c not in svc_cats:
                svc_cats.append(c)
        r["service_categories"] = svc_cats
        out.append(r)

    # Browse ordering (standard marketplace): mentors who are actually bookable RIGHT NOW (have
    # weekly availability set) come first, then by rating, then by review count. A mentor with a
    # service but no availability can't be booked, so they sink to the bottom instead of the top.
    try:
        avail = _supabase.table("weekly_availability").select("mentor_id").eq("is_active", True).execute().data or []
        avail_ids = {a["mentor_id"] for a in avail}
    except Exception:
        avail_ids = set()
    out.sort(key=lambda r: (r["id"] in avail_ids, float(r.get("avg_rating") or 0), int(r.get("review_count") or 0)), reverse=True)
    return out[:limit]


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


def list_supported_countries() -> list[str]:
    """Distinct ISO-2 country codes that have at least one approved, active mentor.
    Powers the 'find a mentor' country dropdown so it only lists countries we actually
    cover; it auto-expands as mentors join from new countries."""
    rows = (
        _supabase.table("mentors")
        .select("expertise_country_codes")
        .eq("status", "approved")
        .eq("is_active", True)
        .execute()
        .data or []
    )
    codes: set[str] = set()
    for r in rows:
        for code in (r.get("expertise_country_codes") or []):
            if code:
                codes.add(code)
    return sorted(codes)


def list_mentor_facets() -> dict[str, Any]:
    """Facets for the find-a-mentor dropdowns: the distinct expertise categories (topics)
    and country codes across approved+active mentors, plus the countries available *per
    category*. Lets the two dropdowns be dependent (pick a topic, only its countries show)
    the way big marketplaces do faceted filtering, and both auto-expand as mentors join
    from new topics/countries. One query, computed in memory."""
    rows = (
        _supabase.table("mentors")
        .select("expertise_categories, expertise_country_codes")
        .eq("status", "approved")
        .eq("is_active", True)
        .execute()
        .data or []
    )
    categories: set[str] = set()
    countries: set[str] = set()
    by_category: dict[str, set[str]] = {}
    for r in rows:
        cats = [c for c in (r.get("expertise_categories") or []) if c]
        ccs = [c for c in (r.get("expertise_country_codes") or []) if c]
        countries.update(ccs)
        for c in cats:
            categories.add(c)
            by_category.setdefault(c, set()).update(ccs)
    return {
        "categories": sorted(categories),
        "countries": sorted(countries),
        "by_category": {k: sorted(v) for k, v in by_category.items()},
    }


def list_legacy_sessions(mentor_id: str) -> list[dict[str, Any]]:
    """Read-only session history imported from the old portal (customer name only, no email)."""
    if not mentor_id:
        return []
    try:
        return _supabase.rpc("mentor_legacy_sessions", {"p_mentor_id": mentor_id}).execute().data or []
    except Exception:
        logger.warning("list_legacy_sessions failed for %s", mentor_id)
        return []


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
        .select("id, slug, display_name, status, session_duration_minutes, rejection_reason, "
                "headline, bio, photo_url, phone, country, home_country_code, served_countries, "
                "city, timezone, languages, social_links, "
                # specializations is edited on the profile form now, so it must be fetched or a save
                # sends [] and wipes it - the same trap served_countries was in (BUG-110/128).
                "expertise_country_codes, expertise_categories, professional_domains, specializations, "
                "years_lived_experience, years_professional_experience, public_notes, submission_count, "
                "hourly_rate, currency, currency_rates, smart_pricing, needs_onboarding, "
                "pending_changes, pending_submitted_at")
        .eq("profile_id", profile_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def backfill_profile_name(profile_id: str, full_name: Optional[str] = None) -> None:
    """Fill profiles.full_name/display_name when they're empty. The signup trigger
    creates the profile with a null name (magic-link OTP carries no name), and the
    name is only entered later during password setup - which updates auth metadata
    but not the profiles row. This closes that gap. Uses the passed name, or falls
    back to the auth user's metadata. Never overwrites an existing name."""
    if not profile_id:
        return
    name = (full_name or "").strip()
    if not name:
        try:
            resp = _supabase.auth.admin.get_user_by_id(profile_id)
            meta = (resp.user.user_metadata or {}) if resp and resp.user else {}
            name = (meta.get("full_name") or meta.get("name") or "").strip()
        except Exception:
            logger.warning("backfill_profile_name: could not read auth metadata for %s", profile_id)
    if not name:
        return
    try:
        cur = _supabase.table("profiles").select("full_name, display_name").eq("id", profile_id).limit(1).execute()
        row = cur.data[0] if cur.data else {}
        updates: dict[str, Any] = {}
        if not (row.get("full_name") or "").strip():
            updates["full_name"] = name
        if not (row.get("display_name") or "").strip():
            updates["display_name"] = name
        if updates:
            _supabase.table("profiles").update(updates).eq("id", profile_id).execute()
    except Exception:
        logger.warning("backfill_profile_name: update failed for %s", profile_id)


def link_guest_bookings(profile_id: str, email: Optional[str]) -> int:
    """Attach a signed-in account to the guest bookings it made before signing up
    (same email, candidate_id still null) so they appear under My sessions."""
    if not profile_id or not email:
        return 0
    try:
        res = (
            _supabase.table("bookings")
            .update({"candidate_id": profile_id})
            .is_("candidate_id", "null")
            .eq("candidate_email", email.strip().lower())
            .execute()
        )
        return len(res.data or [])
    except Exception:
        logger.warning("link_guest_bookings failed for %s", profile_id)
        return 0


def set_mentor_smart_pricing(mentor_id: str, enabled: bool) -> None:
    """Flip the mentor's smart (PPP) pricing and re-sync is_ppp across all their
    services, so the mentor-level flag is the single source of truth for PPP."""
    _supabase.table("mentors").update({"smart_pricing": enabled}).eq("id", mentor_id).execute()
    _sync_services_ppp(mentor_id, {"smart_pricing": enabled})


def _sync_services_ppp(mentor_id: str, written_fields: dict[str, Any]) -> None:
    """compute_booking_price/display_service_prices key off the per-service is_ppp column, not
    mentors.smart_pricing directly - any live write of smart_pricing must re-sync is_ppp across the
    mentor's services too, or PPP silently keeps not applying (the Pricing-tab toggle used to update
    the mentor flag but never touch existing services)."""
    if "smart_pricing" not in written_fields:
        return
    _supabase.table("services").update(
        {"is_ppp": bool(written_fields["smart_pricing"])}
    ).eq("mentor_id", mentor_id).execute()


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
            .select("id, slug, display_name, status, profile_id, needs_onboarding")
            .eq("profile_id", profile_id)
            .limit(1)
            .execute()
        )
        if already.data:
            return already.data[0]

        candidates = (
            _supabase.table("mentors")
            .select("id, slug, display_name, status, profile_id, needs_onboarding")
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
    expertise_categories: list[str] | None = None,
    languages: list[str] | None = None,
    professional_domains: list[str] | None = None,
    specializations: list[str] | None = None,
    years_lived_experience: Optional[int] = None,
    years_professional_experience: Optional[int] = None,
    bio: Optional[str] = None,
    photo_url: Optional[str] = None,
    phone: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    home_country_code: Optional[str] = None,
    served_countries: Optional[list[dict]] = None,
    social_links: list[dict[str, str]] | None = None,
    public_notes: Optional[str] = None,
    hourly_rate: Optional[float] = None,
    currency: str = "USD",
    currency_rates: Optional[list[dict]] = None,
    smart_pricing: bool = False,
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
        "expertise_categories": expertise_categories or [],
        "languages": languages or [],
        "professional_domains": professional_domains or [],
        "specializations": specializations or [],
        "years_lived_experience": years_lived_experience,
        "years_professional_experience": years_professional_experience,
        "bio": bio,
        "photo_url": photo_url,
        "phone": phone,
        "city": city,
        "country": country,
        "home_country_code": (home_country_code or "").upper() or None,
        "served_countries": served_countries or [],
        "social_links": social_links or [],
        "public_notes": public_notes,
        "hourly_rate": hourly_rate,
        "currency": (currency or "USD").upper(),
        "currency_rates": currency_rates or [],
        "smart_pricing": smart_pricing,
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
    "phone", "city", "country", "home_country_code", "social_links", "public_notes",
    "languages", "timezone", "session_duration_minutes",
}

# Critical: expertise claims - changes flip status back to pending_review.
_CRITICAL_MENTOR_FIELDS = {
    "expertise_country_codes", "years_lived_experience", "years_professional_experience", "professional_domains",
}

# Every profile field a mentor may edit from the dashboard (Phase 2 unified model).
# Availability, services and booking rules are handled by separate endpoints and never
# need re-approval; they are intentionally not here.
_EDITABLE_PROFILE_FIELDS = {
    "display_name", "headline", "bio", "photo_url",
    "phone", "city", "country", "home_country_code", "served_countries",
    "social_links", "public_notes", "languages", "timezone",
    "expertise_country_codes", "expertise_categories", "years_lived_experience",
    "years_professional_experience", "professional_domains", "specializations",
    "hourly_rate", "currency", "currency_rates", "smart_pricing",
}


def save_mentor_profile_edit(mentor_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Status-aware profile edit (Phase 2).

    - approved: the profile is live, so proposed values are staged in `pending_changes`
      (the live row is untouched) and `pending_submitted_at` is stamped. An admin reviews
      and applies them via apply_pending_changes().
    - changes_requested / rejected: the profile is not live yet, so edits are written
      straight to the live row and the application is resubmitted (status ->
      pending_review, submission_count +1, reviewer note cleared).
    - pending_review / suspended: locked. Raises PermissionError.
    """
    safe = {k: v for k, v in fields.items() if k in _EDITABLE_PROFILE_FIELDS}
    if not safe:
        raise ValueError("No valid profile fields to update")
    cur = (
        _supabase.table("mentors")
        .select("status, submission_count")
        .eq("id", mentor_id)
        .limit(1)
        .execute()
    )
    if not cur.data:
        raise ValueError(f"Mentor {mentor_id!r} not found")
    status = cur.data[0].get("status")
    now = datetime.now(timezone.utc).isoformat()

    if status in ("pending_review", "suspended"):
        raise PermissionError(f"Profile cannot be edited while status is {status}")

    if status == "approved":
        payload: dict[str, Any] = {
            "pending_changes": safe,
            "pending_submitted_at": now,
            "updated_at": now,
        }
    else:  # changes_requested / rejected -> edit live row and resubmit
        count = (cur.data[0].get("submission_count") or 1) + 1
        payload = {
            **safe,
            "status": "pending_review",
            "submission_count": count,
            "rejection_reason": None,
            "pending_changes": None,
            "pending_submitted_at": None,
            "updated_at": now,
        }

    res = _supabase.table("mentors").update(payload).eq("id", mentor_id).execute()
    if not res.data:
        raise ValueError(f"Mentor {mentor_id!r} not found")
    if status != "approved":   # changes_requested/rejected branch writes straight to the live row
        _sync_services_ppp(mentor_id, safe)
    return res.data[0]


def stage_mentor_approval_edit(mentor_id: str, fields: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Stage APPROVAL-required profile fields (name / country) for admin review without touching the
    live row (BUG-075). Merges into any existing pending_changes and stamps pending_submitted_at.
    Returns the row, or None when there is nothing stageable."""
    safe = {k: v for k, v in fields.items() if k in _EDITABLE_PROFILE_FIELDS}
    if not safe:
        return None
    now = datetime.now(timezone.utc).isoformat()
    cur = _supabase.table("mentors").select("pending_changes").eq("id", mentor_id).limit(1).execute()
    if not cur.data:
        raise ValueError(f"Mentor {mentor_id!r} not found")
    existing = cur.data[0].get("pending_changes") or {}
    res = _supabase.table("mentors").update({
        "pending_changes": {**existing, **safe},
        "pending_submitted_at": now,
        "updated_at": now,
    }).eq("id", mentor_id).execute()
    if not res.data:
        raise ValueError(f"Mentor {mentor_id!r} not found")
    return res.data[0]


def save_mentor_profile_live(mentor_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Apply profile edits straight to the live row, with no staging for re-approval. Used during a
    migrated mentor's first-login onboarding AND for an approved mentor's non-approval edits (BUG-075:
    everything except name/country goes live immediately). Reprices services if a rate field changed."""
    safe = {k: v for k, v in fields.items() if k in _EDITABLE_PROFILE_FIELDS}
    if not safe:
        raise ValueError("No valid profile fields to update")
    safe["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _supabase.table("mentors").update(safe).eq("id", mentor_id).execute()
    if not res.data:
        raise ValueError(f"Mentor {mentor_id!r} not found")
    if any(k in safe for k in _RATE_FIELDS):
        reprice_mentor_services(mentor_id)   # BUG-079: apply the new rate to existing services
    _sync_services_ppp(mentor_id, safe)
    return res.data[0]


def complete_mentor_onboarding(mentor_id: str) -> dict[str, Any]:
    """Clear the migrated-mentor onboarding gate once they've set a rate and confirmed their
    sessions. onboarded_at is the durable completion marker: setup-script re-runs re-gate only
    mentors who have never finished (onboarded_at IS NULL), never someone who completed."""
    # Do NOT reprice here. Migrated mentors keep their real per-session prices unless they explicitly
    # click "Update my session prices" (which reprices via set_mentor_initial_rate(reprice=True)).
    # Finishing onboarding must not silently collapse a cheap intro + pricier session into one rate.
    payload = {"needs_onboarding": False, "onboarded_at": datetime.now(timezone.utc).isoformat()}
    try:
        res = _supabase.table("mentors").update(payload).eq("id", mentor_id).execute()
    except Exception:
        # onboarded_at column not migrated yet - still clear the gate so the mentor isn't stuck.
        res = _supabase.table("mentors").update({"needs_onboarding": False}).eq("id", mentor_id).execute()
    if not res.data:
        raise ValueError("Mentor not found")
    return res.data[0]


def apply_pending_changes(mentor_id: str) -> dict[str, Any]:
    """Admin approves an approved-mentor's staged profile revision: copy pending_changes
    onto the live row and clear the staging fields. Status stays approved (still live)."""
    cur = (
        _supabase.table("mentors")
        .select("pending_changes")
        .eq("id", mentor_id)
        .limit(1)
        .execute()
    )
    if not cur.data:
        raise ValueError(f"Mentor {mentor_id!r} not found")
    changes = cur.data[0].get("pending_changes") or {}
    safe = {k: v for k, v in changes.items() if k in _EDITABLE_PROFILE_FIELDS}
    payload: dict[str, Any] = {
        **safe,
        "pending_changes": None,
        "pending_submitted_at": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = _supabase.table("mentors").update(payload).eq("id", mentor_id).execute()
    if not res.data:
        raise ValueError(f"Mentor {mentor_id!r} not found")
    if any(k in safe for k in _RATE_FIELDS):
        reprice_mentor_services(mentor_id)   # BUG-079: staged rate change just went live -> reprice
    _sync_services_ppp(mentor_id, safe)
    return res.data[0]


def discard_pending_changes(mentor_id: str, reason: Optional[str] = None) -> dict[str, Any]:
    """Admin requests changes on / rejects a staged revision: drop pending_changes (live
    row keeps serving) and optionally record a reviewer note for the mentor to see."""
    payload: dict[str, Any] = {
        "pending_changes": None,
        "pending_submitted_at": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if reason is not None:
        payload["rejection_reason"] = reason.strip() or None
    res = _supabase.table("mentors").update(payload).eq("id", mentor_id).execute()
    if not res.data:
        raise ValueError(f"Mentor {mentor_id!r} not found")
    return res.data[0]


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


def list_mentors_by_status(status: str, limit: int = 500) -> list[dict[str, Any]]:
    """Return mentor rows with the given status, enriched with profile email/full_name.
    Limit is generous so the admin always sees EVERY mentor of a status; with 66 migrated
    mentors + seeds the old cap of 100 was close, and it must never silently truncate."""
    base = "id, slug, display_name, headline, photo_url, timezone, status, is_active, submission_count, created_at, profile_id, pending_submitted_at"

    def fetch(cols: str):
        return (
            _supabase.table("mentors").select(cols)
            .eq("status", status).order("created_at").limit(limit).execute().data or []
        )

    # commission_* may not be migrated yet; fall back so the admin list never breaks on a missing col.
    try:
        rows = fetch(base + ", commission_pct, commission_expires_at")
    except Exception:
        rows = fetch(base)
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

    # bookable = has >=1 active (admin-approved) service, so the admin can split the list into
    # Active (is_active + bookable) / Inactive (is_active false) / No services (active but nothing
    # to book). One query for all mentors on the page.
    ids = [r["id"] for r in rows]
    bookable_ids: set[str] = set()
    if ids:
        try:
            svcs = _supabase.table("services").select("mentor_id, is_active, status").in_("mentor_id", ids).execute().data or []
            for s in svcs:
                if s.get("is_active") and s.get("status") in (None, "approved"):
                    bookable_ids.add(s["mentor_id"])
        except Exception:
            logger.warning("list_mentors_by_status: could not load service flags")
    for row in rows:
        row["bookable"] = row["id"] in bookable_ids
    return rows


def list_pending_revisions(limit: int = 100) -> list[dict[str, Any]]:
    """Approved mentors who have staged profile changes (pending_changes) awaiting review.
    The live profile keeps serving until an admin applies the revision."""
    rows = (
        _supabase.table("mentors")
        .select("id, slug, display_name, headline, photo_url, phone, country, status, submission_count, "
                "created_at, profile_id, pending_changes, pending_submitted_at")
        .eq("status", "approved")
        .not_.is_("pending_changes", "null")
        .order("pending_submitted_at")
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
    On reject/changes_requested, stores `reason` (the reviewer note shown to the mentor);
    clears it on any other status."""
    payload: dict[str, Any] = {
        "status": status,
        "is_active": (status == "approved"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload["rejection_reason"] = (
        (reason or "").strip() or None if status in ("rejected", "changes_requested") else None
    )
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
        return {"exists": False, "has_password": False, "confirmed": False}
    try:
        res = _supabase.rpc("email_account_status", {"p_email": email}).execute()
        if not res.data:
            return {"exists": False, "has_password": False, "providers": [], "oauth_only": False, "confirmed": False}
        row = res.data[0]
        has_pw = bool(row.get("has_password"))
        providers = row.get("providers") or []
        confirmed = bool(row.get("confirmed"))
        # oauth_only == this account exists, has NO password, and can sign in via Google.
        # The popup uses it to say "continue with Google" instead of emailing an OTP link
        # (which would otherwise let a Google user accidentally add a password).
        oauth_only = (not has_pw) and ("google" in providers)
        # confirmed tells the guest-booking flow whether this is a REAL account (verified / has a
        # login) or a dangling half-started signup that should not block a guest booking.
        return {"exists": True, "has_password": has_pw, "providers": providers, "oauth_only": oauth_only, "confirmed": confirmed}
    except Exception:
        # Degrade safely: treat the email as passwordless so the popup emails a
        # verification/magic link (which works for both new and existing accounts)
        # instead of wrongly demanding a password. Never infer "has a password" from a
        # profile row: signInWithOtp creates a profile with NO password, so that would
        # trap a mid-signup user on the login screen.
        logger.exception("email_account_status RPC failed; treating email as passwordless (send link)")
        return {"exists": False, "has_password": False, "providers": [], "oauth_only": False, "confirmed": False}


def claim_welcome_email(profile_id: str) -> bool:
    """True exactly once per account: stamps welcome_sent_at and reports whether THIS call won it.
    /auth/sync runs on every login, so the send has to be claimed, not just checked."""
    if not profile_id:
        return False
    try:
        res = (_supabase.table("profiles")
               .update({"welcome_sent_at": datetime.now(timezone.utc).isoformat()})
               .eq("id", profile_id).is_("welcome_sent_at", "null")
               .execute())
        return bool(res.data)
    except Exception:
        logger.exception("claim_welcome_email failed profile=%s", profile_id)
        return False


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


def get_mentor_identity(mentor_id: str) -> Optional[dict[str, Any]]:
    """{profile_id, email} for a mentor, used to block a mentor from booking/paying for their own
    session (by account or by email match). None if the mentor can't be resolved."""
    name, email = get_mentor_email(mentor_id)
    try:
        row = (_supabase.table("mentors").select("profile_id").eq("id", mentor_id).limit(1).execute()).data
    except Exception:
        return None
    if not row:
        return None
    return {"profile_id": row[0].get("profile_id"), "email": (email or "").strip().lower() or None}


def is_self_booking(mentor_id: str, candidate_id: Optional[str], candidate_email: Optional[str]) -> bool:
    """True if the person booking IS this mentor - by signed-in account OR by using the mentor's own
    email. A mentor may appear in the directory but must never be able to pay for their own session
    (they can test with a DIFFERENT email as a normal guest)."""
    ident = get_mentor_identity(mentor_id)
    if not ident:
        return False
    if candidate_id and ident.get("profile_id") and candidate_id == ident["profile_id"]:
        return True
    email = (candidate_email or "").strip().lower()
    return bool(email and ident.get("email") and email == ident["email"])


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
    # Real availability the mentor configured (cal.com-style weekly hours + session
    # types + booking rules + date overrides). The legacy mentor_availability table
    # is deprecated and empty for new mentors.
    from . import direct_booking as _booking
    try:
        mentor["weekly_availability"] = _booking.list_weekly_availability(mentor_id)
        mentor["services"] = _booking.list_services(mentor_id)
        mentor["availability_rules"] = _booking.get_availability_rules(mentor_id)
        mentor["date_overrides"] = _booking.list_specific_availability(mentor_id)
    except Exception:
        logger.exception("Failed to load availability for mentor %s", mentor_id)
        mentor.setdefault("weekly_availability", [])
        mentor.setdefault("services", [])
        mentor.setdefault("availability_rules", {})
        mentor.setdefault("date_overrides", [])
    return mentor


def list_country_pricing() -> list[dict[str, Any]]:
    """All per-country platform fee + tax rows (incl. the DEFAULT fallback) for the admin editor."""
    try:
        return _supabase.table("country_pricing").select("*").order("country_code").execute().data or []
    except Exception:
        logger.warning("list_country_pricing failed (table not migrated yet?)")
        return []


def set_country_pricing(country_code: str, platform_fee_pct: float, tax_pct: float,
                        tax_label: Optional[str]) -> dict[str, Any]:
    """Upsert a country's platform fee + tax (customer-country based). country_code='DEFAULT' is the
    fallback used for any country without an explicit row."""
    cc = (country_code or "").strip().upper()
    if not cc:
        raise ValueError("country_code is required")
    payload = {
        "country_code": cc,
        "platform_fee_pct": platform_fee_pct,
        "tax_pct": tax_pct,
        "tax_label": (tax_label or "").strip() or None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = _supabase.table("country_pricing").upsert(payload, on_conflict="country_code").execute()
    if not res.data:
        raise ValueError("Could not save country pricing")
    return res.data[0]


def get_platform_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a single platform_settings value (e.g. the global mentor commission %)."""
    try:
        res = _supabase.table("platform_settings").select("value").eq("key", key).limit(1).execute()
        if res.data and res.data[0].get("value") is not None:
            return res.data[0]["value"]
    except Exception:
        logger.warning("get_platform_setting(%s) failed", key)
    return default


def set_platform_setting(key: str, value: str) -> None:
    """Upsert a single platform_settings value without disturbing its description. Update-first so a
    brand-new key still inserts."""
    now = datetime.now(timezone.utc).isoformat()
    res = _supabase.table("platform_settings").update({"value": value, "updated_at": now}).eq("key", key).execute()
    if not res.data:
        _supabase.table("platform_settings").insert({"key": key, "value": value}).execute()


def list_audit_events(booking_id: Optional[str] = None, entity_type: Optional[str] = None,
                      limit: int = 200) -> list[dict[str, Any]]:
    """Unified platform activity trail (newest first), optionally scoped to one booking or entity
    type. Reads through the admin-only admin_audit_events RPC (service-role)."""
    try:
        res = _supabase.rpc("admin_audit_events", {
            "p_booking_id": booking_id or None,
            "p_entity_type": (entity_type or None),
            "p_limit": limit,
        }).execute()
        return res.data or []
    except Exception:
        logger.warning("list_audit_events failed (audit trail not migrated yet?)")
        return []


_RATE_FIELDS = {"hourly_rate", "currency", "currency_rates"}


def reprice_mentor_services(mentor_id: str) -> int:
    """Re-derive every PAID service's price from the mentor's CURRENT hourly rate + currencies
    (BUG-079). Service prices are always derived (duration x hourly rate), never hand-set, so a rate
    or currency change must flow through to the live service rows - otherwise the customer keeps
    seeing the old price. The free intro (set_price 0) is left free. Returns how many were repriced.

    Mirrors the frontend proration exactly: set_price = round(hourly * duration/60, 2), and one
    currency_prices entry per additional currency rate (round(rate * duration/60, 2))."""
    try:
        m = (_supabase.table("mentors").select("hourly_rate, currency, currency_rates")
             .eq("id", mentor_id).single().execute()).data or {}
    except Exception:
        return 0
    hourly = m.get("hourly_rate")
    if hourly is None or float(hourly) <= 0:
        return 0
    currency = (m.get("currency") or "USD").upper()
    rates = m.get("currency_rates") or []
    svcs = (_supabase.table("services").select("id, duration, set_price")
            .eq("mentor_id", mentor_id).execute()).data or []
    repriced = 0
    for s in svcs:
        try:
            if float(s.get("set_price") or 0) <= 0:
                continue  # free intro stays free
            dur = int(s.get("duration") or 0)
        except (TypeError, ValueError):
            continue
        if dur <= 0:
            continue
        set_price = round(float(hourly) * dur / 60.0, 2)
        cprices: list[dict[str, Any]] = []
        for r in rates:
            try:
                rc = str((r or {}).get("currency") or "").upper()
                rr = float((r or {}).get("hourly_rate") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if rc and rc != currency and rr > 0:
                cprices.append({"currency": rc, "base_price": round(rr * dur / 60.0, 2)})
        try:
            _supabase.table("services").update({
                # BUG-101: compute_booking_price/display_service_prices both read
                # COALESCE(set_offer_price, set_price) for the primary currency, so a stale
                # set_offer_price from an earlier (now-outdated) rate silently outlives every
                # future rate change and keeps winning over the freshly-derived set_price - the
                # customer sees an old absolute figure instead of today's rate. Since prices here
                # are always derived (never hand-set), any leftover offer price no longer applies
                # once the base rate has moved and must be cleared, not left to shadow it.
                "set_price": set_price, "set_currency": currency, "currency_prices": cprices,
                # Derived pricing has NO separate offer price. Clear any stale one (migrated rows had
                # set_offer_price set), otherwise the engine's COALESCE(set_offer_price, set_price) keeps
                # charging the old offer amount instead of the re-derived base price.
                "set_offer_price": None,
            }).eq("id", s["id"]).execute()
            repriced += 1
        except Exception:
            logger.exception("reprice_mentor_services: failed for service=%s", s.get("id"))
    return repriced


def derive_rate_prefill(mentor: dict[str, Any]) -> dict[str, Any]:
    """What the first-login rate form should START with for a migrated mentor.

    Derived from the mentor's OWN imported sessions (currency of their priciest session + that session's
    price scaled to an hour), never from mentors.currency alone: the import left many rows whose currency
    disagrees with their sessions (e.g. sessions priced INR 3196 on a mentor row marked USD), and
    prefilling that shows a foreign currency the mentor never chose and would reprice every session in it
    if they just hit Finish. Falls back to the stored currency/rate, then INR, when there are no priced
    sessions. The mentor can still change both before submitting."""
    res = (_supabase.table("services").select("set_price, set_currency, duration")
           .eq("mentor_id", mentor["id"]).eq("is_active", True).execute())
    priced = [s for s in (res.data or [])
              if (s.get("set_price") or 0) > 0 and (s.get("duration") or 0) > 0 and s.get("set_currency")]
    if priced:
        # Some mentors price different sessions in different currencies, so comparing raw numbers picks
        # whichever currency has the smallest face value (INR 1998/hr "beats" AUD 53/hr, which is really
        # ~INR 3050/hr). Normalize to the EUR pivot before taking the max, same as list_active_mentors.
        fx: dict[str, float] = {}
        try:
            rows = _supabase.table("fx_rates").select("quote, rate").eq("base", "EUR").execute().data or []
            fx = {r["quote"].upper(): float(r["rate"]) for r in rows if r.get("rate")}
        except Exception:
            logger.exception("derive_rate_prefill: FX unavailable, comparing session prices unnormalized")
        fx["EUR"] = 1.0

        def hourly(s: dict[str, Any]) -> float:
            return float(s["set_price"]) * 60.0 / float(s["duration"])

        def hourly_eur(s: dict[str, Any]) -> float:
            rate = fx.get(str(s["set_currency"]).upper())
            return hourly(s) / rate if rate else hourly(s)

        top = max(priced, key=hourly_eur)
        return {
            "currency": str(top["set_currency"]).upper(),
            "hourly_rate": round(hourly(top), 2),
            "source": "sessions",
        }
    # No priced session to derive from, and the stored rate is no more trustworthy here (serviceless
    # migrated mentors carry rows like "INR 80", a USD-magnitude number mislabelled INR). Open on our
    # default currency with the amount BLANK rather than inventing one: a prefilled number tends to be
    # accepted as-is, and "Finish setup" is already gated on the mentor entering a valid rate.
    return {"currency": "INR", "hourly_rate": None, "source": "default"}


def set_mentor_initial_rate(mentor_id: str, hourly_rate: float, currency: str,
                            currency_rates: list[dict], smart_pricing: bool,
                            reprice: bool = False) -> dict[str, Any]:
    """First-login rate capture for a migrated mentor: write rate/currency/smart_pricing straight to
    the live row (not staged), so they become priceable immediately.

    reprice: only when the mentor explicitly asks to apply the rate to their sessions (the "Update my
    session prices" button). Migrated mentors arrive with their real per-SESSION prices restored (a
    cheap 15-min intro alongside a pricier 30-min), which a single per-hour rate can't reproduce; the
    seeded rate is just the popup default, so saving it must NOT silently overwrite those prices."""
    payload = {
        "hourly_rate": hourly_rate,
        "currency": currency,
        "currency_rates": currency_rates,
        "smart_pricing": smart_pricing,
    }
    res = _supabase.table("mentors").update(payload).eq("id", mentor_id).execute()
    if not res.data:
        raise ValueError("Mentor not found")
    if reprice:
        reprice_mentor_services(mentor_id)   # derive every session from the new rate (duration x rate)
    _sync_services_ppp(mentor_id, payload)
    return res.data[0]


def set_mentor_commission(mentor_id: str, commission_pct: Optional[float], expires_at: Optional[str]) -> dict[str, Any]:
    """Set or clear a mentor's commission override. pct=None clears it (mentor falls back to the
    global commission). expires_at is an ISO timestamp or None (never expires)."""
    payload: dict[str, Any] = {"commission_pct": commission_pct}
    payload["commission_expires_at"] = expires_at if commission_pct is not None else None
    res = _supabase.table("mentors").update(payload).eq("id", mentor_id).execute()
    if not res.data:
        raise ValueError("Mentor not found")
    return res.data[0]


def get_admin_stats() -> dict[str, int]:
    """Platform-level counters for the admin overview.
    'active' = mentors a user/guest can actually see AND book (approved + is_active + at least one
    bookable service) - i.e. exactly what the browse list returns. 'hidden' = approved mentors that
    are inactive or have no bookable service (still kept + visible to the admin, just not to users).
    Previously the 'Active mentors' card showed the count of ALL approved rows, which wrongly
    included the hidden/serviceless ones (the "68 is not true" bug)."""
    try:
        pending = _supabase.table("mentors").select("id", count="exact").eq("status", "pending_review").execute().count or 0
        approved = _supabase.table("mentors").select("id", count="exact").eq("status", "approved").execute().count or 0
        active_flag = _supabase.table("mentors").select("id", count="exact").eq("status", "approved").eq("is_active", True).execute().count or 0
        bookings = _supabase.table("bookings").select("id", count="exact").execute().count or 0
        # Reuse the exact browse filter so 'active' matches what users see. Small roster, so fetching
        # all and counting is fine (revisit with a SQL count if it ever grows huge). Inactive and
        # no-service are kept as SEPARATE buckets so the admin can tell them apart.
        bookable = len(list_active_mentors(limit=1000))
        # Services an approved mentor added after going live, awaiting review (the "mentor added a
        # session type" event needs an admin surface). Reuses the exact review-queue query.
        from . import direct_booking as _booking
        pending_services = len(_booking.list_pending_services())
        return {
            "pending_mentor_count": pending,
            "approved_mentor_count": approved,                          # all approved (= Mentors-tab size)
            "active_mentor_count": bookable,                            # approved + active + a bookable service
            "inactive_mentor_count": max(approved - active_flag, 0),    # is_active = false (hidden by admin)
            "no_service_mentor_count": max(active_flag - bookable, 0),  # active toggle but nothing to book
            "pending_service_count": pending_services,                  # services from live mentors awaiting review
            "total_bookings": bookings,
        }
    except Exception:
        logger.exception("Failed to fetch admin stats")
        return {"pending_mentor_count": 0, "approved_mentor_count": 0, "active_mentor_count": 0,
                "inactive_mentor_count": 0, "no_service_mentor_count": 0, "pending_service_count": 0,
                "total_bookings": 0}


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
                ors = [f"candidate_email.ilike.*{safe}*", f"candidate_name.ilike.*{safe}*"]
                # Also match by mentor name: resolve matching mentor ids, then OR them into the filter.
                try:
                    mm = _supabase.table("mentors").select("id").ilike("display_name", f"*{safe}*").execute().data or []
                    mids = [m["id"] for m in mm]
                    if mids:
                        ors.append(f"mentor_id.in.({','.join(mids)})")
                except Exception:
                    pass
                query = query.or_(",".join(ors))
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


def list_all_legacy_sessions(q: Optional[str] = None, status: Optional[str] = None,
                             limit: int = 1000) -> list[dict[str, Any]]:
    """All imported legacy sessions for the admin Bookings view, with the mentor name attached.
    Filters: status (exact) and a search over mentor OR customer name (q)."""
    try:
        query = (
            _supabase.table("legacy_sessions")
            .select("id, mentor_id, status, service_title, service_type, customer_name, "
                    "slot_start, duration_min, amount_total, amount_currency")
            .order("slot_start", desc=True)
            .limit(limit)
        )
        if status:
            query = query.eq("status", status)
        rows = query.execute().data or []
        ids = list({r["mentor_id"] for r in rows if r.get("mentor_id")})
        names: dict[str, Any] = {}
        if ids:
            mres = _supabase.table("mentors").select("id, display_name, slug").in_("id", ids).execute()
            names = {m["id"]: m for m in (mres.data or [])}
        for r in rows:
            info = names.get(r.get("mentor_id")) or {}
            r["mentor_name"] = info.get("display_name")
            r["mentor_slug"] = info.get("slug")
        # Name search runs in Python (small dataset): the searchable name lives on the joined
        # mentors row + the denormalized customer_name, not on legacy_sessions itself.
        if q:
            ql = q.strip().lower()
            rows = [r for r in rows
                    if ql in (r.get("mentor_name") or "").lower()
                    or ql in (r.get("customer_name") or "").lower()]
        return rows
    except Exception:
        logger.warning("list_all_legacy_sessions failed")
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

