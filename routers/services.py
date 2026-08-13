import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

import db
from core.permissions import require_mentor
from services import pricing_input

logger = logging.getLogger("immigroov.routers.services")

router = APIRouter(prefix="/mentor/services", tags=["mentor-services"])


# ── Service CRUD ───────────────────────────────────────────────────────────────

class ServiceCreateBody(BaseModel):
    title: str
    description: Optional[str] = None
    type: str = "video"
    duration: int = 30
    category: Optional[str] = None
    set_price: float = 0
    set_offer_price: Optional[float] = None
    currency_prices: list[dict] = []     # explicit per-currency prices [{currency, base_price, offer_price?}]
    is_active: bool = True
    is_ppp: bool = False
    tags: list[str] = []

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for t in (v or []):
            t = (t or "").strip()[:40]
            if t and t.lower() not in {c.lower() for c in cleaned}:
                cleaned.append(t)
        if len(cleaned) > 5:
            raise ValueError("You can add up to 5 tags")
        return cleaned

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("video", "dm"):
            raise ValueError("type must be 'video' or 'dm'")
        return v

    @field_validator("duration")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v < 5 or v > 480:
            raise ValueError("duration must be between 5 and 480 minutes")
        return v

    @field_validator("set_price")
    @classmethod
    def validate_price(cls, v: float) -> float:
        if v < 0:
            raise ValueError("set_price must be >= 0")
        return round(v, 2)


@router.get("")
def list_services(mentor: dict = Depends(require_mentor)):
    mentor_id = mentor["id"]
    try:
        return db.list_services(mentor_id)
    except Exception:
        logger.exception("list_services failed mentor=%s", mentor_id)
        raise HTTPException(status_code=500, detail="Failed to load services")


@router.post("")
def create_service(body: ServiceCreateBody, mentor: dict = Depends(require_mentor)):
    mentor_id = mentor["id"]
    primary_ccy = (mentor.get("currency") or "USD").upper()
    # BUG-059: free is reserved for a single Introductory-call-style slot - reject a second
    # zero-priced service server-side too, since the frontend check alone can be bypassed.
    if body.set_price == 0 and any(float(s.get("set_price") or 0) == 0 for s in db.list_services(mentor_id)):
        raise HTTPException(status_code=422, detail="You already have a free session. Only one is allowed.")
    try:
        currency_prices = pricing_input.validate_currency_prices(primary_ccy, body.currency_prices)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    try:
        service_id = db.create_service(
            mentor_id=mentor_id,
            title=body.title.strip(),
            description=(body.description or "").strip() or None,
            type=body.type,
            duration=body.duration,
            category=(body.category or "").strip() or None,
            set_price=body.set_price,
            is_active=body.is_active,
            is_ppp=body.is_ppp,
            tags=body.tags,
            set_currency=primary_ccy,   # hub-created services are priced in the mentor's own currency
            set_offer_price=body.set_offer_price,
            currency_prices=currency_prices,
        )
        return {"id": service_id}
    except Exception:
        logger.exception("create_service failed mentor=%s", mentor_id)
        raise HTTPException(status_code=500, detail="Failed to create service")


class ServiceUpdateBody(BaseModel):
    is_active: bool


# BUG-137: a mentor previously had no way to edit an existing service at all - only toggle
# active/delete. This is deliberately scoped to the service's TEXT/DETAIL fields (title,
# description, category, tags); duration and price are left untouched here since they're tied to
# the one-service-per-duration slot model and the hourly-rate-derived pricing business rule
# (BUG-135/create_service) - editing those isn't what "view and edit the details of an existing
# service" was asking for, and changing that pricing model is out of scope for this fix.
class ServiceEditBody(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("Title cannot be empty")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        cleaned: list[str] = []
        for t in v:
            t = (t or "").strip()[:40]
            if t and t.lower() not in {c.lower() for c in cleaned}:
                cleaned.append(t)
        if len(cleaned) > 5:
            raise ValueError("You can add up to 5 tags")
        return cleaned


def _assert_service_owner(service_id: str, mentor_id: str) -> None:
    if db.get_service_mentor_id(service_id) != mentor_id:
        raise HTTPException(status_code=403, detail="You do not own this service")


@router.post("/{service_id}/active")
def set_service_active(service_id: str, body: ServiceUpdateBody, mentor: dict = Depends(require_mentor)):
    _assert_service_owner(service_id, mentor["id"])
    try:
        db.set_service_active(service_id, body.is_active)
        return {"updated": True}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update service")


@router.post("/{service_id}/update")
def update_service(service_id: str, body: ServiceEditBody, mentor: dict = Depends(require_mentor)):
    """Edit an existing service's title/description/category/tags in place, whatever its current
    status (approved/pending/rejected) - the same permission level the mentor already has to
    toggle it active or delete it. Does not touch or re-trigger the admin approval workflow;
    that stays governed entirely by /approve and /reject."""
    _assert_service_owner(service_id, mentor["id"])
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "title" in fields and fields["title"] is not None:
        fields["title"] = fields["title"].strip()
    if "description" in fields:
        fields["description"] = (fields["description"] or "").strip() or None
    if "category" in fields:
        # BUG-118: never let an edit put category back to NULL - same safe default the create
        # path and the migrated-services backfill both use.
        fields["category"] = (fields["category"] or "").strip() or "General Guidance"
    try:
        db.update_service(service_id, fields)
        return {"updated": True}
    except Exception:
        logger.exception("update_service failed id=%s", service_id)
        raise HTTPException(status_code=500, detail="Failed to update service")


@router.post("/{service_id}/delete")
def delete_service(service_id: str, mentor: dict = Depends(require_mentor)):
    _assert_service_owner(service_id, mentor["id"])
    try:
        db.delete_service(service_id)
        return {"deleted": True}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete service")


# ── Questions ──────────────────────────────────────────────────────────────────

class QuestionCreateBody(BaseModel):
    service_id: str
    question_text: str
    is_required: bool = False
    question_type: str = "text"

    @field_validator("question_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("text", "multiple_choice", "yes_no"):
            raise ValueError("question_type must be 'text', 'multiple_choice', or 'yes_no'")
        return v

    @field_validator("question_text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question_text cannot be empty")
        if len(v) > 500:
            raise ValueError("question_text must be 500 chars or fewer")
        return v


@router.get("/{service_id}/questions")
def list_questions(service_id: str, mentor: dict = Depends(require_mentor)):
    _assert_service_owner(service_id, mentor["id"])
    try:
        return db.list_questions(service_id)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load questions")


@router.post("/{service_id}/questions")
def add_question(service_id: str, body: QuestionCreateBody, mentor: dict = Depends(require_mentor)):
    if service_id != body.service_id:
        raise HTTPException(status_code=400, detail="service_id mismatch")
    _assert_service_owner(service_id, mentor["id"])
    try:
        q_id = db.add_question(
            service_id=service_id,
            text=body.question_text,
            required=body.is_required,
            q_type=body.question_type,
        )
        return {"id": q_id}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to add question")


@router.post("/questions/{question_id}/delete")
def delete_question(question_id: str, mentor: dict = Depends(require_mentor)):
    if db.get_question_mentor_id(question_id) != mentor["id"]:
        raise HTTPException(status_code=403, detail="You do not own this question")
    try:
        db.delete_question(question_id)
        return {"deleted": True}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete question")


# ── Public: list a mentor's active services ────────────────────────────────────

@router.get("/public/{mentor_slug}")
def list_public_services(mentor_slug: str):
    """Public endpoint - returns active services for a mentor profile page."""
    mentor = db.get_mentor_by_slug(mentor_slug)
    if not mentor:
        raise HTTPException(status_code=404, detail="Mentor not found")
    try:
        rows = db.list_services(mentor["id"], active_only=True)
        return {"services": rows}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load services")


@router.get("/{service_id}/questions/public")
def list_public_questions(service_id: str):
    """Public endpoint - returns active intake questions for a service (shown during booking)."""
    try:
        return db.list_questions(service_id)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load questions")
