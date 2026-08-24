"""Legal Documents CMS data layer.

Every call is a thin wrapper over an RPC in migrations/legal_documents_setup.sql.
The rules that matter — a version is immutable, a draft never publishes, publishing
is one transaction, a document only reaches the roles it targets — live in SQL so
they hold for any caller, not just this module.

Errors are deliberately NOT swallowed here. A failed save or publish has to reach
the admin as a message, not a silently empty result, so the router maps the SQL
guards (SQLSTATE P0001, user-facing text) to a 400.
"""
import logging
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.legal")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


# ── Admin: read ──────────────────────────────────────────────────────────────
def legal_admin_documents() -> list[dict[str, Any]]:
    """All 14 documents for the CMS table: audience, current version, last updated,
    publisher, and whether an unpublished draft is waiting."""
    res = _supabase.rpc("legal_admin_documents", {}).execute()
    return res.data or []


def legal_admin_document(document_id: str) -> Optional[dict[str, Any]]:
    """One document for the editor: editor_content (the draft if there is one, else the
    published text), the live version, and the full version history."""
    res = _supabase.rpc("legal_admin_document", {"p_document_id": document_id}).execute()
    return res.data or None


def legal_admin_version(version_id: str) -> Optional[dict[str, Any]]:
    """A single archived version, read-only."""
    res = _supabase.rpc("legal_admin_version", {"p_version_id": version_id}).execute()
    return res.data or None


# ── Admin: write ─────────────────────────────────────────────────────────────
def legal_save_draft(document_id: str, actor_id: str, content: str) -> dict[str, Any]:
    """Save the working copy. Creates no version, moves no date, notifies nobody."""
    res = _supabase.rpc("legal_save_draft", {
        "p_document_id": document_id, "p_actor": actor_id, "p_content": content,
    }).execute()
    return res.data or {}


def legal_discard_draft(document_id: str, actor_id: str) -> dict[str, Any]:
    """Throw the draft away and fall back to the published text."""
    res = _supabase.rpc("legal_discard_draft", {
        "p_document_id": document_id, "p_actor": actor_id,
    }).execute()
    return res.data or {}


def legal_publish(document_id: str, actor_id: str, change_note: Optional[str] = None,
                  major: bool = False) -> dict[str, Any]:
    """Publish the draft as a new official version.

    One RPC call = one transaction: the version insert, the current-version switch and
    the draft clear all land together or not at all. Raises on an empty draft or on a
    draft identical to what is already live (accidental duplicate publish)."""
    res = _supabase.rpc("publish_legal_document", {
        "p_document_id": document_id, "p_actor": actor_id,
        "p_change_note": change_note, "p_major": major,
    }).execute()
    return res.data or {}


# ── User ─────────────────────────────────────────────────────────────────────
def legal_user_documents(user_id: str) -> list[dict[str, Any]]:
    """The published documents that apply to this user's role and region, latest version
    of each, with their acknowledgement state."""
    res = _supabase.rpc("legal_user_documents", {"p_user": user_id}).execute()
    return res.data or []


def legal_user_document(user_id: str, slug: str) -> Optional[dict[str, Any]]:
    """One applicable document by slug. None when it does not apply to this user, so the
    caller 404s rather than showing a customer a mentor contract."""
    res = _supabase.rpc("legal_user_document", {"p_user": user_id, "p_slug": slug}).execute()
    return res.data or None


def legal_pending_updates(user_id: str) -> list[dict[str, Any]]:
    """Documents this user should see the "Legal document updated" notice for: applicable,
    published since their account existed, and not yet acknowledged at the current version."""
    res = _supabase.rpc("legal_pending_updates", {"p_user": user_id}).execute()
    return res.data or []


def legal_acknowledge(user_id: str, version_id: str) -> dict[str, Any]:
    """Record that this user reviewed this exact version. Idempotent."""
    res = _supabase.rpc("legal_acknowledge", {
        "p_user": user_id, "p_version_id": version_id,
    }).execute()
    return res.data or {}


# ── Public (no sign-in) ──────────────────────────────────────────────────────
def legal_public_documents() -> list[dict[str, Any]]:
    """Every publicly readable published document, in catalogue order, with content.

    Backs the single public legal page, which renders all of them as sections."""
    res = _supabase.rpc("legal_public_documents", {}).execute()
    return res.data or []


def legal_public_document(slug: str) -> Optional[dict[str, Any]]:
    """A published document that is marked publicly readable, by slug.

    Backs /privacy and /terms, which have to render for visitors with no account.
    Returns None for a document that is not public, so the caller 404s rather than
    leaking a role-targeted contract to an anonymous request."""
    res = _supabase.rpc("legal_public_document", {"p_slug": slug}).execute()
    return res.data or None


# ── Helper for the seed script ───────────────────────────────────────────────
def first_admin_profile_id() -> Optional[str]:
    """Any admin account, used as the publisher when the seed script is run without
    --actor so the initial import has a name against it rather than an empty cell."""
    try:
        res = (
            _supabase.table("profiles")
            .select("id")
            .eq("role", "admin")
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        return res.data[0]["id"] if res.data else None
    except Exception:
        logger.exception("first_admin_profile_id failed")
        return None
