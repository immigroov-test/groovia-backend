import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import db
from core.auth import AuthUser, require_admin
from core.permissions import require_mentor

logger = logging.getLogger("immigroov.routers.referrals")

router = APIRouter(prefix="/referrals", tags=["referrals"])


# ── Checkout (public) ────────────────────────────────────────────────────────

class ValidateBody(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)


@router.post("/validate")
def validate_code(body: ValidateBody):
    """Public: check a referral code at checkout. Returns {valid, discount_pct, ...}.
    The discount the customer sees comes from HERE (backend-checked), never the client."""
    try:
        return db.validate_referral_code(body.code.strip())
    except Exception:
        logger.exception("validate_referral_code failed")
        return {"valid": False, "discount_pct": 0, "reason": "error"}


# ── Mentor (their own codes) ─────────────────────────────────────────────────

class GenerateCodeBody(BaseModel):
    discount_pct: float = Field(0, ge=0, le=100)   # hard ceiling; the DB enforces referral_max_discount_pct
    redemption_cap: Optional[int] = Field(None, ge=1, le=1_000_000)  # None -> DB applies a finite default
    expires_at: Optional[str] = None               # ISO8601; None -> DB applies the default expiry


class CodeActiveBody(BaseModel):
    is_active: bool


@router.get("/mine")
def my_referrals(mentor: dict = Depends(require_mentor)):
    """This mentor's referral codes + promoter earnings."""
    return db.mentor_referral_overview(mentor["id"])


@router.post("/codes")
def create_code(body: GenerateCodeBody, mentor: dict = Depends(require_mentor)):
    try:
        code = db.generate_referral_code(
            mentor_id=mentor["id"],
            discount_pct=body.discount_pct,
            redemption_cap=body.redemption_cap,
            expires_at=body.expires_at,
        )
        return {"code": code}
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "Discount must be between" in msg:
            raise HTTPException(status_code=400, detail="That discount is above the allowed maximum")
        logger.exception("generate_referral_code failed")
        raise HTTPException(status_code=500, detail="Could not create the code")


@router.post("/codes/{code_id}/active")
def toggle_code_active(code_id: str, body: CodeActiveBody, mentor: dict = Depends(require_mentor)):
    if not db.set_code_active(code_id, body.is_active, mentor["id"]):
        raise HTTPException(status_code=404, detail="Code not found")
    return {"ok": True}


# ── Admin ────────────────────────────────────────────────────────────────────

@router.get("/admin/overview")
def admin_overview(user: AuthUser = Depends(require_admin)):
    """One row per affiliate: codes, referrals, discount, money."""
    return db.admin_referrals_overview()


@router.get("/admin/bookings")
def admin_bookings(affiliate_id: Optional[str] = Query(None), user: AuthUser = Depends(require_admin)):
    """One row per referred booking: who gave the code, customer, service, discount, split, amount."""
    return db.admin_referral_bookings(affiliate_id)


class CommissionStatusBody(BaseModel):
    status: str = Field(..., pattern="^(approved|rejected|paid|void)$")
    note: Optional[str] = None


@router.post("/admin/commission/{ledger_id}")
def admin_commission_status(ledger_id: str, body: CommissionStatusBody, user: AuthUser = Depends(require_admin)):
    try:
        # admin_id left NULL: referral_admin_actions.admin_id FKs profiles(id); the action + note
        # are still recorded for the audit trail.
        db.admin_set_commission_status(ledger_id, body.status, None, body.note)
        return {"ok": True}
    except Exception:
        logger.exception("admin_set_commission_status failed")
        raise HTTPException(status_code=500, detail="Could not update the commission")
