# Groovia — Frontend (`groovia-frontend`)

Next.js 16 frontend for Groovia, an AI-powered career and immigration assistant. All backend calls are proxied server-side through Next.js API routes — the backend URL never reaches the browser.

## Tech stack

| Layer | Technology |
|---|---|
| Framework | Next.js 16 (App Router, Turbopack) |
| Styling | Tailwind CSS v4 |
| Language | TypeScript |
| Auth | Supabase SSR (`@supabase/ssr`) |
| Markdown | react-markdown + remark-gfm |
| Icons | lucide-react |

## Local development

```bash
npm install
cp .env.local.example .env.local   # fill in values (see below)
npm run dev
```

Open http://localhost:3000.

## Key env vars

| Variable | Required | Notes |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Yes | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Yes | Supabase anon key (public) |
| `BACKEND_URL` | Yes | FastAPI backend base URL — **server-side only, no `NEXT_PUBLIC_` prefix** |
| `NEXT_PUBLIC_FEATURE_*` | No | Feature flags (see `lib/features.ts`) |

## Architecture

### BFF proxy pattern
All requests to the FastAPI backend go through `app/api/` routes. The client never calls the backend directly. This keeps `BACKEND_URL` and any tokens server-side.

### Pages

| Route | Description |
|---|---|
| `/` | Chat interface (AI career agent) |
| `/mentors` | Public mentor browse |
| `/mentors/[slug]` | Mentor profile + booking widget |
| `/mentor` | Mentor hub (schedule, services, sessions, profile) |
| `/mentor/onboarding` | Mentor signup flow |
| `/admin` | Admin dashboard (approve / reject mentors) |
| `/account` | Candidate account + profile edit |
| `/auth/*` | Login / sign up pages |

### Key components

| Component | Description |
|---|---|
| `ChatInterface.tsx` | Stateful chat (file upload, intent selection, markdown rendering). Internal links open in the same tab; external links open in a new tab. |
| `DirectBookingWidget.tsx` | 3-step in-app booking: service select → calendar + time slot → confirm form. Supports guest (unauthenticated) booking. |
| `ServicesManager.tsx` | Mentor hub panel for creating / managing session types and intake questions. |
| `AvailabilityManagerV2.tsx` | Mentor hub panel for weekly schedule, date overrides, and booking rules. |
| `BookingManager.tsx` | Mentee + mentor booking management: cancel, reschedule, respond to requests, report/resolve no-shows. |

### Mentor profile page (`/mentors/[slug]`)

The page uses a priority waterfall:
1. If the mentor has active services → show `DirectBookingWidget` (in-app direct booking)
2. Fallback → plain profile with "coming soon"

## Deployment (Vercel)

1. Import the repo in the Vercel dashboard.
2. Set environment variables: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `BACKEND_URL` (production backend URL).
3. Deploy. The `app/api/` proxy routes run as Vercel serverless functions.
