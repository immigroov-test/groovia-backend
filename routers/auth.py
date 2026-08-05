from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import db
from core.auth import AuthUser, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class CheckEmailBody(BaseModel):
    email: str


class SyncBody(BaseModel):
    full_name: Optional[str] = None


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
def sync_account(body: SyncBody = SyncBody(), user: AuthUser = Depends(get_current_user)):
    """Run right after login/signup. Three idempotent jobs:
    1. Link a pre-approved mentor (mentors row matched by email, no account yet).
    2. Backfill the profile's name (the signup trigger left it null; the name is
       entered later during password setup).
    3. Attach any guest bookings this email made before signing up."""
    mentor = db.link_mentor_by_email(user.id, user.email)
    db.backfill_profile_name(user.id, body.full_name)
    linked_bookings = db.link_guest_bookings(user.id, user.email)
    return {
        "linked": bool(mentor),
        "role": "mentor" if mentor else "candidate",
        "mentor_status": mentor.get("status") if mentor else None,
        # Migrated mentors must pass the first-login flow; the client routes them to /mentor
        # (where the mandatory welcome popup fires) when this is true.
        "needs_onboarding": bool(mentor.get("needs_onboarding")) if mentor else False,
        "linked_bookings": linked_bookings,
    }
