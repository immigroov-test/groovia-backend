"""The scheduling dispatcher — one entrypoint, invoked on a fixed interval by an
external scheduler (Render Cron Job in production; run directly for local/manual
testing).

Scope: the money-correctness jobs that keep the Razorpay flow robust to crashes/missed
webhooks, plus the scheduled booking notifications (24h/1h session reminders and the
T-60 mentor attendance nudge). Each notification is claimed atomically before sending
(booking_reminders UNIQUE constraint), so overlapping ticks never double-send.

- ONE Render Cron Job, not one per task.
- Every job here is already safe under overlap (deterministic idempotency keys,
  claim-then-act, or read-only). The dispatcher-wide lease lock
  (services/dispatcher_lock.py) is the blanket backstop for Render starting a
  second container mid-tick if a previous tick overran its interval.
- Infrequent jobs self-gate: refresh_fx_rates (once per UTC day - prices are frozen per day,
  BUG-087) via fx_rates.fetched_at, reconcile_payments (24h) via job_run_history — registered
  every tick but only do real work when actually due.
- Each job is wrapped in its own try/except: one job's failure (e.g. Frankfurter
  is down) must never prevent the others in the same tick from running.

Run directly for a manual/local tick:  python -m jobs.run_due
"""
import logging
from datetime import timedelta

import db
from services import mailer
from services import notifications
from services.dispatcher_lock import LockNotAcquired, dispatcher_lock

logger = logging.getLogger("immigroov.jobs.run_due")

# FX is frozen per UTC day (BUG-087): the dispatcher checks daily and refresh_fx_rates() itself
# hard-gates to one fetch per UTC day, so the rate (and every price) is stable within a day.
_FX_REFRESH_INTERVAL = timedelta(hours=24)
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
def _daily_backup() -> dict:
    """Encrypted database backup to R2. Self-gates to once a day, so the 5-minute tick costs
    one job_run_history lookup. Lives here because Render cron jobs need a paid plan."""
    from services import backup
    return backup.run_backup()


def _fx_staleness_alert() -> dict:
    """Warn admins when FX stops refreshing, BEFORE it starts refusing bookings.

    Every price is derived from these rates and compute_booking_price fails closed once they pass
    fx_max_age_minutes, so a silent refresh failure ends with every checkout failing. That is exactly
    what nearly happened: the dispatcher could not import (jobs/ was missing from the Docker image),
    so the daily refresh never ran, and rates stayed current only because Render's free tier
    cold-starts often and startup also refreshes. Nothing would have told us.

    Warns at half the hard limit, so there is a day's notice rather than an outage. Re-alerts at most
    once every 6 hours so a sustained failure does not bury the inbox."""
    from datetime import datetime, timezone
    from db import jobs as job_db

    limit_min = db.fx_max_age_minutes()

    newest = db.fx_newest_fetched_at()
    if newest is None:
        age_h, blocking = 9999.0, True
    else:
        age_h = (datetime.now(timezone.utc) - newest).total_seconds() / 3600.0
        blocking = age_h >= limit_min / 60.0

    warn_at_h = (limit_min / 60.0) / 2.0
    if age_h < warn_at_h:
        return {"ok": True, "age_hours": round(age_h, 1)}

    if not job_db.job_is_due("fx_stale_alert", timedelta(hours=6)):
        return {"stale": True, "age_hours": round(age_h, 1), "skipped": "alerted within 6h"}

    recipients = db.admin_notify_emails()
    if not recipients:
        logger.error("FX STALE (%.1fh) but no admin recipients configured", age_h)
        return {"stale": True, "age_hours": round(age_h, 1), "error": "no admin recipients"}

    for to in recipients:
        try:
            mailer.send_transactional(to, "fx_stale_alert", {
                "age_hours": round(age_h, 1),
                "limit_hours": round(limit_min / 60.0, 1),
                "newest": newest.isoformat() if newest else "never",
                "blocking": blocking,
            })
        except Exception:
            logger.exception("could not send fx_stale_alert to %s", to)
    job_db.mark_job_run("fx_stale_alert")
    return {"stale": True, "age_hours": round(age_h, 1), "alerted": len(recipients), "blocking": blocking}


_JOBS = [
    ("refresh_fx_rates", _refresh_fx_rates_if_stale),
    ("fx_staleness_alert", _fx_staleness_alert),
    ("backup_to_r2", _daily_backup),
    ("expire_stale_holds", db.expire_stale_holds),
    ("sweep_verify_payments", db.sweep_verify_payments),
    ("process_refunds", db.process_refunds),
    ("reconcile_payments", _reconcile_payments_if_due),
    ("session_reminders", notifications.send_session_reminders),
    ("attendance_checks", notifications.send_attendance_checks),
    ("review_requests", notifications.send_review_requests),
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
