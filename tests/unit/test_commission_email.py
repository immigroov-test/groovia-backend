# tests/unit/test_commission_email.py
# Port of the "commission approved" affiliate email, previously a documented
# gap (fires from run_referral_fraud_checks inside process_referral_commissions,
# a pg_cron job with no request context). claim_approved_commissions() polls
# commission_ledger.status='approved' directly, covering both paths that can
# produce it (the cron auto-approve, and the admin-triggered
# admin_resolve_fraud_flag) uniformly - so this one job replaces two would-be
# hook sites. No live Postgres here (same limitation as every other module)
# — these tests cover the Python orchestration only.
import uuid
from unittest.mock import patch

import db.referrals as referrals


def _row(**overrides):
    row = {
        "ledger_id": str(uuid.uuid4()), "booking_id": str(uuid.uuid4()),
        "email": "affiliate@example.com", "affiliate_name": "Affiliate A",
        "commission_amount_inr": 150.0,
    }
    row.update(overrides)
    return row


def test_send_pending_commission_emails_sends_one_email_per_claimed_row():
    rows = [_row(), _row(email="b@example.com")]
    with patch.object(referrals, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional") as mocked_send:
        mock_supabase.rpc.return_value.execute.return_value.data = rows
        result = referrals.send_pending_commission_emails()
    assert result == {"claimed": 2, "emails_sent": 2}
    mock_supabase.rpc.assert_called_once_with("claim_approved_commissions", {})
    assert mocked_send.call_count == 2
    _, template, data = mocked_send.call_args_list[0].args
    assert template == "commission_approved"
    assert data["commission_amount_inr"] == rows[0]["commission_amount_inr"]


def test_send_pending_commission_emails_isolates_per_row_failures():
    rows = [_row(email="bad@example.com"), _row(email="good@example.com")]

    def flaky_send(to, template, data):
        if to == "bad@example.com":
            raise RuntimeError("resend rejected")

    with patch.object(referrals, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional", side_effect=flaky_send) as mocked_send:
        mock_supabase.rpc.return_value.execute.return_value.data = rows
        result = referrals.send_pending_commission_emails()
    assert result == {"claimed": 2, "emails_sent": 1}
    assert mocked_send.call_count == 2


def test_send_pending_commission_emails_empty_claim_sends_nothing():
    with patch.object(referrals, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional") as mocked_send:
        mock_supabase.rpc.return_value.execute.return_value.data = []
        result = referrals.send_pending_commission_emails()
    assert result == {"claimed": 0, "emails_sent": 0}
    mocked_send.assert_not_called()


def test_second_overlapping_claim_returns_empty_and_sends_nothing():
    """Regression test for the overlap-safety property: claim_approved_commissions'
    underlying SQL is UPDATE ... WHERE notified_at IS NULL ... RETURNING, so a
    ledger row already claimed by a concurrent/earlier tick is simply absent
    from a second call's result set."""
    row = _row()
    with patch.object(referrals, "_supabase") as mock_supabase, \
         patch("services.mailer.send_transactional") as mocked_send:
        mock_supabase.rpc.return_value.execute.side_effect = [
            type("R", (), {"data": [row]})(),
            type("R", (), {"data": []})(),
        ]
        run1 = referrals.send_pending_commission_emails()
        run2 = referrals.send_pending_commission_emails()
    assert run1 == {"claimed": 1, "emails_sent": 1}
    assert run2 == {"claimed": 0, "emails_sent": 0}
    mocked_send.assert_called_once()
