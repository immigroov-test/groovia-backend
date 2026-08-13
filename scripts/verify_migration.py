#!/usr/bin/env python3
"""Read-only audit: did every mentor + their services / availability / key fields make it in?

Two independent checks so you never have to eyeball mentors one by one:

  1. EXTRACTION  (mentor_migration_raw.json vs mentor_migration_preview.json)
     What the legacy API returned vs what we kept after transform. Differences here are the
     INTENTIONAL drops (duplicate services by title+duration, blank/duplicate mentors, blank
     services), reported so you can confirm nothing real was lost.

  2. LOAD        (preview vs the live Supabase DB)   [needs SUPABASE creds]
     For every mentor we intended to load (by legacy_id), check the row exists and its service +
     weekly-availability counts match the preview. Flags any mentor that didn't fully land.

    python scripts/verify_migration.py
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python scripts/verify_migration.py   # + load check
"""
import json
import os
import sys

RAW = "mentor_migration_raw.json"
PREVIEW = "mentor_migration_preview.json"


def _name(m: dict) -> str:
    return (m.get("display_name") or m.get("email") or m.get("legacy_id") or "?")


def load(path: str):
    if not os.path.exists(path):
        print(f"  (missing {path} - run the migration first to generate it)")
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_extraction(raw, preview):
    print("=" * 70)
    print("1. EXTRACTION  (API -> transformed/preview)")
    print("=" * 70)
    raw_mentors = len(raw)
    raw_services = sum(len(b.get("services") or []) for b in raw)
    # Count individual weekly SLOTS in both (raw groups slots under weekday blocks; the transform
    # flattens them), so the two numbers are comparable.
    raw_weekly = sum(len(day.get("slots") or []) for b in raw for day in (b.get("weekly") or []))
    prev_mentors = len(preview)
    prev_services = sum(len(r.get("services") or []) for r in preview)
    prev_weekly = sum(len(r.get("weekly") or []) for r in preview)
    active = sum(1 for r in preview if r["mentor"]["is_active"])

    print(f"  mentors:   API {raw_mentors:>4}   kept {prev_mentors:>4}   (dropped {raw_mentors - prev_mentors}: dupe email / blank)")
    print(f"  services:  API {raw_services:>4}   kept {prev_services:>4}   (dropped {raw_services - prev_services}: dupe title+duration / blank)")
    print(f"  weekly:    API {raw_weekly:>4}   kept {prev_weekly:>4}")
    print(f"  active mentors (shown to users): {active} / {prev_mentors}")

    # Field coverage across the loaded mentors.
    def has(r, key):
        v = r["mentor"].get(key)
        return bool(v) and (not isinstance(v, (list, str)) or len(v) > 0)

    for key in ("email", "bio", "headline", "country", "languages", "expertise_categories"):
        n = sum(1 for r in preview if has(r, key))
        print(f"  with {key:<22}: {n:>4} / {prev_mentors}")
    no_email = [r for r in preview if not r["mentor"].get("email")]
    if no_email:
        print(f"  !! {len(no_email)} mentors have NO email (cannot link on login): {[_name(r['mentor']) for r in no_email][:8]}")


def check_load(preview):
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    print()
    print("=" * 70)
    print("2. LOAD  (preview -> live DB)")
    print("=" * 70)
    if not url or not key:
        print("  skipped: set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY to run this check.")
        return
    from supabase import create_client
    sb = create_client(url, key)

    missing, svc_mismatch, wk_mismatch, ok = [], [], [], 0
    for r in preview:
        lid = r["mentor"].get("legacy_id")
        exp_svc, exp_wk = len(r.get("services") or []), len(r.get("weekly") or [])
        row = sb.table("mentors").select("id").eq("legacy_id", lid).limit(1).execute().data
        if not row:
            missing.append(_name(r["mentor"]))
            continue
        mid = row[0]["id"]
        got_svc = sb.table("services").select("id", count="exact").eq("mentor_id", mid).execute().count or 0
        got_wk = sb.table("weekly_availability").select("id", count="exact").eq("mentor_id", mid).execute().count or 0
        if got_svc != exp_svc:
            svc_mismatch.append(f"{_name(r['mentor'])}: services expected {exp_svc}, in DB {got_svc}")
        if got_wk != exp_wk:
            wk_mismatch.append(f"{_name(r['mentor'])}: weekly expected {exp_wk}, in DB {got_wk}")
        if got_svc == exp_svc and got_wk == exp_wk:
            ok += 1

    print(f"  fully matched: {ok} / {len(preview)}")
    for title, items in (("NOT in DB", missing), ("service count off", svc_mismatch), ("weekly count off", wk_mismatch)):
        if items:
            print(f"  !! {len(items)} {title}:")
            for it in items[:20]:
                print(f"       - {it}")
        else:
            print(f"  ok: no {title}")


def main():
    raw, preview = load(RAW), load(PREVIEW)
    if not preview:
        print("Need mentor_migration_preview.json (and ideally the raw file). Run the migration first.")
        sys.exit(1)
    if raw:
        check_extraction(raw, preview)
    check_load(preview)
    print("\nDone.")


if __name__ == "__main__":
    main()
