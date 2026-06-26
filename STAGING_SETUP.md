# Staging Environment Setup

Test on a deployed `staging` branch instead of fighting local setup. Everything is
isolated from production — its own branch, its own Render service, its own Vercel
preview, and its **own separate Supabase project** (so booking/cancel tests never
touch prod data).

```
main     →  Vercel (prod)             +  Render: groovia-api          +  Supabase (prod project)
staging  →  Vercel (preview, auto)    +  Render: groovia-api-staging  +  Supabase (STAGING project)
```

Workflow: develop on `staging` → it auto-deploys → test on the staging URLs → when
green, open a PR `staging → main` and merge → production auto-deploys.

---

## Every tool we use, and what changes for staging

| Tool | Used for | Change for staging |
|---|---|---|
| **GitHub** (monorepo) | source for both apps | create the `staging` branch |
| **Supabase** | DB + auth | reuse the testing project, rename → `groovia-staging`; set Site URL + Redirect URLs to the staging Vercel URL; ensure Google provider is enabled |
| **Render** | backend host | new service `groovia-api-staging` (branch `staging`) — already in `render.yaml`; fill its env vars |
| **Vercel** | frontend host | auto preview for `staging`; set **Preview**-scoped env vars; disable Deployment Protection |
| **Google Cloud Console** | Google OAuth | add the staging Supabase callback (`https://<staging-ref>.supabase.co/auth/v1/callback`) to the OAuth client's Authorized redirect URIs |
| **Groq / Tavily / Exa** | LLM + search | none — reuse the same keys |
| **Resend** | transactional email (in-house mailer) | reuse the key, or set `MOCK_SERVICES=true` on staging to skip real mail |
| **reCAPTCHA** | signup bot check | register the staging domain in the reCAPTCHA admin, **or** leave `NEXT_PUBLIC_RECAPTCHA_SITE_KEY` blank on staging to disable it |

---

## 1. Git branch

```bash
git checkout main && git pull
git checkout -b staging
git push -u origin staging
```

Vercel auto-builds every branch. `staging` gets a **stable** alias:
`https://groovia-frontend-git-staging-<your-scope>.vercel.app`
(Find the exact URL in Vercel → Deployments → the staging deployment → "Domains".)

---

## 2. Staging Supabase project (the isolated DB)

You already have one: the Supabase project you've been testing against. It's separate
from prod, so reuse it as staging — no need to create a new project.

1. **Rename it:** Supabase → your testing project → Settings → General → Project name →
   `groovia-staging`. The ref/URL/keys are unchanged; only the display name updates.
2. **Schema + seed (if not already done, or to start clean):** SQL editor →
   - `groovia-backend/migrations/testing_db_reset.sql` to clear old test data, then
   - `groovia-backend/migrations/testing_db_setup.sql` (full schema + seed mentors with
     services & varied availability). It already folds in 015–018. If the project was
     set up before those, just run `testing_db_setup.sql` again (it's idempotent).
3. **Grab the keys** (Project Settings → API, and → Database):
   - `SUPABASE_URL` = Project URL (`https://<ref>.supabase.co`)
   - `SUPABASE_SERVICE_ROLE_KEY` = `service_role` secret
   - `SUPABASE_JWT_SECRET` = Settings → API → JWT Settings → JWT Secret
   - anon key = `NEXT_PUBLIC_SUPABASE_ANON_KEY` (for the frontend)
   - `DATABASE_URL` = Settings → Database → Connection string → **Transaction pooler (6543)**
4. **Google OAuth provider:** Authentication → Providers → Google → enable, and paste
   the same Google client ID/secret you use in prod (or a separate OAuth client).
   In Google Cloud Console, add this project's callback to the OAuth client's
   Authorized redirect URIs: `https://<staging-ref>.supabase.co/auth/v1/callback`.
5. **Auth URLs:** Authentication → URL Configuration →
   - **Site URL:** `https://groovia-frontend-git-staging-<scope>.vercel.app`
   - **Redirect URLs:** add `https://groovia-frontend-git-staging-<scope>.vercel.app/**`

---

## 3. Render — staging backend

The `render.yaml` blueprint already defines **`groovia-api-staging`** (tracks the
`staging` branch). Sync the blueprint (Render → Blueprints → your repo → it picks up
the new service), then set the `sync: false` env vars in the Render dashboard for
`groovia-api-staging`:

| Env var | Value |
|---|---|
| `CORS_ORIGINS` | the staging Vercel URL |
| `FRONTEND_URL` | the staging Vercel URL |
| `SUPABASE_URL` | staging Supabase URL |
| `SUPABASE_SERVICE_ROLE_KEY` | staging service_role key |
| `SUPABASE_JWT_SECRET` | staging JWT secret |
| `DATABASE_URL` | staging pooler URL (6543) |
| `GROQ_API_KEY` / `TAVILY_API_KEY` / `EXA_API_KEY` | reuse your existing keys |
| `RESEND_API_KEY` | optional (omit to skip real emails) |
| `MOCK_SERVICES` | `false` (or `true` to mock email/webhooks) |

This gives you `https://groovia-api-staging.onrender.com`.

> If you'd rather not use the blueprint: New Web Service → connect the repo →
> **Branch: `staging`** → Runtime: Docker → set the env vars above.

---

## 4. Vercel — staging frontend env

Vercel → Project → Settings → Environment Variables → scope to **Preview**:

| Env var | Value |
|---|---|
| `BACKEND_URL` | `https://groovia-api-staging.onrender.com` *(server-side; not `NEXT_PUBLIC_`)* |
| `NEXT_PUBLIC_SUPABASE_URL` | staging Supabase URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | staging anon key |
| `NEXT_PUBLIC_RECAPTCHA_SITE_KEY` | leave **blank** on staging to disable reCAPTCHA, or register the staging domain in the reCAPTCHA admin |

Then redeploy the staging branch so the new env vars take effect.

**Gotcha — Deployment Protection:** Vercel → Settings → Deployment Protection.
Preview deployments are often login-gated by default; disable protection for previews
(or you'll hit a Vercel login wall when opening the staging URL).

---

## 5. Test checklist (on the staging URLs)

- [ ] Open the staging Vercel URL — app loads, talks to the staging backend (`/health` is green).
- [ ] **Google login** → redirects back to the **staging** domain (not prod), lands signed in.
- [ ] **Email signup** → confirmation link points at the staging domain.
- [ ] Browse `/mentors/maya-singh` → the in-app **Book** widget shows open slots.
- [ ] Book a session → appears in **Account → Your sessions**.
- [ ] As the mentor → **Mentor Hub → Your sessions** → cancel / reschedule / no-show actions work.
- [ ] Mentor signup → admin (`yokeshmd99@gmail.com`) approves → mentor goes live.

---

## 6. Promote to production

```bash
# open a PR and merge, or:
git checkout main && git merge staging && git push
```

`main` push → prod Render + prod Vercel auto-deploy. Nothing in the staging Supabase
project touches prod.

---

## Quick reference — what points where

| | Frontend (`BACKEND_URL`) | Backend (`CORS_ORIGINS`/`FRONTEND_URL`) | Supabase |
|---|---|---|---|
| **prod** | `groovia-api.onrender.com` | prod Vercel URL | prod project |
| **staging** | `groovia-api-staging.onrender.com` | staging Vercel URL | staging project |
