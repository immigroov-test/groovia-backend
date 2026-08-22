# BUG-158: a mentor could only block one date at a time - select a date, block it, wait for the
# page to reload, select the next. This file covers the backend piece: POST
# /mentor/availability-v2/block-dates, which takes a list and writes each date.
#
# The multi-select calendar itself is frontend-only (AvailabilityManagerV2.tsx).
from datetime import date

import pytest
from unittest.mock import patch
from fastapi import HTTPException
from pydantic import ValidationError

import db
from core.auth import AuthUser
from routers.availability import BlockDatesBody, block_dates


def _user():
    return AuthUser(id="profile-1", email="mentor@example.com", role="mentor")


def _mentor():
    return {"id": "mentor-1", "status": "approved", "slug": "a-mentor"}


def test_blocks_every_date_in_one_call():
    body = BlockDatesBody(slot_dates=[date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)])
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor()), \
         patch.object(db, "block_date") as blk:
        result = block_dates(body, user=_user())

    assert result == {"blocked": ["2026-09-01", "2026-09-02", "2026-09-03"], "failed": []}
    assert blk.call_count == 3
    assert [c.kwargs["slot_date"] for c in blk.call_args_list] == \
        ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert {c.kwargs["mentor_id"] for c in blk.call_args_list} == {"mentor-1"}


def test_duplicate_dates_are_written_once_and_order_is_kept():
    body = BlockDatesBody(slot_dates=[date(2026, 9, 3), date(2026, 9, 1), date(2026, 9, 3)])
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor()), \
         patch.object(db, "block_date") as blk:
        result = block_dates(body, user=_user())

    assert result["blocked"] == ["2026-09-03", "2026-09-01"]
    assert blk.call_count == 2


def test_one_bad_date_does_not_lose_the_others():
    """A partial failure keeps the writes that worked and names the ones that did not, rather than
    reporting the whole action as failed and leaving the mentor unsure what got through."""
    def _fail_on_second(*, mentor_id, slot_date):
        if slot_date == "2026-09-02":
            raise RuntimeError("db is unhappy")

    body = BlockDatesBody(slot_dates=[date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)])
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor()), \
         patch.object(db, "block_date", side_effect=_fail_on_second):
        result = block_dates(body, user=_user())

    assert result == {"blocked": ["2026-09-01", "2026-09-03"], "failed": ["2026-09-02"]}


def test_errors_only_when_nothing_could_be_written():
    body = BlockDatesBody(slot_dates=[date(2026, 9, 1), date(2026, 9, 2)])
    with patch.object(db, "get_mentor_by_profile_id", return_value=_mentor()), \
         patch.object(db, "block_date", side_effect=RuntimeError("db is down")), \
         pytest.raises(HTTPException) as exc:
        block_dates(body, user=_user())

    assert exc.value.status_code == 500


def test_rejects_an_empty_selection():
    with pytest.raises(ValidationError):
        BlockDatesBody(slot_dates=[])


def test_rejects_a_runaway_selection():
    with pytest.raises(ValidationError):
        BlockDatesBody(slot_dates=[date(2026, 1, 1)] * 367)


def test_requires_a_mentor_profile():
    body = BlockDatesBody(slot_dates=[date(2026, 9, 1)])
    with patch.object(db, "get_mentor_by_profile_id", return_value=None), \
         pytest.raises(HTTPException) as exc:
        block_dates(body, user=_user())

    assert exc.value.status_code == 404
