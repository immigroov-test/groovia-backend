"""Align mentors.currency / hourly_rate with the mentor's OWN imported sessions.

The mentor import left many rows whose currency disagrees with their sessions: sessions priced INR 3196
on a mentor row marked USD, sessions in AUD/EUR/CAD on rows marked USD, etc. That wrong currency is what
the first-login rate form used to prefill, so a mentor saw a currency they never picked (and repricing
from it would have multiplied every session price by the FX rate).

Sets currency = the currency of the mentor's priciest active session, and hourly_rate = that session's
price scaled to 60 minutes. Only touches mentors whose stored currency actually disagrees, only when
they have priced active sessions, and never touches session prices themselves. Idempotent.

  python -m scripts.fix_mentor_base_currency --dry-run
  python -m scripts.fix_mentor_base_currency
"""
import sys

import db
from db.mentors import _supabase


def main() -> int:
    dry = "--dry-run" in sys.argv[1:]
    mentors = _supabase.table("mentors").select("id, slug, currency, hourly_rate").execute().data or []
    changed = 0
    for m in mentors:
        p = db.derive_rate_prefill(m)
        if p["source"] != "sessions" or not p["hourly_rate"]:
            continue
        same_ccy = (m.get("currency") or "").upper() == p["currency"]
        same_rate = m.get("hourly_rate") is not None and abs(float(m["hourly_rate"]) - p["hourly_rate"]) < 0.01
        if same_ccy and same_rate:
            continue
        print(f"{m['slug']:<38} {m.get('currency')} {m.get('hourly_rate')}  ->  {p['currency']} {p['hourly_rate']}")
        changed += 1
        if not dry:
            _supabase.table("mentors").update(
                {"currency": p["currency"], "hourly_rate": p["hourly_rate"]}).eq("id", m["id"]).execute()
    print(f"\n{changed} mentor(s) {'would be' if dry else ''} updated ({len(mentors)} scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
