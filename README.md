# Groovia by Immigroov

AI-powered career and immigration consultant for internationally mobile professionals. Users upload a resume and get a personalised country comparison report, real mentor connections, and follow-up Q&A — all in one chat interface.

## Monorepo structure

```
groovia/
├── groovia-backend/    # FastAPI + LangGraph AI backend
└── groovia-frontend/   # Next.js 16 frontend
```

Both services deploy independently and communicate over HTTPS.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (App Router), React 19, Tailwind v4, Supabase SSR |
| Backend | FastAPI, LangGraph, Groq (Llama-4), Supabase |
| AI | Groq Llama-3.3-70b (agent) + Llama-3.1-8b (reviewer) |
| Search tools | Tavily (broad web), Exa (neural / precise) |
| Database | Supabase Postgres + Auth + Storage |
| Deployment | Fly.io (backend), Vercel (frontend) |

## Quick start

**Backend**

```bash
cd groovia-backend
cp .env.example .env          # fill in API keys
pip install -r requirements.txt
python main.py                # or: uvicorn main:api --reload --port 8000
```

**Frontend**

```bash
cd groovia-frontend
cp .env.local.example .env.local   # fill in Supabase + backend URL
npm install
npm run dev
```

## Architecture

The backend runs a phase-driven LangGraph agent:

```
no_resume → awaiting_intent → report | mentor | qna
```

Each real LLM response goes through a reflection loop (reviewer LLM audits, revises up to N times). Short-circuit gates handle trivial turns with zero LLM cost.

Run `python groovia-backend/generate_architecture.py` to regenerate `system_architecture.png` (requires `pip install diagrams` and Graphviz on PATH).

## Database migrations

Run SQL files from `groovia-backend/migrations/` sequentially in the Supabase SQL editor (001 → 014).

## Roadmap

- **Payments** — Stripe (global) + Razorpay (India) for paid mentor sessions
- **Candidate dashboard** — session history, AI career roadmap, saved mentors
- **Mentor earnings** — earnings summary, upcoming sessions
- **Commission engine** — 2-axis model: source attribution × session volume tiers
- **Reviews & ratings** — verified post-session reviews, mentor responses
- **RAG / pgvector** — context-aware answers from curated visa and country guides
- **CV optimizer** — AI-tailored resume for target country + role
- **Sponsor radar** — searchable IND-registered Netherlands employer database
- **Group sessions / webinars** — multi-attendee mentor-led sessions (V3)
- **GDPR tools** — data export and right-to-deletion flows
