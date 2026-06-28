# Secrets and per-environment values come from .env; everything else is hardcoded here.
import os
import sys
from dotenv import load_dotenv

load_dotenv(override=True)

# Secrets (from .env)
GROQ_API_KEY              = os.getenv("GROQ_API_KEY")
TAVILY_API_KEY            = os.getenv("TAVILY_API_KEY")
EXA_API_KEY               = os.getenv("EXA_API_KEY")
SUPABASE_URL              = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # backend writes bypass RLS with this
SUPABASE_JWT_SECRET       = os.getenv("SUPABASE_JWT_SECRET")        # verify user JWTs locally (no network)
SUPABASE_DB_URL           = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")

# Per-environment
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
PORT = int(os.getenv("PORT", 8000))  # Fly.io auto-injects PORT
HOST = "0.0.0.0"
# Where the frontend lives — used to build links inside LLM responses (e.g. "browse all mentors").
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
EXA_NUM_RESULTS          = 3
EXA_HIGHLIGHT_MAX_CHARS  = 1000

# Resend transactional email
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM     = os.getenv("EMAIL_FROM", "Immigroov <noreply@immigroov.com>")

# Feature flags, default ON. Keep in sync with groovia-frontend/lib/features.ts.
def _flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")

MOCK_SERVICES = _flag("MOCK_SERVICES", default=False)        # Intercept Resend + webhook sig checks locally

FEATURE_CHAT_HISTORY    = _flag("FEATURE_CHAT_HISTORY")     # Recent-chats sidebar list
FEATURE_GUEST_MODE      = _flag("FEATURE_GUEST_MODE")       # Chat works without sign-in until resume upload
FEATURE_MENTORS_PUBLIC  = _flag("FEATURE_MENTORS_PUBLIC")   # Anyone can browse /mentors
FEATURE_WEB_SEARCH_TOOL = _flag("FEATURE_WEB_SEARCH_TOOL")  # Agent can call Tavily + Exa
FEATURE_MENTOR_TOOL     = _flag("FEATURE_MENTOR_TOOL")      # Agent can call retrieve_matching_mentors
FEATURE_RESUME_UPLOAD   = _flag("FEATURE_RESUME_UPLOAD")    # Upload control on chat composer
FEATURE_GOOGLE_OAUTH    = _flag("FEATURE_GOOGLE_OAUTH")     # Show "Continue with Google" button

# Dev-only
DRAW_GRAPH = os.getenv("DRAW_GRAPH", "false").lower() == "true"

# Fail fast if any required secret is missing.
_missing = [k for k, v in {
    "GROQ_API_KEY": GROQ_API_KEY,
    "TAVILY_API_KEY": TAVILY_API_KEY,
    "EXA_API_KEY": EXA_API_KEY,
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_SERVICE_ROLE_KEY": SUPABASE_SERVICE_ROLE_KEY,
    "SUPABASE_JWT_SECRET": SUPABASE_JWT_SECRET,
    "SUPABASE_DB_URL (or DATABASE_URL)": SUPABASE_DB_URL,
}.items() if not v]
if _missing:
    sys.exit(f"[FATAL] Missing required environment variables: {', '.join(_missing)}")

# Resend is optional at startup — booking still creates events without it.
if not RESEND_API_KEY:
    import warnings
    warnings.warn("[WARN] RESEND_API_KEY not set — transactional emails will be skipped", stacklevel=1)
