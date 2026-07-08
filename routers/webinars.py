import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, field_validator

import db
from services import mailer

logger = logging.getLogger("immigroov.routers.webinars")

router = APIRouter(prefix="/webinars", tags=["webinars"])


@router.get("")
def list_webinars():
    """Public browse: upcoming, public, still-open webinars."""
    return db.list_webinars()


@router.get("/{webinar_id}")
def webinar_detail(webinar_id: str):
    """Share-link page — any visibility/status; having the link is the gate,
    matching immigroov's own design. The frontend does its own closed check."""
    webinar = db.webinar_public(webinar_id)
    if not webinar:
        raise HTTPException(status_code=404, detail="Webinar not found")
    return webinar


class RegisterBody(BaseModel):
    email: str
    name: Optional[str] = None

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email address")
        return v


@router.post("/{webinar_id}/register")
def register(webinar_id: str, body: RegisterBody, background_tasks: BackgroundTasks):
    try:
        result = db.register_webinar(webinar_id, body.email, body.name)
    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        if "no longer open" in msg.lower() or "already started" in msg.lower() or "is full" in msg.lower():
            raise HTTPException(status_code=409, detail=msg)
        logger.exception("register_webinar failed webinar=%s", webinar_id)
        raise HTTPException(status_code=500, detail="Registration failed")

    if not result.get("already"):
        background_tasks.add_task(
            mailer.send_transactional, body.email, "webinar_registered", {
                "recipient_name": body.name or "",
                "webinar_title": result.get("title") or "",
                "start_time": str(result.get("start_time") or ""),
                "room_url": result.get("room_url") or "",
            },
        )
    return result
