"""Pull the latest EUR-pivot FX rates and upsert them into fx_rates.

Run this DAILY. It is the source of truth for keeping prices current; pricing hard-fails once fx_rates
is older than platform_settings.fx_max_age_minutes, and a stale/wrong rate silently mis-prices every
cross-currency booking (the INR->EUR bug: seed EUR->INR=90 vs real ~110).

  python -m scripts.refresh_fx_rates            # daily refresh (frozen once per UTC day)
  python -m scripts.refresh_fx_rates --force    # ignore the per-day freeze and refetch now

Wire it up as a Render Cron Job (recommended) or any daily scheduler. On a fetch failure it changes
nothing, so the previously stored (last-day) rates stay in place - it never writes a stale placeholder.
"""
import sys

import db


def main() -> int:
    force = "--force" in sys.argv[1:]
    try:
        res = db.refresh_fx_rates(force=force)
    except Exception as e:  # noqa: BLE001
        print(f"FX refresh FAILED: {e}", file=sys.stderr)
        print("Previously stored rates are untouched (last-day fallback).", file=sys.stderr)
        return 1
    if res.get("skipped"):
        print(f"FX refresh skipped: {res['skipped']}")
    else:
        print(f"FX refreshed from {res.get('provider')}: {res.get('count')} rates, as_of {res.get('as_of')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
