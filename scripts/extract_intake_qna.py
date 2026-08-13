#!/usr/bin/env python3
"""Pull the customer intake Q&A off the legacy /bookings endpoint into a local file.

The legacy API returns a `questions_and_answers` list on past bookings: the answers a customer
typed while booking, which the mentor read before the call. Our migration transform drops them,
and `service_questions` / `booking_question_answers` are both still empty, so this is the only
copy that exists. Parked as a file now, loaded into the DB when FEAT-007 lands.

Read-only against the legacy API. Writes data/intake_qna.json.
"""
import json, logging, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.migrate_mentors import fetch_all_mentors, fetch_bookings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("qna")

OUT = Path(__file__).resolve().parents[1] / "data" / "intake_qna.json"


def main() -> int:
    mentors = fetch_all_mentors()
    log.info("mentors from legacy API: %d", len(mentors))

    records, questions = [], {}
    for m in mentors:
        mid = m.get("id")
        if not mid:
            continue
        for b in fetch_bookings(mid):
            qna = b.get("questions_and_answers") or []
            if not qna:
                continue
            pairs = [{"question": (q.get("question") or "").strip(),
                      "answer": (q.get("answer") or "").strip()}
                     for q in qna if (q.get("answer") or "").strip()]
            if not pairs:
                continue
            records.append({
                "legacy_booking_id": b.get("id") or b.get("_id"),
                "legacy_mentor_id": mid,
                "mentor_name": " ".join(filter(None, [m.get("first_name"), m.get("last_name")])).strip() or None,
                "mentor_email": m.get("email"),
                "service": b.get("service"),
                "customer": b.get("customer"),
                "slot": b.get("slot"),
                "status": b.get("status"),
                "qna": pairs,
            })
            for p in pairs:
                questions[p["question"]] = questions.get(p["question"], 0) + 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"note": "Legacy customer intake answers. Source of truth until FEAT-007 loads them into "
                 "booking_question_answers. Do not edit by hand.",
         "bookings_with_answers": len(records),
         "distinct_questions": sorted(questions.items(), key=lambda kv: -kv[1]),
         "records": records},
        indent=2, ensure_ascii=False), encoding="utf-8")

    log.info("bookings carrying answers: %d", len(records))
    log.info("distinct questions asked: %d", len(questions))
    for q, n in sorted(questions.items(), key=lambda kv: -kv[1]):
        log.info("   %3dx  %s", n, q[:90])
    log.info("written -> %s", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
