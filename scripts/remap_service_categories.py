"""Re-map migrated services onto the right session category (BUG-118).

Every migrated service already HAS a category, but the original mapping was crude: anything it didn't
recognise fell into "General Guidance", and anything mentioning a job fell into "Jobs & Careers". So
"Business Setup in Netherlands", "Investment Planning Abroad: Property, Taxes & Financial Growth" and
"Settling in Netherlands: Housing, Banking, Healthcare" all landed in General Guidance, while "Ask me
Anything about Netherlands" was filed under Jobs & Careers. Three of the eight categories
(Finance & Taxes, Culture & Daily Life, Business & Startup) had no services at all as a result, so
those browse filters returned nothing.

Matching is ordered most-specific first and scores title + description, so a session about housing
costs isn't stolen by the word "job" appearing once in the description.

  python -m scripts.remap_service_categories --dry-run
  python -m scripts.remap_service_categories
"""
import re
import sys

import db
from db.mentors import _supabase

# (category, keywords). Order matters: the first category to score highest wins ties, so the more
# specific themes are listed before the catch-alls.
RULES: list[tuple[str, list[str]]] = [
    ("Business & Startup", [
        "business setup", "start a business", "startup", "entrepreneur", "freelance", "self employed",
        "self-employed", "company formation", "kvk", "incorporat",
    ]),
    ("Finance & Taxes", [
        "tax", "investment", "invest", "pension", "mortgage", "insurance", "salary negotiation",
        "financial", "finance", "money", "banking basics", "30% ruling", "wealth",
    ]),
    ("Housing & Relocation", [
        "housing", "accommodation", "rent", "apartment", "relocat", "settling in", "settle in",
        "moving in", "utilities", "registration", "first weeks",
    ]),
    ("Culture & Daily Life", [
        "culture", "social life", "family", "kids", "school for children", "pets", "language learning",
        "daily life", "making friends", "integration", "food", "lifestyle",
    ]),
    ("Education & Studies", [
        "study", "student", "university", "admission", "course", "scholarship", "master", "phd",
        "education",
    ]),
    ("Visa & Immigration", [
        "visa", "permit", "immigration", "residence", "citizenship", "pr ", " pr", "asylum",
        "sponsor", "ind", "blue card", "work permit",
    ]),
    ("Jobs & Careers", [
        "job", "career", "cv", "resume", "linkedin", "interview", "hiring", "recruit", "employer",
        "application strategy", "job market",
    ]),
    ("General Guidance", [
        "ask me anything", "general", "q&a", "intro call", "discovery call", "quick call",
        "choosing the right country", "where to move", "clarity",
    ]),
]


def classify(title: str, description: str) -> str:
    """Best category for a session, or '' to leave the existing one alone.

    A TITLE keyword is required. Scoring the description too was worse than the mapping it replaced:
    "Introductory call" and "Quickie" say nothing themselves, so the description decided for them and
    a generic intro session became "Housing & Relocation" purely because housing is one of the things
    it lists. The title is what a session IS; the description is everything it might touch on. When the
    title is silent we keep whatever the service already has rather than guess."""
    t = f" {(title or '').lower()} "
    title_hits = {c: sum(1 for k in kws if k in t) for c, kws in RULES}
    if not any(title_hits.values()):
        return ""
    body = re.sub(r"<[^>]+>", " ", description or "").lower()
    best, best_score = "", 0.0
    for category, keywords in RULES:
        if not title_hits[category]:
            continue
        # Title decides; the description only breaks ties between two categories the title mentions.
        score = title_hits[category] * 3 + sum(0.1 for k in keywords if k in body)
        if score > best_score:
            best, best_score = category, score
    return best


def main() -> int:
    dry = "--dry-run" in sys.argv[1:]
    rows = (_supabase.table("services").select("id, title, description, category").execute().data) or []
    changed = 0
    for s in rows:
        new = classify(s.get("title") or "", s.get("description") or "")
        if not new or new == (s.get("category") or ""):
            continue
        print(f"  {str(s.get('category')):<22} -> {new:<22} {(s.get('title') or '')[:52]}")
        changed += 1
        if not dry:
            _supabase.table("services").update({"category": new}).eq("id", s["id"]).execute()
    print(f"\n{changed} service(s) {'would be' if dry else ''} re-categorised ({len(rows)} scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
