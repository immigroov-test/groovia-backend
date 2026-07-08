import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
from core.auth import AuthUser, get_current_user

logger = logging.getLogger("immigroov.routers.referrals")

router = APIRouter(prefix="/referrals", tags=["referrals"])


# ── Public: click tracking + code validation ─────────────────────────────────

class ClickBody(BaseModel):
    slug: str
    session_token: str


@router.post("/click")
def log_click(body: ClickBody):
    """Public — fired from the landing page when a referral link (/r/{slug})
    is visited. Fails silently on an unknown slug (matches the RPC's own
    silent no-op — a visitor's page load must never break over this)."""
    db.log_referral_click(body.slug, body.session_token)
    return {"logged": True}


@router.get("/validate/{code}")
def validate_code(code: str):
    """Public — checkout UI check for 'code applied — X% off' before payment."""
    return db.validate_referral_code(code)


# ── Affiliate-facing dashboard ────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(user: AuthUser = Depends(get_current_user)):
    try:
        return db.affiliate_dashboard_summary(user.id)
    except Exception as e:
        msg = str(e)
        if "not an affiliate" in msg.lower():
            raise HTTPException(status_code=403, detail=msg)
        logger.exception("affiliate dashboard failed profile=%s", user.id)
        raise HTTPException(status_code=500, detail="Failed to load affiliate dashboard")
