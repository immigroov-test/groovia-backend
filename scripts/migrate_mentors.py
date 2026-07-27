#!/usr/bin/env python3
"""One-off migration: pull mentors from the legacy immigroov API into our Supabase.

READ-ONLY against the source (the old public API). Idempotent on `mentors.legacy_id`, so it is
safe to re-run. Dry-run by default (fetch + transform + write a preview JSON); pass --commit with
Supabase creds to actually load.

    # dry-run: fetch everyone, transform, write preview to ./mentor_migration_preview.json
    python scripts/migrate_mentors.py

    # load into Supabase (staging first!) + copy photos into the mentor-photos bucket
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python scripts/migrate_mentors.py --commit

Decisions baked in (from planning):
  - Photos are copied into the Supabase `mentor-photos` bucket (not just referenced).
  - Test / blank profiles are imported but marked is_active=FALSE (hidden from browse) for review.
  - Mentors are imported as status='approved', profile_id=NULL; the app's link_mentor_by_email
    attaches each account on first login (by email). No passwords injected.
"""
import argparse
import json
import logging
import mimetypes
import os
import re
import sys
from typing import Any, Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("migrate")

SOURCE_BASE = os.getenv("LEGACY_API_BASE", "https://api.immigroov.com/dev/v1")
PREVIEW_PATH = "mentor_migration_preview.json"   # transformed to our shapes (review this)
RAW_PATH = "mentor_migration_raw.json"           # untouched API responses (a backup of the old data)
PHOTO_BUCKET = "mentor-photos"

# Old language names -> our ISO 639-1 codes (lib/languages). Extend as needed; unknowns are logged.
LANG_NAME_TO_CODE = {
    "english": "en", "tamil": "ta", "telugu": "te", "kannada": "kn", "hindi": "hi",
    "malayalam": "ml", "german": "de", "italian": "it", "french": "fr", "dutch": "nl",
    "spanish": "es", "portuguese": "pt", "japanese": "ja", "mandarin": "zh",
    "chinese (mandarin)": "zh", "arabic": "ar", "marathi": "mr", "gujarati": "gu",
    "bengali": "bn", "punjabi": "pa", "urdu": "ur", "russian": "ru", "swedish": "sv",
    "danish": "da", "norwegian": "no", "finnish": "fi", "polish": "pl",
    "georgian": "ka", "greek": "el", "turkish": "tr", "ukrainian": "uk", "romanian": "ro",
    "vietnamese": "vi", "thai": "th", "indonesian": "id", "tagalog": "tl", "filipino": "tl",
    "nepali": "ne", "sinhala": "si", "korean": "ko", "czech": "cs", "hungarian": "hu",
    "odia": "or", "oriya": "or", "assamese": "as", "sindhi": "sd", "konkani": "kok",
}
# Old social platform -> our social_links `type`.
SOCIAL_PLATFORM_TO_TYPE = {
    "linkedin": "linkedin", "youtube": "youtube", "instagram": "instagram",
    "twitter": "twitter", "x": "twitter", "facebook": "facebook", "github": "github",
    "website": "website",
}
# Old service `category_search_id` -> (our SERVICE_CATEGORIES value, our expertise_category code).
CATEGORY_MAP = {
    "job-market-career-abroad": ("Jobs & Careers", "job_career"),
    "visa-immigration": ("Visa & Immigration", "visa_pr"),
    "study-abroad": ("Education & Studies", "study_abroad"),
    "housing-relocation": ("Housing & Relocation", "life_settling"),
    "finance-taxes": ("Finance & Taxes", "life_settling"),
    "culture-daily-life": ("Culture & Daily Life", "life_settling"),
    "business-startup": ("Business & Startup", "entrepreneur"),
}
DEFAULT_SERVICE_CATEGORY = "General Guidance"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ── small parsers / cleaners ─────────────────────────────────────────────────────

def _num(s: Any) -> Optional[float]:
    m = re.search(r"\d+(?:\.\d+)?", str(s or ""))
    return float(m.group()) if m else None

def parse_hours(s: Any) -> Optional[float]:
    """'24 hours' -> 24, '90 days' -> 2160, '15 minutes' -> 0.25."""
    v = _num(s)
    if v is None:
        return None
    t = str(s).lower()
    if "day" in t:
        return v * 24
    if "min" in t:
        return v / 60
    return v

def parse_days(s: Any) -> Optional[int]:
    v = _num(s)
    if v is None:
        return None
    return int(v * 30) if "month" in str(s).lower() else int(v)

def clamp(v: Optional[float], lo: float, hi: float, default: float) -> float:
    if v is None:
        return default
    return max(lo, min(hi, v))

def clean_text(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()

def clean_city(s: Any) -> Optional[str]:
    c = clean_text(s)
    # The old data often puts a timezone label in `city` ("UTC+01:00 - Central Europe").
    if not c or c.upper().startswith("UTC") or re.match(r"^[+-]?\d{1,2}:\d{2}", c):
        return None
    return c

def norm_url(u: str) -> str:
    u = (u or "").strip()
    if u and not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u

def is_blank(s: Any) -> bool:
    return not clean_text(re.sub(r"<[^>]+>", "", str(s or "")))


# ── extraction (read-only against the legacy API) ────────────────────────────────

def _get(path: str) -> Any:
    r = requests.get(f"{SOURCE_BASE}{path}", timeout=30,
                     headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()

def fetch_all_mentors() -> list[dict]:
    out, page = [], 1
    while True:
        data = _get(f"/mentors?page={page}&page_size=50&search=")
        batch = data.get("mentors") or []
        out.extend(batch)
        total = data.get("total") or len(out)
        log.info("fetched page %s (%s/%s)", page, len(out), total)
        if len(out) >= total or not batch:
            break
        page += 1
    return out

def fetch_services(mid: str) -> list[dict]:
    try:
        return _get(f"/mentor/{mid}/services") or []
    except Exception as e:
        log.warning("services fetch failed for %s: %s", mid, e)
        return []

def fetch_weekly(mid: str) -> list[dict]:
    try:
        return (_get(f"/mentor/{mid}/weekly-availability") or {}).get("data") or []
    except Exception as e:
        log.warning("weekly fetch failed for %s: %s", mid, e)
        return []


# ── transform (pure) ─────────────────────────────────────────────────────────────

def transform_mentor(raw: dict, services: list[dict], weekly: list[dict]) -> dict:
    """Map one legacy mentor (+ its services + weekly availability) to our shapes.
    Returns {mentor, services, weekly, photo_url, warnings}."""
    warnings: list[str] = []
    name = clean_text(f"{raw.get('first_name','')} {raw.get('last_name','')}")
    email = (raw.get("email") or "").strip().lower()
    if not email:
        warnings.append("no email (cannot link an account on login)")

    country = (raw.get("country") or "").strip().upper()
    expertise_countries = [country] if re.fullmatch(r"[A-Z]{2}", country) else []

    languages: list[str] = []
    for lang in raw.get("languages") or []:
        code = LANG_NAME_TO_CODE.get((lang.get("name") or "").strip().lower())
        if code:
            languages.append(code)
        else:
            warnings.append(f"unmapped language: {lang.get('name')}")

    socials = []
    for s in raw.get("social_links") or []:
        t = SOCIAL_PLATFORM_TO_TYPE.get((s.get("platform") or "").strip().lower())
        url = norm_url(s.get("url", ""))
        if t and url:
            socials.append({"type": t, "url": url})

    # Test / blank profiles come across but hidden (is_active=FALSE) for review.
    blank = is_blank(raw.get("title")) and is_blank(raw.get("about_me"))
    looks_test = "test" in name.lower() or "test" in clean_text(raw.get("title")).lower()
    is_active = bool(raw.get("is_available")) and not blank and not looks_test
    if blank or looks_test:
        warnings.append("test/blank profile -> imported inactive")

    # Service categories seed the mentor's expertise tags.
    expertise_categories: list[str] = []
    out_services = []
    seen_services: set[tuple[str, int]] = set()   # drop duplicate services (same title + length)
    for sv in services:
        if is_blank(sv.get("title")):
            continue
        dedup_key = (clean_text(sv.get("title")).lower(), int(sv.get("duration") or 30))
        if dedup_key in seen_services:
            warnings.append(f"duplicate service dropped: {clean_text(sv.get('title'))}")
            continue
        seen_services.add(dedup_key)
        cat_slug = sv.get("category_search_id")
        svc_cat, exp_cat = CATEGORY_MAP.get(cat_slug, (DEFAULT_SERVICE_CATEGORY, None))
        if exp_cat and exp_cat not in expertise_categories:
            expertise_categories.append(exp_cat)
        pricing = (sv.get("pricing") or [{}])[0]
        stype = sv.get("type") if sv.get("type") in ("video", "dm") else "video"
        out_services.append({
            "title": clean_text(sv.get("title")),
            "description": sv.get("description") or None,
            "type": stype,
            "duration": int(sv.get("duration") or 30),
            "is_ppp": bool(sv.get("is_ppp")),
            "is_active": bool(sv.get("is_active")),
            "set_price": float(pricing.get("base_price") or 0),
            "set_currency": (pricing.get("currency") or raw.get("currency") or "USD").upper(),
            "category": svc_cat,
            "status": "approved",
            "legacy_id": sv.get("id"),
        })

    out_weekly = []
    seen_slots: set[tuple[str, str, str]] = set()   # drop duplicate weekly slots
    for day in weekly:
        wd = clean_text(day.get("weekday"))
        for slot in day.get("slots") or []:
            st, et = slot.get("start_time"), slot.get("end_time")
            if wd and st and et and st < et and (wd, st, et) not in seen_slots:
                seen_slots.add((wd, st, et))
                out_weekly.append({"weekday": wd, "start_time": st, "end_time": et,
                                   "timezone": raw.get("app_timezone") or "UTC"})

    mentor = {
        "legacy_id": raw.get("id"),
        "email": email,
        "display_name": name or email or "Mentor",   # mentors has no full_name column; the name lives here
        "headline": clean_text(raw.get("title")) or None,
        "bio": (raw.get("about_me") or None) if not is_blank(raw.get("about_me")) else None,
        "public_notes": clean_text(raw.get("disclaimer")) or None,
        "phone": clean_text(raw.get("phone_number")) or None,
        "country": country or None,
        "city": clean_city(raw.get("city")),
        "timezone": raw.get("app_timezone") or "UTC",
        "app_timezone": raw.get("app_timezone") or "UTC",
        "currency": (raw.get("currency") or "USD").upper(),
        # Our PPP control is the mentor-level smart_pricing toggle (the charge + the browse card
        # both key off it). The legacy data carried PPP per service, so enable smart_pricing when
        # the mentor used fair pricing on any service; otherwise their PPP intent would be lost.
        "smart_pricing": any(s["is_ppp"] for s in out_services),
        "languages": languages,
        "social_links": socials,
        "expertise_country_codes": expertise_countries,
        "expertise_categories": expertise_categories,
        "min_notice_hours": clamp(parse_hours(raw.get("app_minimum_notice")), 0, 24, 24),
        "days_ahead": int(clamp(parse_days(raw.get("app_booking_window")), 1, 90, 30)),
        "cancel_notice_hours": int(clamp(parse_hours(raw.get("app_cancellation_policy")), 2, 48, 24)),
        "avg_rating": float(raw.get("overall_rating") or 0),
        "review_count": int(raw.get("review_count") or 0),
        "status": "approved",
        "is_active": is_active,
        "legacy_data": {
            "total_sessions": raw.get("total_sessions"),
            "response_time": raw.get("response_time"),
            "app_buffertime": raw.get("app_buffertime"),
            "app_reschedule_policy": raw.get("app_reschedule_policy"),
            "source_profile_pic_url": raw.get("profile_pic_url") or None,
        },
    }
    return {"mentor": mentor, "services": out_services, "weekly": out_weekly,
            "photo_url": (raw.get("profile_pic_url") or "").strip() or None, "warnings": warnings}


# ── load (needs Supabase service-role creds) ─────────────────────────────────────

def _supabase():
    from supabase import create_client
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        log.error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY required for --commit")
        sys.exit(1)
    return create_client(url, key)

def _unique_slug(sb, name: str) -> str:
    base = _SLUG_RE.sub("-", (name or "mentor").lower()).strip("-") or "mentor"
    slug = base
    for i in range(6):
        if not sb.table("mentors").select("id").eq("slug", slug).limit(1).execute().data:
            return slug
        slug = f"{base}-{i+2}"
    import uuid
    return f"{base}-{uuid.uuid4().hex[:6]}"

def copy_photo(sb, legacy_id: str, src_url: str) -> Optional[str]:
    try:
        r = requests.get(src_url, timeout=30)
        r.raise_for_status()
        ext = os.path.splitext(src_url.split("?")[0])[1] or mimetypes.guess_extension(r.headers.get("Content-Type", "")) or ".jpg"
        path = f"legacy/{legacy_id}{ext}"
        sb.storage.from_(PHOTO_BUCKET).upload(
            path, r.content,
            {"content-type": r.headers.get("Content-Type", "image/jpeg"), "upsert": "true"},
        )
        return sb.storage.from_(PHOTO_BUCKET).get_public_url(path)
    except Exception as e:
        log.warning("photo copy failed for %s: %s", legacy_id, e)
        return None

def load_one(sb, rec: dict) -> None:
    m = dict(rec["mentor"])
    # min_notice/days_ahead/cancel are applied via avail_set_rules after the row exists.
    min_notice = m.pop("min_notice_hours"); days_ahead = m.pop("days_ahead"); cancel = m.pop("cancel_notice_hours")
    existing = sb.table("mentors").select("id, slug").eq("legacy_id", m["legacy_id"]).limit(1).execute().data
    if existing:
        mid, m["slug"] = existing[0]["id"], existing[0]["slug"]
        sb.table("mentors").update(m).eq("id", mid).execute()
    else:
        m["slug"] = _unique_slug(sb, m["display_name"])
        mid = sb.table("mentors").insert(m).execute().data[0]["id"]

    if rec.get("photo_url"):
        new_url = copy_photo(sb, m["legacy_id"], rec["photo_url"])
        if new_url:
            sb.table("mentors").update({"photo_url": new_url}).eq("id", mid).execute()

    sb.rpc("avail_set_rules", {"p_mentor_id": mid, "p_days_ahead": days_ahead,
                               "p_min_notice_hours": min_notice, "p_cancel_hours": cancel}).execute()

    # Idempotent children: clear then re-insert.
    sb.table("services").delete().eq("mentor_id", mid).execute()
    for sv in rec["services"]:
        sb.table("services").insert({**{k: v for k, v in sv.items() if k != "legacy_id"}, "mentor_id": mid}).execute()
    sb.table("weekly_availability").delete().eq("mentor_id", mid).execute()
    for wk in rec["weekly"]:
        sb.table("weekly_availability").insert({**wk, "mentor_id": mid}).execute()


# ── main ─────────────────────────────────────────────────────────────────────────

def dedupe_by_email(records: list[dict]) -> list[dict]:
    """Two legacy rows sharing an email would both try to link to one account on login. Keep the
    better of each (active first, then more services); drop the rest. Rows without an email pass through."""
    best: dict[str, dict] = {}
    passthrough: list[dict] = []
    dropped: list[str] = []
    for r in records:
        email = r["mentor"]["email"]
        if not email:
            passthrough.append(r)
            continue
        cur = best.get(email)
        if cur is None:
            best[email] = r
        else:
            winner = max((cur, r), key=lambda x: (x["mentor"]["is_active"], len(x["services"])))
            loser = r if winner is cur else cur
            best[email] = winner
            dropped.append(f'{loser["mentor"]["legacy_id"]} ({email})')
    if dropped:
        log.info("de-duplicated %s mentors sharing an email: %s", len(dropped), dropped)
    return list(best.values()) + passthrough


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="actually load into Supabase (else dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="only process the first N mentors (testing)")
    args = ap.parse_args()

    raw_mentors = fetch_all_mentors()
    if args.limit:
        raw_mentors = raw_mentors[: args.limit]
    log.info("transforming %s mentors", len(raw_mentors))

    raw_bundle, records = [], []
    for raw in raw_mentors:
        mid = raw.get("id")
        services, weekly = fetch_services(mid), fetch_weekly(mid)
        raw_bundle.append({"profile": raw, "services": services, "weekly": weekly})
        rec = transform_mentor(raw, services, weekly)
        records.append(rec)
        if rec["warnings"]:
            log.info("  %s (%s): %s", rec["mentor"]["display_name"], rec["mentor"]["email"], "; ".join(rec["warnings"]))

    # A raw, untouched backup of the old data (in case we need to re-map anything later).
    with open(RAW_PATH, "w", encoding="utf-8") as f:
        json.dump(raw_bundle, f, indent=2, ensure_ascii=False)

    records = dedupe_by_email(records)
    with open(PREVIEW_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    log.info("wrote raw -> %s and preview -> %s (%s mentors, %s inactive)",
             RAW_PATH, PREVIEW_PATH, len(records), sum(1 for r in records if not r["mentor"]["is_active"]))

    if not args.commit:
        log.info("dry-run: review %s, then re-run with --commit + Supabase creds", PREVIEW_PATH)
        return

    sb = _supabase()
    ok = 0
    for rec in records:
        try:
            load_one(sb, rec)
            ok += 1
        except Exception as e:
            log.error("load failed for %s (%s): %s", rec["mentor"]["display_name"], rec["mentor"]["legacy_id"], e)
    log.info("loaded %s/%s mentors", ok, len(records))


if __name__ == "__main__":
    main()
