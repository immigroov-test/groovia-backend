# Groovia — Backend (`groovia-backend`)

FastAPI + LangGraph backend. Handles the AI agent, mentor lifecycle, auth, and all data access.

## How the agent works

Conversations are phase-driven: **no_resume → awaiting_intent → report | mentor | qna**.

Within `report` and `mentor`, a **reflection loop** reviews the LLM draft with a reviewer model (Llama-3.1-8b) and revises up to `MAX_REVISION` times. Tools available: `general_search` (Tavily), `precise_search` (Exa), `retrieve_matching_mentors` (Supabase).

Short-circuit gates fire *before* any LLM call for: missing resume, bare acks, ambiguous intent, missing country/track. These produce canned responses with zero API cost.

## Structure

```
groovia-backend/
├── main.py               # FastAPI app, lifespan, CORS, router registration
├── config.py             # All env vars, tunable parameters, feature flags
├── content.py            # UI strings, agent intent phrases, canned messages
├── schema.py             # Pydantic response models
├── backend.py            # Compatibility shim: maps `backend` → `ai.graph` for tests
├── utils.py              # Compatibility shim: maps `utils` → `ai.tools` for tests
├── core/
│   ├── auth.py           # JWT verification, AuthUser dependency, role checks
│   └── rate_limit.py     # slowapi limiter (20/min per IP on /chat)
├── ai/
│   ├── graph.py          # LangGraph StateGraph, all nodes + edges, PostgreSQL checkpointer
│   ├── prompts.py        # System prompts for report, mentor, qna, reviewer, compression
│   └── tools.py          # Tool definitions: Tavily, Exa, mentor DB lookup, PDF/DOCX parsers
├── db/
│   ├── __init__.py       # Re-exports all public db functions
│   ├── mentors.py        # Mentor + profile queries (list, create, update, approve)
│   ├── bookings.py       # Booking queries (upsert, find by cal path, email lookup)
│   └── chat.py           # Thread + ai_event queries (upsert_chat_thread, upsert_ai_event)
├── services/
│   └── mailer.py         # Resend transactional email (approval, booking confirmation)
├── routers/
│   ├── chat.py           # POST /chat, GET /chat/threads, POST /chat/threads/{id}/claim
│   ├── mentor.py         # /mentor/signup, /mentor/me, /mentor/me/availability
│   ├── mentors.py        # GET /mentors, GET /mentors/{slug}, POST /mentors/{slug}/book
│   ├── admin.py          # /admin/mentors/* (approve, reject, list pending)
│   ├── auth.py           # /auth/* stub (future endpoints)
│   ├── webhooks.py       # POST /webhooks/cal (Cal.com HMAC-verified)
│   └── dev.py            # Mock endpoints (only mounted when MOCK_SERVICES=true)
├── migrations/           # Sequential SQL migrations (001 → 014); run in Supabase SQL editor
├── tests/
│   ├── conftest.py       # Fixtures: mock_llm, mock_db, agent_app (MemorySaver), client
│   ├── integration/      # Full /chat flow tests (LLMs mocked, state real)
│   └── unit/             # Gate logic, extractors, sanitizer, webhook sig tests
├── Dockerfile            # python:3.13-slim, copies only source (no venv/Lib)
├── fly.toml              # Fly.io: region=ams, 512 MB shared VM, min 1 machine
└── render.yaml           # Render.com alternative deployment config
```

## Running locally

```bash
cp .env.example .env        # fill in secrets
pip install -r requirements.txt
python main.py              # or: uvicorn main:api --reload --port 8000
```

### Running tests

```bash
pip install -r requirements-dev.txt
pytest                      # runs all unit + integration tests (no Postgres needed)
```

## Key env vars

| Variable | Required | Notes |
|---|---|---|
| `SUPABASE_URL` | Yes | Project URL (e.g. `https://xxxx.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Bypasses RLS — backend only, never expose to client |
| `SUPABASE_JWT_SECRET` | Yes | HS256 secret for verifying user JWTs locally |
| `SUPABASE_DB_URL` | Yes | Direct Postgres URL for LangGraph checkpointer (`postgresql://...`) |
| `GROQ_API_KEY` | Yes | LLM inference (Llama-3.3-70b + Llama-3.1-8b) |
| `TAVILY_API_KEY` | Yes | `general_search` tool |
| `EXA_API_KEY` | Yes | `precise_search` tool |
| `FRONTEND_URL` | Yes | Used in CORS and LLM-generated mentor directory links |
| `CORS_ORIGINS` | No | Comma-separated list; defaults to `http://localhost:3000` |
| `RESEND_API_KEY` | No | Transactional email; booking still works without it |
| `CAL_WEBHOOK_SECRET` | No | HMAC secret for Cal.com webhooks; `/webhooks/cal` returns 503 without it |
| `MOCK_SERVICES` | No | `true` skips Resend and webhook sig checks — **never deploy with this** |
| `PORT` | No | Fly.io injects automatically; defaults to 8000 |

## Feature flags

All flags default to `true` unless explicitly set to `false`/`0`. Keep in sync with `groovia-frontend/lib/features.ts`.

| Flag | Controls |
|---|---|
| `FEATURE_CHAT_HISTORY` | Recent-chats sidebar |
| `FEATURE_GUEST_MODE` | Chat without sign-in until resume upload |
| `FEATURE_MENTORS_PUBLIC` | Public `/mentors` browse without auth |
| `FEATURE_WEB_SEARCH_TOOL` | Agent may call Tavily + Exa |
| `FEATURE_MENTOR_TOOL` | Agent may call `retrieve_matching_mentors` |
| `FEATURE_RESUME_UPLOAD` | File attachment button visible |
| `FEATURE_GOOGLE_OAUTH` | "Continue with Google" button in auth modal |

## PostgreSQL / LangGraph checkpointer

LangGraph uses `AsyncPostgresSaver` backed by Supabase Postgres (`SUPABASE_DB_URL`).

- **Linux (production)**: `AsyncConnectionPool` (min=2, max=10) — handles reconnection automatically.
- **Windows (local dev)**: single `AsyncConnection`. The backend calls `ensure_pg_connected()` before every `ainvoke` call, which pings `SELECT 1` and reconnects if the idle connection was dropped by Supabase.

Use the **direct connection URL** (port 5432), not the transaction-mode pooler (port 6543), because LangGraph uses prepared statements.

## Deployment (Fly.io)

```bash
fly auth login
fly launch --no-deploy          # first time only
fly secrets set GROQ_API_KEY=... SUPABASE_URL=... SUPABASE_DB_URL=...
fly deploy
```

The `fly.toml` targets region `ams` (Amsterdam) with a 512 MB shared VM and `min_machines_running = 1`.

## Database migrations

Run each file sequentially in the Supabase SQL editor:

| File | Content |
|---|---|
| `001_init_slice0.sql` | profiles, mentors, mentor_availability, bookings, webhook_events tables |
| `003_fix_user_trigger.sql` | profiles auto-create on auth.users insert |
| `005_bookings.sql` | bookings table extended fields |
| `006_profile_summary.sql` | profiles.profile_summary column |
| `007_nylas.sql` | mentors Nylas OAuth fields |
| `008_mentor_timezone.sql` | mentors.timezone column |
| `009_mentor_signup_role.sql` | profiles.role metadata |
| `010_mentor_profile_edit.sql` | mentor self-edit fields |
| `011_ai_events.sql` | ai_events observability table |
| `012_photo_url.sql` | mentors.photo_url column |
| `013_schema_audit.sql` | bookings uniqueness, is_active trigger, RLS cleanup |
| `014_mentor_extended_fields.sql` | additional mentor profile fields |
