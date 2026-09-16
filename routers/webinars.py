from datetime import datetime, timezone
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

import config
import db
from core.auth import AuthUser, get_current_user, require_admin
from core.permissions import require_active_mentor

router = APIRouter(tags=["webinars"])


class WebinarBody(BaseModel):
    title: str = Field(min_length=4, max_length=140)
    description: str = Field(default="", max_length=10000)
    banner_url: Optional[str] = None
    mentor_id: Optional[str] = None
    starts_at: datetime
    duration_minutes: int = Field(ge=15, le=480)
    timezone: str = Field(default="UTC", max_length=80)
    registration_deadline: Optional[datetime] = None
    capacity: int = Field(default=100, ge=1, le=10000)
    is_paid: bool = False
    price: float = Field(default=0, ge=0)
    currency: str = "INR"
    meeting_provider: Literal["jitsi_public", "google_meet", "zoom", "teams", "custom"] = "jitsi_public"
    meeting_url: Optional[str] = None

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("Currency must be a three-letter code")
        return value

    def db_fields(self) -> dict:
        data = self.model_dump(mode="json")
        if not data["is_paid"]:
            data["price"] = 0
        elif data["price"] <= 0:
            raise HTTPException(status_code=422, detail="A paid webinar needs a price")
        if data["meeting_provider"] != "jitsi_public" and not data.get("meeting_url"):
            raise HTTPException(status_code=422, detail="A meeting URL is required for this provider")
        return data


class MentorRequestBody(BaseModel):
    title: str = Field(min_length=4, max_length=140)
    description: str = Field(min_length=20, max_length=10000)
    starts_at: datetime
    duration_minutes: int = Field(default=60, ge=15, le=480)
    timezone: str = Field(default="UTC", max_length=80)
    capacity: int = Field(default=100, ge=1, le=10000)
    is_paid: bool = False
    price: float = Field(default=0, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("Currency must be a three-letter code")
        return value


class DecisionBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


@router.get("/webinars")
def public_list():
    return db.list_public_webinars()


@router.get("/webinars/mine")
def mine(user: AuthUser = Depends(get_current_user)):
    return db.my_registrations(user.id)


@router.get("/webinars/{slug}")
def public_detail(slug: str):
    webinar = db.get_public_webinar(slug)
    if not webinar:
        raise HTTPException(status_code=404, detail="Webinar not found")
    return webinar


@router.post("/webinars/{webinar_id}/register")
def register(webinar_id: str, user: AuthUser = Depends(get_current_user)):
    webinar = db.get_webinar(webinar_id)
    if not webinar or webinar.get("status") != "published":
        raise HTTPException(status_code=404, detail="Webinar not found")
    try:
        registration = db.register(webinar_id, user.id, bool(webinar.get("is_paid")))
    except Exception as exc:
        message = str(exc)
        if "FULL" in message or "CLOSED" in message:
            raise HTTPException(status_code=409, detail="Registration is closed or full")
        raise
    if webinar.get("is_paid") and registration.get("status") in ("confirmed", "attended"):
        return {"registration": registration, "payment_required": False, "already_registered": True}
    if not webinar.get("is_paid"):
        return {"registration": registration, "payment_required": False}
    try:
        order = db.create_webinar_razorpay_order(registration)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not create payment order")
    return {"registration": registration, "payment_required": True, "order": {
        "order_id": order["id"], "key_id": config.RAZORPAY_KEY_ID,
        "amount": order["amount"], "currency": order["currency"]}}


class ConfirmPaymentBody(BaseModel):
    registration_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str = ""


@router.post("/webinars/payments/confirm")
def confirm_payment(body: ConfirmPaymentBody, user: AuthUser = Depends(get_current_user)):
    reg = db.registration_for_id(body.registration_id)
    if not reg or reg.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="Registration not found")
    try:
        return db.confirm_razorpay(body.registration_id, body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature)
    except ValueError:
        raise HTTPException(status_code=400, detail="Payment verification failed")


@router.get("/webinars/{webinar_id}/join")
def join(webinar_id: str, user: AuthUser = Depends(get_current_user)):
    is_admin = db.get_profile_role(user.id) == "admin"
    mentor = db.get_mentor_by_profile_id(user.id)
    try:
        details = db.join_details(webinar_id, user.id, is_admin=is_admin, mentor_id=(mentor or {}).get("id"))
    except ValueError as exc:
        detail = "The webinar room has closed" if "CLOSED" in str(exc) else "The webinar room opens 15 minutes before it starts"
        raise HTTPException(status_code=403, detail=detail)
    if not details:
        raise HTTPException(status_code=403, detail="A confirmed registration is required")
    return details


class AttendanceBody(BaseModel):
    event: Literal["join", "leave"]


@router.post("/webinars/{webinar_id}/attendance")
def attendance(webinar_id: str, body: AttendanceBody, user: AuthUser = Depends(get_current_user)):
    if not db.registration_for(webinar_id, user.id):
        raise HTTPException(status_code=403, detail="Registration required")
    db.record_attendance(webinar_id, user.id, body.event)
    return {"ok": True}


@router.get("/mentor/webinars")
def mentor_list(mentor: dict = Depends(require_active_mentor)):
    return db.list_mentor_webinars(mentor["id"])


@router.post("/mentor/webinars/requests")
def mentor_request(body: MentorRequestBody, user: AuthUser = Depends(get_current_user), mentor: dict = Depends(require_active_mentor)):
    fields = body.model_dump(mode="json")
    fields.update({"mentor_id": mentor["id"], "meeting_provider": "jitsi_public", "price": body.price if body.is_paid else 0})
    return db.create_webinar(fields, creator_id=user.id, source="mentor_request", status="pending_review")


@router.get("/admin/webinars")
def admin_list(user: AuthUser = Depends(require_admin)):
    return db.list_admin_webinars()


@router.post("/admin/webinars")
def admin_create(body: WebinarBody, user: AuthUser = Depends(require_admin)):
    return db.create_webinar(body.db_fields(), creator_id=user.id)


@router.patch("/admin/webinars/{webinar_id}")
def admin_update(webinar_id: str, body: WebinarBody, user: AuthUser = Depends(require_admin)):
    if not db.get_webinar(webinar_id):
        raise HTTPException(status_code=404, detail="Webinar not found")
    return db.update_webinar(webinar_id, body.db_fields())


@router.post("/admin/webinars/{webinar_id}/{action}")
def admin_action(webinar_id: str, action: Literal["approve", "request-changes", "reject", "publish", "cancel", "complete"], body: DecisionBody = DecisionBody(), user: AuthUser = Depends(require_admin)):
    webinar = db.get_webinar(webinar_id)
    if not webinar:
        raise HTTPException(status_code=404, detail="Webinar not found")
    states = {"approve": "approved", "request-changes": "changes_requested", "reject": "rejected", "publish": "published", "cancel": "cancelled", "complete": "completed"}
    if action == "publish":
        if webinar.get("meeting_provider") != "jitsi_public" and not webinar.get("meeting_url"):
            raise HTTPException(status_code=409, detail="Add a meeting URL before publishing")
        if datetime.fromisoformat(webinar["starts_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="A webinar must start in the future")
    fields = {"status": states[action], "admin_note": body.note}
    if action == "publish": fields["published_at"] = datetime.now(timezone.utc).isoformat()
    return db.update_webinar(webinar_id, fields)
