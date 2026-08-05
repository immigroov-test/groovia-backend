#!/usr/bin/env python3
"""Local DR backup + cross-check of the mentor data.

- Resets PPP OFF for imported mentors still in onboarding (smart_pricing + service is_ppp), so every
  old mentor chooses fair-pricing themselves at first login.
- Dumps the core app tables to scripts/backups/db_backup_<ts>.json (gitignored - a restore point).
- Cross-checks the DB against the legacy source (mentor_migration_preview.json) and flags issues:
  missing/extra mentors, mentors with no service or no availability, orphan bookings, PPP status.
- Writes scripts/backups/crosscheck_<ts>.md and prints a summary.

Targets whatever Supabase the given env file points at (default .env.staging). Never prints secrets.

    python scripts/db_backup_crosscheck.py [env_file]
"""
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

ENV_FILE = sys.argv[1] if len(sys.argv) > 1 else ".env.staging"
load_dotenv(ENV_FILE, override=True)
from supabase import create_client  # noqa: E402

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "backups")
os.makedirs(OUT_DIR, exist_ok=True)
TS = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def fetch_all(table: str, cols: str = "*") -> list[dict]:
    rows, start, page = [], 0, 1000
    while True:
        try:
            res = sb.table(table).select(cols).range(start, start + page - 1).execute()
        except Exception as e:  # noqa: BLE001
            print(f"  [skip] {table}: {e}")
            return rows
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page:
            break
        start += page
    return rows


# 1. (--apply only) Old mentors: PPP OFF + re-open the first-login gate so each chooses fair pricing
# via the popup. WRITES to the DB, so it is opt-in. A plain run is read-only (backup + cross-check).
mentors = fetch_all("mentors")
if "--apply" in sys.argv:
    ppp_off = [m for m in mentors if m.get("legacy_id") and m.get("smart_pricing")]
    for m in ppp_off:
        sb.table("mentors").update({"smart_pricing": False}).eq("id", m["id"]).execute()
        sb.table("services").update({"is_ppp": False}).eq("mentor_id", m["id"]).execute()
    regate = [m for m in mentors if m.get("legacy_id") and not m.get("needs_onboarding")]
    for m in regate:
        sb.table("mentors").update({"needs_onboarding": True}).eq("id", m["id"]).execute()
    print(f"[--apply] PPP -> OFF for {len(ppp_off)} imported mentors; first-login gate re-opened for {len(regate)}.")
    mentors = fetch_all("mentors")   # re-read post-update
else:
    print("(read-only backup + cross-check; pass --apply to force imported mentors PPP-off + re-open onboarding)")

# 2. Full backup of the core app tables.
TABLES = [
    "mentors", "services", "service_questions", "weekly_availability", "specific_availability",
    "mentor_availability", "mentor_cancellation_policy", "mentor_bank_accounts",
    "bookings", "booking_pricing", "customer_payments", "mentor_payouts", "booking_ledger",
    "legacy_sessions", "reviews", "affiliates", "referral_codes", "commission_ledger",
    "platform_settings", "country_pricing", "ppp_factors", "fx_rates",
]
backup: dict = {"_meta": {"taken_at": TS, "source_env": ENV_FILE}}
backup["mentors"] = mentors
for t in TABLES:
    if t == "mentors":
        continue
    backup[t] = fetch_all(t)
backup_path = os.path.join(OUT_DIR, f"db_backup_{TS}.json")
with open(backup_path, "w", encoding="utf-8") as f:
    json.dump(backup, f, indent=2, default=str)

# 3. Cross-check against the legacy source + internal consistency.
preview_path = os.path.join(os.path.dirname(HERE), "mentor_migration_preview.json")
legacy = []
if os.path.exists(preview_path):
    raw_prev = json.load(open(preview_path, encoding="utf-8"))
    legacy = [(x.get("mentor") or x) for x in raw_prev]   # each preview row wraps the mentor under 'mentor'

services = backup["services"]
weekly = backup["weekly_availability"]
specific = backup["specific_availability"]
bookings = backup["bookings"]
legacy_sessions = backup["legacy_sessions"]

svc_by_mentor: dict = {}
for s in services:
    svc_by_mentor.setdefault(s.get("mentor_id"), []).append(s)
avail_mentor_ids = {w.get("mentor_id") for w in weekly} | {sp.get("mentor_id") for sp in specific}
mentor_ids = {m["id"] for m in mentors}
db_emails = {(m.get("email") or "").lower() for m in mentors if m.get("email")}
db_legacy = {m.get("legacy_id") for m in mentors if m.get("legacy_id")}

legacy_emails = {(m.get("email") or "").lower() for m in legacy if m.get("email")}
legacy_ids = {str(m.get("legacy_id")) for m in legacy if m.get("legacy_id")}
missing = [m for m in legacy if (m.get("email") or "").lower() not in db_emails and str(m.get("legacy_id")) not in {str(x) for x in db_legacy}]

no_service = [m for m in mentors if not svc_by_mentor.get(m["id"])]
no_avail = [m for m in mentors if m["id"] not in avail_mentor_ids]
still_ppp = [m for m in mentors if m.get("smart_pricing")]
orphan_bookings = [b for b in bookings if b.get("mentor_id") not in mentor_ids]

lines = [
    f"# Mentor data cross-check ({TS} UTC, {ENV_FILE})", "",
    f"- Backup file: `{os.path.relpath(backup_path)}`  ({len(mentors)} mentors)",
    f"- Legacy source (preview): **{len(legacy)}** mentors  ·  DB: **{len(mentors)}** mentors",
    f"- Bookings: {len(bookings)}  ·  Imported past sessions: {len(legacy_sessions)}  ·  Services: {len(services)}",
    "",
    f"## Missing from DB (in legacy source, not imported): {len(missing)}",
    *[f"- {m.get('display_name')} <{m.get('email')}>" for m in missing[:50]],
    "",
    f"## Mentors with NO bookable service: {len(no_service)}",
    *[f"- {m.get('display_name')} <{m.get('email')}>" for m in no_service[:80]],
    "",
    f"## Mentors with NO availability (weekly or specific): {len(no_avail)}",
    *[f"- {m.get('display_name')} <{m.get('email')}>" for m in no_avail[:80]],
    "",
    f"## PPP status: {len(still_ppp)} mentor(s) still have smart_pricing ON",
    *[f"- {m.get('display_name')} (needs_onboarding={m.get('needs_onboarding')})" for m in still_ppp[:80]],
    "",
    f"## Orphan bookings (mentor_id not in mentors): {len(orphan_bookings)}",
    *[f"- booking {b.get('id')}" for b in orphan_bookings[:50]],
    "",
]
report_path = os.path.join(OUT_DIR, f"crosscheck_{TS}.md")
with open(report_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"Backup:     {os.path.relpath(backup_path)}")
print(f"Crosscheck: {os.path.relpath(report_path)}")
print("---")
print(f"DB mentors={len(mentors)}  legacy={len(legacy)}  missing={len(missing)}  "
      f"no_service={len(no_service)}  no_availability={len(no_avail)}  ppp_on={len(still_ppp)}  "
      f"bookings={len(bookings)}  orphan_bookings={len(orphan_bookings)}  past_sessions={len(legacy_sessions)}")
