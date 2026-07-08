"""Dev-only router - only mounted when MOCK_SERVICES=true.
Provides local visibility into mocked external service calls."""
import logging

from fastapi import APIRouter

from services import mailer

logger = logging.getLogger("immigroov.routers.dev")

router = APIRouter(prefix="/dev", tags=["dev"])


@router.get("/emails")
def list_dev_emails(limit: int = 50):
    """Return the last N emails captured by the Resend mock (newest first)."""
    return {"emails": mailer.read_dev_emails(limit=limit)}


@router.post("/emails/clear")
def clear_dev_emails():
    """Truncate the dev email log."""
    count = mailer.clear_dev_emails()
    return {"deleted": count}
