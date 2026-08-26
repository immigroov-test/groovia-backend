"""One-time (idempotent) fix: restore migrated mentors' real per-session prices from the raw export.

An earlier version of the setup SQL re-derived every migrated session's price from a per-hour rate and
corrupted them (a 399 INR session became ~10). The real prices still live in mentor_migration_raw.json.
This restores services.set_price to the price the customer actually pays (the offer when set, else the
base), clears any leftover set_offer_price, fixes set_currency, and seeds the mentor's hourly_rate AND
currency from the highest session's per-hour equivalent, compared across currencies via the EUR pivot
(the first-login popup default).

Scope: only mentors still in the first-login flow (needs_onboarding = TRUE), matched to the raw file by
email; services matched by normalized title + duration. A mentor who already onboarded and set their
own rate is left untouched. Safe to run repeatedly (it just re-asserts the same values).

  python -m scripts.reimport_migrated_prices            # apply
  python -m scripts.reimport_migrated_prices --dry-run  # show what would change, write nothing

Needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in env/.env (use .env.staging for staging).
"""
import argparse
import json
import os
import re
import sys

RAW = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mentor_migration_raw.json")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def _sell(pricing: dict) -> float:
    base = float(pricing.get("base_price") or 0)
    offer = float(pricing.get("offer_price") or 0)
    return offer if offer > 0 else base


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = ap.parse_args()
    try:  # some legacy titles carry non-cp1252 characters; never let a print abort the run
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    from dotenv import load_dotenv
    from supabase import create_client
    load_dotenv()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set.", file=sys.stderr)
        return 1
    sb = create_client(url, key)
    raw = json.load(open(RAW, encoding="utf-8"))

    mentors = sb.table("mentors").select("id, email, display_name, needs_onboarding, currency, hourly_rate").execute().data
    by_email = {(m.get("email") or "").lower(): m for m in mentors if m.get("email")}
    # EUR-pivot rates, to compare per-hour figures that are quoted in different currencies.
    fx: dict[str, float] = {"EUR": 1.0}
    for r in (sb.table("fx_rates").select("quote, rate").eq("base", "EUR").execute().data or []):
        if r.get("rate"):
            fx[str(r["quote"]).upper()] = float(r["rate"])
    services = sb.table("services").select("id, mentor_id, title, duration, set_price, set_offer_price, set_currency").execute().data
    by_mentor: dict[str, list[dict]] = {}
    for s in services:
        by_mentor.setdefault(s["mentor_id"], []).append(s)

    svc_updates = 0
    rate_updates = 0
    skipped_onboarded = 0
    for e in raw:
        email = (e.get("profile", {}).get("email") or "").lower()
        m = by_email.get(email)
        if not m:
            continue
        if not m.get("needs_onboarding"):
            skipped_onboarded += 1
            continue
        ours = by_mentor.get(m["id"], [])
        sell_rates: list[tuple[float, str]] = []
        for sv in e.get("services", []):
            pricing = (sv.get("pricing") or [{}])[0]
            sell = round(_sell(pricing), 2)
            ccy = (pricing.get("currency") or e.get("profile", {}).get("currency") or m.get("currency") or "USD").upper()
            dur = int(sv.get("duration") or 0)
            if sell > 0 and dur:
                sell_rates.append((round(sell * 60.0 / dur, 2), ccy))
            match = [o for o in ours if _norm(o["title"]) == _norm(sv.get("title")) and int(o.get("duration") or 0) == dur]
            if len(match) != 1:
                continue
            o = match[0]
            if (float(o.get("set_price") or 0) != sell or o.get("set_offer_price") is not None
                    or (o.get("set_currency") or "").upper() != ccy):
                svc_updates += 1
                print(f"  SVC {m['display_name'][:18]:18} '{(sv.get('title') or '')[:32]:32}' "
                      f"{o.get('set_price')} -> {sell} {ccy}")
                if not args.dry_run:
                    sb.table("services").update({
                        "set_price": sell, "set_offer_price": None, "set_currency": ccy,
                    }).eq("id", o["id"]).execute()
        # Legacy mentors priced different sessions in different currencies, so comparing the raw
        # per-hour numbers picks whichever currency has the smallest face value: INR 1998/hr "beats"
        # AUD 53/hr, which is really ~INR 3050/hr. Normalize to the EUR pivot before taking the max,
        # same rule as derive_rate_prefill(). And write the WINNING CURRENCY alongside the rate:
        # seeding hourly_rate while leaving mentors.currency untouched is what left mentor rows in one
        # currency and their services in another, breaking set_currency == mentors.currency.
        if sell_rates:
            top_rate, top_ccy = max(sell_rates, key=lambda rc: rc[0] / fx.get(rc[1], 1.0))
            if (float(m.get("hourly_rate") or 0) != top_rate
                    or (m.get("currency") or "").upper() != top_ccy):
                print(f"  RATE {m['display_name'][:18]:18} hourly_rate -> {top_rate} {top_ccy}")
                rate_updates += 1
                if not args.dry_run:
                    sb.table("mentors").update(
                        {"hourly_rate": top_rate, "currency": top_ccy}
                    ).eq("id", m["id"]).execute()

    tag = "(dry-run) " if args.dry_run else ""
    print(f"\n{tag}services repriced: {svc_updates}, mentor rates seeded: {rate_updates}, "
          f"onboarded mentors skipped: {skipped_onboarded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
