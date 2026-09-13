# BUG-091: the mentor availability dashboard showed UTC instead of the mentor's own timezone.
#
# The cause was two columns that were never kept in step. mentors.timezone is what the signup and
# profile forms write; mentors.app_timezone (DEFAULT 'UTC') is what the availability dashboard, the
# slot generator (get_available_slots) and the booking emails actually read. create_mentor wrote
# only the first, so EVERY self-signup mentor had their real zone in one column and a bare 'UTC' in
# the one that mattered - not just the migrated rows.
#
# These tests pin the write side. The read side is SQL (both *_db_setup.sql now use the BUG-114
# expression COALESCE(NULLIF(app_timezone,'UTC'), timezone, 'UTC')) and is verified by running it.
from unittest.mock import patch

import db
from core.auth import AuthUser


def _profile_body(**over):
    from routers.mentor import ProfileUpdateBody
    return ProfileUpdateBody(**over)


def test_signup_writes_both_timezone_columns():
    """A new mentor must not be born with the two columns disagreeing."""
    captured = {}

    class _Q:
        """Chainable stub. create_mentor_signup does a slug-uniqueness select before the insert,
        so both chains have to work or the insert is never reached."""
        def __init__(self):
            self.data = []
        def select(self, *a, **k):
            return self
        def eq(self, *a, **k):
            return self
        def limit(self, *a, **k):
            return self
        def insert(self, row):
            captured.update(row)
            self.data = [{"id": "m1", **row}]
            return self
        def update(self, *a, **k):
            # create_mentor_signup also promotes the profile row to role=mentor afterwards.
            return self
        def execute(self):
            return self

    with patch.object(db.mentors, "_supabase") as sb:
        sb.table.side_effect = lambda *_a, **_k: _Q()
        db.mentors.create_mentor_signup(
            "p1", display_name="A Mentor", headline=None, timezone_name="Europe/Amsterdam",
        )

    assert captured.get("timezone") == "Europe/Amsterdam"
    assert captured.get("app_timezone") == "Europe/Amsterdam", \
        "app_timezone must be written at signup - it is the column the dashboard and slot generator read"


def test_profile_edit_mirrors_timezone_into_app_timezone():
    """Setting the timezone on the Profile tab must actually move the one that is read."""
    from routers.mentor import update_profile
    from fastapi import BackgroundTasks

    mentor = {"id": "m1", "status": "approved", "display_name": "A Mentor",
              "slug": "a-mentor", "needs_onboarding": False}

    with patch.object(db, "get_mentor_by_profile_id", return_value=mentor), \
         patch.object(db, "save_mentor_profile_live", return_value=mentor) as live, \
         patch.object(db, "stage_mentor_approval_edit", return_value=None), \
         patch("routers.mentor._email_admin_changes_submitted"):
        update_profile(
            _profile_body(timezone="Asia/Kolkata"),
            BackgroundTasks(),
            user=AuthUser(id="p1", email="m@example.com", role="authenticated"),
        )

    fields = live.call_args.args[1]
    assert fields["timezone"] == "Asia/Kolkata"
    assert fields["app_timezone"] == "Asia/Kolkata", \
        "the profile form must mirror timezone into app_timezone or the change is invisible"


def test_app_timezone_survives_the_editable_field_filter():
    """save_mentor_profile_live drops anything not whitelisted; app_timezone must not be dropped."""
    assert "app_timezone" in db.mentors._EDITABLE_PROFILE_FIELDS
    assert "timezone" in db.mentors._EDITABLE_PROFILE_FIELDS


def test_app_timezone_is_not_client_supplied():
    """The client never sends app_timezone - it is derived from `timezone` server-side, so the two
    columns cannot be made to disagree by a crafted request."""
    from routers.mentor import ProfileUpdateBody
    assert "app_timezone" not in ProfileUpdateBody.model_fields
