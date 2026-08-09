"""BUG-110: expertise countries = current country + served countries only, never the home country;
and every non-current expertise country must live in served_countries so the edit form can show it.

Migration and an old profile-save bug left mentors with (a) their HOME country baked into
expertise_country_codes (a mentor guides people to where they now live + places they can advise on, not
their origin), and (b) 'orphaned' expertise countries that were never mirrored into served_countries, so
the Edit Profile form couldn't display them. This re-derives both fields for every mentor, preserving
any years already recorded on served entries. Idempotent (safe to run repeatedly).

  python -m scripts.fix_expertise_countries [--dry-run]

Needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in env/.env (use .env.staging for staging).
"""
import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = ap.parse_args()
    try:
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

    rows = sb.table("mentors").select(
        "id, display_name, country, home_country_code, served_countries, expertise_country_codes"
    ).execute().data
    changed = 0
    for m in rows:
        current = (m.get("country") or "").upper()
        home = (m.get("home_country_code") or "").upper()
        exp = [(c or "").upper() for c in (m.get("expertise_country_codes") or [])]
        served = m.get("served_countries") or []

        # Rebuild served: keep existing (with their years), then fold in any orphaned expertise country.
        # Always exclude the current country (it's the "Location") and the home country.
        served_years: dict[str, object] = {}
        order: list[str] = []
        for s in served:
            c = (s.get("code") or "").upper()
            if c and c not in served_years:
                served_years[c] = s.get("years"); order.append(c)
        for c in exp:
            if c and c not in served_years:
                served_years[c] = None; order.append(c)
        new_served = [{"code": c, "years": served_years[c]}
                      for c in order if c and c != current and c != home]

        # Expertise = current country + served codes (deduped, current first, home excluded).
        new_exp: list[str] = []
        for c in [current] + [s["code"] for s in new_served]:
            if c and c not in new_exp:
                new_exp.append(c)

        old_served_norm = [((s.get("code") or "").upper(), s.get("years")) for s in served]
        new_served_norm = [(s["code"], s["years"]) for s in new_served]
        if new_exp != exp or new_served_norm != old_served_norm:
            changed += 1
            print(f"  {(m.get('display_name') or '')[:22]:22} exp {exp} -> {new_exp} | "
                  f"served {[s.get('code') for s in served]} -> {[s['code'] for s in new_served]}")
            if not args.dry_run:
                sb.table("mentors").update({
                    "expertise_country_codes": new_exp,
                    "served_countries": new_served,
                }).eq("id", m["id"]).execute()

    print(f"\n{'(dry-run) ' if args.dry_run else ''}mentors updated: {changed} / {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
