"""The scheduling dispatcher — one entrypoint, invoked on a fixed interval by an
external scheduler (Render Cron Job in production; run directly for local/manual
testing).

Payment-only scope: this dispatcher runs the money-correctness jobs that keep the
Razorpay flow robust to crashes/missed webhooks. It intentionally does NOT run
the wider reminder/webinar/review jobs from the source fork (out of scope here).

- ONE Render Cron Job, not one per task.
- Every job here is already safe under overlap (deterministic idempotency keys,
  claim-then-act, or read-only). The dispatcher-wide lease lock
  (services/dispatcher_lock.py) is the blanket backstop for Render starting a
  second container mid-tick if a previous tick overran its interval.
- Infrequent jobs self-gate: refresh_fx_rates (6h) via fx_rates.fetched_at,
  reconcile_payments (24h) via job_run_history — registered every tick but only
  do real work when actually due.
- Each job is wrapped in its own try/except: one job's failure (e.g. Frankfurter
  is down) must never prevent the others in the same tick from running.

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


# Money-correctness jobs run first (FX freshness, hold expiry), then the
# webhook-backstop sweeps. No hard dependency between any two.
_JOBS = [
    ("refresh_fx_rates", _refresh_fx_rates_if_stale),
    ("expire_stale_holds", db.expire_stale_holds),
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
