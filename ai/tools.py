import io
import json
import logging
from typing import Optional

import docx2txt
from pypdf import PdfReader
from langchain_core.tools import tool
from langchain_tavily import TavilySearch
from exa_py import Exa
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import db
from config import (
    EXA_API_KEY, FRONTEND_URL,
    TAVILY_MAX_RESULTS, EXA_NUM_RESULTS, EXA_HIGHLIGHT_MAX_CHARS,
)

logger = logging.getLogger("immigroov.tools")

exa = Exa(api_key=EXA_API_KEY)
_tavily = TavilySearch(max_results=TAVILY_MAX_RESULTS)

# Transient 5xx/network blips from search providers are common. Retry twice with backoff.
_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)


@_retry
def _tavily_invoke(query: str):
    return _tavily.invoke({"query": query})


@_retry
def _exa_search(query: str):
    return exa.search(
        query,
        type="neural",
        num_results=EXA_NUM_RESULTS,
        contents={"highlights": {"max_characters": EXA_HIGHLIGHT_MAX_CHARS}},
    )


def parse_pdf_to_text(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    return "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])


def parse_docx_to_text(file_bytes: bytes) -> str:
    return docx2txt.process(io.BytesIO(file_bytes))


@tool
def general_search(query: str) -> str:
    """Broad web search for context: culture, cost of living, lifestyle, market trends, pros/cons.
    Examples: 'tech scene in Germany', 'cost of living in Amsterdam', 'expat life in the USA'.
    Not for visa rules, salary figures, or official policy - use precise_search for those.
    """
    logger.info("tool=general_search (Tavily) query=%r", query)
    try:
        return str(_tavily_invoke(query))
    except Exception as e:
        return f"[SEARCH_ERROR] general_search failed: {e}"


@tool
def precise_search(query: str) -> str:
    """Precise search for facts, with source URLs: exact visa names, salary/tuition figures,
    immigration rules, official government policy, university programmes.
    Argument: query - a specific question about a visa, rule, salary, or policy.
    """
    logger.info("tool=precise_search (Exa) query=%r", query)
    try:
        response = _exa_search(query)
        results = [
            {"url": r.url, "summary": r.highlights[0] if getattr(r, "highlights", None) else "N/A"}
            for r in response.results
        ]
        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        return f"[SEARCH_ERROR] precise_search failed: {e}"


# Topic labels/synonyms -> the expertise_categories code stored on mentors, so the agent
# can pass whatever the user said ("Career", "visa", "study abroad") and still filter.
_CATEGORY_ALIASES = {
    "job_career": "job_career", "career": "job_career", "careers": "job_career",
    "job": "job_career", "jobs": "job_career", "job & career": "job_career",
    "jobs & careers": "job_career", "work": "job_career", "employment": "job_career",
    "study_abroad": "study_abroad", "study": "study_abroad", "study abroad": "study_abroad",
    "studies": "study_abroad", "education": "study_abroad", "student": "study_abroad",
    "visa_pr": "visa_pr", "visa": "visa_pr", "visa & pr": "visa_pr", "pr": "visa_pr",
    "permanent residency": "visa_pr", "residency": "visa_pr", "green card": "visa_pr",
    "life_settling": "life_settling", "life": "life_settling", "life abroad": "life_settling",
    "settling": "life_settling", "settling in": "life_settling", "relocation": "life_settling",
    "work_visa": "work_visa", "work visa": "work_visa",
    "asylum": "asylum", "refugee": "asylum", "asylum & refugee": "asylum",
    "family_visa": "family_visa", "family": "family_visa", "family reunification": "family_visa",
    "entrepreneur": "entrepreneur", "entrepreneurship": "entrepreneur", "startup": "entrepreneur",
    "entrepreneur & startup": "entrepreneur",
}


def _normalize_category(raw: str) -> Optional[str]:
    """Map a free-text topic (label, code, or synonym) to a stored category code, or None
    if it isn't one we filter on (so we fall back to an unfiltered country search)."""
    if not raw:
        return None
    return _CATEGORY_ALIASES.get(raw.strip().lower())


@tool
def retrieve_matching_mentors(target_country: str, category: str = "", profile_keyword: str = "") -> str:
    """Get mentors for a country (and optionally a focus area) from the database.
    target_country: ISO 3166-1 alpha-2 code (US, GB, AU, NL, DE, CA...).
    category (optional): the focus area the user wants help with. Accepts either a code
      (job_career, study_abroad, visa_pr, life_settling) or a label the user typed
      (Career, Study Abroad, Visa & PR, Life Abroad). Narrows results to that focus.
    profile_keyword (optional): filter by headline domain, e.g. 'Software', 'AI', 'Finance'.
    """
    norm_cat = _normalize_category(category)
    logger.info(
        "tool=retrieve_matching_mentors country=%r category=%r->%r keyword=%r",
        target_country, category, norm_cat, profile_keyword,
    )
    try:
        rows = db.list_active_mentors(
            country_code=target_country,
            category=norm_cat,
            profile_keyword=profile_keyword or None,
            limit=20,
        )
        # If nobody matches that exact focus in this country, fall back to all mentors there
        # rather than a dead end - the caller can still offer a relevant person.
        if not rows and norm_cat:
            rows = db.list_active_mentors(
                country_code=target_country,
                profile_keyword=profile_keyword or None,
                limit=20,
            )
        results = [
            {
                "name": r["display_name"],
                "headline": r.get("headline") or "",
                "booking_url": f"{FRONTEND_URL}/mentors/{r['slug']}",
            }
            for r in rows
        ]
        return json.dumps(results)
    except Exception as e:
        logger.exception("Mentor retrieval failed")
        return f"[TOOL_ERROR] Mentor retrieval failed: {e}"
