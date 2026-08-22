"""BUG-162: read the Immigroov bug board from the admin dashboard.

The board is a SEPARATE Supabase project, so this module owns its own client rather than reusing
`db.mentors._supabase`. The client is built lazily and cached: importing this module must not fail,
and must not open a connection, on an environment where the board is not configured (local dev,
CI, and staging until the env vars are set).

Only the `bugs` table is read. The board also has todos / test_modules / test_cases; those are a
different tool's job and are deliberately not surfaced in the mentor-platform admin.
"""
import logging
from typing import Any, Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.bug_board")

# The board's own vocabulary. Kept here so the API can reject an unknown value before it reaches
# the database, where it would hit a CHECK constraint and come back as an opaque 500.
BUG_STATUSES = ("yet_to_review", "in_progress", "to_be_tested", "completed")

_COLUMNS = ("id, ref_id, title, description, status, priority, issue_type, "
            "reported_by, handled_by, tags, screenshot_urls, created_at, updated_at")

_client: Optional[Client] = None


def enabled() -> bool:
    return config.BUG_BOARD_ENABLED


def _board() -> Client:
    """The bug-board client, created on first use. Raises if the board is not configured - callers
    are expected to check enabled() first and answer the request without touching this."""
    global _client
    if not config.BUG_BOARD_ENABLED:
        raise RuntimeError("Bug board is not configured")
    if _client is None:
        # Anon, not service role - the board's RLS already grants anon full access to `bugs`, so
        # nothing here needs to bypass it. See config.BUG_BOARD_SUPABASE_ANON_KEY.
        _client = create_client(
            config.BUG_BOARD_SUPABASE_URL,
            config.BUG_BOARD_SUPABASE_ANON_KEY,
        )
    return _client


def list_bugs(status: Optional[str] = None, limit: int = 300) -> list[dict[str, Any]]:
    """Every open item on the board, newest first, optionally filtered to one status."""
    query = _board().table("bugs").select(_COLUMNS).order("created_at", desc=True).limit(limit)
    if status:
        query = query.eq("status", status)
    return query.execute().data or []


def set_bug_status(bug_id: str, status: str) -> dict[str, Any]:
    """Move one item to another column. `updated_at` is set here rather than left to the board's
    own default, which only fires on insert."""
    from datetime import datetime, timezone
    if status not in BUG_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(BUG_STATUSES)}")
    res = (_board().table("bugs")
           .update({"status": status, "updated_at": datetime.now(timezone.utc).isoformat()})
           .eq("id", bug_id).execute())
    if not res.data:
        raise ValueError("Bug not found")
    return res.data[0]
