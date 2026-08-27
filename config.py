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
EMAIL_FROM     = os.getenv("EMAIL_FROM", "Immigroov <support@immigroov.com>")
# FEAT-038: separate sending identities per purpose, so one stream's reputation cannot
# sink another's. Every stream falls back to EMAIL_FROM, so leaving these unset keeps the
# current single-sender behaviour and nothing breaks before the DNS records exist.
#
# The point of the split is isolation. A review request is the one email here a recipient
# might mark as spam; a password reset and a booking confirmation are the two that must
# never be filtered. Sending them all from one domain means the first can damage delivery
# of the other two. Mailbox providers score reputation per sending domain, so putting each
# purpose on its own subdomain contains the damage to that purpose.
#
# Each subdomain has to be added and verified in Resend (its own SPF/DKIM/DMARC records),
# and each should be warmed separately. Suggested layout, all under immigroov.com:
#   auth.immigroov.com     security  - sign-in, signup confirmation, password recovery
#   send.immigroov.com     bookings  - confirmations, reminders, reschedules, money
#   hello.immigroov.com    account   - welcome, mentor application outcomes, legal updates
#   updates.immigroov.com  updates   - review requests (the complaint-prone stream)
#   alerts.immigroov.com   alerts    - internal only: ops alerts, admin copies, contact form
EMAIL_FROM_AUTH     = os.getenv("EMAIL_FROM_AUTH", "") or EMAIL_FROM
EMAIL_FROM_BOOKINGS = os.getenv("EMAIL_FROM_BOOKINGS", "") or EMAIL_FROM
EMAIL_FROM_ACCOUNT  = os.getenv("EMAIL_FROM_ACCOUNT", "") or EMAIL_FROM
EMAIL_FROM_UPDATES  = os.getenv("EMAIL_FROM_UPDATES", "") or EMAIL_FROM
EMAIL_FROM_ALERTS   = os.getenv("EMAIL_FROM_ALERTS", "") or EMAIL_FROM
# Where replies should land. A no-reply From with no Reply-To is a dead end for someone
# answering a booking email; point it at the inbox a human actually reads.
EMAIL_REPLY_TO = os.getenv("EMAIL_REPLY_TO", "").strip()

# Ops inbox copied on every booking / reschedule / cancellation. Empty = no admin copy.
ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "")
# Testing without a verified domain: when set, ALL transactional emails are routed to
# this one inbox (tagged with the intended recipient) instead of the real mentor/
# mentee/admin. Set it to the Resend account owner's email so sandbox delivers. Empty = live.
EMAIL_TEST_REDIRECT = os.getenv("EMAIL_TEST_REDIRECT", "").strip()

# BUG-026: Supabase Auth "Send Email" hook signing secret (Authentication -> Hooks -> Send Email
# in the Supabase dashboard; the secret is shown once when the hook is enabled, format
# "v1,whsec_...", but only the "whsec_..." part is needed here). Wiring the hook up is what makes
# sign-in/signup/recovery emails go out from EMAIL_FROM via Resend instead of Supabase's own
# sender - until it's set, routers/auth.py's hook endpoint rejects every request (fails closed:
# an unverifiable hook must never be allowed to silently skip the signature check).
SUPABASE_AUTH_HOOK_SECRET = os.getenv("SUPABASE_AUTH_HOOK_SECRET", "").strip()

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

# BUG-162: the Immigroov bug board lives in its OWN Supabase project, so the admin dashboard reads
# it through a second client rather than the main one. Both unset = the feature is simply off and
# the endpoint says so, rather than erroring - staging and local dev should not need these to boot.
# The ANON key is deliberate, not a shortcut: the board's own RLS (sql/004_rls_policies.sql in
# immigroov-bug-board) grants the anon role full read/write on `bugs`, so anon is all this needs.
# A service-role key for a second project would sit in this backend's environment with far more
# reach than reading a bug list justifies.
BUG_BOARD_SUPABASE_URL = os.getenv("BUG_BOARD_SUPABASE_URL", "").strip()
BUG_BOARD_SUPABASE_ANON_KEY = os.getenv("BUG_BOARD_SUPABASE_ANON_KEY", "").strip()
BUG_BOARD_ENABLED = bool(BUG_BOARD_SUPABASE_URL and BUG_BOARD_SUPABASE_ANON_KEY)

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
if not SUPABASE_AUTH_HOOK_SECRET:
    import warnings
    warnings.warn(
        "[WARN] SUPABASE_AUTH_HOOK_SECRET not set - the Send Email auth hook is disabled, so "
        "sign-in/signup/recovery emails still come from Supabase's own sender, not Immigroov's",
        stacklevel=1,
    )

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

# ── Video calls (Jitsi) ────────────────────────────────────────────────────────
# BUG-120: the free meet.jit.si REFUSES to host an embedded call for more than 5 minutes ("Embedding
# meet.jit.si is only meant for demo purposes"), which kills every 30-minute session mid-call. That is
# their policy, not something we can code around: a production embed needs Jitsi as a Service (8x8) or
# a self-hosted server. Both are a domain + credentials, so they are env-driven here and the code path
# is already in place - set these and the limit is gone with no deploy of new logic.
#   JITSI_DOMAIN       8x8.vc for JaaS, or your own server. Defaults to the demo server.
#   JITSI_APP_ID       JaaS AppID (vpaas-magic-cookie-...). Enables JWT auth when set.
#   JITSI_PRIVATE_KEY  JaaS RSA private key (PEM) used to sign the room token.
#   JITSI_KID          JaaS key id that pairs with the private key.
JITSI_DOMAIN      = os.getenv("JITSI_DOMAIN", "meet.jit.si").strip()
JITSI_APP_ID      = os.getenv("JITSI_APP_ID", "").strip()
JITSI_PRIVATE_KEY = os.getenv("JITSI_PRIVATE_KEY", "").strip().replace("\\n", "\n")
JITSI_KID         = os.getenv("JITSI_KID", "").strip()
JITSI_JAAS_READY  = bool(JITSI_APP_ID and JITSI_PRIVATE_KEY and JITSI_KID)

# FEAT-033: how long chat history is kept. Guest threads expire sooner because they have no owner: if
# that person later asks us to delete their data we cannot find it, so a shorter window is the only
# control we have. An owned thread can be found on request, so it can be kept longer.
CHAT_RETENTION_GUEST_DAYS = int(os.getenv("CHAT_RETENTION_GUEST_DAYS", "90"))
CHAT_RETENTION_USER_DAYS  = int(os.getenv("CHAT_RETENTION_USER_DAYS", "365"))
