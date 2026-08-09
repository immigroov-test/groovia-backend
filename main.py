# FastAPI app entry point: app setup + router includes + lifespan.
import asyncio
import logging
import sys
from contextlib import asynccontextmanager

# psycopg's async driver needs the Selector loop, not the default Proactor (Windows-only).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import config
import db
from core.rate_limit import limiter
from routers import admin as admin_router
from routers import auth as auth_router
from routers import availability as availability_router
from routers import booking as booking_router
from routers import chat as chat_router
from routers import contact as contact_router
from routers import mentor as mentor_router
from routers import mentors as mentors_router
from routers import payments as payments_router
from routers import pricing as pricing_router
from routers import quote as quote_router
from routers import referrals as referrals_router
from routers import reviews as reviews_router
from routers import services as services_router

if config.MOCK_SERVICES:
    from routers import dev as dev_router


def _configure_logging() -> None:
    """Structured JSON logs in production, plain text in local dev.
    Render/Vercel log viewers handle JSON well; dev terminals are friendlier with plain."""
    if sys.platform == "win32":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        return
    try:
        from pythonjsonlogger.json import JsonFormatter  # type: ignore
    except ImportError:
        # Fallback path if the dep isn't installed yet.
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


_configure_logging()
logger = logging.getLogger("immigroov.api")


@asynccontextmanager
async def lifespan(api: FastAPI):
    # PPP pricing country comes from the signed edge-geo header; without INTERNAL_GEO_TOKEN the backend
    # falls back to the CLIENT-supplied country, which anyone can spoof for cheaper pricing. Warn loudly
    # so this is never silently missing in a real deploy (must be set on BOTH the backend and the BFF).
    if not config.INTERNAL_GEO_TOKEN:
        logger.warning("INTERNAL_GEO_TOKEN is not set: PPP pricing trusts the client-supplied country "
                       "and is spoofable. Set it on the backend AND the frontend (Vercel) before going live.")
    from ai import init_agent, shutdown_agent
    await init_agent()
    try:
        yield
    finally:
        await shutdown_agent()


api = FastAPI(title="Immigroov AI Career Engine", lifespan=lifespan)
api.state.limiter = limiter
api.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# When Supabase (or another upstream) is paused, restarting, or times out, the
# client raises a raw error. Turn those into a clean 503 so users see a friendly
# "try again" message instead of a stack trace / Cloudflare HTML page.
_SERVICE_UNAVAILABLE = {"detail": "Our service is temporarily unavailable. Please try again in a moment."}
_UPSTREAM_SIGNS = (
    "json could not be generated", "522", "523", "524", "503", "504",
    "timed out", "timeout", "connection", "gateway", "unavailable",
)


@api.exception_handler(APIError)
async def _handle_postgrest_error(request: Request, exc: APIError):
    blob = f"{getattr(exc, 'message', '')} {getattr(exc, 'code', '')} {getattr(exc, 'details', '')}".lower()
    if any(sign in blob for sign in _UPSTREAM_SIGNS):
        logger.warning("Supabase upstream unavailable (code=%s): %s", getattr(exc, "code", "?"), getattr(exc, "message", ""))
        return JSONResponse(status_code=503, content=_SERVICE_UNAVAILABLE)
    # A genuine query error (constraint, bad request, etc.): log it, don't leak internals.
    logger.exception("Database (PostgREST) error")
    return JSONResponse(status_code=500, content={"detail": "Something went wrong. Please try again."})


@api.exception_handler(httpx.TransportError)
async def _handle_transport_error(request: Request, exc: httpx.TransportError):
    logger.warning("Upstream transport error: %s", exc)
    return JSONResponse(status_code=503, content=_SERVICE_UNAVAILABLE)

api.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    max_age=600,  # cache OPTIONS preflight for 10 minutes
)


@api.get("/health")
def health():
    """Cheap liveness check - always returns 200 if the process is up."""
    return {"status": "ok"}


@api.get("/health/full")
async def health_full():
    """Deep health check. Verifies the DB is reachable. Used by Render / monitors."""
    checks = {"api": True, "db": False, "agent": False}
    try:
        # Sync supabase-py call → push to thread pool.
        await asyncio.to_thread(
            lambda: db.client().table("mentors").select("id").limit(1).execute()
        )
        checks["db"] = True
    except Exception:
        logger.exception("/health/full DB check failed")

    try:
        import ai.graph as _agent_graph
        checks["agent"] = _agent_graph.app is not None
    except Exception:
        pass

    ok = all(checks.values())
    return {"ok": ok, "checks": checks}


api.include_router(admin_router.router)
api.include_router(auth_router.router)
api.include_router(availability_router.router)
api.include_router(booking_router.router)
api.include_router(chat_router.router)
api.include_router(contact_router.router)
api.include_router(mentor_router.router)
api.include_router(mentors_router.router)
api.include_router(payments_router.router)
api.include_router(pricing_router.router)
api.include_router(quote_router.router)
api.include_router(referrals_router.router)
api.include_router(reviews_router.router)
api.include_router(services_router.router)

if config.MOCK_SERVICES:
    api.include_router(dev_router.router)
    logger.warning("MOCK_SERVICES=true - Resend and webhook signatures are mocked. Never deploy with this flag.")


if __name__ == "__main__":
    # uvicorn.run() forces Proactor on Windows; drive Server.serve() ourselves with a Selector loop instead.
    if sys.platform == "win32":
        uvicorn_config = uvicorn.Config(
            api,
            host=config.HOST,
            port=config.PORT,
            loop="asyncio",
            timeout_graceful_shutdown=30,
        )
        server = uvicorn.Server(uvicorn_config)
        policy = asyncio.WindowsSelectorEventLoopPolicy()
        asyncio.run(server.serve(), loop_factory=policy.new_event_loop)
    else:
        uvicorn.run(
            api,
            host=config.HOST,
            port=config.PORT,
            timeout_graceful_shutdown=30,
        )
