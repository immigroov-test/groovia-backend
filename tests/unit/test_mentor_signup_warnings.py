# tests/unit/test_mentor_signup_warnings.py
# BUG-012: weekly_availability/services/booking_rules/date_overrides inserts during
# mentor_signup were each wrapped in a bare try/except that only logged - a mentor
# could get a "successful" signup (mentor row created) while everything else silently
# failed to save, with zero indication why. routers/mentor.mentor_signup now collects
# each failure into a `warnings` list on the response instead of swallowing it.
# Calling the endpoint function directly (not through TestClient) since FastAPI's
# Depends() are only resolved by the app router - a plain function call lets us pass
# BackgroundTasks()/AuthUser() ourselves and mock db.* without any auth scaffolding.
from unittest.mock import patch
from fastapi import BackgroundTasks

import db
from core.auth import AuthUser
from routers.mentor import MentorSignupBody, WeeklySlot, ServiceDraft, mentor_signup


def _user():
    return AuthUser(id="11111111-1111-1111-1111-111111111111", email="mentor@example.com", role="authenticated")


def _base_body(**overrides):
    fields = dict(
        display_name="Test Mentor", agreed_to_mentor_terms=True,
        expertise_country_codes=["NL"], languages=["en"], years_lived_experience=3,
        weekly_availability=[WeeklySlot(weekday="Monday", start_time="09:00", end_time="17:00")],
        services=[ServiceDraft(title="Career Chat", duration=30, set_price=50)],
    )
    fields.update(overrides)
    return MentorSignupBody(**fields)


def _run(body):
    with patch.object(db, "get_mentor_by_profile_id", return_value=None), \
         patch.object(db, "create_mentor_signup", return_value={"id": "mentor-1"}), \
         patch.object(db, "get_mentor_email", return_value=("Test Mentor", None)):
        return mentor_signup(body, BackgroundTasks(), user=_user())


def test_signup_succeeds_with_no_warnings_when_everything_saves():
    with patch.object(db, "add_weekly_availability"), patch.object(db, "create_service"):
        result = _run(_base_body())
    assert result["id"] == "mentor-1"
    assert "warnings" not in result


def test_signup_reports_warning_when_availability_insert_fails():
    with patch.object(db, "add_weekly_availability", side_effect=RuntimeError("db down")), \
         patch.object(db, "create_service"):
        result = _run(_base_body())
    assert result["warnings"] == ["Could not save your Monday availability. Please re-add it from your dashboard."]


def test_signup_reports_warning_when_service_insert_fails():
    with patch.object(db, "add_weekly_availability"), \
         patch.object(db, "create_service", side_effect=RuntimeError("db down")):
        result = _run(_base_body())
    assert result["warnings"] == ['Could not save your session type "Career Chat". Please re-add it from your dashboard.']


def test_signup_collects_multiple_independent_failures():
    with patch.object(db, "add_weekly_availability", side_effect=RuntimeError("db down")), \
         patch.object(db, "create_service", side_effect=RuntimeError("db down")):
        result = _run(_base_body())
    assert len(result["warnings"]) == 2


def test_signup_does_not_raise_when_one_item_fails_among_many():
    """One bad row must not stop the rest of the loop from being attempted."""
    calls = []

    def flaky_add(**kwargs):
        calls.append(kwargs["weekday"])
        if kwargs["weekday"] == "Monday":
            raise RuntimeError("db down")

    body = _base_body(weekly_availability=[
        WeeklySlot(weekday="Monday", start_time="09:00", end_time="17:00"),
        WeeklySlot(weekday="Tuesday", start_time="09:00", end_time="17:00"),
    ])
    with patch.object(db, "add_weekly_availability", side_effect=flaky_add), \
         patch.object(db, "create_service"):
        result = _run(body)
    assert calls == ["Monday", "Tuesday"]   # Tuesday was still attempted
    assert len(result["warnings"]) == 1
