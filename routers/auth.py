from fastapi import APIRouter, Depends
from pydantic import BaseModel

import db
from core.auth import AuthUser, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class CheckEmailBody(BaseModel):
    email: str


@router.post("/check-email")
def check_email(body: CheckEmailBody):
    """Pre-login check → {exists, has_password}. Only accounts that actually have a
    password go to the login screen; unconfirmed / passwordless ones (signInWithOtp
    creates the row before the user finishes) are routed back to verify + set-password.
    Sends no email, so it never trips Supabase's OTP rate limit."""
    return db.get_email_account_status(body.email)


@router.post("/set-guest")
def set_guest(user: AuthUser = Depends(get_current_user)):
    """Mark the (just email-verified, passwordless) account as a guest. Guests book
    once and cannot log in or manage sessions afterwards."""
    db.set_profile_role_guest(user.id)
    return {"role": "guest"}


@router.post("/sync")
def sync_account(user: AuthUser = Depends(get_current_user)):
    """Run once right after login. If this email was pre-approved as a mentor by an
    admin (a mentors row with a matching email and no linked account yet), attach it
    to this account so they get mentor access without registering again."""
    mentor = db.link_mentor_by_email(user.id, user.email)
    return {
        "linked": bool(mentor),
        "role": "mentor" if mentor else "candidate",
        "mentor_status": mentor.get("status") if mentor else None,
    }
