# tests/unit/test_dispatcher_lock.py
# Fix 4/4 from DISPATCHER_SAFETY_CHECKLIST.md's headline finding #2: Render
# Cron Jobs have no built-in mutex, so a dispatcher tick that overruns its
# schedule interval could otherwise run concurrently with the next tick,
# double-processing every job it manages. services/dispatcher_lock.py is the
# reusable guard the (not-yet-built) dispatcher will wrap its job loop in.
#
# The actual atomicity guarantee lives in the SQL function
# try_acquire_dispatcher_lock (UPDATE-if-expired-else-INSERT, where a UNIQUE
# constraint collision on the INSERT is what Postgres uses to arbitrate two
# real concurrent callers) — not executed against live Postgres here, same
# limitation as every other module (see MIGRATION_STATUS.md). These tests
# cover the Python wrapper: that a losing "acquire" raises without running the
# wrapped job, and that release always happens on the way out.
from unittest.mock import patch

import services.dispatcher_lock as dispatcher_lock


def test_try_acquire_true_when_rpc_reports_acquired():
    with patch.object(dispatcher_lock, "_supabase") as mock_supabase:
        mock_supabase.rpc.return_value.execute.return_value.data = True
        result = dispatcher_lock.try_acquire("reminders-tick")
    assert result is True
    mock_supabase.rpc.assert_called_once_with(
        "try_acquire_dispatcher_lock", {"p_lock_name": "reminders-tick", "p_ttl_seconds": 300},
    )


def test_try_acquire_false_when_rpc_reports_held():
    with patch.object(dispatcher_lock, "_supabase") as mock_supabase:
        mock_supabase.rpc.return_value.execute.return_value.data = False
        result = dispatcher_lock.try_acquire("reminders-tick")
    assert result is False


def test_release_calls_rpc_with_lock_name():
    with patch.object(dispatcher_lock, "_supabase") as mock_supabase:
        dispatcher_lock.release("reminders-tick")
    mock_supabase.rpc.assert_called_once_with("release_dispatcher_lock", {"p_lock_name": "reminders-tick"})


# ── dispatcher_lock() context manager ────────────────────────────────────────

def test_dispatcher_lock_runs_wrapped_code_when_acquired():
    ran = []
    with patch.object(dispatcher_lock, "try_acquire", return_value=True), \
         patch.object(dispatcher_lock, "release") as mocked_release:
        with dispatcher_lock.dispatcher_lock("tick"):
            ran.append("job ran")
    assert ran == ["job ran"]
    mocked_release.assert_called_once_with("tick")


def test_dispatcher_lock_releases_even_when_wrapped_code_raises():
    """The lock must not leak if a job inside the tick throws — otherwise one
    bad job would wedge every future tick until the TTL expires."""
    with patch.object(dispatcher_lock, "try_acquire", return_value=True), \
         patch.object(dispatcher_lock, "release") as mocked_release:
        try:
            with dispatcher_lock.dispatcher_lock("tick"):
                raise RuntimeError("job blew up")
            assert False, "expected the job's exception to propagate"
        except RuntimeError:
            pass
    mocked_release.assert_called_once_with("tick")


def test_dispatcher_lock_raises_and_skips_the_job_when_not_acquired():
    ran = []
    with patch.object(dispatcher_lock, "try_acquire", return_value=False), \
         patch.object(dispatcher_lock, "release") as mocked_release:
        try:
            with dispatcher_lock.dispatcher_lock("tick"):
                ran.append("job ran")   # must never execute
            assert False, "expected LockNotAcquired"
        except dispatcher_lock.LockNotAcquired:
            pass
    assert ran == []
    mocked_release.assert_not_called()   # never acquired, so nothing to release


# ── Overlap-prevention regression ────────────────────────────────────────────

def test_second_overlapping_tick_never_runs_the_job():
    """Regression test for the exact scenario this exists to prevent: tick 1
    is still running (holds the lease) when tick 2 fires. Simulated via two
    sequential try_acquire results — True for tick 1, False for tick 2,
    exactly what the real RPC returns for a lease that hasn't expired yet."""
    executions = []

    def run_tick(label):
        try:
            with dispatcher_lock.dispatcher_lock("reminders-tick"):
                executions.append(label)
        except dispatcher_lock.LockNotAcquired:
            pass

    with patch.object(dispatcher_lock, "try_acquire", side_effect=[True, False]), \
         patch.object(dispatcher_lock, "release"):
        run_tick("tick-1")
        run_tick("tick-2")

    assert executions == ["tick-1"]   # tick-2 never ran its job body
