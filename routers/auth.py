from fastapi import APIRouter, Depends

import db
from core.auth import AuthUser, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


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
