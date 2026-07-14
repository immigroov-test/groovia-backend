import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, field_validator, model_validator

import config
import db
from services import mailer
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


# ── Initial signup ─────────────────────────────────────────────────────────────

class WeeklySlot(BaseModel):
    weekday: str        # "Monday" .. "Sunday"
    start_time: str     # "HH:MM"
    end_time: str       # "HH:MM"


class ServiceDraft(BaseModel):
    title: str
    duration: int       # 15 | 30 | 45 | 60
    is_active: bool = True
    set_price: float = 0   # prorated from the mentor's hourly rate, editable per session
    description: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = []


class BookingRules(BaseModel):
    days_ahead: int = 30
    min_notice_hours: float = 2
    cancel_hours: int = 24


class DateOverrideDraft(BaseModel):
    slot_date: str                      # YYYY-MM-DD
    is_blackout: bool = False
    start_time: Optional[str] = None    # HH:MM (custom hours)
    end_time: Optional[str] = None


class MentorSignupBody(BaseModel):
    display_name: str
    headline: Optional[str] = None
    photo_url: Optional[str] = None
    phone: Optional[str] = None
    bio: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    timezone: str = "UTC"
    languages: list[str] = []
    social_links: list[SocialLink] = []
    public_notes: Optional[str] = None
    expertise_country_codes: list[str] = []
    expertise_categories: list[str] = []
    years_lived_experience: Optional[int] = None
    professional_domains: list[str] = []
    agreed_to_mentor_terms: bool = False
    hourly_rate: Optional[float] = None
    currency: str = "USD"
    smart_pricing: bool = False
    weekly_availability: list[WeeklySlot] = []
    services: list[ServiceDraft] = []
    booking_rules: Optional[BookingRules] = None
    date_overrides: list[DateOverrideDraft] = []

    @field_validator("city")
    @classmethod
    def validate_city(cls, v: Optional[str]) -> Optional[str]:
        return _validate_city(v)

    @field_validator("years_lived_experience")
    @classmethod
    def validate_years(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 0 or v > 60):
            raise ValueError("years_lived_experience must be between 0 and 60")
        return v

    @field_validator("expertise_country_codes")
    @classmethod
    def validate_expertise_countries(cls, v: list[str]) -> list[str]:
        if len(v) > 2:
            raise ValueError("You can select a maximum of 2 countries of expertise")
        return v


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
    if not body.expertise_country_codes:
        raise HTTPException(status_code=400, detail="Select at least one country of expertise")
    if not body.languages:
        raise HTTPException(status_code=400, detail="Select at least one language")
    if body.years_lived_experience is None:
        raise HTTPException(status_code=400, detail="Years of lived experience is required")
    result = db.create_mentor_signup(
        user.id,
        display_name=display_name,
        headline=(body.headline or "").strip() or None,
        photo_url=(body.photo_url or "").strip() or None,
        phone=(body.phone or "").strip() or None,
        bio=(body.bio or "").strip() or None,
        country=(body.country or "").strip() or None,
        city=(body.city or "").strip() or None,
        timezone_name=body.timezone,
        languages=body.languages,
        social_links=[s.model_dump() for s in body.social_links],
        public_notes=(body.public_notes or "").strip() or None,
        expertise_country_codes=body.expertise_country_codes,
        expertise_categories=body.expertise_categories,
        years_lived_experience=body.years_lived_experience,
        professional_domains=body.professional_domains,
        hourly_rate=body.hourly_rate,
        currency=body.currency,
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
                              tags=svc.tags)
        except Exception:
            logger.exception("Service create failed during signup for mentor %s", mentor_id)
            warnings.append(f"Could not save your session type \"{svc.title}\". Please re-add it from your dashboard.")
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
    if config.ADMIN_EMAIL:
        background_tasks.add_task(
            mailer.send_transactional,
            config.ADMIN_EMAIL,
            "admin_mentor_application",
            {
                "display_name": display_name,
                "mentor_email": mentor_email or "",
                "headline": (body.headline or "").strip(),
                "review_url": config.FRONTEND_URL + "/admin",
            },
        )
    return result


# ── Profile editing ────────────────────────────────────────────────────────────

class ProfileUpdateBody(BaseModel):
    display_name: Optional[str] = None
    headline: Optional[str] = None
    photo_url: Optional[str] = None
    phone: Optional[str] = None
    bio: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    timezone: Optional[str] = None
    languages: Optional[list[str]] = None
    social_links: Optional[list[SocialLink]] = None
    public_notes: Optional[str] = None
    expertise_country_codes: Optional[list[str]] = None
    expertise_categories: Optional[list[str]] = None
    years_lived_experience: Optional[int] = None
    professional_domains: Optional[list[str]] = None
    hourly_rate: Optional[float] = None
    currency: Optional[str] = None

    @field_validator("city")
    @classmethod
    def validate_city(cls, v: Optional[str]) -> Optional[str]:
        return _validate_city(v)

    @field_validator("years_lived_experience")
    @classmethod
    def validate_years(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 0 or v > 60):
            raise ValueError("years_lived_experience must be between 0 and 60")
        return v

    @field_validator("hourly_rate")
    @classmethod
    def validate_rate(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and (v < 0 or v > 100000):
            raise ValueError("hourly_rate must be between 0 and 100000")
        return round(v, 2) if v is not None else v


@router.post("/profile")
def update_profile(body: ProfileUpdateBody, user: AuthUser = Depends(get_current_user)):
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
    if body.expertise_country_codes is not None:
        if not body.expertise_country_codes:
            raise HTTPException(status_code=400, detail="Select at least one country of expertise")
        fields["expertise_country_codes"] = body.expertise_country_codes
    if body.expertise_categories is not None:
        fields["expertise_categories"] = body.expertise_categories
    if body.years_lived_experience is not None:
        fields["years_lived_experience"] = body.years_lived_experience
    if body.professional_domains is not None:
        fields["professional_domains"] = body.professional_domains
    if body.hourly_rate is not None:
        fields["hourly_rate"] = body.hourly_rate
    if body.currency is not None:
        fields["currency"] = (body.currency.strip().upper() or "USD")
    try:
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
