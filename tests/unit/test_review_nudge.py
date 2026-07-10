# tests/unit/test_review_nudge.py
# Port of the review-request nudge email, previously a documented gap
# (mark_past_bookings_completed generates the token via pg_cron with no
# request context to send mail from). Same claim-then-send architecture as
# db/reminders.py: claim_due_review_requests() (SQL) is the atomic claim, not
# a post-send marker. No live Postgres here (same limitation as every other
# module) — these tests cover the Python orchestration only.
import uuid
from unittest.mock import patch

import db.reviews as reviews


def _row(**overrides):
    row = {
        "booking_id": str(uuid.uuid4()), "token": str(uuid.uuid4()),
        "email": "a@example.com", "candidate_name": "A", "mentor_name": "Mentor B",
    }
    row.update(overrides)
    return row


def test_send_due_review_requests_sends_one_email_per_claimed_row():
    rows = [_row(), _row(email="b@example.com")]
    with patch.object(reviews, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional") as mocked_send:
        mock_supabase.rpc.return_value.execute.return_value.data = rows
        result = reviews.send_due_review_requests()
    assert result == {"claimed": 2, "emails_sent": 2}
    mock_supabase.rpc.assert_called_once_with("claim_due_review_requests", {})
    assert mocked_send.call_count == 2
    # token/candidate_name/mentor_name are what the mailer template needs to
    # build the /review/[token] link and greeting.
    _, template, data = mocked_send.call_args_list[0].args
    assert template == "review_request"
    assert data["token"] == rows[0]["token"]


def test_send_due_review_requests_isolates_per_row_failures():
    rows = [_row(email="bad@example.com"), _row(email="good@example.com")]

    def flaky_send(to, template, data):
        if to == "bad@example.com":
            raise RuntimeError("resend rejected")

    with patch.object(reviews, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional", side_effect=flaky_send) as mocked_send:
        mock_supabase.rpc.return_value.execute.return_value.data = rows
        result = reviews.send_due_review_requests()
    assert result == {"claimed": 2, "emails_sent": 1}
    assert mocked_send.call_count == 2  # both attempted despite the first failing


def test_send_due_review_requests_empty_claim_sends_nothing():
    with patch.object(reviews, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional") as mocked_send:
        mock_supabase.rpc.return_value.execute.return_value.data = []
        result = reviews.send_due_review_requests()
    assert result == {"claimed": 0, "emails_sent": 0}
    mocked_send.assert_not_called()


def test_second_overlapping_claim_returns_empty_and_sends_nothing():
    """Regression test for the overlap-safety property: claim_due_review_requests'
    underlying SQL is UPDATE ... WHERE notified_at IS NULL ... RETURNING, so a
    token already claimed by a concurrent/earlier tick is simply absent from a
    second call's result set."""
    row = _row()
    with patch.object(reviews, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional") as mocked_send:
        mock_supabase.rpc.return_value.execute.side_effect = [
            type("R", (), {"data": [row]})(),
            type("R", (), {"data": []})(),
        ]
        run1 = reviews.send_due_review_requests()
        run2 = reviews.send_due_review_requests()
    assert run1 == {"claimed": 1, "emails_sent": 1}
    assert run2 == {"claimed": 0, "emails_sent": 0}
    mocked_send.assert_called_once()
