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

from fastapi import APIRouter, Depends, HTTPException, Request
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

import db
from core.auth import AuthUser, get_current_user, require_admin
from core.rate_limit import limiter

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


@router.post("/admin/documents/{document_id}/publish")
@limiter.limit("20/minute")
def publish(request: Request, document_id: str, body: PublishBody,
            user: AuthUser = Depends(require_admin)):
    """Publish Official Update.

    Creates the next version from the draft, stamps the date and publisher, and makes it
    the live version — all in one transaction. From that moment every applicable user who
    has not acknowledged this version is eligible for the notice; eligibility is derived
    from the version rows on read, so there is no queue that could disagree with what was
    actually published.

    Rejected with a 400 when there is no draft, or when the draft is identical to the
    live version (a double-click on Publish, or a re-save with no real change)."""
    try:
        return db.legal_publish(document_id, user.id, body.change_note, body.major)
    except Exception as e:
        _raise_legal_error(e, "Could not publish the update")


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
