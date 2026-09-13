# BUG-162: the Immigroov bug board, surfaced in the admin dashboard.
#
# The board is a SEPARATE Supabase project, so the interesting cases are the ones where it is not
# configured or not reachable: neither may take the admin dashboard down with it.
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import db
from core.auth import AuthUser
from routers.admin import BugStatusBody, list_bugs, set_bug_status


def _admin():
    return AuthUser(id="admin-1", email="admin@example.com", role="admin")


def _bug(**over):
    base = {
        "id": "11111111-1111-4000-8000-000000000001", "ref_id": "BUG-162",
        "title": "Integrate bug board", "description": None, "status": "in_progress",
        "priority": "medium", "issue_type": "bug", "reported_by": "gautham",
        "handled_by": None, "tags": [], "screenshot_urls": [],
        "created_at": "2026-08-01T00:00:00+00:00", "updated_at": None,
    }
    base.update(over)
    return base


# ── not configured is a state, not a failure ──────────────────────────────────

def test_listing_an_unconfigured_board_is_not_an_error():
    """Local dev and a fresh staging box have no board credentials. The dashboard has to render."""
    with patch.object(db.bug_board, "enabled", return_value=False):
        result = list_bugs(user=_admin())

    assert result == {"configured": False, "bugs": [], "statuses": list(db.bug_board.BUG_STATUSES)}


def test_updating_an_unconfigured_board_is_refused_clearly():
    with patch.object(db.bug_board, "enabled", return_value=False), \
         pytest.raises(HTTPException) as exc:
        set_bug_status("some-id", BugStatusBody(status="completed"), user=_admin())
    assert exc.value.status_code == 503


# ── reading ───────────────────────────────────────────────────────────────────

def test_lists_the_board():
    with patch.object(db.bug_board, "enabled", return_value=True), \
         patch.object(db.bug_board, "list_bugs", return_value=[_bug()]) as lister:
        result = list_bugs(user=_admin())

    assert result["configured"] is True
    assert result["bugs"][0]["ref_id"] == "BUG-162"
    assert lister.call_args.kwargs["status"] is None


def test_filters_by_status():
    with patch.object(db.bug_board, "enabled", return_value=True), \
         patch.object(db.bug_board, "list_bugs", return_value=[]) as lister:
        list_bugs(status="completed", user=_admin())
    assert lister.call_args.kwargs["status"] == "completed"


def test_rejects_a_status_the_board_does_not_have():
    """Caught here rather than as an opaque CHECK-constraint 500 from Postgres."""
    with patch.object(db.bug_board, "enabled", return_value=True), \
         pytest.raises(HTTPException) as exc:
        list_bugs(status="wontfix", user=_admin())
    assert exc.value.status_code == 422


def test_an_unreachable_board_is_a_502_not_a_crash():
    with patch.object(db.bug_board, "enabled", return_value=True), \
         patch.object(db.bug_board, "list_bugs", side_effect=RuntimeError("network is down")), \
         pytest.raises(HTTPException) as exc:
        list_bugs(user=_admin())
    assert exc.value.status_code == 502


# ── writing ───────────────────────────────────────────────────────────────────

def test_moves_a_bug_between_columns():
    with patch.object(db.bug_board, "enabled", return_value=True), \
         patch.object(db.bug_board, "set_bug_status", return_value=_bug(status="completed")) as setter:
        result = set_bug_status("bug-1", BugStatusBody(status="completed"), user=_admin())

    assert result["status"] == "completed"
    assert setter.call_args.args == ("bug-1", "completed")


def test_a_bad_status_is_a_422():
    with patch.object(db.bug_board, "enabled", return_value=True), \
         patch.object(db.bug_board, "set_bug_status", side_effect=ValueError("status must be one of: ...")), \
         pytest.raises(HTTPException) as exc:
        set_bug_status("bug-1", BugStatusBody(status="nope"), user=_admin())
    assert exc.value.status_code == 422


@pytest.mark.parametrize("status", ["yet_to_review", "in_progress", "to_be_tested", "completed"])
def test_the_vocabulary_matches_the_board_schema(status):
    """These are the board's CHECK constraint values; drifting from them breaks every write."""
    assert status in db.bug_board.BUG_STATUSES


def test_set_bug_status_validates_before_touching_the_network():
    """The guard is in the data layer too, so a direct caller cannot skip it."""
    with pytest.raises(ValueError):
        db.bug_board.set_bug_status("bug-1", "not-a-real-status")
