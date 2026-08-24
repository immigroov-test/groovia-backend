# Groovia - Backend (`groovia-backend`)

Backend and "brain" for **Immigroov** - a two-sided immigration mentorship marketplace
that connects people moving between countries with mentors who have lived the journey.
At its centre is **Groovia**, an AI agent that profiles each candidate, recommends
countries, and matches them to mentors they can book a paid session with.

This repo is **FastAPI + LangGraph + Supabase**. It owns the AI agent, authentication,
the mentor lifecycle (signup → admin approval), the in-house booking system, and all
data access. The user-facing UI lives in the **separate** [`groovia-frontend`](https://github.com/immigroov-test/groovia-frontend)
repo (Next.js on Vercel); this service is its API.

> Two repos, one product: **`groovia-backend`** (this) = API/agent on Render +
> Supabase. **`groovia-frontend`** = Next.js UI on Vercel that proxies to this API.

---

## Current status (what works today)

| Area | Status |
|---|---|
| Groovia AI agent (country discovery, Q&A, mentor matching) | ✅ Working |
| Auth - Supabase email/password + Google OAuth, JWT verification | ✅ Working |
| Mentor lifecycle - signup → onboarding → **admin approval** | ✅ Working |
| In-house booking + lifecycle v2 (cancel / reschedule / no-show) | ✅ Working |
| Transactional email (Resend) | ✅ Working (needs verified domain for real sends) |
| Deployment - Render (API) + Supabase (DB/Auth) + Vercel (UI) | ✅ Live on `staging` |

## Future developments (planned per PRD v2.1 - not yet built)

Payments (Stripe + Razorpay) & credits · commission/attribution engine · reviews &
ratings · mentor earnings & payouts · candidate dashboard & roadmap · CV optimizer ·
RAG knowledge base (pgvector) · Sponsor Radar · group sessions / webinars · auto
Google Meet links · analytics (GA4 / PostHog / GTM) · cookie consent & GDPR export/delete · MFA.

---

## How the agent works

Conversations are phase-driven: **no_resume → awaiting_intent → report | mentor | qna**.

Within `report` and `mentor`, a **reflection loop** reviews the LLM draft with a reviewer
model (Llama-3.1-8b) and revises up to `MAX_REVISION` times. Tools: `web_search`
(Tavily, advanced depth), `retrieve_matching_mentors` (Supabase). All mentor
links in AI responses use the Groovia platform URL (`/mentors/{slug}`).

Short-circuit gates fire *before* any LLM call for: missing resume, bare acks, ambiguous
intent, missing country/track - canned responses with zero API cost.

## Structure - what each file holds

```
groovia-backend/            # ← this folder is the repo root
├── main.py                 # FastAPI app, lifespan, CORS, router registration
├── config.py               # All env vars, tunable parameters, feature flags
├── content.py              # UI strings, agent intent phrases, canned messages
├── schema.py               # Pydantic response models
├── core/
│   ├── auth.py             # JWT verification (HS256 + asymmetric JWKS), AuthUser, require_admin
│   ├── permissions.py      # Centralized authz: require_mentor, authorize_booking_party
│   └── rate_limit.py       # slowapi limiter (20/min per IP on /chat)
├── ai/
│   ├── graph.py            # LangGraph StateGraph: nodes, edges, PostgreSQL checkpointer
│   ├── prompts.py          # System prompts (report, mentor, qna, reviewer, compression)
│   └── tools.py            # Tools: Tavily web search, mentor DB lookup, PDF/DOCX parsers
├── db/
│   ├── mentors.py          # Mentor + profile queries (list, create, update, approve)
│   ├── bookings.py         # Booking queries
│   ├── chat.py             # Thread + ai_event queries
│   └── direct_booking.py   # Booking RPCs: slots, book, cancel, reschedule, services, availability
├── services/
│   └── mailer.py           # Resend transactional email (mocked when MOCK_SERVICES=true)
├── routers/
│   ├── chat.py             # POST /chat, GET /chat/threads, thread claim
│   ├── mentor.py           # /mentor/signup, /mentor/me, profile
│   ├── mentors.py          # GET /mentors, GET /mentors/{slug}
│   ├── booking.py          # /booking/* - slot booking, cancel, reschedule, no-show
│   ├── services.py         # /mentor/services/* - session types + intake questions
│   ├── availability.py     # /mentor/availability-v2/* - weekly schedule, overrides, rules
│   ├── admin.py            # /admin/mentors/* (approve, reject, list pending)
│   └── auth.py             # /auth/* (recaptcha-adjacent stubs)
├── migrations/             # SQL setup + reset scripts (see below)
├── tests/                  # unit + integration (LLMs mocked, no Postgres needed)
├── Dockerfile              # python:3.13-slim - what Render builds
├── render.yaml             # Render blueprint (prod + staging web services)
└── requirements.txt
```

(`backend.py` / `utils.py` are thin compatibility shims mapping to `ai.graph` / `ai.tools` for tests.)

## Running locally

```bash
cp .env.example .env        # fill in secrets
pip install -r requirements.txt
python main.py              # or: uvicorn main:api --reload --port 8000
```

Set `MOCK_SERVICES=true` to intercept Resend and skip webhook signature checks.
Run tests with `pip install -r requirements-dev.txt && pytest` (no Postgres needed).

## Key env vars

| Variable | Required | Notes |
|---|---|---|
| `SUPABASE_URL` | Yes | Project URL (`https://xxxx.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Bypasses RLS - backend only, never expose to client |
| `SUPABASE_JWT_SECRET` | Yes | Verifies user JWTs locally |
| `DATABASE_URL` | Yes | Postgres URL for LangGraph - **Session pooler, port 5432** (not 6543) |
| `GROQ_API_KEY` / `TAVILY_API_KEY` | Yes | LLM + web search |
| `FRONTEND_URL` | Yes | CORS + LLM-generated mentor links |
| `CORS_ORIGINS` | Yes (deploy) | Comma-separated allowed origins, no trailing slash |
| `RESEND_API_KEY` / `EMAIL_FROM` | No | Transactional email; booking works without it |
| `MOCK_SERVICES` | No | `true` mocks email/webhooks - **never deploy `true`** |

Feature flags (`FEATURE_*`, default ON) are listed in `config.py` and mirrored in
`groovia-frontend/lib/features.ts`.

## Booking system

Mentors set a weekly schedule (`weekly_availability`) + session types (`services`).
Candidates browse slots and book via PostgreSQL RPCs. **All RPCs run server-side with
the service-role key, so `auth.uid()` is NULL - every ownership/authz check is enforced
at the FastAPI layer** (`core/permissions.py`). Lifecycle v2 adds deadline-aware cancel,
mentor↔candidate reschedule negotiation, and no-show handling.

LangGraph uses `AsyncPostgresSaver` on Supabase Postgres via the **Session pooler (5432)**
- not the transaction pooler (6543), because it relies on prepared statements.

## Database migrations - 2 files per environment

| File | Purpose |
|---|---|
| `production_db_setup.sql` | Full production schema (no seed mentors). Run once on a fresh project. |
| `production_clear_users.sql` | Promotes the admin, then deletes mentee accounts only (keeps mentors). |
| `testing_db_setup.sql` | Full schema + 14 seed mentors with services & availability. Run once. |
| `testing_db_reset.sql` | Promotes the admin, clears test data, keeps seed mentors. Re-run between test runs. |
| `legal_documents_setup.sql` | Legal Documents CMS. Already folded into both `*_db_setup.sql`; run this one **on an existing database** to add the feature without re-running the full schema. |

**First-time setup:** run the `*_db_setup.sql` → sign up once as the admin email →
run the matching clear/reset file (it promotes that account to `admin`). Setup files are
re-runnable (functions whose return type changed are dropped first).

### Legal Documents CMS

Schema only creates the 14 catalogue rows; the document text lives in `content/legal/*.md`
and is loaded separately, so a 6,000-word contract never sits inside a schema file:

```bash
python -m scripts.seed_legal_documents --dry-run   # report what would happen
python -m scripts.seed_legal_documents             # publish v1.0 where nothing is published
```

Re-running is safe: a document that already has a published version is skipped, because
once v1.0 exists the CMS owns the text and the file on disk is only the initial import.
Use `--as-draft` to load a revised file into the draft slot instead, leaving the decision
to publish with an admin.

## Deployment (Render)

`render.yaml` defines two Docker web services: `groovia-api` (branch `main`) and
`groovia-api-staging` (branch `staging`). **Root Directory = repo root** (the Dockerfile
is at the root). Set the `sync: false` secrets in the Render dashboard. A legacy
`fly.toml` is included but Render is the active target.
