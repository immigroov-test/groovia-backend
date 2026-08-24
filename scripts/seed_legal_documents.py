"""Load the legal document TEXT and publish v1.0 for anything not yet published.

migrations/legal_documents_setup.sql seeds the 14 catalogue rows (title, audience,
region scope) but no content — a 6,000-word contract does not belong in a schema file.
This script fills that in from content/legal/<slug>.md.

It is deliberately conservative, because it runs against a database that may already
have published versions an admin edited by hand:

  * A document that already has a published version is SKIPPED. The file on disk is
    the initial import, not the source of truth — once v1.0 exists the CMS owns the
    text, and re-running this must never quietly revert an admin's edit.
  * --as-draft loads the file into the DRAFT slot instead of publishing, for a
    document that already has versions. The admin then sees the change in the CMS
    and decides whether it is worth an official update.

Publishing goes through publish_legal_document() rather than an INSERT, so the seed
takes exactly the same transactional path as the Publish button, gets the same
version numbering, and lands the same audit record.

Usage:
  python -m scripts.seed_legal_documents --dry-run     # report what would happen
  python -m scripts.seed_legal_documents               # publish v1.0 where missing
  python -m scripts.seed_legal_documents --as-draft    # load files as drafts instead
  python -m scripts.seed_legal_documents --only privacy-policy,cookie-policy
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content" / "legal"


def _read(slug: str) -> str | None:
    path = CONTENT_DIR / f"{slug}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed legal document content.")
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    ap.add_argument("--as-draft", action="store_true",
                    help="save as a draft instead of publishing (for already-published documents)")
    ap.add_argument("--only", default="", help="comma-separated slugs to limit the run to")
    ap.add_argument("--actor", default="", help="profile id to record as the publisher")
    args = ap.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()}

    # The publisher recorded against v1.0. Falls back to any admin account, so the
    # history line reads as a person rather than an empty cell.
    actor = args.actor or db.first_admin_profile_id()
    if not actor and not args.dry_run:
        print("No admin profile found and no --actor given; the seed would have no publisher.")
        return 1

    docs = db.legal_admin_documents()
    if not docs:
        print("No legal_documents rows. Run migrations/legal_documents_setup.sql first.")
        return 1

    published = skipped = drafted = missing = 0
    for d in docs:
        slug = d["slug"]
        if only and slug not in only:
            continue

        content = _read(slug)
        if content is None:
            print(f"  MISSING  {slug:<32} no content/legal/{slug}.md")
            missing += 1
            continue

        has_version = bool(d.get("current_version_id"))

        if has_version and not args.as_draft:
            print(f"  skip     {slug:<32} already at {d.get('current_version')}")
            skipped += 1
            continue

        action = "draft" if (has_version or args.as_draft) else "publish v1.0"
        if args.dry_run:
            print(f"  would {action:<12} {slug:<32} {len(content):>6} chars")
            continue

        db.legal_save_draft(d["id"], actor, content)
        if has_version or args.as_draft:
            print(f"  drafted  {slug:<32} {len(content):>6} chars")
            drafted += 1
        else:
            res = db.legal_publish(d["id"], actor, change_note="Initial import")
            print(f"  published {slug:<31} {res.get('version')}  {len(content):>6} chars")
            published += 1

    print(f"\npublished={published} drafted={drafted} skipped={skipped} missing={missing}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
