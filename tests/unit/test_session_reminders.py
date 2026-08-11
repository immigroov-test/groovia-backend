# BUG-094: 24h/30min session reminders must reach BOTH the candidate and the mentor,
# must never double-send on a retried/overlapping dispatcher tick, and must use each
# party's own local time.
from contextlib import ExitStack
from unittest.mock import patch

from services import notifications

BOOKING_ID = "11111111-1111-1111-1111-111111111111"

NOTIFY_INFO = {
    "candidate_email": "candidate@example.com",
    "candidate_name": "Cara Candidate",
    "mentor_email": "mentor@example.com",
    "mentor_name": "Max Mentor",
}

TIMES = {
    "customer_local": "2026-08-12T09:00:00",
    "customer_tz": "Europe/Amsterdam",
    "mentor_local": "2026-08-12T15:00:00",
    "mentor_tz": "Asia/Kolkata",
}


def _patched(stack: ExitStack, due_ids=(BOOKING_ID,), claim_result=True):
    stack.enter_context(patch("db.due_unreminded_bookings", return_value=list(due_ids)))
    stack.enter_context(patch("db.claim_reminder", return_value=claim_result))
    stack.enter_context(patch("db.get_booking_notify_info", return_value=dict(NOTIFY_INFO)))
    stack.enter_context(patch("db.get_booking_times_display", return_value=dict(TIMES)))
    return stack.enter_context(patch("services.mailer.send_transactional"))


def test_sends_to_both_candidate_and_mentor():
    """Root cause of BUG-094: only the candidate was ever emailed. Both parties
    must now receive a reminder for a due booking."""
    with ExitStack() as stack:
        send = _patched(stack)
        result = notifications.send_session_reminders()

    to_addresses = {c.args[0] for c in send.call_args_list}
    assert to_addresses == {"candidate@example.com", "mentor@example.com"}
    # 2 windows (24h, 30min) x 2 recipients, since the booking is "due" for both windows here.
    assert result["reminders_sent"] == 4


def test_uses_each_partys_own_local_time():
    with ExitStack() as stack:
        send = _patched(stack)
        notifications.send_session_reminders()

    payloads_by_recipient = {c.args[0]: c.args[2] for c in send.call_args_list}
    assert "Amsterdam" in payloads_by_recipient["candidate@example.com"]["session_time"]
    assert "Kolkata" in payloads_by_recipient["mentor@example.com"]["session_time"]


def test_uses_the_correct_template_kind():
    with ExitStack() as stack:
        send = _patched(stack)
        notifications.send_session_reminders()

    kinds = {c.args[1] for c in send.call_args_list}
    assert kinds == {"session_reminder_24h", "session_reminder_30min"}


def test_no_duplicate_send_when_already_claimed():
    """Simulates a retried/overlapping dispatcher tick: claim_reminder already
    returned True once elsewhere, so this tick must see False and send nothing."""
    with ExitStack() as stack:
        send = _patched(stack, claim_result=False)
        result = notifications.send_session_reminders()

    send.assert_not_called()
    assert result["reminders_sent"] == 0


def test_no_due_bookings_sends_nothing():
    """Cancelled sessions (and anything outside the reminder window) are filtered
    out upstream by due_unreminded_bookings' status check - nothing to send here."""
    with ExitStack() as stack:
        send = _patched(stack, due_ids=())
        result = notifications.send_session_reminders()

    send.assert_not_called()
    assert result["reminders_sent"] == 0


def test_missing_recipient_email_is_skipped_not_errored():
    info = dict(NOTIFY_INFO)
    info["mentor_email"] = None
    with patch("db.due_unreminded_bookings", return_value=[BOOKING_ID]), \
         patch("db.claim_reminder", return_value=True), \
         patch("db.get_booking_notify_info", return_value=info), \
         patch("db.get_booking_times_display", return_value=dict(TIMES)), \
         patch("services.mailer.send_transactional") as send:
        result = notifications.send_session_reminders()

    to_addresses = {c.args[0] for c in send.call_args_list}
    assert to_addresses == {"candidate@example.com"}
    assert result["reminders_sent"] == 2  # one per window, candidate only


def test_one_recipients_send_failure_does_not_block_the_other():
    """A transient failure emailing the mentor must not swallow the candidate's
    (already-attempted) reminder, and vice versa - each recipient is independent."""
    def side_effect(to, *_a, **_kw):
        if to == "mentor@example.com":
            raise RuntimeError("smtp down")

    with patch("db.due_unreminded_bookings", return_value=[BOOKING_ID]), \
         patch("db.claim_reminder", return_value=True), \
         patch("db.get_booking_notify_info", return_value=dict(NOTIFY_INFO)), \
         patch("db.get_booking_times_display", return_value=dict(TIMES)), \
         patch("services.mailer.send_transactional", side_effect=side_effect) as send:
        result = notifications.send_session_reminders()

    # Both were attempted (2 windows x 2 recipients = 4 calls)...
    assert send.call_count == 4
    # ...but only the candidate's succeeded each time.
    assert result["reminders_sent"] == 2
