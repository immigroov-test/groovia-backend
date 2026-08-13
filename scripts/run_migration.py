#!/usr/bin/env python3
"""Run the whole production data migration, in the one order that is correct.

The individual scripts still exist and still work on their own; this exists because the ORDER is not
obvious and getting it wrong is expensive. Two orderings in particular cause damage that is not
visible until a customer sees a price:

  * FX must be populated BEFORE anything picks a mentor's base currency. fix_mentor_base_currency and
    derive_rate_prefill choose the mentor's priciest session, normalising through the EUR pivot to
    compare fairly. With fx_rates empty that normalisation silently degrades to comparing raw numbers,
    so INR 1998/hr "beats" AUD 53/hr (really ~INR 3050/hr) and the mentor lands on the wrong currency
    with every derived price wrong from then on.

  * fix_mentor_base_currency must run at all. The legacy import left many mentors whose stored currency
    disagrees with their own sessions. That mismatch is what makes a price look right in one place and
    wrong in another.

So the FX check is a hard gate, not a warning: the run stops rather than producing plausible-looking
wrong data.

Usage:
    python -m scripts.run_migration --dry-run     # report only, writes nothing
    python -m scripts.run_migration               # commit
    python -m scripts.run_migration --skip-mentors  # data fixes only, mentors already imported

Safe to re-run: every step it calls is idempotent.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("migration")

# How stale FX may be before we refuse to price anything from it. Matches the fx_max_age_minutes
# platform setting (2880 = 48h), so the gate and the pricing engine agree.
FX_MAX_AGE = timedelta(minutes=2880)
FX_MIN_ROWS = 20


def _rule(title: str) -> None:
    log.info("\n%s\n%s", title, "-" * len(title))


def check_fx() -> bool:
    """Hard gate. Everything downstream derives from these rates."""
    _rule("0. FX preflight")
    from db.mentors import _supabase
    try:
        rows = (_supabase.table("fx_rates").select("quote, fetched_at")
                .eq("base", "EUR").execute()).data or []
    except Exception as e:
        log.error("   could not read fx_rates: %s", e)
        return False

    if len(rows) < FX_MIN_ROWS:
        log.error("   fx_rates has %d EUR rows, expected at least %d.", len(rows), FX_MIN_ROWS)
        log.error("   Nothing below can price correctly. Let the dispatcher cron refresh them")
        log.error("   (it runs on the backend, which has network), then re-run this.")
        return False

    stamps = [r["fetched_at"] for r in rows if r.get("fetched_at")]
    if stamps:
        newest = max(stamps)
        try:
            when = datetime.fromisoformat(str(newest).replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - when
            if age > FX_MAX_AGE:
                log.error("   newest rate is %s old (limit %s). Refusing to migrate on stale FX.",
                          age, FX_MAX_AGE)
                return False
            log.info("   %d rates, newest %s old. OK.", len(rows), age)
        except ValueError:
            log.warning("   %d rates, could not parse timestamp %r. Continuing.", len(rows), newest)
    return True


def step(n: str, module: str, dry_run: bool, supports_dry_run: bool = True) -> bool:
    """Import a migration script and call its main(), passing --dry-run only where it exists."""
    _rule(f"{n}. {module}")
    argv = sys.argv
    sys.argv = [module] + (["--dry-run"] if (dry_run and supports_dry_run) else [])
    try:
        mod = importlib.import_module(f"scripts.{module}")
        rc = mod.main()
        ok = rc in (0, None)
        if not ok:
            log.error("   %s returned %s", module, rc)
        return ok
    except Exception:
        log.exception("   %s raised", module)
        return False
    finally:
        sys.argv = argv


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing (steps without a dry-run mode are skipped)")
    ap.add_argument("--skip-mentors", action="store_true",
                    help="skip the mentor import; use when mentors are already loaded")
    ap.add_argument("--skip-fx-check", action="store_true",
                    help="proceed on stale or missing FX. You will get wrong prices. Do not use.")
    args = ap.parse_args()

    if args.dry_run:
        log.info("DRY RUN: nothing will be written.\n")

    if not args.skip_fx_check and not check_fx():
        log.error("\nStopped at the FX gate. Nothing was changed.")
        return 1
    if args.skip_fx_check:
        log.warning("\n!! FX gate skipped. Prices derived below may be wrong. !!")

    # (label, module, supports --dry-run)
    steps: list[tuple[str, str, bool]] = [
        ("1", "refresh_ppp_factors", False),
    ]
    if not args.skip_mentors:
        steps.append(("2", "migrate_mentors", True))
    steps += [
        ("3", "reimport_migrated_prices", True),
        ("4", "fix_mentor_base_currency", False),   # must follow the price reimport and the FX gate
        ("5", "fix_expertise_countries", True),
        ("6", "remap_service_categories", False),
        ("7", "verify_migration", False),
    ]

    failed: list[str] = []
    for n, module, supports in steps:
        if args.dry_run and not supports:
            _rule(f"{n}. {module}")
            log.info("   no dry-run mode; skipped. It will run for real without --dry-run.")
            continue
        if not step(n, module, args.dry_run, supports):
            failed.append(module)
            # Everything after a failed step reads what that step should have written, so carrying on
            # just produces a second, more confusing failure.
            log.error("\nStopping: %s failed and the remaining steps depend on it.", module)
            break

    _rule("Result")
    if failed:
        log.error("FAILED at: %s", ", ".join(failed))
        log.error("Fix the cause and re-run; every step is idempotent, so a repeat is safe.")
        return 1
    if args.dry_run:
        log.info("Dry run finished. Re-run without --dry-run to commit.")
    else:
        log.info("Migration finished. Now check on the site: browse mentors, confirm prices show in")
        log.info("your local currency, and open one mentor to confirm session prices look right.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
