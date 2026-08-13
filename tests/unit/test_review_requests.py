# BUG-139: automatic post-session review-request email. This module already existed
# (send_review_requests / db.due_review_requests / claim_reminder-based dedup / the
# review_request mail template) - these tests verify the pieces the bug asked for:
# claim-then-send dedup on retry, and that a claim failure for one booking never blocks
# the rest of the batch.
from unittest.mock import patch

import db
from services import mailer
from services.notifications import send_review_requests


def _notify_info(bid):
    return {
        "candidate_email": f"{bid}@example.com",
        "candidate_name": "Alex",
        "mentor_name": "Sam Mentor",
    }


def test_send_review_requests_sends_and_builds_session_scoped_url():
    with patch.object(db, "due_review_requests", side_effect=lambda reminder: ["b1"] if not reminder else []), \
         patch.object(db, "claim_reminder", return_value=True), \
         patch.object(db, "get_booking_notify_info", side_effect=lambda bid: _notify_info(bid)), \
         patch.object(mailer, "send_transactional") as send:
        result = send_review_requests()

    assert result == {"review_requests_sent": 1}
    send.assert_called_once()
    to, template, ctx = send.call_args.args
    assert to == "b1@example.com"
    assert template == "review_request"
    assert ctx["review_url"].endswith("/session/b1")
    assert ctx["is_reminder"] is False


def test_send_review_requests_skips_when_already_claimed():
    """A retried/overlapping tick must not double-send: claim_reminder returning False
    (unique_violation on booking_reminders) means this dispatcher run sends nothing."""
    with patch.object(db, "due_review_requests", side_effect=lambda reminder: ["b1"] if not reminder else []), \
         patch.object(db, "claim_reminder", return_value=False), \
         patch.object(db, "get_booking_notify_info") as notify_info, \
         patch.object(mailer, "send_transactional") as send:
        result = send_review_requests()

    assert result == {"review_requests_sent": 0}
    send.assert_not_called()
    notify_info.assert_not_called()


def test_send_review_requests_one_failure_does_not_block_the_batch():
    with patch.object(db, "due_review_requests", side_effect=lambda reminder: ["b1", "b2"] if not reminder else []), \
         patch.object(db, "claim_reminder", return_value=True), \
         patch.object(db, "get_booking_notify_info", side_effect=lambda bid: _notify_info(bid)), \
         patch.object(mailer, "send_transactional", side_effect=[Exception("resend down"), None]) as send:
        result = send_review_requests()

    assert result == {"review_requests_sent": 1}
    assert send.call_count == 2


def test_send_review_requests_skips_bookings_with_no_candidate_email():
    """A guest checkout or missing email must not crash the batch - it's just skipped."""
    with patch.object(db, "due_review_requests", side_effect=lambda reminder: ["b1"] if not reminder else []), \
         patch.object(db, "claim_reminder", return_value=True), \
         patch.object(db, "get_booking_notify_info", return_value={"candidate_name": "Alex"}), \
         patch.object(mailer, "send_transactional") as send:
        result = send_review_requests()

    assert result == {"review_requests_sent": 0}
    send.assert_not_called()


def test_send_review_requests_sends_reminder_kind_with_is_reminder_flag():
    with patch.object(db, "due_review_requests", side_effect=lambda reminder: [] if not reminder else ["b1"]), \
         patch.object(db, "claim_reminder", return_value=True) as claim, \
         patch.object(db, "get_booking_notify_info", side_effect=lambda bid: _notify_info(bid)), \
         patch.object(mailer, "send_transactional") as send:
        result = send_review_requests()

    assert result == {"review_requests_sent": 1}
    claim.assert_called_once_with("b1", "review_reminder")
    _, _, ctx = send.call_args.args
    assert ctx["is_reminder"] is True
