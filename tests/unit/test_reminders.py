# tests/unit/test_reminders.py
# Ports of immigroov's process_due_reminders/process_mentor_reminders (4
# pg_cron jobs: reminders-24h, reminders-1h, mentor-reminders-1h,
# mentor-reminders-10m) into db/reminders.py, built claim-then-send from day
# one (not a retrofit) — see DISPATCHER_SAFETY_CHECKLIST.md. No live Postgres
# in this environment (same limitation as every other module); the SQL claim
# functions themselves aren't executed here — these tests cover the Python
# orchestration: that a claim's results are what gets emailed, that a losing
# concurrent claim (empty result) sends nothing, and that a per-row send
# failure doesn't stop the rest of the batch.
import uuid
from unittest.mock import patch

import db
import db.reminders as reminders
from core.auth import AuthUser, get_current_user


def _as_admin(client):
    admin_user = AuthUser(id=str(uuid.uuid4()), email="admin@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: admin_user
    return admin_user


def _row(**overrides):
    row = {
        "booking_id": str(uuid.uuid4()), "email": "a@example.com", "first_name": "A",
        "slot_utc": "2026-08-01T10:00:00Z", "other_party_name": "B", "meeting_url": "https://x",
    }
    row.update(overrides)
    return row


# ── db.reminders._reminder_link (attendance-tracking join link) ─────────────
# Reminder emails now link to /join/[token] (records mentor_joined/
# candidate_joined - the whole point of the attendance engine) instead of
# the plain meeting_url, falling back to meeting_url when no join_token is
# present (e.g. 'dm' services).

def test_reminder_link_prefers_join_token():
    token = str(uuid.uuid4())
    row = _row(join_token=token)
    with patch.object(reminders.config, "FRONTEND_URL", "https://app.example.com"):
        assert reminders._reminder_link(row) == f"https://app.example.com/join/{token}"


def test_reminder_link_falls_back_to_meeting_url_without_join_token():
    row = _row(join_token=None)
    assert reminders._reminder_link(row) == "https://x"


def test_reminder_link_empty_when_neither_present():
    row = _row(join_token=None, meeting_url=None)
    assert reminders._reminder_link(row) == ""


# ── db.reminders._send_claimed_reminders (shared send loop) ─────────────────

def test_send_claimed_reminders_sends_one_email_per_row():
    rows = [_row(), _row(email="b@example.com")]
    with patch("services.mailer.send_transactional") as mocked_send:
        result = reminders._send_claimed_reminders(rows, "session_reminder_1h")
    assert result == {"claimed": 2, "emails_sent": 2}
    assert mocked_send.call_count == 2


def test_send_claimed_reminders_uses_join_link_in_email_data():
    token = str(uuid.uuid4())
    rows = [_row(join_token=token)]
    with patch("services.mailer.send_transactional") as mocked_send:
        reminders._send_claimed_reminders(rows, "session_reminder_1h")
    _, _, data = mocked_send.call_args.args
    assert data["meeting_url"].endswith(f"/join/{token}")


def test_send_claimed_reminders_isolates_per_row_failures():
    rows = [_row(email="bad@example.com"), _row(email="good@example.com")]

    def flaky_send(to, template, data):
        if to == "bad@example.com":
            raise RuntimeError("resend rejected")

    with patch("services.mailer.send_transactional", side_effect=flaky_send) as mocked_send:
        result = reminders._send_claimed_reminders(rows, "session_reminder_1h")
    assert result == {"claimed": 2, "emails_sent": 1}
    assert mocked_send.call_count == 2   # both attempted despite the first failing


def test_send_claimed_reminders_empty_claim_sends_nothing():
    with patch("services.mailer.send_transactional") as mocked_send:
        result = reminders._send_claimed_reminders([], "session_reminder_1h")
    assert result == {"claimed": 0, "emails_sent": 0}
    mocked_send.assert_not_called()


# ── Overlap-safety regression: a losing concurrent claim sends nothing ──────

def test_second_overlapping_claim_returns_empty_and_sends_nothing():
    """Regression test demonstrating the property that makes this job safe
    under dispatcher overlap: claim_due_customer_reminders' underlying SQL is
    INSERT ... ON CONFLICT DO NOTHING ... RETURNING against
    booking_reminders' UNIQUE(booking_id, kind) — a booking already claimed
    by a concurrent/earlier call is simply absent from a second call's
    result set (that's what the RPC mock simulates here: run 1 returns the
    row, run 2 returns empty, exactly as the real INSERT would for an
    already-claimed booking_id+kind pair). No separate 'mark sent' step
    exists to race against, unlike the send-then-mark pattern this was
    deliberately NOT built as."""
    row = _row()
    with patch.object(reminders, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional") as mocked_send:
        mock_supabase.rpc.return_value.execute.side_effect = [
            type("R", (), {"data": [row]})(),
            type("R", (), {"data": []})(),
        ]
        run1 = reminders.send_customer_reminders_1h()
        run2 = reminders.send_customer_reminders_1h()

    assert run1 == {"claimed": 1, "emails_sent": 1}
    assert run2 == {"claimed": 0, "emails_sent": 0}
    mocked_send.assert_called_once()   # the registrant was only emailed by run 1


# ── RPC parameter wiring (kind + window minutes match the source cron jobs) ─

def test_send_customer_reminders_24h_uses_correct_window():
    with patch.object(reminders, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional"):
        mock_supabase.rpc.return_value.execute.return_value.data = []
        reminders.send_customer_reminders_24h()
    mock_supabase.rpc.assert_called_once_with(
        "claim_due_customer_reminders", {"p_kind": "reminder_24h", "p_lo_minutes": 23 * 60, "p_hi_minutes": 25 * 60},
    )


def test_send_mentor_reminders_10m_uses_widened_window():
    """DISPATCHER_SAFETY_CHECKLIST.md §3a: widened from immigroov's 5-15 min
    to 3-15 min so a single missed 5-minute dispatcher tick can't drop this
    reminder (the narrowest window on the whole job list otherwise had zero
    margin against exactly one missed tick)."""
    with patch.object(reminders, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional"):
        mock_supabase.rpc.return_value.execute.return_value.data = []
        reminders.send_mentor_reminders_10m()
    mock_supabase.rpc.assert_called_once_with(
        "claim_due_mentor_reminders", {"p_kind": "mentor_10m", "p_lo_minutes": 3, "p_hi_minutes": 15},
    )


# ── POST /admin/bookings/send-reminders ─────────────────────────────────────

def test_admin_send_customer_reminders_requires_admin(client):
    resp = client.post("/admin/bookings/send-reminders")
    assert resp.status_code in (401, 403)


def test_admin_send_customer_reminders_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "send_customer_reminders_24h", return_value={"claimed": 1, "emails_sent": 1}), \
             patch.object(db, "send_customer_reminders_1h", return_value={"claimed": 2, "emails_sent": 2}):
            resp = client.post("/admin/bookings/send-reminders")
        assert resp.status_code == 200
        assert resp.json() == {
            "reminder_24h": {"claimed": 1, "emails_sent": 1},
            "reminder_1h": {"claimed": 2, "emails_sent": 2},
        }
    finally:
        client.app.dependency_overrides.clear()


def test_admin_send_mentor_reminders_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "send_mentor_reminders_1h", return_value={"claimed": 0, "emails_sent": 0}), \
             patch.object(db, "send_mentor_reminders_10m", return_value={"claimed": 1, "emails_sent": 1}):
            resp = client.post("/admin/bookings/send-mentor-reminders")
        assert resp.status_code == 200
        assert resp.json()["mentor_10m"]["emails_sent"] == 1
    finally:
        client.app.dependency_overrides.clear()


# ── db.reminders.send_attendance_checks (mentor pre-confirmation nudge) ─────
# A separate, older mechanism from the join-link attendance engine (0027 vs
# 0079) - not a duplicate of the reminder jobs above. claim_due_attendance_checks
# re-checks mentor_confirmed_at IS NULL at claim time, so a mentor confirming
# between ticks means the next tick's claim simply returns no row for that
# booking (same overlap-safety property as the other claim-then-send jobs).

def _attendance_row(**overrides):
    row = {
        "booking_id": str(uuid.uuid4()), "email": "mentor@example.com", "first_name": "M",
        "slot_utc": "2026-08-01T10:00:00Z", "service_title": "Career chat",
    }
    row.update(overrides)
    return row


def test_send_attendance_checks_sends_one_email_per_claimed_row():
    rows = [_attendance_row(), _attendance_row(email="m2@example.com")]
    with patch.object(reminders, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional") as mocked_send:
        mock_supabase.rpc.return_value.execute.return_value.data = rows
        result = reminders.send_attendance_checks()
    assert result == {"claimed": 2, "emails_sent": 2}
    mock_supabase.rpc.assert_called_once_with("claim_due_attendance_checks", {})
    assert mocked_send.call_count == 2
    _, template, data = mocked_send.call_args_list[0].args
    assert template == "attendance_check"
    assert data["service_title"] == "Career chat"


def test_send_attendance_checks_isolates_per_row_failures():
    rows = [_attendance_row(email="bad@example.com"), _attendance_row(email="good@example.com")]

    def flaky_send(to, template, data):
        if to == "bad@example.com":
            raise RuntimeError("resend rejected")

    with patch.object(reminders, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional", side_effect=flaky_send) as mocked_send:
        mock_supabase.rpc.return_value.execute.return_value.data = rows
        result = reminders.send_attendance_checks()
    assert result == {"claimed": 2, "emails_sent": 1}
    assert mocked_send.call_count == 2


def test_send_attendance_checks_empty_claim_sends_nothing():
    with patch.object(reminders, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional") as mocked_send:
        mock_supabase.rpc.return_value.execute.return_value.data = []
        result = reminders.send_attendance_checks()
    assert result == {"claimed": 0, "emails_sent": 0}
    mocked_send.assert_not_called()


def test_second_overlapping_attendance_check_claim_returns_empty():
    """Regression coverage for the overlap-safety property: a booking already
    claimed (or where the mentor confirmed in between) is simply absent from
    a second call's result set - no separate 'sent' marker to race against."""
    row = _attendance_row()
    with patch.object(reminders, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional") as mocked_send:
        mock_supabase.rpc.return_value.execute.side_effect = [
            type("R", (), {"data": [row]})(),
            type("R", (), {"data": []})(),
        ]
        run1 = reminders.send_attendance_checks()
        run2 = reminders.send_attendance_checks()
    assert run1 == {"claimed": 1, "emails_sent": 1}
    assert run2 == {"claimed": 0, "emails_sent": 0}
    mocked_send.assert_called_once()
