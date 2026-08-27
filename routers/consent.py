import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

import db
from core.auth import AuthUser, get_current_user_optional
from core.rate_limit import limiter

logger = logging.getLogger("immigroov.routers.consent")

router = APIRouter(prefix="/consent", tags=["consent"])


class ConsentBody(BaseModel):
    """A cookie/tracking choice from the banner.

    `analytics` and `marketing` are recorded separately rather than as one boolean: refusing analytics
    while allowing marketing is a real combination, and a single flag could not tell them apart later.
    """
    kind: str = Field(default="cookies", max_length=60)
    analytics: bool = False
    marketing: bool = False
    policy_version: Optional[str] = Field(default=None, max_length=40)
    # A guest has no user_id yet; this is the same localStorage-persisted identity used
    # by the Groovia AI Terms gate (see lib/guestSession.ts on the frontend), so both
    # guest consent events before an account exists share one identity.
    session_id: Optional[str] = Field(default=None, max_length=100)


@router.post("")
@limiter.limit("30/minute")
def record(request: Request, body: ConsentBody,
           user: Optional[AuthUser] = Depends(get_current_user_optional)):
    """Record a cookie/tracking choice (BUG-143).

    Public on purpose: the banner is answered before anyone signs in, and a guest's decision has to be
    recorded too. When a token is present the row is attributed to that user.

    Always returns ok. GDPR asks us to be able to demonstrate consent, but a banner that fails when
    the write fails would be a worse outcome than a missing row, so the client treats this as
    fire-and-forget and db.record_consent logs rather than raising.
    """
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    uid = user.id if user else None

    # One row per category, so a later "did they agree to analytics" is a direct lookup rather than
    # something to be inferred from a combined record.
    for category, granted in (("analytics", body.analytics), ("marketing", body.marketing)):
        db.record_consent(
            kind=f"{body.kind}:{category}",
            user_id=uid,
            granted=granted,
            policy_version=body.policy_version,
            ip=ip,
            user_agent=ua,
        )

    # Consent Flow Spec Section 1/8: the banner is also the Cookie Policy's active-consent
    # trigger point, logged once to the central table regardless of which choice was made
    # (Accept/Reject/Customize) - all three mean the document was shown and answered. This
    # is separate from the per-category analytics/marketing preference rows above, which
    # track WHAT was chosen; this records THAT the document itself was consented to.
    if body.kind == "cookies":
        try:
            db.record_legal_consent(
                ["cookie-policy"], user_id=uid, session_id=(None if uid else body.session_id),
                consent_method="cookie_banner", ip=ip, user_agent=ua,
            )
        except Exception:
            logger.exception("Cookie Policy consent log failed")
    return {"ok": True}
