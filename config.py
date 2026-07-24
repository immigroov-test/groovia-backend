# Secrets and per-environment values come from .env; everything else is hardcoded here.
import os
import sys
from dotenv import load_dotenv

load_dotenv(override=True)

# Secrets (from .env)
GROQ_API_KEY              = os.getenv("GROQ_API_KEY")
TAVILY_API_KEY            = os.getenv("TAVILY_API_KEY")
SUPABASE_URL              = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # backend writes bypass RLS with this
SUPABASE_JWT_SECRET       = os.getenv("SUPABASE_JWT_SECRET")        # verify user JWTs locally (no network)
SUPABASE_DB_URL           = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")

# Per-environment
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
PORT = int(os.getenv("PORT", 8000))  # Fly.io auto-injects PORT
HOST = "0.0.0.0"
# Where the frontend lives - used to build links inside LLM responses (e.g. "browse all mentors").
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")

# Models
MAIN_MODEL_NAME   = "llama-3.3-70b-versatile"
REVIEW_MODEL_NAME = "llama-3.1-8b-instant"
TEMPERATURE       = 0.0

# Agent tuning
NUM_COUNTRIES        = 3
MAX_REVISION         = 1
MAX_TOOL_ITERATIONS  = 3
MAX_HISTORY          = 5           # message-window per LLM call
AGENT_TIMEOUT_SEC    = 120.0       # per /chat request

# API limits
MAX_FILE_BYTES = 5 * 1024 * 1024   # 5 MB upload cap
RATE_LIMIT     = "20/minute"       # per-IP /chat rate limit

# Search tools
TAVILY_MAX_RESULTS       = 5

# Resend transactional email.
# NOTE: EMAIL_FROM must be a Resend-verified sender - either "onboarding@resend.dev"
# (Resend's shared sandbox, which only delivers to the Resend account owner) or an
# address at a domain you've verified in Resend (e.g. noreply@immigroov.com).
# A personal gmail/outlook address will be REJECTED by Resend (every send fails).
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM     = os.getenv("EMAIL_FROM", "Immigroov <onboarding@resend.dev>")
# Ops inbox copied on every booking / reschedule / cancellation. Empty = no admin copy.
ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "")
# Testing without a verified domain: when set, ALL transactional emails are routed to
# this one inbox (tagged with the intended recipient) instead of the real mentor/
# mentee/admin. Set it to the Resend account owner's email so sandbox delivers. Empty = live.
EMAIL_TEST_REDIRECT = os.getenv("EMAIL_TEST_REDIRECT", "").strip()

# Mentor bank details are encrypted at rest with this key (Fernet). Generate one with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Optional at startup (mentors just can't submit/view bank details until it's set); may hold
# several comma-separated keys for rotation (first encrypts, all decrypt). Never commit it.
BANK_ENC_KEY = os.getenv("BANK_ENC_KEY", "").strip()

# Razorpay (customer payments). Optional at startup - MOCK_SERVICES=true lets booking/
# payment flows work locally without real credentials (mirrors how RESEND_API_KEY is
# optional). RAZORPAY_WEBHOOK_SECRET is a SEPARATE secret from RAZORPAY_KEY_SECRET - it
# signs webhook deliveries, not API requests.
RAZORPAY_KEY_ID         = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET     = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

# Shared secret between the Vercel BFF and this backend for trusting the visitor's
# geolocated country (used for PPP pricing). The BFF reads the country from Vercel's
# edge header (x-vercel-ip-country, which the browser cannot forge) and forwards it
# with this token. When SET, the backend trusts the country ONLY on requests bearing
# this token (blocking a direct call that fakes ?country= to claim a discount) and
# falls back to no-country (no PPP discount) otherwise. When EMPTY, geo enforcement
# is off and the client-supplied country is used as before. Must match the Vercel
# INTERNAL_GEO_TOKEN env var (server-only, never NEXT_PUBLIC).
INTERNAL_GEO_TOKEN = os.getenv("INTERNAL_GEO_TOKEN", "")

# Shared secret protecting the /payments/run-dispatcher trigger. Lets a scheduler
# (Supabase pg_cron via net.http_post, or any external cron) run the money-correctness
# dispatcher (expire holds, verify sweep, FX refresh, refunds) without a paid Render
# Cron Job. Empty = the trigger endpoint is disabled (403).
DISPATCHER_TOKEN = os.getenv("DISPATCHER_TOKEN", "")

# Feature flags, default ON. Keep in sync with groovia-frontend/lib/features.ts.
def _flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")

MOCK_SERVICES = _flag("MOCK_SERVICES", default=False)        # Intercept Resend + webhook sig checks locally

FEATURE_CHAT_HISTORY    = _flag("FEATURE_CHAT_HISTORY")     # Recent-chats sidebar list
FEATURE_GUEST_MODE      = _flag("FEATURE_GUEST_MODE")       # Inert: /chat now requires auth (Q&A + report are login-gated; find-a-mentor is public via /mentors)
FEATURE_MENTORS_PUBLIC  = _flag("FEATURE_MENTORS_PUBLIC")   # Anyone can browse /mentors
FEATURE_WEB_SEARCH_TOOL = _flag("FEATURE_WEB_SEARCH_TOOL")  # Agent can call the Tavily web_search tool
FEATURE_MENTOR_TOOL     = _flag("FEATURE_MENTOR_TOOL")      # Agent can call retrieve_matching_mentors
FEATURE_RESUME_UPLOAD   = _flag("FEATURE_RESUME_UPLOAD")    # Upload control on chat composer
FEATURE_GOOGLE_OAUTH    = _flag("FEATURE_GOOGLE_OAUTH")     # Show "Continue with Google" button

# Dev-only
DRAW_GRAPH = os.getenv("DRAW_GRAPH", "false").lower() == "true"

# Fail fast if any required secret is missing.
_missing = [k for k, v in {
    "GROQ_API_KEY": GROQ_API_KEY,
    "TAVILY_API_KEY": TAVILY_API_KEY,
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_SERVICE_ROLE_KEY": SUPABASE_SERVICE_ROLE_KEY,
    "SUPABASE_JWT_SECRET": SUPABASE_JWT_SECRET,
    "SUPABASE_DB_URL (or DATABASE_URL)": SUPABASE_DB_URL,
}.items() if not v]
if _missing:
    sys.exit(f"[FATAL] Missing required environment variables: {', '.join(_missing)}")

# Resend is optional at startup - booking still creates events without it.
if not RESEND_API_KEY:
    import warnings
    warnings.warn("[WARN] RESEND_API_KEY not set - transactional emails will be skipped", stacklevel=1)

# Razorpay is optional at startup - set MOCK_SERVICES=true to use the mock confirm
# endpoint instead of a real gateway (see routers/payments.py).
if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET):
    import warnings
    warnings.warn(
        "[WARN] RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET/RAZORPAY_WEBHOOK_SECRET not fully set - "
        "real payments will fail; use MOCK_SERVICES=true for local dev",
        stacklevel=1,
    )

# Loud warning: when EMAIL_TEST_REDIRECT is set, EVERY transactional email is routed
# to that one inbox instead of the real mentor/mentee/admin. Easy to forget in prod
# and it looks like "only the admin gets mail" - so make it visible on startup.
if EMAIL_TEST_REDIRECT and not MOCK_SERVICES:
    import warnings
    warnings.warn(
        f"[WARN] EMAIL_TEST_REDIRECT={EMAIL_TEST_REDIRECT!r} - ALL transactional email is "
        "being redirected to this one inbox, not real recipients. Unset it in production.",
        stacklevel=1,
    )
