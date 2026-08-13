"""Quote of the day. Fetches ZenQuotes at most once per day (cached in-process) and
serves the same quote to everyone. Falls back to a default if the API is unreachable."""
import datetime
import logging

import httpx
from fastapi import APIRouter

logger = logging.getLogger("immigroov.routers.quote")

router = APIRouter(prefix="/quote", tags=["quote"])

_ZENQUOTES_URL = "https://zenquotes.io/api/today"
# Mirrors groovia-frontend/lib/content.ts `quote` so the fallback matches the UI default.
_DEFAULT = {"text": "Seek wealth, even if it means crossing the ocean.", "author": ""}
_cache: dict = {"date": None, "quote": None}


@router.get("/today")
def quote_today() -> dict:
    today = datetime.date.today().isoformat()
    if _cache["date"] == today and _cache["quote"]:
        return _cache["quote"]
    try:
        resp = httpx.get(_ZENQUOTES_URL, timeout=8)
        resp.raise_for_status()
        item = resp.json()[0]
        quote = {"text": (item.get("q") or "").strip(), "author": (item.get("a") or "").strip()}
        if quote["text"]:
            _cache.update(date=today, quote=quote)
            return quote
    except Exception:
        logger.warning("ZenQuotes fetch failed; serving default quote")
    return _DEFAULT
