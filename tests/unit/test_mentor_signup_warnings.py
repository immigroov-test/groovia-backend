# tests/unit/test_mentor_signup_warnings.py
# BUG-012: weekly_availability/services/booking_rules/date_overrides inserts during
# mentor_signup were each wrapped in a bare try/except that only logged - a mentor
# could get a "successful" signup (mentor row created) while everything else silently
# failed to save, with zero indication why. routers/mentor.mentor_signup now collects
# each failure into a `warnings` list on the response instead of swallowing it.
# Calling the endpoint function directly (not through TestClient) since FastAPI's
# Depends() are only resolved by the app router - a plain function call lets us pass
# BackgroundTasks()/AuthUser() ourselves and mock db.* without any auth scaffolding.
import pytest
from unittest.mock import patch
from fastapi import BackgroundTasks, HTTPException

import db
from core.auth import AuthUser
from routers.mentor import MentorSignupBody, WeeklySlot, ServiceDraft, BankDetailsBody, mentor_signup


def _user():
    return AuthUser(id="11111111-1111-1111-1111-111111111111", email="mentor@example.com", role="authenticated")


def _bank(**overrides):
    fields = dict(country_code="NL", account_holder_name="Test Mentor", iban="NL91ABNA0417164300")
    fields.update(overrides)
    return BankDetailsBody(**fields)


def _base_body(**overrides):
    fields = dict(
        display_name="Test Mentor", agreed_to_mentor_terms=True,
        expertise_country_codes=["NL"], languages=["en"],
        country="NL", home_country_code="IN", years_lived_experience=3, years_professional_experience=5,
        weekly_availability=[WeeklySlot(weekday="Monday", start_time="09:00", end_time="17:00")],
        services=[ServiceDraft(title="Career Chat", duration=30, set_price=50)],
        bank=_bank(),
    )
    fields.update(overrides)
    return MentorSignupBody(**fields)


def _run(body):
    # Bank details are mandatory: pretend the encryption key is configured and stub the encrypted
    # write, so these tests exercise the availability/service warning logic, not the crypto/DB.
    with patch.object(db, "get_mentor_by_profile_id", return_value=None), \
         patch.object(db, "create_mentor_signup", return_value={"id": "mentor-1"}), \
         patch.object(db, "get_mentor_email", return_value=("Test Mentor", None)), \
         patch.object(db, "upsert_mentor_bank"), \
         patch("routers.mentor.bank_crypto.is_configured", return_value=True):
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


def test_signup_rejected_without_bank_details():
    """Bank details are mandatory - signup with none is a 400 before any mentor row is created."""
    with patch.object(db, "get_mentor_by_profile_id", return_value=None), \
         patch.object(db, "create_mentor_signup") as created, \
         patch("routers.mentor.bank_crypto.is_configured", return_value=True):
        with pytest.raises(HTTPException) as exc:
            mentor_signup(_base_body(bank=None), BackgroundTasks(), user=_user())
    assert exc.value.status_code == 400
    created.assert_not_called()   # never leaves a half-created mentor


def test_signup_rejected_with_invalid_bank_details():
    """Invalid bank details are a 422 before the mentor row is created."""
    with patch.object(db, "get_mentor_by_profile_id", return_value=None), \
         patch.object(db, "create_mentor_signup") as created, \
         patch("routers.mentor.bank_crypto.is_configured", return_value=True):
        with pytest.raises(HTTPException) as exc:
            mentor_signup(_base_body(bank=_bank(iban="NL00BAD")), BackgroundTasks(), user=_user())
    assert exc.value.status_code == 422
    created.assert_not_called()


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
