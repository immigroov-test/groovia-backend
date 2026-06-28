import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

import db
from core.permissions import require_mentor

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
    is_active: bool = True
    is_ppp: bool = False

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
        )
        return {"id": service_id}
    except Exception:
        logger.exception("create_service failed mentor=%s", mentor_id)
        raise HTTPException(status_code=500, detail="Failed to create service")


class ServiceUpdateBody(BaseModel):
    is_active: bool


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
    """Public endpoint — returns active services for a mentor profile page."""
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
    """Public endpoint — returns active intake questions for a service (shown during booking)."""
    try:
        return db.list_questions(service_id)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load questions")
