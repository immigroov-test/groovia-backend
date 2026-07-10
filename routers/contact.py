import logging

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel, Field, field_validator

import config
from core.rate_limit import limiter
from services import mailer

logger = logging.getLogger("immigroov.routers.contact")

router = APIRouter(prefix="/contact", tags=["contact"])


class ContactBody(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(default="", max_length=100)
    email: str = Field(min_length=3, max_length=200)
    topic: str = Field(default="", max_length=120)
    message: str = Field(min_length=1, max_length=6000)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip()
        if "@" not in v or "." not in v.rsplit("@", 1)[-1]:
            raise ValueError("Invalid email address")
        return v


@router.post("")
@limiter.limit("5/minute")
def submit_contact(request: Request, body: ContactBody, background_tasks: BackgroundTasks):
    """Public contact form. Emails the message to the support inbox (best-effort)."""
    to = config.ADMIN_EMAIL or "support@immigroov.com"
    background_tasks.add_task(
        mailer.send_transactional, to, "contact_form",
        {
            "first_name": body.first_name,
            "last_name": body.last_name,
            "email": body.email,
            "topic": body.topic,
            "message": body.message,
        },
    )
    logger.info("contact form from %s topic=%r", body.email, body.topic)
    return {"ok": True}
