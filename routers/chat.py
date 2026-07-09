# routers/chat.py
import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import config
import db
import ai.graph as backend  # accessed at request-time so we read the lifespan-initialized app
from core.auth import AuthUser, get_current_user, get_current_user_optional
from ai.graph import _text
from core.rate_limit import limiter
from schema import ChatResponse
from ai.tools import parse_docx_to_text, parse_pdf_to_text

logger = logging.getLogger("immigroov.routers.chat")

router = APIRouter(tags=["chat"])

PDF_MAGIC = b"%PDF"
DOCX_MAGIC = b"PK\x03\x04"


def _detect_file_type(file_bytes: bytes, filename: str) -> Optional[str]:
    """Validate actual file content against declared extension via magic bytes."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf" and file_bytes[:4] == PDF_MAGIC:
        return "pdf"
    if ext == ".docx" and file_bytes[:4] == DOCX_MAGIC:
        return "docx"
    return None


# ── Groq rate-limit detection ───────────────────────────────────────────────────
# When Groq's per-minute/per-day token or request limit is hit it raises a 429. We
# surface that to the frontend as a clean 429 with the number of seconds to wait
# (from the provider's Retry-After header, or parsed from its message), so the UI can
# show a countdown instead of a generic error. The wait duration is NOT hardcoded -
# per-minute limits reset in ~seconds, per-day limits can be hours; we use whatever the
# provider tells us.

def _duration_to_seconds(text: str) -> Optional[int]:
    """Parse '30' (bare seconds) or '1h2m3.5s' / 'try again in 7m30s' into whole seconds."""
    text = text.strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    m = re.search(r"(?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", text)
    if m and (m.group(1) or m.group(2) or m.group(3)):
        total = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0)
        return int(total) + 1 if total > 0 else None
    m2 = re.search(r"in\s+(\d+)\s*minute", text)
    if m2:
        return int(m2.group(1)) * 60
    return None


def _looks_like_rate_limit(exc: BaseException) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 429:
        return True
    if "ratelimit" in exc.__class__.__name__.lower():
        return True
    text = str(exc).lower()
    return "rate limit" in text and "429" in text


def _rate_limit_retry_after(exc: BaseException) -> Optional[int]:
    """If exc (or anything in its cause chain) is a 429 rate-limit error, return seconds
    to wait (clamped 1..86400), else None."""
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if _looks_like_rate_limit(cur):
            secs: Optional[int] = None
            headers = getattr(getattr(cur, "response", None), "headers", None)
            if headers:
                for key in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
                    val = headers.get(key)
                    if val and (secs := _duration_to_seconds(str(val))) is not None:
                        break
            if secs is None:
                secs = _duration_to_seconds(str(getattr(cur, "message", "") or cur))
            return max(1, min(secs or 60, 86400))
        cur = cur.__cause__ or cur.__context__
    return None


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(config.RATE_LIMIT)
async def chat_handler(
    request: Request,
    message: str = Form(...),
    thread_id: str = Form(...),
    file: Optional[UploadFile] = File(None),
    user: Optional[AuthUser] = Depends(get_current_user_optional),
):
    """Main chat endpoint. Works in guest mode (no JWT) and authenticated mode."""
    try:
        uuid.UUID(thread_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid thread_id format.")

    session_config = {"configurable": {"thread_id": thread_id}}

    resume_text = None
    if file:
        file_bytes = await file.read()
        if len(file_bytes) > config.MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Max {config.MAX_FILE_BYTES // (1024 * 1024)} MB.",
            )

        file_type = _detect_file_type(file_bytes, file.filename or "")
        if not file_type:
            raise HTTPException(
                status_code=415,
                detail="Unsupported or mismatched file type. Upload PDF or DOCX only.",
            )
        resume_text = (
            parse_pdf_to_text(file_bytes) if file_type == "pdf" else parse_docx_to_text(file_bytes)
        )

    input_state = {"messages": [HumanMessage(content=message)]}
    if resume_text:
        input_state["resume_text"] = resume_text
        input_state["resume_processed"] = False

    if backend.app is None:
        raise HTTPException(status_code=503, detail="Agent not ready. Try again in a moment.")

    await backend.ensure_pg_connected()

    t_start = time.monotonic()
    try:
        final_state = await asyncio.wait_for(
            backend.app.ainvoke(input_state, config=session_config),
            timeout=config.AGENT_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Agent timed out. Please try again.")
    except Exception as e:
        retry_after = _rate_limit_retry_after(e)
        if retry_after is not None:
            logger.warning("Groq rate limit hit; retry_after=%ss", retry_after)
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "Groovia is handling a lot of requests right now. You can chat again shortly.",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
        logger.exception("Agent invocation failed", extra={"thread_id": thread_id})
        raise HTTPException(status_code=500, detail="Internal error. Please try again.")
    latency_ms = int((time.monotonic() - t_start) * 1000)

    # Title seed: a short snippet of the user's real message (not the system marker).
    title_seed = None
    if message and not message.startswith("[SYSTEM_"):
        title_seed = message.strip()

    # supabase-py is synchronous - run on the default thread pool so we don't block the event loop.
    await asyncio.to_thread(
        db.upsert_chat_thread,
        thread_id=thread_id,
        user_id=user.id if user else None,
        user_intent=final_state.get("user_intent"),
        track=final_state.get("track"),
        title_seed=title_seed,
    )

    # Mirror compressed resume onto the user's profile, write-once (manual edits stay).
    if user and final_state.get("resume_processed") and final_state.get("resume_text"):
        await asyncio.to_thread(
            db.save_profile_summary_if_empty, user.id, final_state["resume_text"]
        )

    # Log AI quality metrics for observability (non-blocking, best-effort).
    all_msgs = final_state.get("messages", [])
    tool_call_count = sum(1 for m in all_msgs if isinstance(m, ToolMessage))
    revision_count = final_state.get("revision_count", 0)
    quality_failure = revision_count > 0 and final_state.get("critique") not in (None, "PASSED")
    await asyncio.to_thread(
        db.upsert_ai_event,
        thread_id=thread_id,
        intent=final_state.get("user_intent"),
        revision_count=revision_count,
        tool_calls=tool_call_count,
        latency_ms=latency_ms,
        model=config.MAIN_MODEL_NAME,
        quality_failure=quality_failure,
    )

    return {
        "status": "success",
        "response": _text(final_state["messages"][-1].content),
        "thread_id": thread_id,
    }


@router.get("/chat/threads")
def list_threads(user: AuthUser = Depends(get_current_user)):
    """List the authenticated user's chat threads (newest first).
    Sidebar visibility is gated on the frontend via FEATURES.chatHistory; the data
    endpoint is always available so chat-persist (auto-resume last thread on sign-in)
    can work even when the sidebar history list is hidden."""
    return {"threads": db.list_user_threads(user.id)}


@router.post("/chat/threads/{thread_id}/claim")
def claim_thread(thread_id: str, user: AuthUser = Depends(get_current_user)):
    """Link a guest thread (user_id=NULL) to the now-signed-in user.
    Called by the frontend after sign-in so prior guest chats show up in history."""
    try:
        uuid.UUID(thread_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid thread_id format.")
    ok = db.claim_thread(thread_id, user.id)
    return {"claimed": ok}


@router.get("/chat/threads/{thread_id}/messages")
async def get_thread_messages(
    thread_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """Return the message history for a thread the user owns."""
    try:
        uuid.UUID(thread_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid thread_id format.")

    owner = await asyncio.to_thread(db.get_thread_owner, thread_id)
    if owner != user.id:
        raise HTTPException(status_code=404, detail="Thread not found.")

    if backend.app is None:
        raise HTTPException(status_code=503, detail="Agent not ready.")

    state = await backend.app.aget_state({"configurable": {"thread_id": thread_id}})
    raw_messages = state.values.get("messages", []) if state and state.values else []

    out = []
    for m in raw_messages:
        if isinstance(m, HumanMessage):
            role = "user"
        elif isinstance(m, AIMessage):
            role = "assistant"
        else:
            continue  # skip ToolMessage / SystemMessage
        content = _text(m.content).strip()
        if content:
            out.append({"role": role, "content": content})

    return {"thread_id": thread_id, "messages": out}
