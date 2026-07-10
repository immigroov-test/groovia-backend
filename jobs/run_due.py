"""The scheduling dispatcher — one entrypoint, invoked on a fixed interval by
whatever external scheduler triggers it (Render Cron Job in production; run
directly for local/manual testing). See INFRASTRUCTURE_ARCHITECTURE_PLAN.md
§3 for the full design rationale and §3a for the timing justification, and
DISPATCHER_SAFETY_CHECKLIST.md for the per-job concurrency analysis that
gated building this at all.

Design summary:
- ONE Render Cron Job, not one per task — see the architecture plan for why
  N separate services would just relocate the fragmentation problem.
- Every job here is already safe under overlap (claim-then-send patterns,
  deterministic idempotency keys, or read-only) — see the safety checklist.
  The dispatcher-wide lease lock (services/dispatcher_lock.py) is the
  blanket backstop for the one thing no single job's own design can rule
  out: Render starting a second container mid-tick if a previous tick
  overran its interval.
- Infrequent jobs (refresh_fx_rates: 6h: reconcile_payments: 24h) self-gate
  using db.jobs' last-run tracking / fx_rates' own fetched_at column, so
  they're registered every tick but only do real work when actually due —
  this is what lets one 5-minute cadence serve jobs with very different
  real-world frequencies without needing N separate schedules.
- Each job is wrapped in its own try/except: one job's failure (e.g.
  Frankfurter is down) must never prevent the other jobs in the same tick
  from running — this is a hard requirement from the safety checklist's
  headline finding #2, not an optimization.

Run directly for a manual/local tick:  python -m jobs.run_due
"""
import logging
from datetime import timedelta

import db
from services.dispatcher_lock import LockNotAcquired, dispatcher_lock

logger = logging.getLogger("immigroov.jobs.run_due")

_FX_REFRESH_INTERVAL = timedelta(hours=6)
_RECONCILE_INTERVAL = timedelta(hours=24)


def _run_job(name: str, fn) -> None:
    try:
        result = fn()
        logger.info("job=%s completed result=%s", name, result)
    except Exception:
        logger.exception("job=%s failed", name)


def _refresh_fx_rates_if_stale() -> dict:
    if not db.fx_rates_are_stale(_FX_REFRESH_INTERVAL):
        return {"skipped": "fx_rates not yet stale"}
    return db.refresh_fx_rates()


def _reconcile_payments_if_due() -> dict:
    if not db.job_is_due("reconcile_payments", _RECONCILE_INTERVAL):
        return {"skipped": "reconcile_payments not yet due"}
    result = db.reconcile_payments()
    db.mark_job_run("reconcile_payments")
    return result


# Registration order matters only in that money-correctness jobs (FX, hold
# expiry — though the latter is pg_cron, not here) run first, reminders and
# less time-sensitive sweeps after. Not a hard dependency between any two.
_JOBS = [
    ("refresh_fx_rates", _refresh_fx_rates_if_stale),
    ("send_webinar_reminders", db.send_due_webinar_reminders),
    ("send_customer_reminders_24h", db.send_customer_reminders_24h),
    ("send_customer_reminders_1h", db.send_customer_reminders_1h),
    ("send_mentor_reminders_1h", db.send_mentor_reminders_1h),
    ("send_mentor_reminders_10m", db.send_mentor_reminders_10m),
    ("send_attendance_checks", db.send_attendance_checks),
    ("send_review_requests", db.send_due_review_requests),
    ("send_commission_approved_emails", db.send_pending_commission_emails),
    ("sweep_verify_payments", db.sweep_verify_payments),
    ("process_refunds", db.process_refunds),
    ("reconcile_payments", _reconcile_payments_if_due),
]


def run_due() -> None:
    try:
        with dispatcher_lock("dispatcher-tick"):
            for name, fn in _JOBS:
                _run_job(name, fn)
    except LockNotAcquired:
        logger.info("dispatcher tick skipped — lease held by another run")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_due()
