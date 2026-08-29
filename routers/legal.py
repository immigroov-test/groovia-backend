"""Legal Documents CMS — admin editing/publishing and the user-facing read/acknowledge.

Two audiences on one router, split by dependency:
  /legal/admin/*   require_admin      — the CMS: edit, save draft, publish, history
  /legal/*         get_current_user   — a signed-in user's own applicable documents

The authorization that matters is not only "is this caller an admin". A normal user
must never be able to read a document aimed at a different role, so the user endpoints
resolve applicability in SQL from the caller's OWN id — there is no document-id
parameter a caller could point at someone else's contract.
"""
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

import db
from core.auth import AuthUser, get_current_user, get_current_user_optional, require_admin
from core.rate_limit import limiter
from services import mailer

logger = logging.getLogger("immigroov.routers.legal")

router = APIRouter(prefix="/legal", tags=["legal"])


def _raise_legal_error(e: Exception, fallback: str) -> None:
    """Every guard in the legal SQL layer raises SQLSTATE P0001 with text written for the
    person reading it ("There is no draft to publish", "This draft is identical to the
    published v1.2"). Surface those as a 400 so the admin sees the reason; anything else
    is an internal fault and gets the generic message."""
    if isinstance(e, APIError) and getattr(e, "code", None) == "P0001":
        raise HTTPException(status_code=400, detail=getattr(e, "message", None) or str(e))
    raise HTTPException(status_code=500, detail=fallback)


# ── Bodies ───────────────────────────────────────────────────────────────────
class DraftBody(BaseModel):
    # Long-form contracts: the longest of the fourteen is ~6.5k characters, so the cap
    # is generous enough to never truncate real content but still bounds a bad request.
    content: str = Field(..., max_length=200_000)


class PublishBody(BaseModel):
    change_note: Optional[str] = Field(None, max_length=500)
    # A minor bump (v1.2 -> v1.3) is the default because most updates are amendments.
    # major=True (v1.2 -> v2.0) marks a rewrite substantial enough that the version
    # number itself should say so.
    major: bool = False


class AcknowledgeBody(BaseModel):
    version_id: str


class SetActiveBody(BaseModel):
    is_active: bool


class GrooviaTermsStatusBody(BaseModel):
    # A guest has no bearer identity yet; this is the localStorage-persisted id the
    # client mints once and reuses for every guest consent write before an account or
    # booking exists (see lib/chatStorage.ts on the frontend).
    session_id: Optional[str] = Field(None, max_length=100)


class DataSubjectRequestBody(BaseModel):
    name: str = Field(..., max_length=200)
    email: str = Field(..., max_length=320)
    request_type: str = Field(..., pattern="^(access|rectification|erasure|portability|other)$")
    details: Optional[str] = Field(None, max_length=5000)


class DataSubjectRequestStatusBody(BaseModel):
    status: str = Field(..., pattern="^(open|in_progress|closed)$")


# ── Admin ────────────────────────────────────────────────────────────────────
@router.get("/admin/documents")
def admin_documents(user: AuthUser = Depends(require_admin)):
    """The CMS table: all 14 documents with audience, current version, last updated,
    who published it, and whether a draft is waiting."""
    return db.legal_admin_documents()


@router.get("/admin/documents/{document_id}")
def admin_document(document_id: str, user: AuthUser = Depends(require_admin)):
    """One document for the editor, plus its full version history."""
    doc = db.legal_admin_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Legal document not found")
    return doc


@router.get("/admin/versions/{version_id}")
def admin_version(version_id: str, user: AuthUser = Depends(require_admin)):
    """An archived version, read-only. Nothing in the API can modify it — the table has
    a trigger that rejects UPDATE and DELETE outright."""
    version = db.legal_admin_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return version


@router.post("/admin/documents/{document_id}/draft")
@limiter.limit("60/minute")
def save_draft(request: Request, document_id: str, body: DraftBody,
               user: AuthUser = Depends(require_admin)):
    """Save Draft.

    Writes the working copy and nothing else: no version row, no change to the official
    version or its date, and no notification. A draft is invisible to users by
    construction — the user-facing queries read from legal_document_versions, which a
    draft never touches."""
    try:
        return db.legal_save_draft(document_id, user.id, body.content)
    except Exception as e:
        _raise_legal_error(e, "Could not save the draft")


@router.post("/admin/documents/{document_id}/draft/discard")
def discard_draft(document_id: str, user: AuthUser = Depends(require_admin)):
    """Throw away the unpublished draft and fall back to the published text."""
    try:
        return db.legal_discard_draft(document_id, user.id)
    except Exception as e:
        _raise_legal_error(e, "Could not discard the draft")


@router.post("/admin/documents/{document_id}/active")
def set_active(document_id: str, body: SetActiveBody, user: AuthUser = Depends(require_admin)):
    """Take a document out of circulation, or bring it back.

    Never a delete: the immutability trigger on legal_document_versions already makes
    that impossible for a document with published history, and is_active is the
    sanctioned way to stop serving one while its version history and every
    acknowledgement/consent event against it stay exactly as they were."""
    try:
        return db.legal_admin_set_active(document_id, user.id, body.is_active)
    except Exception as e:
        _raise_legal_error(e, "Could not update the document")



def _email_legal_update(document_id: str, version: str, change_note: Optional[str]) -> None:
    """Tell everyone a materially-revised document binds that it changed.

    Best-effort and per-recipient: one bad address must not stop the rest of the send, and
    a mail failure must never make a successful publish look like it failed. The document
    is already live either way - the email is a notification, not part of publishing."""
    # legal_publish returns the version, not the document, so the title is looked up here
    # rather than threaded through the response shape.
    try:
        doc = db.legal_admin_document(document_id) or {}
        title = doc.get("title") or "legal document"
        recipients = db.legal_document_recipients(document_id)
    except Exception:
        logger.exception("legal update email: could not resolve recipients for %s", document_id)
        return
    sent = 0
    for r in recipients:
        email = (r.get("email") or "").strip()
        if not email:
            continue
        try:
            mailer.send_transactional(email, "legal_document_updated", {
                "recipient_name": r.get("name") or "",
                "doc_title": title,
                "version": version,
                "change_note": change_note,
            })
            sent += 1
        except Exception:
            logger.exception("legal update email failed for %s", email)
    logger.info("legal update email: %s/%s sent for %s", sent, len(recipients), title)


@router.post("/admin/documents/{document_id}/publish")
@limiter.limit("20/minute")
def publish(request: Request, document_id: str, body: PublishBody,
            background_tasks: BackgroundTasks, user: AuthUser = Depends(require_admin)):
    """Publish Official Update.

    Creates the next version from the draft, stamps the date and publisher, and makes it
    the live version — all in one transaction. From that moment every applicable user who
    has not acknowledged this version is eligible for the notice; eligibility is derived
    from the version rows on read, so there is no queue that could disagree with what was
    actually published.

    Rejected with a 400 when there is no draft, or when the draft is identical to the
    live version (a double-click on Publish, or a re-save with no real change)."""
    try:
        result = db.legal_publish(document_id, user.id, body.change_note, body.major)
    except Exception as e:
        _raise_legal_error(e, "Could not publish the update")
        raise   # unreachable: _raise_legal_error always raises. Keeps the type checker honest.
    # FEAT-030: notify only on a MATERIAL revision. Emailing everyone about a corrected
    # typo is how a notice stops being read, and then the one that matters goes unread too.
    if body.major:
        background_tasks.add_task(
            _email_legal_update, document_id, (result or {}).get("version") or "", body.change_note,
        )
    return result


# ── Public ───────────────────────────────────────────────────────────────────
@router.get("/public")
def public_documents():
    """Every publicly readable legal document, with content, in catalogue order.

    No authentication: this backs the public legal page, which is linked from the
    cookie banner and the signup form and is read by people who have no account.
    Only documents flagged is_public in the database are returned."""
    return db.legal_public_documents()


@router.get("/public/{slug}")
def public_document(slug: str):
    """A published legal document, with no authentication at all.

    Deliberately open: /privacy and /terms are linked from the cookie banner and the
    signup form, sit in the sitemap, and are read by people who have no account. Only
    documents flagged is_public in the database are served here - the SQL applies that
    gate, so this endpoint cannot be pointed at a mentor or customer contract."""
    doc = db.legal_public_document(slug)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


# ── User ─────────────────────────────────────────────────────────────────────
@router.get("/documents")
def my_documents(user: AuthUser = Depends(get_current_user)):
    """The published documents that apply to this user, latest version of each.
    Read-only: there is no user-facing write path to a document's content."""
    return db.legal_user_documents(user.id)


@router.get("/documents/{slug}")
def my_document(slug: str, user: AuthUser = Depends(get_current_user)):
    """One applicable document by slug. 404 when it does not apply to this user — a
    customer asking for the Mentor Agreement gets the same answer as for a slug that
    does not exist, which is also what stops the URL being used to enumerate documents
    aimed at other roles."""
    doc = db.legal_user_document(user.id, slug)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/pending")
def pending_updates(user: AuthUser = Depends(get_current_user)):
    """Documents to show the "Legal document updated" notice for. Called on login.

    Empty for a user who has acknowledged everything current, and empty again for the
    same version once acknowledged — the notice only comes back when a NEW version is
    published."""
    return db.legal_pending_updates(user.id)


@router.get("/pending/full")
def pending_updates_full(user: AuthUser = Depends(get_current_user)):
    """The same set as /pending, with full content. Backs the single bundled review
    page: everything the notice is about, on one page, behind one acknowledgement."""
    return db.legal_pending_updates_full(user.id)


@router.post("/acknowledge-all")
@limiter.limit("15/minute")
def acknowledge_all(request: Request, user: AuthUser = Depends(get_current_user)):
    """"I have reviewed these documents" for the whole pending set in one click.

    Per the Bundling Guide: one acceptance click per session, but each document still
    gets its own timestamped row in user_legal_acknowledgements - so a dispute over one
    specific document (the Mentor Commission Terms, say) still has a precise,
    per-document record to point to, even though the user only clicked once.

    A MATERIAL revision also writes a consent event. The two tables answer different
    questions: user_legal_acknowledgements records that someone was shown a new version and
    responded, while legal_consent_events records that they AGREED, against an immutable
    version id and with the address and client that did it. For an editorial fix the first
    is an honest record of what happened; for a change to terms someone is already bound by,
    the second is the one that has to exist, because it is the evidence of agreement.

    Consent is captured BEFORE the acknowledgement, while the pending set still names the
    material documents - acknowledging is what empties it."""
    material: list[str] = []
    try:
        pending = db.legal_pending_updates(user.id) or []
        material = [p["slug"] for p in pending if p.get("is_major") and p.get("slug")]
    except Exception:
        logger.exception("Could not resolve material updates for user %s", user.id)

    result = db.legal_acknowledge_all(user.id)

    if material:
        try:
            db.record_legal_consent(
                material, user_id=user.id, consent_method="checkbox_reconsent",
                ip=(request.client.host if request.client else None),
                user_agent=request.headers.get("user-agent"))
        except Exception:
            logger.exception("Re-consent record failed for user %s", user.id)
    return result


@router.post("/acknowledge")
@limiter.limit("30/minute")
def acknowledge(request: Request, body: AcknowledgeBody,
                user: AuthUser = Depends(get_current_user)):
    """"I have reviewed this document" — records (user, document, version, timestamp).

    Tied to the VERSION, not the document, which is the whole mechanism: acknowledging
    v1.2 leaves v1.3 unacknowledged, so publishing v1.3 shows the notice again."""
    try:
        return db.legal_acknowledge(user.id, body.version_id)
    except Exception as e:
        _raise_legal_error(e, "Could not record your acknowledgement")


# ── Groovia AI Terms — one-time gate on the first message sent to Groovia ────
# Distinct from the AI Disclosure Notice (a passive transparency label, always visible):
# this is the liability/usage agreement, and it is ACTIVE - the spec requires it be
# clicked through once, not merely displayed.
@router.get("/groovia-ai-terms/status")
def groovia_terms_status(session_id: Optional[str] = None,
                          user: Optional[AuthUser] = Depends(get_current_user_optional)):
    """Has this identity already accepted the CURRENT version of the Groovia AI Terms?
    Checked before the chat send action fires, so a returning user or guest is never
    re-prompted for a version they already accepted. A signed-in user's own id always
    wins over a stray session_id, since a signed-in acceptance is the stronger record."""
    accepted = db.legal_has_current_consent(
        "groovia-ai-terms", user_id=(user.id if user else None), session_id=session_id)
    return {"accepted": accepted}


@router.post("/groovia-ai-terms/accept")
@limiter.limit("20/minute")
def groovia_terms_accept(request: Request, body: GrooviaTermsStatusBody,
                         user: Optional[AuthUser] = Depends(get_current_user_optional)):
    """Record acceptance of the Groovia AI Terms - the modal's Accept button.

    One-time per user, or per guest session. A guest who later creates an account is
    NOT carried forward (recommended by the spec itself): their new user_id has no
    prior row, so the gate fires again under the new identity - by design, not a bug."""
    if not user and not body.session_id:
        raise HTTPException(status_code=400, detail="Missing session identity")
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    try:
        return db.record_legal_consent(
            ["groovia-ai-terms"],
            user_id=(user.id if user else None),
            session_id=(None if user else body.session_id),
            consent_method="modal_groovia_ai_terms", ip=ip, user_agent=ua,
        )
    except Exception as e:
        _raise_legal_error(e, "Could not record your acceptance")


# ── Data Subject Rights — a rights-exercise page, not a consent document ─────
# Intake only: this creates a ticket and notifies admins. It does not itself export or
# delete anything - fulfillment is a manual operational workflow, matching the spec's
# own caution ("confirm the deletion workflow exists... or you're creating a promise
# the system can't yet keep").
@router.post("/data-subject-requests")
@limiter.limit("5/minute")
def submit_data_subject_request(request: Request, body: DataSubjectRequestBody,
                                background_tasks: BackgroundTasks,
                                user: Optional[AuthUser] = Depends(get_current_user_optional)):
    """Public on purpose: exercising your rights must not itself require an account."""
    row = db.create_data_subject_request(
        body.name.strip(), body.email.strip().lower(), body.request_type,
        (body.details or "").strip() or None, user.id if user else None,
    )
    recipients = db.admin_notify_emails() or ["support@immigroov.com"]
    for to in recipients:
        background_tasks.add_task(
            mailer.send_transactional, to, "data_subject_request",
            {"name": body.name, "email": body.email, "request_type": body.request_type,
             "details": body.details or "", "request_id": row.get("id", "")},
        )
    return {"ok": True}


@router.get("/admin/data-subject-requests")
def admin_list_data_subject_requests(status: Optional[str] = None,
                                     user: AuthUser = Depends(require_admin)):
    """Admin queue for Section 7 intake tickets."""
    return db.list_data_subject_requests(status)


@router.post("/admin/data-subject-requests/{request_id}/status")
def admin_update_data_subject_request(request_id: str, body: DataSubjectRequestStatusBody,
                                      user: AuthUser = Depends(require_admin)):
    """Mark a request in_progress or closed once it has been handled outside this
    table (a manual export/deletion workflow)."""
    return db.close_data_subject_request(request_id, body.status)
