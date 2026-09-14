import logging
import uuid
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
    service_id: Optional[str] = None   # the session type at checkout; a scoped code needs it
    email: Optional[str] = None        # so a repeat use is refused before payment, not after


@router.post("/validate")
def validate_code(body: ValidateBody):
    """Public: check a referral code at checkout. Returns {valid, discount_pct, ...}.
    The discount the customer sees comes from HERE (backend-checked), never the client."""
    try:
        email = (body.email or "").strip().lower() or None
        return db.validate_referral_code(body.code.strip(), body.service_id, email)
    except Exception:
        logger.exception("validate_referral_code failed")
        return {"valid": False, "discount_pct": 0, "reason": "error"}


class ClickBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=120)
    token: Optional[str] = Field(None, min_length=8, max_length=128)


@router.post("/click")
def record_click(body: ClickBody):
    """Public: a promoter's link was opened. Records the click against a browser session token and
    returns the token to store, so the same visitor can still be matched at checkout up to 60 days
    later. The token identifies a browser, not a person, and grants nothing on its own: it only
    matches if a real click was logged against it."""
    token = (body.token or "").strip() or uuid.uuid4().hex
    try:
        res = db.record_referral_click(body.slug.strip(), token)
    except Exception:
        logger.exception("record_referral_click failed")
        return {"ok": False, "token": token, "reason": "error"}
    return {**res, "token": token}


# ── Mentor (their own codes) ─────────────────────────────────────────────────

class GenerateCodeBody(BaseModel):
    discount_pct: float = Field(0, ge=0, le=20)    # v2: 20% hard cap; the DB enforces it too
    redemption_cap: Optional[int] = Field(None, ge=1, le=1_000_000)  # None -> DB applies a finite default
    expires_at: Optional[str] = None               # ISO8601; None -> DB applies the default expiry
    service_id: Optional[str] = None               # scope the code to one session type; None = any


class JoinBody(BaseModel):
    agreed: bool = False
    terms_version: str = Field(db.TERMS_VERSION, max_length=32)


class CodeActiveBody(BaseModel):
    is_active: bool


@router.get("/mine")
def my_referrals(mentor: dict = Depends(require_mentor)):
    """This mentor's referral codes + promoter earnings."""
    return db.mentor_referral_overview(mentor["id"])


@router.post("/join")
def join_program(body: JoinBody, mentor: dict = Depends(require_mentor)):
    """The mentor joins the referral programme. Their link and codes earn referral splits from
    this moment; before it, bookings through their own link paid the organic rate."""
    if not body.agreed:
        raise HTTPException(status_code=400, detail="Please accept the programme terms to join")
    try:
        return db.join_referral_program(mentor["id"], body.terms_version)
    except Exception:
        logger.exception("join_referral_program failed")
        raise HTTPException(status_code=500, detail="Could not join the programme")


@router.post("/leave")
def leave_program(mentor: dict = Depends(require_mentor)):
    """Leaving ends every open attribution tied to this mentor and deactivates their codes."""
    try:
        return db.leave_referral_program(mentor["id"])
    except Exception:
        logger.exception("leave_referral_program failed")
        raise HTTPException(status_code=500, detail="Could not leave the programme")


@router.post("/codes")
def create_code(body: GenerateCodeBody, mentor: dict = Depends(require_mentor)):
    try:
        code = db.generate_referral_code(
            mentor_id=mentor["id"],
            discount_pct=body.discount_pct,
            redemption_cap=body.redemption_cap,
            expires_at=body.expires_at,
            service_id=body.service_id,
        )
        return {"code": code}
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "Discount must be between" in msg:
            raise HTTPException(status_code=400, detail="The discount cannot be above 20%")
        if "does not belong" in msg:
            raise HTTPException(status_code=400, detail="That session type is not one of yours")
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


class OnboardAffiliateBody(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=3, max_length=200)
    audience_corridor: Optional[str] = Field(None, max_length=120)   # e.g. "IN -> NL", free text
    is_house_channel: bool = False
    discount_pct: Optional[float] = Field(None, ge=0, le=20)          # set -> a code is issued with it (20% cap)
    redemption_cap: Optional[int] = Field(None, ge=1, le=1_000_000)
    code_expires_at: Optional[str] = None


@router.post("/admin/affiliates")
def admin_onboard_affiliate(body: OnboardAffiliateBody, user: AuthUser = Depends(require_admin)):
    """Create a non-mentor influencer with their link, and a code if a discount is given."""
    try:
        return db.admin_onboard_affiliate(
            display_name=body.display_name.strip(), email=body.email.strip().lower(),
            audience_corridor=body.audience_corridor, is_house_channel=body.is_house_channel,
            discount_pct=body.discount_pct, redemption_cap=body.redemption_cap,
            code_expires_at=body.code_expires_at,
        )
    except Exception as e:
        msg = str(e)
        if "already exists" in msg:
            raise HTTPException(status_code=409, detail="An affiliate with this email already exists")
        if "valid email" in msg:
            raise HTTPException(status_code=400, detail="A valid email is required")
        if "name is required" in msg:
            raise HTTPException(status_code=400, detail="A name is required")
        if "Discount must be between" in msg:
            raise HTTPException(status_code=400, detail="That discount is above the allowed maximum")
        logger.exception("admin_onboard_affiliate failed")
        raise HTTPException(status_code=500, detail="Could not create the affiliate")


@router.post("/admin/affiliates/{affiliate_id}/codes")
def admin_affiliate_code(affiliate_id: str, body: GenerateCodeBody, user: AuthUser = Depends(require_admin)):
    """Issue another code for an affiliate the admin manages."""
    try:
        code = db.admin_generate_affiliate_code(
            affiliate_id, discount_pct=body.discount_pct,
            redemption_cap=body.redemption_cap, expires_at=body.expires_at,
            service_id=body.service_id,
        )
        return {"code": code}
    except Exception as e:
        msg = str(e)
        if "Discount must be between" in msg:
            raise HTTPException(status_code=400, detail="The discount cannot be above 20%")
        if "does not belong" in msg:
            raise HTTPException(status_code=400, detail="That session type does not belong to this affiliate")
        if "Affiliate not found" in msg:
            raise HTTPException(status_code=404, detail="Affiliate not found")
        logger.exception("admin_generate_affiliate_code failed")
        raise HTTPException(status_code=500, detail="Could not create the code")


class AffiliateStatusBody(BaseModel):
    status: str = Field(..., pattern="^(active|frozen)$")
    note: Optional[str] = None


@router.post("/admin/affiliates/{affiliate_id}/status")
def admin_affiliate_status(affiliate_id: str, body: AffiliateStatusBody, user: AuthUser = Depends(require_admin)):
    """Freeze or reactivate an affiliate's channel. What they already earned is not touched."""
    try:
        return db.admin_set_affiliate_status(affiliate_id, body.status, None, body.note)
    except Exception as e:
        if "Affiliate not found" in str(e):
            raise HTTPException(status_code=404, detail="Affiliate not found")
        logger.exception("admin_set_affiliate_status failed")
        raise HTTPException(status_code=500, detail="Could not update the affiliate")


@router.get("/admin/flags")
def admin_flags(include_resolved: bool = Query(False), user: AuthUser = Depends(require_admin)):
    """The review queue: open fraud flags, each with the affiliate's history and the booking."""
    return db.admin_fraud_queue(include_resolved)


class FlagDecisionBody(BaseModel):
    decision: str = Field(..., pattern="^(approve|approve_with_note|reject_and_hold)$")
    note: Optional[str] = None


@router.post("/admin/flags/{flag_id}")
def admin_flag_decision(flag_id: str, body: FlagDecisionBody, user: AuthUser = Depends(require_admin)):
    try:
        return db.admin_resolve_fraud_flag(flag_id, body.decision, body.note)
    except Exception as e:
        msg = str(e)
        if "note is required" in msg:
            raise HTTPException(status_code=400, detail="Add a note to approve with a note")
        if "already been decided" in msg:
            raise HTTPException(status_code=409, detail="This flag has already been decided")
        if "Flag not found" in msg:
            raise HTTPException(status_code=404, detail="Flag not found")
        logger.exception("admin_resolve_fraud_flag failed")
        raise HTTPException(status_code=500, detail="Could not record the decision")


@router.get("/admin/payout-batches")
def admin_batches(user: AuthUser = Depends(require_admin)):
    """Payout batches with their commission count and total."""
    return db.admin_payout_batches()


class BuildBatchBody(BaseModel):
    batch_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.post("/admin/payout-batches")
def admin_build_batch(body: BuildBatchBody, user: AuthUser = Depends(require_admin)):
    """Sweep every approved, eligible, unflagged commission into this batch."""
    try:
        return db.build_payout_batch(body.batch_date)
    except Exception as e:
        msg = str(e)
        if "1st and the 15th" in msg:
            raise HTTPException(status_code=400, detail="Batches run on the 1st and the 15th only")
        if "already finalized" in msg:
            raise HTTPException(status_code=409, detail="That batch is already finalized and cannot be rebuilt")
        logger.exception("build_payout_batch failed")
        raise HTTPException(status_code=500, detail="Could not build the batch")


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
