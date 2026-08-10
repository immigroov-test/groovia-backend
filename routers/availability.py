import logging
from datetime import date, time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

import db
from core.auth import AuthUser, get_current_user
from services import booking_rules

logger = logging.getLogger("immigroov.routers.availability")

router = APIRouter(prefix="/mentor/availability-v2", tags=["mentor-availability"])

_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _get_mentor(user: AuthUser) -> dict:
    mentor = db.get_mentor_by_profile_id(user.id)
    if not mentor:
        raise HTTPException(status_code=404, detail="No mentor profile for this account")
    if mentor.get("status") not in ("approved", "pending_review"):
        raise HTTPException(status_code=403, detail="Mentor profile not active")
    return mentor


def _get_mentor_id(user: AuthUser) -> str:
    return _get_mentor(user)["id"]


# ── Booking rules ──────────────────────────────────────────────────────────────

class BookingRulesBody(BaseModel):
    """Sanity bounds only. The product limits live in services/booking_rules and are enforced in the
    handler, where the mentor is known (BUG-045: test profiles are exempt)."""
    days_ahead: int = 30
    min_notice_hours: float = 2.0
    cancel_hours: Optional[int] = None

    @field_validator("days_ahead")
    @classmethod
    def validate_days(cls, v: int) -> int:
        if v < 0 or v > 365:
            raise ValueError("Booking window must be a sensible number of days")
        return v

    @field_validator("min_notice_hours")
    @classmethod
    def validate_notice(cls, v: float) -> float:
        if v < 0 or v > 168:
            raise ValueError("Minimum booking notice must be a sensible number of hours")
        return v

    @field_validator("cancel_hours")
    @classmethod
    def validate_cancel(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 0 or v > 168):
            raise ValueError("Cancellation / rescheduling notice must be a sensible number of hours")
        return v


@router.get("/rules")
def get_rules(user: AuthUser = Depends(get_current_user)):
    mentor = _get_mentor(user)
    try:
        rules = db.get_availability_rules(mentor["id"])
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load booking rules")
    # BUG-045: ship the limits with the rules so the form validates as you type against the same
    # numbers the server enforces, instead of keeping a second hardcoded copy in the client.
    rules = dict(rules or {})
    rules["limits"] = booking_rules.limits()
    rules["limits_enforced"] = mentor.get("slug") not in booking_rules.EXEMPT_MENTOR_SLUGS
    return rules


@router.post("/rules")
def set_rules(body: BookingRulesBody, user: AuthUser = Depends(get_current_user)):
    mentor = _get_mentor(user)
    mentor_id = mentor["id"]
    try:                                                    # BUG-045: enforce the product limits
        booking_rules.validate(
            days_ahead=body.days_ahead,
            min_notice_hours=body.min_notice_hours,
            cancel_hours=body.cancel_hours,
            mentor_slug=mentor.get("slug"),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    try:
        db.set_availability_rules(
            mentor_id=mentor_id,
            days_ahead=body.days_ahead,
            min_notice_hours=body.min_notice_hours,
            cancel_hours=body.cancel_hours,
        )
        return {"updated": True}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update booking rules")


# ── Weekly schedule ────────────────────────────────────────────────────────────

class WeeklySlotBody(BaseModel):
    weekday: str
    start_time: str   # "HH:MM"
    end_time: str     # "HH:MM"

    @field_validator("weekday")
    @classmethod
    def validate_day(cls, v: str) -> str:
        if v not in _DAYS:
            raise ValueError(f"weekday must be one of: {', '.join(_DAYS)}")
        return v

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError("time must be HH:MM")
        h, m = parts
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("Invalid time")
        return v


@router.get("/weekly")
def list_weekly(user: AuthUser = Depends(get_current_user)):
    mentor_id = _get_mentor_id(user)
    try:
        return db.list_weekly_availability(mentor_id)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load weekly schedule")


@router.post("/weekly")
def add_weekly(body: WeeklySlotBody, user: AuthUser = Depends(get_current_user)):
    mentor_id = _get_mentor_id(user)
    try:
        db.add_weekly_availability(
            mentor_id=mentor_id,
            weekday=body.weekday,
            start_time=body.start_time,
            end_time=body.end_time,
        )
        return {"added": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("add_weekly failed mentor=%s", mentor_id)
        raise HTTPException(status_code=500, detail="Failed to add weekly slot")


@router.post("/weekly/{slot_id}/delete")
def delete_weekly(slot_id: str, user: AuthUser = Depends(get_current_user)):
    _get_mentor_id(user)
    try:
        db.remove_weekly_availability(slot_id)
        return {"deleted": True}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete weekly slot")


# ── Specific overrides ─────────────────────────────────────────────────────────

class BlockDateBody(BaseModel):
    slot_date: date


@router.post("/block-date")
def block_date(body: BlockDateBody, user: AuthUser = Depends(get_current_user)):
    mentor_id = _get_mentor_id(user)
    try:
        db.block_date(mentor_id=mentor_id, slot_date=str(body.slot_date))
        return {"blocked": True}
    except Exception:
        logger.exception("block_date failed mentor=%s date=%s", mentor_id, body.slot_date)
        raise HTTPException(status_code=500, detail="Failed to block date")


class OverrideDateBody(BaseModel):
    slot_date: date
    start_time: str
    end_time: str

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError("time must be HH:MM")
        return v


@router.post("/override-date")
def override_date(body: OverrideDateBody, user: AuthUser = Depends(get_current_user)):
    mentor_id = _get_mentor_id(user)
    try:
        db.override_date(
            mentor_id=mentor_id,
            slot_date=str(body.slot_date),
            start_time=body.start_time,
            end_time=body.end_time,
        )
        return {"overridden": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to override date")


@router.get("/specific")
def list_specific(user: AuthUser = Depends(get_current_user)):
    mentor_id = _get_mentor_id(user)
    try:
        return db.list_specific_availability(mentor_id)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load specific availability")


@router.post("/specific/{entry_id}/delete")
def delete_specific(entry_id: str, user: AuthUser = Depends(get_current_user)):
    _get_mentor_id(user)
    try:
        db.remove_specific_availability(entry_id)
        return {"deleted": True}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete entry")


# ── Mentor sessions ────────────────────────────────────────────────────────────

@router.get("/sessions")
def get_sessions(user: AuthUser = Depends(get_current_user)):
    mentor_id = _get_mentor_id(user)
    try:
        return db.list_mentor_sessions(mentor_id)
    except Exception:
        logger.exception("get_sessions failed mentor=%s", mentor_id)
        raise HTTPException(status_code=500, detail="Failed to load sessions")
