# Groovia — Backend (`groovia-backend`)

FastAPI + LangGraph backend. Handles the AI agent, mentor lifecycle, auth, direct booking, and all data access.

## How the agent works

Conversations are phase-driven: **no_resume → awaiting_intent → report | mentor | qna**.

Within `report` and `mentor`, a **reflection loop** reviews the LLM draft with a reviewer model (Llama-3.1-8b) and revises up to `MAX_REVISION` times. Tools: `general_search` (Tavily), `precise_search` (Exa), `retrieve_matching_mentors` (Supabase). All mentor links in AI responses use the Groovia platform URL (`/mentors/{slug}`), not Cal.com.

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
│   ├── auth.py           # JWT verification (HS256 + asymmetric JWKS), AuthUser, require_admin
│   ├── permissions.py    # Centralized authz: require_mentor, authorize_booking_party
│   └── rate_limit.py     # slowapi limiter (20/min per IP on /chat)
├── ai/
│   ├── graph.py          # LangGraph StateGraph, all nodes + edges, PostgreSQL checkpointer
│   ├── prompts.py        # System prompts for report, mentor, qna, reviewer, compression
│   └── tools.py          # Tool definitions: Tavily, Exa, mentor DB lookup, PDF/DOCX parsers
├── db/
│   ├── __init__.py       # Re-exports all public db functions
│   ├── mentors.py        # Mentor + profile queries (list, create, update, approve)
│   ├── bookings.py       # Booking queries (upsert, find by cal path, email lookup)
│   ├── chat.py           # Thread + ai_event queries
│   └── direct_booking.py # Direct booking RPCs: slots, book, cancel, reschedule, services, availability
├── services/
│   └── mailer.py         # Resend transactional email; writes to _dev_emails.jsonl when MOCK_SERVICES=true
├── routers/
│   ├── chat.py           # POST /chat, GET /chat/threads, POST /chat/threads/{id}/claim
│   ├── mentor.py         # /mentor/signup, /mentor/me, /mentor/me/availability
│   ├── mentors.py        # GET /mentors, GET /mentors/{slug}
│   ├── booking.py        # /booking/* — direct slot booking, cancel, reschedule negotiation
│   ├── services.py       # /mentor/services/* — session type + intake question management
│   ├── availability.py   # /mentor/availability-v2/* — weekly schedule, overrides, rules
│   ├── admin.py          # /admin/mentors/* (approve, reject, list pending)
│   ├── auth.py           # /auth/* stub (future endpoints)
│   └── dev.py            # Mock endpoints (only mounted when MOCK_SERVICES=true)
├── migrations/           # SQL migrations
├── tests/
│   ├── conftest.py       # Fixtures: mock_llm, mock_db, agent_app (MemorySaver), client
│   ├── integration/      # Full /chat flow tests (LLMs mocked, state real)
│   └── unit/             # Gate logic, extractors, sanitizer tests
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

Set `MOCK_SERVICES=true` in `.env` to intercept Resend calls and skip webhook signature checks. Captured emails are viewable at `GET /dev/emails`.

### Running tests

```bash
pip install -r requirements-dev.txt
pytest                      # runs all unit + integration tests (no Postgres needed)
```

## Key env vars

| Variable | Required | Notes |
|---|---|---|
| `SUPABASE_URL` | Yes | Project URL (`https://xxxx.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Bypasses RLS — backend only, never expose to client |
| `SUPABASE_JWT_SECRET` | Yes | HS256 secret for verifying user JWTs locally |
| `SUPABASE_DB_URL` | Yes | Direct Postgres URL for LangGraph checkpointer (`postgresql://...`) |
| `GROQ_API_KEY` | Yes | LLM inference (Llama-3.3-70b + Llama-3.1-8b) |
| `TAVILY_API_KEY` | Yes | `general_search` tool |
| `EXA_API_KEY` | Yes | `precise_search` tool |
| `FRONTEND_URL` | Yes | Used in CORS and LLM-generated mentor links |
| `CORS_ORIGINS` | No | Comma-separated; defaults to `http://localhost:3000` |
| `RESEND_API_KEY` | No | Transactional email; booking still works without it |
| `EMAIL_FROM` | No | Sender address; defaults to `Immigroov <noreply@immigroov.com>` |
| `MOCK_SERVICES` | No | `true` intercepts Resend + skips webhook sig checks — **never deploy with this** |
| `PORT` | No | Fly.io injects automatically; defaults to 8000 |

## Feature flags

All flags default to `true` unless set to `false`/`0`. Keep in sync with `groovia-frontend/lib/features.ts`.

| Flag | Controls |
|---|---|
| `FEATURE_CHAT_HISTORY` | Recent-chats sidebar |
| `FEATURE_GUEST_MODE` | Chat without sign-in until resume upload |
| `FEATURE_MENTORS_PUBLIC` | Public `/mentors` browse without auth |
| `FEATURE_WEB_SEARCH_TOOL` | Agent may call Tavily + Exa |
| `FEATURE_MENTOR_TOOL` | Agent may call `retrieve_matching_mentors` |
| `FEATURE_RESUME_UPLOAD` | File attachment button visible |
| `FEATURE_GOOGLE_OAUTH` | "Continue with Google" button in auth modal |

## Direct booking system

Mentors set a weekly schedule (`weekly_availability`) and session types (`services`). Candidates browse available slots via the `get_available_slots` PostgreSQL RPC and book with `book_session`. All booking RPCs are called server-side by FastAPI using the service role key — `auth.uid()` is always NULL in this context, so all ownership/auth checks are enforced at the FastAPI layer.

Key security notes:
- Cancel, reschedule propose/accept/confirm, and attendance confirmation all verify the caller is the booking's mentor or candidate before calling the underlying RPC.
- Service and question CRUD verifies the caller owns the parent service before any write.

## PostgreSQL / LangGraph checkpointer

LangGraph uses `AsyncPostgresSaver` backed by Supabase Postgres (`SUPABASE_DB_URL`).

- **Linux (production)**: `AsyncConnectionPool` (min=2, max=10)
- **Windows (local dev)**: single `AsyncConnection` with `ensure_pg_connected()` ping before each call

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

The old per-step migrations (`001`–`014`) were consolidated. The folder now holds
self-contained setup scripts plus the most recent incremental deltas.

**Fresh project (production):** Run `migrations/production_db_setup.sql` — the complete
schema (no seed mentors) in one file, including the v2 booking lifecycle.

**Fresh project (local testing):** Run `migrations/testing_db_setup.sql` — same schema
plus 14 seed mentors, each with a bookable service + weekly availability.

| File | Purpose |
|---|---|
| `production_db_setup.sql` | Full production schema, one file (fold-in of all migrations through 018) |
| `testing_db_setup.sql` | Full schema + seed mentors/services/availability for local testing |
| `testing_db_reset.sql` | Clears test data between runs (keeps seed mentors) |
| `production_clear_users.sql` | Deletes mentee accounts only — preserves mentors + availability |
| `015_direct_booking_system.sql` | Incremental delta: direct booking (services, availability, reschedule, RPCs) |
| `016_ppp_and_validation.sql` | Incremental delta: PPP pricing, IANA timezone validation, reschedule slot guard |
| `017_booking_lifecycle_v2.sql` | Incremental delta: deadline states, booking_requests, cancel/reschedule/no-show, cron |
| `018_booking_reads.sql` | Incremental delta: `my_bookings` + v2 `mentor_sessions` read RPCs |

For an **existing** DB already on 015/016, apply `017` then `018` to catch up.
`testing_db_reset.sql` resets test data; `production_clear_users.sql` removes mentees in prod.

> **pg_cron:** `017` schedules `resolve-requests` (10 min) and `auto-complete` (15 min)
> when the `pg_cron` extension is enabled; it skips them gracefully otherwise.
