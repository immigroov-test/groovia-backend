import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, field_validator, model_validator

import config
import db
from services import mailer, bank_validation, bank_crypto, pricing_input
from core.auth import AuthUser, get_current_user
from core.permissions import require_mentor

logger = logging.getLogger("immigroov.routers.mentor")

router = APIRouter(prefix="/mentor", tags=["mentor"])


# Letters (incl. accented), spaces, hyphens, apostrophes, periods, commas - covers
# real-world city names ("Winston-Salem", "Xi'an", "Washington, D.C.", "Düsseldorf")
# while rejecting stray digits/symbols (BUG-004).
_CITY_ALLOWED_RE = re.compile(r"^[A-Za-zÀ-ɏḀ-ỿ\s'.,-]+$")
_CITY_HAS_LETTER_RE = re.compile(r"[A-Za-zÀ-ɏḀ-ỿ]")


def _validate_city(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    v = v.strip()
    if not v:
        return None
    if len(v) > 100:
        raise ValueError("City must be 100 characters or fewer")
    if not _CITY_ALLOWED_RE.match(v) or not _CITY_HAS_LETTER_RE.search(v):
        raise ValueError("City must contain only letters, spaces, hyphens, apostrophes, periods, and commas")
    return v


_SOCIAL_DOMAINS: dict[str, list[str]] = {
    "linkedin":  ["linkedin.com"],
    "github":    ["github.com"],
    "twitter":   ["x.com", "twitter.com"],
    "youtube":   ["youtube.com"],
    "instagram": ["instagram.com"],
    "tiktok":    ["tiktok.com"],
    "website":   [],
}


class SocialLink(BaseModel):
    type: str
    url: str

    @model_validator(mode="after")
    def validate_social_link(self) -> "SocialLink":
        if self.type not in _SOCIAL_DOMAINS:
            raise ValueError(f"Unknown social link type: {self.type!r}")
        url = self.url.strip()
        if not url.startswith("https://"):
            raise ValueError("Social link URL must start with https://")
        allowed = _SOCIAL_DOMAINS[self.type]
        if allowed:
            try:
                hostname = urlparse(url).netloc.lower().lstrip("www.")
            except Exception:
                raise ValueError("Invalid URL")
            if not any(hostname.endswith(d) for d in allowed):
                raise ValueError(f"URL is not a valid {self.type} link")
        self.url = url
        return self


class AvailabilitySlot(BaseModel):
    day_of_week: int   # 0=Mon … 6=Sun
    start_time: str    # "HH:MM"
    end_time: str      # "HH:MM"


@router.get("/me")
def get_my_mentor(user: AuthUser = Depends(get_current_user)):
    """Returns the mentor row linked to the logged-in user, or 404 if not a mentor."""
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor:
        raise HTTPException(status_code=404, detail="No mentor profile for this account")
    return mentor


@router.get("/legacy-sessions")
def my_legacy_sessions(mentor: dict = Depends(require_mentor)):
    """The logged-in mentor's OWN imported past sessions (private track record for their dashboard).
    Scoped to this mentor only, never other mentors; the public profile does not show these at all."""
    return {"sessions": db.list_legacy_sessions(mentor["id"])}


class InitialRateBody(BaseModel):
    hourly_rate: float
    currency: str = "INR"
    currency_rates: list[dict] = []      # additional-currency base rates [{currency, hourly_rate}]
    smart_pricing: bool = False


@router.post("/setup-rate")
def setup_rate(body: InitialRateBody, user: AuthUser = Depends(get_current_user)):
    """First-login rate capture for a migrated mentor who has no per-hour rate yet. Applies directly
    to the live row (not staged for re-approval) so the mentor is immediately priceable. First-time
    only: once a rate exists, edits go through the normal profile flow."""
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor:
        raise HTTPException(status_code=404, detail="No mentor profile for this account")
    # A migrated mentor can (re)set their rate freely while still in first-login onboarding; once
    # they've finished (needs_onboarding cleared), a set rate is locked and edits go via /profile.
    if mentor.get("hourly_rate") and not mentor.get("needs_onboarding"):
        raise HTTPException(status_code=409, detail="A rate is already set for this mentor")
    if body.hourly_rate is None or body.hourly_rate <= 0 or body.hourly_rate > 100000:
        raise HTTPException(status_code=422, detail="Enter a valid hourly rate")
    try:
        validated_rates = pricing_input.validate_currency_rates(body.currency, body.currency_rates)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return db.set_mentor_initial_rate(
        mentor["id"],
        hourly_rate=round(body.hourly_rate, 2),
        currency=(body.currency or "INR").strip().upper(),
        currency_rates=validated_rates,
        smart_pricing=body.smart_pricing,
    )


@router.post("/complete-onboarding")
def complete_onboarding(user: AuthUser = Depends(get_current_user)):
    """Clear the first-login onboarding gate for a migrated mentor once they've set their rate and
    confirmed their sessions on the availability page. Idempotent. Requires a rate so they're
    priceable before the hub unlocks. They stay approved/live throughout (no admin re-review)."""
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor:
        raise HTTPException(status_code=404, detail="No mentor profile for this account")
    if not mentor.get("needs_onboarding"):
        return {"completed": True}
    if not mentor.get("hourly_rate"):
        raise HTTPException(status_code=422, detail="Set your hourly rate before finishing setup.")
    return db.complete_mentor_onboarding(mentor["id"])


# ── Initial signup ─────────────────────────────────────────────────────────────

class WeeklySlot(BaseModel):
    weekday: str        # "Monday" .. "Sunday"
    start_time: str     # "HH:MM"
    end_time: str       # "HH:MM"


class ServiceDraft(BaseModel):
    title: str
    duration: int       # 15 | 30 | 45 | 60
    is_active: bool = True
    set_price: float = 0   # prorated from the mentor's primary hourly rate
    currency_prices: list[dict] = []   # per-currency prices derived from the mentor's other rates
    description: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = []


class BookingRules(BaseModel):
    days_ahead: int = 30
    min_notice_hours: float = 2
    cancel_hours: int = 24

    @field_validator("days_ahead")
    @classmethod
    def _cap_days_ahead(cls, v: int) -> int:
        if not (1 <= v <= 90):
            raise ValueError("Mentees can book between 1 and 90 days ahead")
        return v

    @field_validator("min_notice_hours")
    @classmethod
    def _cap_min_notice(cls, v: float) -> float:
        if not (0 <= v <= 24):
            raise ValueError("Minimum booking notice must be between 0 and 24 hours")
        return v

    @field_validator("cancel_hours")
    @classmethod
    def _cap_cancel_hours(cls, v: int) -> int:
        if not (2 <= v <= 48):
            raise ValueError("Cancellation/rescheduling notice must be between 2 and 48 hours")
        return v


class DateOverrideDraft(BaseModel):
    slot_date: str                      # YYYY-MM-DD
    is_blackout: bool = False
    start_time: Optional[str] = None    # HH:MM (custom hours)
    end_time: Optional[str] = None


class BankDetailsBody(BaseModel):
    # Country-driven payout details. Which fields matter is decided by country_code; the backend
    # validates + normalizes in services/bank_validation. Sensitive numbers are encrypted at rest.
    country_code: str
    account_holder_name: str
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    iban: Optional[str] = None
    routing_number: Optional[str] = None
    account_type: Optional[str] = None      # us: checking | savings
    sort_code: Optional[str] = None         # uk
    ifsc: Optional[str] = None              # india
    swift_bic: Optional[str] = None         # iban (optional) / swift
    bank_address: Optional[str] = None      # swift (optional)


class ServedCountry(BaseModel):
    """A country (besides the mentor's current one) they can advise on, with years lived there."""
    code: str
    years: Optional[int] = None

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if len(v) != 2 or not v.isalpha():
            raise ValueError("Country code must be a 2-letter ISO code")
        return v

    @field_validator("years")
    @classmethod
    def validate_years(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 0 or v > 60):
            raise ValueError("Years must be between 0 and 60")
        return v


class MentorSignupBody(BaseModel):
    display_name: str
    headline: Optional[str] = None
    photo_url: Optional[str] = None
    phone: Optional[str] = None
    bio: Optional[str] = None
    country: Optional[str] = None
    home_country_code: Optional[str] = None   # legacy/back-compat; no longer collected at signup
    served_countries: list[ServedCountry] = []   # other countries they can advise on (max 2), with years
    city: Optional[str] = None
    timezone: str = "UTC"
    languages: list[str] = []
    social_links: list[SocialLink] = []
    public_notes: Optional[str] = None
    expertise_country_codes: list[str] = []
    expertise_categories: list[str] = []
    years_lived_experience: Optional[int] = None
    years_professional_experience: Optional[int] = None
    professional_domains: list[str] = []
    specializations: list[str] = []
    agreed_to_mentor_terms: bool = False
    hourly_rate: Optional[float] = None
    currency: str = "USD"
    currency_rates: list[dict] = []      # additional-currency base rates [{currency, hourly_rate}]
    smart_pricing: bool = False
    weekly_availability: list[WeeklySlot] = []
    services: list[ServiceDraft] = []
    booking_rules: Optional[BookingRules] = None
    date_overrides: list[DateOverrideDraft] = []
    bank: Optional[BankDetailsBody] = None

    @field_validator("city")
    @classmethod
    def validate_city(cls, v: Optional[str]) -> Optional[str]:
        return _validate_city(v)

    @field_validator("years_lived_experience", "years_professional_experience")
    @classmethod
    def validate_years(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 0 or v > 60):
            raise ValueError("Years must be between 0 and 60")
        return v

    @field_validator("served_countries")
    @classmethod
    def validate_served_countries(cls, v: list[ServedCountry]) -> list[ServedCountry]:
        if len(v) > 2:
            raise ValueError("You can add at most 2 other countries")
        return v


def _derive_expertise(country: Optional[str], extra_codes: list[str] | None = None) -> list[str]:
    """The countries a mentee can browse a mentor by = their current country plus the other countries
    they can advise on (served_countries). Deduped, order-preserving. Derived, not asked for, so it can
    never drift from the inputs."""
    codes = [(country or "").strip().upper()]
    codes += [(c or "").strip().upper() for c in (extra_codes or [])]
    return list(dict.fromkeys(c for c in codes if c))


@router.post("/signup")
def mentor_signup(body: MentorSignupBody, background_tasks: BackgroundTasks, user: AuthUser = Depends(get_current_user)):
    """Self-service mentor signup: creates a new mentor row, pending admin review."""
    if db.get_mentor_by_profile_id(user.id):
        raise HTTPException(status_code=409, detail="This account is already linked to a mentor profile")
    display_name = body.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="Display name is required")
    if not body.agreed_to_mentor_terms:
        raise HTTPException(status_code=400, detail="You must accept the mentor agreement")
    if not body.languages:
        raise HTTPException(status_code=400, detail="Select at least one language")
    if not (body.country or "").strip():
        raise HTTPException(status_code=400, detail="Select your current country")
    if body.years_professional_experience is None:
        raise HTTPException(status_code=400, detail="Years of professional experience is required")
    # Bank details are mandatory. Validate them BEFORE creating the mentor row so a missing/invalid
    # payout method never leaves a half-created mentor (which would 409 any retry).
    if body.bank is None:
        raise HTTPException(status_code=400, detail="Bank details are required to receive payouts")
    if not bank_crypto.is_configured():
        logger.error("Mentor signup blocked: BANK_ENC_KEY is not configured")
        raise HTTPException(status_code=503, detail="Bank details can't be saved right now. Please try again shortly.")
    try:
        validated_bank = bank_validation.validate_bank_details(body.bank.country_code, body.bank.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    try:
        validated_rates = pricing_input.validate_currency_rates(body.currency, body.currency_rates)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    result = db.create_mentor_signup(
        user.id,
        display_name=display_name,
        headline=(body.headline or "").strip() or None,
        photo_url=(body.photo_url or "").strip() or None,
        phone=(body.phone or "").strip() or None,
        bio=(body.bio or "").strip() or None,
        country=(body.country or "").strip() or None,
        home_country_code=(body.home_country_code or "").strip() or None,
        served_countries=[s.model_dump() for s in body.served_countries],
        city=(body.city or "").strip() or None,
        timezone_name=body.timezone,
        languages=body.languages,
        social_links=[s.model_dump() for s in body.social_links],
        public_notes=(body.public_notes or "").strip() or None,
        expertise_country_codes=_derive_expertise(body.country, [s.code for s in body.served_countries]),
        expertise_categories=body.expertise_categories,
        years_lived_experience=body.years_lived_experience,
        years_professional_experience=body.years_professional_experience,
        professional_domains=body.professional_domains,
        specializations=body.specializations,
        hourly_rate=body.hourly_rate,
        currency=body.currency,
        currency_rates=validated_rates,
        smart_pricing=body.smart_pricing,
    )
    mentor_id = result["id"]
    # BUG-012: these inserts used to fail silently (logged server-side only), so a
    # transient error left a mentor with a "successful" signup but an empty
    # Availability/Sessions tab and no idea why. Collect what failed and surface it
    # in the response instead of pretending everything saved.
    warnings: list[str] = []
    # Weekly availability -> weekly_availability (the table the booking engine reads).
    for slot in body.weekly_availability:
        try:
            db.add_weekly_availability(mentor_id=mentor_id, weekday=slot.weekday,
                                       start_time=slot.start_time, end_time=slot.end_time)
        except Exception:
            logger.exception("Weekly availability insert failed during signup for mentor %s", mentor_id)
            warnings.append(f"Could not save your {slot.weekday} availability. Please re-add it from your dashboard.")
    # Session types the mentee can book.
    for svc in body.services:
        try:
            db.create_service(mentor_id=mentor_id, title=svc.title, duration=svc.duration,
                              is_active=svc.is_active, set_price=svc.set_price,
                              description=(svc.description or "").strip() or None,
                              category=(svc.category or "").strip() or None,
                              tags=svc.tags,
                              set_currency=(body.currency or "INR").upper(),
                              currency_prices=pricing_input.validate_currency_prices(body.currency, svc.currency_prices))
        except Exception:
            logger.exception("Service create failed during signup for mentor %s", mentor_id)
            warnings.append(f"Could not save your session type \"{svc.title}\". Please re-add it from your dashboard.")
    # BUG-079: make sure every stored service price matches the base rate (duration x rate), so the
    # price the customer sees on the booking page can never drift from the mentor's rate.
    try:
        db.reprice_mentor_services(mentor_id)
    except Exception:
        logger.warning("Post-signup reprice failed for mentor %s", mentor_id)
    # Booking rules (mandatory) -> stored on the mentor row via avail_set_rules.
    if body.booking_rules:
        try:
            db.set_availability_rules(
                mentor_id=mentor_id,
                days_ahead=body.booking_rules.days_ahead,
                min_notice_hours=body.booking_rules.min_notice_hours,
                cancel_hours=body.booking_rules.cancel_hours,
            )
        except Exception:
            logger.exception("Booking rules save failed during signup for mentor %s", mentor_id)
            warnings.append("Could not save your booking rules. Please re-set them from your dashboard.")
    # Date overrides (optional) -> specific_availability via block/override RPCs.
    for ov in body.date_overrides:
        try:
            if ov.is_blackout:
                db.block_date(mentor_id=mentor_id, slot_date=ov.slot_date)
            elif ov.start_time and ov.end_time:
                db.override_date(mentor_id=mentor_id, slot_date=ov.slot_date, start_time=ov.start_time, end_time=ov.end_time)
        except Exception:
            logger.exception("Date override save failed during signup for mentor %s", mentor_id)
            warnings.append(f"Could not save your date override for {ov.slot_date}. Please re-add it from your dashboard.")
    # Payout bank details were validated up-front; persist them now (encrypted at rest).
    try:
        db.upsert_mentor_bank(mentor_id, validated_bank)
    except Exception:
        logger.exception("Bank details save failed post-create for mentor %s", mentor_id)
        warnings.append("We couldn't save your bank details. Please add them from your Mentor Hub - Payments tab.")
    if warnings:
        result["warnings"] = warnings
    _, mentor_email = db.get_mentor_email(mentor_id)
    if mentor_email:
        background_tasks.add_task(
            mailer.send_transactional,
            mentor_email,
            "mentor_application_received",
            {
                "display_name": display_name,
                "availability_url": config.FRONTEND_URL + "/mentor/availability",
            },
        )
    # Notify the admin(s) that a new application is waiting in the review queue.
    for admin_email in db.admin_notify_emails():
        background_tasks.add_task(
            mailer.send_transactional,
            admin_email,
            "admin_mentor_application",
            {
                "display_name": display_name,
                "mentor_email": mentor_email or "",
                "headline": (body.headline or "").strip(),
                "review_url": config.FRONTEND_URL + "/admin",
            },
        )
    return result


# ── Payout bank details ──────────────────────────────────────────────────────────

@router.get("/bank")
def get_my_bank(mentor: dict = Depends(require_mentor)):
    """Masked payout details for the logged-in mentor. Never returns full account numbers."""
    return db.get_mentor_bank_masked(mentor["id"]) or {"has_details": False}


@router.post("/bank")
def save_my_bank(body: BankDetailsBody, mentor: dict = Depends(require_mentor)):
    """Add or replace the mentor's payout bank details (country-driven, validated, encrypted)."""
    try:
        validated = bank_validation.validate_bank_details(body.country_code, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    try:
        return db.upsert_mentor_bank(mentor["id"], validated)
    except RuntimeError as e:
        logger.error("Bank details save blocked: %s", e)
        raise HTTPException(status_code=503, detail="Saving bank details is temporarily unavailable. Please try again later.")


# ── Profile editing ────────────────────────────────────────────────────────────

class ProfileUpdateBody(BaseModel):
    display_name: Optional[str] = None
    headline: Optional[str] = None
    photo_url: Optional[str] = None
    phone: Optional[str] = None
    bio: Optional[str] = None
    country: Optional[str] = None
    home_country_code: Optional[str] = None
    served_countries: Optional[list[ServedCountry]] = None
    city: Optional[str] = None
    timezone: Optional[str] = None
    languages: Optional[list[str]] = None
    social_links: Optional[list[SocialLink]] = None
    public_notes: Optional[str] = None
    expertise_country_codes: Optional[list[str]] = None
    expertise_categories: Optional[list[str]] = None
    years_lived_experience: Optional[int] = None
    years_professional_experience: Optional[int] = None
    professional_domains: Optional[list[str]] = None
    specializations: Optional[list[str]] = None
    hourly_rate: Optional[float] = None
    currency: Optional[str] = None
    currency_rates: Optional[list[dict]] = None
    smart_pricing: Optional[bool] = None

    @field_validator("city")
    @classmethod
    def validate_city(cls, v: Optional[str]) -> Optional[str]:
        return _validate_city(v)

    @field_validator("years_lived_experience", "years_professional_experience")
    @classmethod
    def validate_years(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 0 or v > 60):
            raise ValueError("Years must be between 0 and 60")
        return v

    @field_validator("hourly_rate")
    @classmethod
    def validate_rate(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and (v < 0 or v > 100000):
            raise ValueError("hourly_rate must be between 0 and 100000")
        return round(v, 2) if v is not None else v


# BUG-075: only Name + Country need admin approval; every other profile field goes live
# immediately and admin is just notified (old -> new). Keep this in sync with the hub UI.
_APPROVAL_PROFILE_FIELDS = {"display_name", "country"}
_PROFILE_FIELD_LABELS = {
    "display_name": "Name", "country": "Country", "home_country_code": "Home country",
    "headline": "Headline", "bio": "About", "city": "City", "timezone": "Timezone",
    "languages": "Languages", "photo_url": "Photo", "phone": "Phone",
    "social_links": "Social links", "public_notes": "Public notes", "served_countries": "Additional countries lived",
    "expertise_country_codes": "Countries of expertise", "expertise_categories": "Focus areas",
    "years_lived_experience": "Years in current country", "years_professional_experience": "Years of experience",
    "professional_domains": "Domains", "specializations": "Specializations",
    "hourly_rate": "Hourly rate", "currency": "Currency", "currency_rates": "Additional currencies",
    "smart_pricing": "Fair pricing",
}


def _fmt_change_value(v: Any) -> str:
    if v is None or v == "":
        return "(empty)"
    if isinstance(v, list):
        parts: list[str] = []
        for x in v:
            if isinstance(x, dict):
                parts.append(", ".join(f"{k}: {xx}" for k, xx in x.items() if xx not in (None, "")))
            else:
                parts.append(str(x))
        return "; ".join(p for p in parts if p) or "(empty)"
    return str(v)


def _profile_change_rows(mentor: dict, fields: dict[str, Any]) -> list[dict[str, str]]:
    """[{label, delta}] for fields whose value actually changed, for the admin email."""
    rows: list[dict[str, str]] = []
    for k, new in fields.items():
        old = mentor.get(k)
        if str(old) == str(new):
            continue
        label = _PROFILE_FIELD_LABELS.get(k, k.replace("_", " ").title())
        rows.append({"label": label, "delta": f"{_fmt_change_value(old)}  →  {_fmt_change_value(new)}"})
    return rows


def _email_admins_change(template: str, mentor_name: str, changes: list[dict[str, str]]) -> None:
    if not changes:
        return
    try:
        for admin_email in db.admin_notify_emails():
            mailer.send_transactional(admin_email, template, {"mentor_name": mentor_name, "changes": changes})
    except Exception:
        logger.warning("admin profile-change email failed template=%s", template)


def _apply_approved_profile_edit(mentor: dict, fields: dict[str, Any], background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Approved (live) mentor edit: apply everything except Name/Country immediately, stage those two
    for approval, and notify admin either way (BUG-075 + BUG-076)."""
    approval = {k: v for k, v in fields.items() if k in _APPROVAL_PROFILE_FIELDS}
    info = {k: v for k, v in fields.items() if k not in _APPROVAL_PROFILE_FIELDS}
    staged_changes = _profile_change_rows(mentor, approval)
    applied_changes = _profile_change_rows(mentor, info)

    row: Optional[dict[str, Any]] = None
    if info:
        row = db.save_mentor_profile_live(mentor["id"], info)      # live now (+ reprice if rate changed)
    if approval:
        row = db.stage_mentor_approval_edit(mentor["id"], approval) or row
    row = row or mentor

    mentor_name = mentor.get("display_name") or "A mentor"
    background_tasks.add_task(_email_admins_change, "admin_mentor_change_request", mentor_name, staged_changes)
    background_tasks.add_task(_email_admins_change, "admin_mentor_change_info", mentor_name, applied_changes)
    return {
        **row,
        "staged_for_review": [c["label"] for c in staged_changes],
        "applied_live": [c["label"] for c in applied_changes],
    }


@router.post("/profile")
def update_profile(body: ProfileUpdateBody, background_tasks: BackgroundTasks, user: AuthUser = Depends(get_current_user)):
    """Edit the mentor profile (Phase 2, status-aware). An APPROVED mentor's edits are
    staged for re-approval (pending_changes) while the live profile keeps serving; a
    mentor in changes_requested/rejected edits in place and resubmits for review; a
    pending_review or suspended profile is locked. See db.save_mentor_profile_edit."""
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor:
        raise HTTPException(status_code=404, detail="No mentor profile for this account")
    fields: dict[str, Any] = {}
    if body.display_name is not None:
        fields["display_name"] = body.display_name.strip() or mentor["display_name"]
    if body.headline is not None:
        fields["headline"] = body.headline.strip() or None
    if body.photo_url is not None:
        fields["photo_url"] = body.photo_url.strip() or None
    if body.phone is not None:
        fields["phone"] = body.phone.strip() or None
    if body.bio is not None:
        fields["bio"] = body.bio.strip() or None
    if body.country is not None:
        fields["country"] = body.country.strip() or None
    if body.home_country_code is not None:
        fields["home_country_code"] = (body.home_country_code.strip().upper() or None)
    if body.served_countries is not None:
        fields["served_countries"] = [s.model_dump() for s in body.served_countries]
    # Expertise (browse) countries are always derived from current + served (+ any legacy home),
    # never sent by the client. Re-derive whenever the current country or the served list changes,
    # using the incoming values or the existing row.
    if body.country is not None or body.served_countries is not None or body.home_country_code is not None:
        new_country = fields["country"] if "country" in fields else mentor.get("country")
        served = (fields["served_countries"] if "served_countries" in fields
                  else (mentor.get("served_countries") or []))
        extra = [c.get("code") for c in served if c.get("code")]
        new_home = fields["home_country_code"] if "home_country_code" in fields else mentor.get("home_country_code")
        if new_home:
            extra.append(new_home)
        derived = _derive_expertise(new_country, extra)
        if derived:
            fields["expertise_country_codes"] = derived
    if body.city is not None:
        fields["city"] = body.city.strip() or None
    if body.timezone is not None:
        fields["timezone"] = body.timezone
    if body.languages is not None:
        fields["languages"] = body.languages
    if body.social_links is not None:
        fields["social_links"] = [s.model_dump() for s in body.social_links]
    if body.public_notes is not None:
        fields["public_notes"] = body.public_notes.strip() or None
    # (expertise_country_codes is derived from home + current above; any client value is ignored)
    if body.expertise_categories is not None:
        fields["expertise_categories"] = body.expertise_categories
    if body.years_lived_experience is not None:
        fields["years_lived_experience"] = body.years_lived_experience
    if body.years_professional_experience is not None:
        fields["years_professional_experience"] = body.years_professional_experience
    if body.professional_domains is not None:
        fields["professional_domains"] = body.professional_domains
    if body.specializations is not None:
        fields["specializations"] = body.specializations
    if body.hourly_rate is not None:
        fields["hourly_rate"] = body.hourly_rate
    if body.currency is not None:
        fields["currency"] = (body.currency.strip().upper() or "USD")
    if body.currency_rates is not None:
        primary = fields.get("currency") or mentor.get("currency")
        try:
            fields["currency_rates"] = pricing_input.validate_currency_rates(primary, body.currency_rates)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    if body.smart_pricing is not None:
        fields["smart_pricing"] = body.smart_pricing
    try:
        # A migrated mentor reviewing their imported profile during first-login onboarding stays
        # approved and live, so their edits apply straight to the live row (no re-review).
        if mentor.get("needs_onboarding"):
            return db.save_mentor_profile_live(mentor["id"], fields)
        # BUG-075: a live (approved) mentor's edits go live immediately, except Name/Country which
        # need approval; admin is notified either way. changes_requested/rejected mentors still edit
        # in place and resubmit the whole application for review.
        if mentor.get("status") == "approved":
            return _apply_approved_profile_edit(mentor, fields, background_tasks)
        return db.save_mentor_profile_edit(mentor["id"], fields)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class SmartPricingBody(BaseModel):
    enabled: bool


@router.post("/smart-pricing")
def set_smart_pricing(body: SmartPricingBody, mentor: dict = Depends(require_mentor)):
    """Toggle smart (PPP) pricing for the mentor. Applied live (not staged for
    re-approval): flips the flag and re-syncs is_ppp across all their services so
    booking prices reflect it immediately."""
    db.set_mentor_smart_pricing(mentor["id"], body.enabled)
    return {"smart_pricing": body.enabled}


# ── Availability ───────────────────────────────────────────────────────────────

class AvailabilityBody(BaseModel):
    slots: list[AvailabilitySlot]
    session_duration_minutes: int = 60
    availability_type: str = "manual"

    @field_validator("session_duration_minutes")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v not in (30, 60, 90):
            raise ValueError("session_duration_minutes must be 30, 60, or 90")
        return v


@router.get("/availability")
def get_availability(user: AuthUser = Depends(get_current_user)):
    """Return this mentor's weekly availability slots."""
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor:
        raise HTTPException(status_code=404, detail="No mentor profile for this account")
    return {
        "slots": db.get_mentor_availability(mentor["id"]),
        "session_duration_minutes": mentor.get("session_duration_minutes", 60),
        "availability_type": mentor.get("availability_type"),
    }


@router.post("/availability")
def set_availability(body: AvailabilityBody, user: AuthUser = Depends(get_current_user)):
    """Replace all weekly availability slots for this mentor."""
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor:
        raise HTTPException(status_code=404, detail="No mentor profile for this account")
    slots = [s.model_dump() for s in body.slots]
    inserted = db.set_mentor_availability(
        mentor["id"],
        slots=slots,
        session_duration_minutes=body.session_duration_minutes,
        availability_type=body.availability_type,
    )
    return {"saved": len(inserted), "session_duration_minutes": body.session_duration_minutes}


@router.post("/me/deactivate")
def deactivate_mentor(user: AuthUser = Depends(get_current_user)):
    """Self-service pause - sets mentor status to 'suspended', hiding them from browse."""
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor:
        raise HTTPException(status_code=404, detail="No mentor profile for this account")
    if mentor["status"] != "approved":
        raise HTTPException(status_code=400, detail="Only approved mentors can deactivate their profile")
    try:
        db.set_mentor_status(mentor["id"], "suspended")
    except ValueError:
        raise HTTPException(status_code=404, detail="Mentor not found")
    return {"deactivated": True}
