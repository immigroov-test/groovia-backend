# tests/unit/test_mentor_city_validation.py
# BUG-004: city was a free-text field with no validation, letting through digits/
# symbols/HTML. routers/mentor.py's MentorSignupBody and ProfileUpdateBody both now
# reject anything but letters/spaces/hyphens/apostrophes/periods/commas via
# _validate_city - test the Pydantic models directly rather than the full endpoints
# since neither has existing auth/mock scaffolding on this branch.
import pytest
from pydantic import ValidationError

from routers.mentor import MentorSignupBody, ProfileUpdateBody


@pytest.mark.parametrize("value", [
    "Amsterdam", "Xi'an", "Winston-Salem", "Washington, D.C.", "Düsseldorf",
    "São Paulo", "St. Louis", "New York", "  Berlin  ",  # leading/trailing whitespace trimmed
])
def test_signup_city_accepts_real_names(value):
    body = MentorSignupBody(display_name="Test", agreed_to_mentor_terms=True, city=value)
    assert body.city == value.strip()


@pytest.mark.parametrize("value", [
    "12345", "Amsterdam123", "<script>alert(1)</script>", "City!!", "a@b.com", "Öland_Test",
])
def test_signup_city_rejects_invalid_input(value):
    with pytest.raises(ValidationError):
        MentorSignupBody(display_name="Test", agreed_to_mentor_terms=True, city=value)


def test_signup_city_blank_becomes_none():
    body = MentorSignupBody(display_name="Test", agreed_to_mentor_terms=True, city="   ")
    assert body.city is None


def test_signup_city_none_stays_none():
    body = MentorSignupBody(display_name="Test", agreed_to_mentor_terms=True, city=None)
    assert body.city is None


def test_signup_city_over_100_chars_rejected():
    with pytest.raises(ValidationError):
        MentorSignupBody(display_name="Test", agreed_to_mentor_terms=True, city="A" * 101)


def test_profile_update_city_accepts_real_name():
    body = ProfileUpdateBody(city="Winston-Salem")
    assert body.city == "Winston-Salem"


def test_profile_update_city_rejects_digits():
    with pytest.raises(ValidationError):
        ProfileUpdateBody(city="Berlin42")


def test_profile_update_city_omitted_stays_none():
    body = ProfileUpdateBody()
    assert body.city is None
