# tests/unit/test_dispatcher.py
# jobs/run_due.py — the scheduling dispatcher. Every job it registers was
# already proven safe under overlap individually (DISPATCHER_SAFETY_CHECKLIST.md);
# these tests cover the dispatcher's OWN contract: the lease lock gates the
# whole tick, one job's failure doesn't stop the others, and the two
# infrequent jobs (FX refresh, reconcile) actually self-gate rather than
# running every 5-minute tick regardless.
from unittest.mock import patch

import jobs.run_due as run_due
from services.dispatcher_lock import LockNotAcquired


def test_run_due_calls_every_registered_job_when_lock_acquired():
    # patch.object defaults to MagicMock, which already implements the
    # context-manager protocol (__enter__/__exit__ return MagicMocks that
    # don't raise) — no manual configuration needed for the `with` block
    # inside run_due() to work.
    with patch.object(run_due, "dispatcher_lock"), \
         patch.object(run_due, "_run_job") as mocked_run_job:
        run_due.run_due()

    called_names = [c.args[0] for c in mocked_run_job.call_args_list]
    assert called_names == [
        "refresh_fx_rates", "send_webinar_reminders",
        "send_customer_reminders_24h", "send_customer_reminders_1h",
        "send_mentor_reminders_1h", "send_mentor_reminders_10m",
        "send_attendance_checks",
        "send_review_requests", "send_commission_approved_emails",
        "sweep_verify_payments", "process_refunds", "reconcile_payments",
    ]


def test_run_due_skips_entirely_when_lock_not_acquired():
    """Regression test for overlap prevention at the dispatcher level: a
    losing tick (another run still holds the lease) must run ZERO jobs, not
    a partial set."""
    def raise_lock_not_acquired(*a, **k):
        raise LockNotAcquired("dispatcher-tick")

    with patch.object(run_due, "dispatcher_lock", side_effect=raise_lock_not_acquired), \
         patch.object(run_due, "_run_job") as mocked_run_job:
        run_due.run_due()  # must not raise
    mocked_run_job.assert_not_called()


def test_run_job_isolates_one_failure_from_the_rest():
    """Hard requirement from DISPATCHER_SAFETY_CHECKLIST.md's headline
    finding #2: one job raising must not prevent the next job in the same
    tick from running."""
    calls = []

    def good_job():
        calls.append("good")
        return "ok"

    def bad_job():
        calls.append("bad-attempted")
        raise RuntimeError("frankfurter down")

    run_due._run_job("bad", bad_job)
    run_due._run_job("good", good_job)

    assert calls == ["bad-attempted", "good"]


def test_run_job_logs_result_on_success():
    with patch.object(run_due.logger, "info") as mocked_info:
        run_due._run_job("some_job", lambda: {"ok": True})
    mocked_info.assert_called_once()
    assert "some_job" in mocked_info.call_args.args[1]


def test_run_job_logs_exception_on_failure_without_raising():
    def failing():
        raise RuntimeError("boom")

    with patch.object(run_due.logger, "exception") as mocked_exception:
        run_due._run_job("some_job", failing)  # must not raise
    mocked_exception.assert_called_once()


# ── Self-gating: refresh_fx_rates (real cadence 6h, dispatcher tick 5min) ───

def test_fx_refresh_skipped_when_not_stale():
    with patch("db.fx_rates_are_stale", return_value=False), \
         patch("db.refresh_fx_rates") as mocked_refresh:
        result = run_due._refresh_fx_rates_if_stale()
    assert result == {"skipped": "fx_rates not yet stale"}
    mocked_refresh.assert_not_called()


def test_fx_refresh_runs_when_stale():
    with patch("db.fx_rates_are_stale", return_value=True) as mocked_stale, \
         patch("db.refresh_fx_rates", return_value={"ok": True, "count": 28}) as mocked_refresh:
        result = run_due._refresh_fx_rates_if_stale()
    assert result == {"ok": True, "count": 28}
    mocked_stale.assert_called_once_with(run_due._FX_REFRESH_INTERVAL)
    mocked_refresh.assert_called_once()


# ── Self-gating: reconcile_payments (real cadence 24h) ──────────────────────

def test_reconcile_payments_skipped_when_not_due():
    with patch("db.job_is_due", return_value=False), \
         patch("db.reconcile_payments") as mocked_reconcile, \
         patch("db.mark_job_run") as mocked_mark:
        result = run_due._reconcile_payments_if_due()
    assert result == {"skipped": "reconcile_payments not yet due"}
    mocked_reconcile.assert_not_called()
    mocked_mark.assert_not_called()


def test_reconcile_payments_runs_and_marks_when_due():
    with patch("db.job_is_due", return_value=True) as mocked_due, \
         patch("db.reconcile_payments", return_value={"ok": True, "checked": 5, "mismatches": 0}) as mocked_reconcile, \
         patch("db.mark_job_run") as mocked_mark:
        result = run_due._reconcile_payments_if_due()
    assert result == {"ok": True, "checked": 5, "mismatches": 0}
    mocked_due.assert_called_once_with("reconcile_payments", run_due._RECONCILE_INTERVAL)
    mocked_reconcile.assert_called_once()
    mocked_mark.assert_called_once_with("reconcile_payments")
