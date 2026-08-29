import base64
import hashlib
import hmac
import logging
import time
from typing import Optional

from fastapi import BackgroundTasks, APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import config
import db
from core.auth import AuthUser, get_current_user
from services import mailer

logger = logging.getLogger("immigroov.routers.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


class CheckEmailBody(BaseModel):
    email: str


class SyncBody(BaseModel):
    full_name: Optional[str] = None
    # Only sent by the ONE-TIME signup-completion call (AuthModal's password-setup
    # step) - never by the routine sync calls that fire on every login/tab-focus, so
    # this endpoint being "run on every login" does not repeatedly re-record consent.
    accepted_terms: Optional[bool] = None
    marketing_consent: Optional[bool] = None
    # Where the tick happened. Signing IN is a consent event in its own right: the box is on
    # the first step of the modal and has to be ticked every time, so a returning user agrees
    # to whatever is live at that moment. Recording it is what makes that agreement provable
    # (GDPR Art. 7(1) puts the burden of demonstrating consent on the controller); a UI gate
    # with no row behind it proves nothing.
    consent_context: Optional[str] = None      # 'signup' | 'signin'


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
def sync_account(request: Request, background_tasks: BackgroundTasks, body: SyncBody = SyncBody(),
                 user: AuthUser = Depends(get_current_user)):
    """Run right after login/signup. Idempotent jobs:
    1. Link a pre-approved mentor (mentors row matched by email, no account yet).
    2. Backfill the profile's name (the signup trigger left it null; the name is
       entered later during password setup).
    3. Attach any guest bookings this email made before signing up.
    4. Record consent to the Terms of Use + Privacy Policy, when accepted_terms is sent -
       by the signup completion call, or by a sign-in, where the same checkbox is ticked
       again. Customer T&C, Payment Terms and the Refund & Cancellation Policy are a
       booking-time concern (routers/booking.py, routers/payments.py), not a login one.

    Signup is guarded against a retried/duplicated request re-recording the same signup:
    once this user has a live consent record for the current Privacy Policy version, a
    repeat signup-completion call records nothing. Sign-in is NOT guarded - the
    configuration requires a fresh record on every login, so every tick of the box
    writes, whether or not the text has changed since the last one."""
    mentor = db.link_mentor_by_email(user.id, user.email)
    db.backfill_profile_name(user.id, body.full_name)
    linked_bookings = db.link_guest_bookings(user.id, user.email)
    if body.accepted_terms:
        is_signin = body.consent_context == "signin"
        if is_signin or not db.legal_has_current_consent("privacy-policy", user_id=user.id):
            try:
                ip = request.client.host if request.client else None
                ua = request.headers.get("user-agent")
                method = "checkbox_signin" if is_signin else "checkbox_signup"
                db.record_legal_consent(
                    ["website-terms-of-use", "privacy-policy"],
                    user_id=user.id, consent_method=method, ip=ip, user_agent=ua)
                # Marketing consent (spec: "must be a separate, unbundled checkbox").
                # Logged in our own consent_events table for now; HubSpot contact sync
                # is a separate task once a portal ID/API key exists - not wired here.
                if body.marketing_consent is not None:
                    db.record_consent(kind="marketing:signup", user_id=user.id,
                                      granted=body.marketing_consent, ip=ip, user_agent=ua)
            except Exception:
                logger.exception("Signup consent log failed for user %s", user.id)
    # BUG-147: new customers never got a welcome (mentors get theirs on approval). Claimed once per
    # account, since this endpoint runs on every login, and skipped for mentors who get their own.
    if not mentor and user.email and db.claim_welcome_email(user.id):
        background_tasks.add_task(
            mailer.send_transactional, user.email, "welcome_candidate",
            {"candidate_name": (body.full_name or "").strip() or "there",
             "platform_url": config.FRONTEND_URL},
        )
    return {
        "linked": bool(mentor),
        "role": "mentor" if mentor else "candidate",
        "mentor_status": mentor.get("status") if mentor else None,
        # Migrated mentors must pass the first-login flow; the client routes them to /mentor
        # (where the mandatory welcome popup fires) when this is true.
        "needs_onboarding": bool(mentor.get("needs_onboarding")) if mentor else False,
        "linked_bookings": linked_bookings,
    }


# ── Send Email auth hook (BUG-026) ──────────────────────────────────────────────
# Supabase Auth can be configured (Dashboard -> Authentication -> Hooks -> Send Email) to call this
# endpoint instead of sending sign-in/signup/recovery emails itself, so they go out from Immigroov's
# own Resend sender (config.EMAIL_FROM) instead of Supabase's default. Supabase signs the hook with
# the Standard Webhooks scheme (same as Svix) - a shared secret, NOT a JWT, so it's verified by hand
# here rather than through core.auth.
_WEBHOOK_TOLERANCE_SECONDS = 300   # reject a hook whose timestamp has drifted more than 5 minutes


def _verify_auth_hook_signature(raw_body: bytes, headers: dict) -> bool:
    if not config.SUPABASE_AUTH_HOOK_SECRET:
        return False   # fail closed: never process an unverifiable hook
    webhook_id = headers.get("webhook-id")
    webhook_timestamp = headers.get("webhook-timestamp")
    webhook_signature = headers.get("webhook-signature")
    if not (webhook_id and webhook_timestamp and webhook_signature):
        return False
    try:
        if abs(time.time() - int(webhook_timestamp)) > _WEBHOOK_TOLERANCE_SECONDS:
            return False
    except ValueError:
        return False

    secret = config.SUPABASE_AUTH_HOOK_SECRET
    secret_bytes = base64.b64decode(secret[len("whsec_"):] if secret.startswith("whsec_") else secret)
    signed_content = f"{webhook_id}.{webhook_timestamp}.".encode() + raw_body
    expected = base64.b64encode(hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()).decode()

    # webhook-signature is a space-separated list of "v1,<base64sig>" - any match is valid
    # (Supabase may sign with more than one active secret during rotation).
    for part in webhook_signature.split():
        _, _, sig = part.partition(",")
        if sig and hmac.compare_digest(sig, expected):
            return True
    return False


_AUTH_EMAIL_TEMPLATES = {
    "signup": "auth_signup_confirm",
    "magiclink": "auth_magic_link",
    "recovery": "auth_recovery",
    "invite": "auth_generic",
    "email_change": "auth_generic",
    "email_change_current": "auth_generic",
    "email_change_new": "auth_generic",
    "reauthentication": "auth_generic",
}


@router.post("/email-hook")
async def auth_email_hook(request: Request):
    """Supabase Auth Send Email hook. Verifies the Standard Webhooks signature, then sends the
    matching branded email via Resend (services/mailer.py) instead of letting Supabase send its
    own. Returns 200 on success; Supabase falls back to its own default email on any non-2xx (so
    a real failure here degrades gracefully rather than blocking sign-in)."""
    raw_body = await request.body()
    if not _verify_auth_hook_signature(raw_body, dict(request.headers)):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    user = payload.get("user") or {}
    email_data = payload.get("email_data") or {}
    to = user.get("email")
    token_hash = email_data.get("token_hash")
    action_type = email_data.get("email_action_type")
    if not (to and token_hash and action_type):
        raise HTTPException(status_code=400, detail="Missing required auth hook fields")

    # redirect_to is whatever the client passed as emailRedirectTo/redirectTo when it called
    # signInWithOtp/resetPasswordForEmail - already a full ".../auth/callback?next=..." URL
    # (see AuthModal.tsx's signupSetupRedirect/handleForgot), so just add the two params
    # AuthCallbackClient needs; falls back to a bare callback URL if Supabase ever omits it.
    redirect_to = email_data.get("redirect_to") or f"{config.FRONTEND_URL.rstrip('/')}/auth/callback"
    sep = "&" if "?" in redirect_to else "?"
    action_url = f"{redirect_to}{sep}token_hash={token_hash}&type={action_type}"
    template = _AUTH_EMAIL_TEMPLATES.get(action_type, "auth_generic")
    try:
        mailer.send_transactional(to, template, {"action_url": action_url})
    except Exception:
        logger.exception("auth_email_hook: failed to send %s to %s", template, to)
        raise HTTPException(status_code=500, detail="Failed to send email")
    return {"ok": True}
