# Production setup runbook

Staging is the reference: every value below already exists there, so when in doubt, open the staging
service and copy the *shape* of the setting (never the secret itself).

**Golden rule:** production gets its own keys for everything. Never reuse a staging secret, a test-mode
Razorpay key, or the staging Supabase project.

---

## 0. Before you start

Have these accounts ready with billing enabled where relevant:

| Service | What it's for | Plan needed |
|---|---|---|
| Supabase | database + auth | Pro recommended (backups) |
| Render | backend API + cron | Starter or above (free tier sleeps) |
| Vercel | frontend | Hobby works, Pro for a custom domain SLA |
| Razorpay | payments | **Live mode activated** (KYC approved) |
| Resend | transactional email | Domain verification required |
| Groq + Tavily | AI chat | API keys |

> Render's free tier spins the service down when idle. A sleeping backend means the payment webhook
> arrives at a dead service and a paid booking never confirms. Do not run production on free.

---

## 0.1 Build order, and why it is not top-to-bottom

Several values here do not exist until the *other* service is already deployed. The backend needs the
Vercel URL; Vercel needs the Render URL; Razorpay and Supabase both need the Render URL; Google needs
the Supabase URL. Nothing can be first.

So this runbook is deliberately **two passes**:

**Pass 1 - stand everything up with placeholders.** Deploy the backend and the frontend even though
their cross-references are wrong. Both will boot. Some things will be visibly broken (login redirects
to the wrong host, the frontend cannot reach the API). That is expected and it is not a
misconfiguration you need to debug.

**Pass 2 - go back and paste the real URLs in (§6).** Only now do you have every value. This is where
most launch bugs come from, because each one fails *silently*: a stale `CORS_ORIGINS` looks like a
frontend bug, a stale Razorpay webhook URL means customers pay and the booking never confirms, and
nothing in either log says "you forgot to come back here."

Do not skip §6 because §1-§5 all appeared to succeed.

---

## 1. Supabase (production project)

### 1.1 Create the project
1. supabase.com → **New project**. Name it distinctly (e.g. `immigroov-prod`).
2. Pick the region closest to your customers (India + EU split → `eu-central-1` is the usual compromise).
3. Save the database password somewhere safe; it is shown once.

### 1.2 Collect the keys
**Project Settings → API**:
- `SUPABASE_URL` ← Project URL
- `SUPABASE_SERVICE_ROLE_KEY` ← **service_role** key (secret, backend only, never in the frontend)
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` ← **anon** key (safe for the browser)

**Project Settings → API → JWT Settings**:
- `SUPABASE_JWT_SECRET` ← JWT secret

**Project Settings → Database → Connection string → URI**:
- `DATABASE_URL` ← use the **connection pooler** URI, not the direct one. Replace `[YOUR-PASSWORD]`.

### 1.3 Create the schema
SQL Editor → paste **`migrations/production_db_setup.sql`** → Run.

> Verify first. Staging has had columns added live over time; run the diff described in
> `MIGRATION_CHECKLIST` (section 7) and confirm the prod file contains every one before running it.

### 1.4 Auth settings
**Authentication → URL Configuration**
- Site URL: `https://immigroov.com`
- Redirect URLs: add `https://immigroov.com/auth/callback`

**Authentication → Providers → Google** (if using Google sign-in)
- Enable, paste the Google OAuth client ID + secret
- In Google Cloud Console, add `https://<project>.supabase.co/auth/v1/callback` as an authorised
  redirect URI

**Authentication → Hooks → Send Email Hook** — this is BUG-026
1. Enable **Send Email hook**
2. URI: `https://<your-render-backend>/auth/email-hook`
3. Copy the generated secret → set as `SUPABASE_AUTH_HOOK_SECRET` on Render
4. Until this is set, auth emails come from Supabase's sender instead of Immigroov's. The backend
   logs a warning on every boot while it's missing.

---

## 2. Resend (email)

1. **Domains → Add Domain** → `immigroov.com`
2. Add the DNS records Resend shows (SPF, DKIM, and DMARC if offered) at your DNS provider
3. Wait for **Verified** — this can take up to a few hours
4. **API Keys → Create** with *Sending access* → `RESEND_API_KEY`
5. Set `EMAIL_FROM` to something on the verified domain, e.g. `Immigroov <support@immigroov.com>`

> `EMAIL_FROM` **must** be on a verified domain. A gmail.com address is rejected. `onboarding@resend.dev`
> works only for testing and must not be used in production.
>
> Leave `EMAIL_TEST_REDIRECT` **unset** in production. If set, every email in the system is redirected
> to that one inbox and no customer or mentor receives anything.

---

## 3. Razorpay (payments)

1. Complete KYC and switch the dashboard to **Live mode** (top toggle). Test keys will not charge.
2. **Settings → API Keys → Generate Live Key** → `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`
3. **Settings → Webhooks → Add New Webhook**
   - URL: `https://<your-render-backend>/payments/razorpay/webhook`
   - Secret: generate one, set it as `RAZORPAY_WEBHOOK_SECRET`
   - Active events (all four are required):
     - `payment.captured`
     - `payment.failed`
     - `refund.created`
     - `refund.processed`
4. Enable the currencies you price in (International payments must be enabled for non-INR).

> The webhook is how a paid booking becomes confirmed. If the URL is wrong or the service is asleep,
> customers pay and the booking stays pending. Test with one real low-value booking before launch.

---

## 4. Backend on Render

**New → Web Service** → connect the `groovia-backend` repo → branch **`main`** (see §7).

- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:api --host 0.0.0.0 --port $PORT`
- Health check path: `/health` (if present) or `/`

### 4.1 Environment variables

**Required — the service exits on boot without these:**
```
GROQ_API_KEY, TAVILY_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
SUPABASE_JWT_SECRET, DATABASE_URL
```

**Required for a working product:**
```
RESEND_API_KEY            from §2
EMAIL_FROM                Immigroov <support@immigroov.com>
RAZORPAY_KEY_ID           LIVE key from §3
RAZORPAY_KEY_SECRET       LIVE secret
RAZORPAY_WEBHOOK_SECRET   from §3
SUPABASE_AUTH_HOOK_SECRET from §1.4
BANK_ENC_KEY              Fernet key: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
FRONTEND_URL              https://immigroov.com    <- placeholder on pass 1, fix in §6 row 1
CORS_ORIGINS              https://immigroov.com    <- placeholder on pass 1, fix in §6 row 2
ADMIN_EMAIL               ops inbox for admin notifications
INTERNAL_GEO_TOKEN        long random string, SAME value on Vercel
DISPATCHER_TOKEN          long random string, protects the cron endpoint
```

**Optional:**
```
JITSI_DOMAIN / JITSI_APP_ID / JITSI_PRIVATE_KEY / JITSI_KID   only for JaaS embedded calls
EMAIL_TEST_REDIRECT                                           NEVER set in production
```

> `BANK_ENC_KEY` encrypts mentor payout details at rest. Generate it **once** and never change it:
> rotating it makes every stored bank detail unreadable.
>
> `INTERNAL_GEO_TOKEN` must match on backend and Vercel. Without it the backend trusts the
> client-supplied country, which anyone can spoof for cheaper PPP pricing.

### 4.2 Cron jobs (both required)

**New → Cron Job**, same repo and env group as the web service:

| Schedule | Command | Why |
|---|---|---|
| `*/5 * * * *` | `python -m jobs.run_due` | reminders, refunds, expiring payment holds, reconciliation |
| `15 6 * * *` | `python -m scripts.refresh_fx_rates` | daily FX |

> Without the FX job, rates go stale and every cross-currency booking is mis-priced. This has already
> happened once: a placeholder `EUR→INR = 90` (real ~110) made INR-priced mentors look ~22% more
> expensive to EU customers.

---

## 5. Vercel (frontend)

**Add New → Project** → `groovia-frontend` → branch **`main`** (see §7).

```
NEXT_PUBLIC_SITE_URL                  https://immigroov.com
BACKEND_URL                           https://<render-backend>   (server-side only; §6 row 3)
NEXT_PUBLIC_SUPABASE_URL              from §1.2
NEXT_PUBLIC_SUPABASE_ANON_KEY         from §1.2
INTERNAL_GEO_TOKEN                    SAME value as the backend (§6 row 12)
NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION  from Google Search Console
```

**Domain:** Project → Settings → Domains → add `immigroov.com`, then set the DNS records Vercel shows.

**Google listing (BUG-144):** search.google.com/search-console → add the property → verify with the
meta tag (that's what `NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION` emits) → submit
`https://immigroov.com/sitemap.xml`.

> Only production is indexable. Staging and preview deployments return `Disallow: /` automatically, so
> they cannot compete with the live site for its own content.

---

## 6. Cross-wiring (pass 2)

Everything below is a value produced by one service and pasted into another. Do this **after** Render
and Vercel have both deployed once, so every URL actually exists. Redeploy both after editing, since
neither picks up an env-var change on its own.

Write the two URLs down first, then work the table:

```
RENDER  = https://__________.onrender.com
VERCEL  = https://immigroov.com          (or the .vercel.app host until DNS resolves)
```

| # | Paste this | Into | Exactly | Breaks if wrong |
|---|---|---|---|---|
| 1 | `VERCEL` | Render env `FRONTEND_URL` | no trailing slash | every email link points at the wrong site |
| 2 | `VERCEL` | Render env `CORS_ORIGINS` | scheme + host, no path | browser blocks all API calls; looks like a frontend bug |
| 3 | `RENDER` | Vercel env `BACKEND_URL` | no trailing slash | frontend cannot reach the API at all |
| 4 | `RENDER/auth/email-hook` | Supabase → Auth → Hooks → Send Email | full URL | auth mail comes from Supabase, not Immigroov (BUG-026) |
| 5 | Supabase hook secret | Render env `SUPABASE_AUTH_HOOK_SECRET` | as generated | hook rejected, same symptom as #4 |
| 6 | `RENDER/payments/razorpay/webhook` | Razorpay → Settings → Webhooks | full URL, live mode | **customers pay and the booking never confirms** |
| 7 | Razorpay webhook secret | Render env `RAZORPAY_WEBHOOK_SECRET` | as generated | every webhook fails signature, same as #6 |
| 8 | `VERCEL` | Supabase → Auth → URL Configuration → Site URL | no trailing slash | password reset and magic links land on the wrong host |
| 9 | `VERCEL/auth/callback` | Supabase → Auth → Redirect URLs | full URL | Google sign-in returns to a dead page |
| 10 | `https://<project>.supabase.co/auth/v1/callback` | Google Cloud → Credentials → Authorised redirect URIs | full URL | Google returns `redirect_uri_mismatch` |
| 11 | Google client ID + secret | Supabase → Auth → Providers → Google | both fields | Google button does nothing |
| 12 | one random string | Render `INTERNAL_GEO_TOKEN` **and** Vercel `INTERNAL_GEO_TOKEN` | **identical on both** | backend trusts client-sent country; anyone can spoof cheaper PPP pricing |
| 13 | one random string | Render `DISPATCHER_TOKEN` **and** the cron job's env | identical | reminders and refunds never run |

### 6.1 Google OAuth, in order

The two sides reference each other, so create the credential first and fill the second field after:

1. console.cloud.google.com → your project → **APIs & Services → OAuth consent screen**
   - User type **External**, publish it (in Testing mode only allow-listed accounts can sign in)
   - Add `immigroov.com` under Authorised domains
2. **Credentials → Create Credentials → OAuth client ID → Web application**
   - Authorised JavaScript origins: `VERCEL`
   - Authorised redirect URIs: `https://<project>.supabase.co/auth/v1/callback` (row 10)
3. Copy the client ID and secret into Supabase (row 11)

> Use a **separate** OAuth client for production. Reusing staging's means staging redirect URIs stay
> valid against live accounts.

### 6.2 Webhook checklist

Both webhooks point at Render, so neither can be set before §4 deploys.

| Tool | URL | Events | Verify |
|---|---|---|---|
| Razorpay | `RENDER/payments/razorpay/webhook` | `payment.captured`, `payment.failed`, `refund.created`, `refund.processed` | dashboard shows a 2xx delivery |
| Supabase | `RENDER/auth/email-hook` | send-email hook | sign up once, mail arrives from Immigroov |

> Razorpay silently keeps sending to an old URL if you edit the wrong webhook. After changing it, use
> **Send test webhook** and confirm a 2xx in the delivery log before trusting it.

### 6.3 Confirm pass 2 actually took

```
curl -s -o /dev/null -w "%{http_code}
" $RENDER/health          # 200
curl -s -H "Origin: $VERCEL" -I $RENDER/mentors | grep -i access-control-allow-origin
```

The second must echo your Vercel origin. If it is missing or shows the staging host, `CORS_ORIGINS`
did not take and the site will look broken in a browser while working fine in curl.

---

## 7. Data migration

Order matters. Do this **after** the schema exists and **before** announcing launch.

1. **Re-extract the mentors from the legacy API** (read-only, safe to re-run):
   ```
   python scripts/migrate_mentors.py                 # dry run -> mentor_migration_preview.json
   ```
   Review the preview, then against the **prod** credentials:
   ```
   SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python scripts/migrate_mentors.py --commit
   ```

2. **Do NOT copy staging's transactional data.** Bookings, payments, payouts, pricing rows and quotes
   there were produced by testing and reference Razorpay **test-mode** IDs that do not exist in live
   mode. Copying them corrupts the payout ledger from day one.

3. **Do NOT copy the test mentors.** Three were created through signup on staging
   (`gautham-s-d61803`, `vinoth-kannan`, `vinoth-mentor-2`) and `yokesh-dhanabal` is a test account.
   The migration script only creates mentors that came from the legacy API, so a clean run excludes
   them automatically — verify with:
   ```sql
   SELECT COUNT(*) FROM mentors WHERE legacy_id IS NULL;   -- expect 0 in prod
   ```

4. **Then run the data scripts, in this order:**
   ```
   python -m scripts.reimport_migrated_prices
   python -m scripts.fix_expertise_countries
   python -m scripts.refresh_ppp_factors
   python -m scripts.refresh_fx_rates
   python -m scripts.remap_service_categories
   ```

5. **Restore the production booking rule (BUG-115):** minimum notice back to 2 hours. Staging has
   mentors at 0 for testing; that must not exist in production.

6. **Verify before launch:**
   ```sql
   SELECT COUNT(*) FROM mentors;                        -- expect the legacy count (67 at last check)
   SELECT COUNT(*) FROM bookings;                       -- expect 0
   SELECT COUNT(*) FROM customer_payments;              -- expect 0
   SELECT COUNT(*) FROM fx_rates WHERE base='EUR';      -- expect ~40, fetched today
   SELECT COUNT(*) FROM ppp_factors;                    -- expect ~200
   SELECT value FROM platform_settings WHERE key='fx_max_age_minutes';   -- expect 2880
   ```

---

## 8. Branch check

**Done, 13 Aug 2026.** `staging` was merged into `main` on both repos and the resulting trees are
byte-identical to staging, so `main` deploys exactly what staging runs. Backend `main` had also been
left on the old monorepo layout (backend and frontend in subdirectories); that is corrected.

Both repos: `main` is 0 commits behind `staging`.

Point Render and Vercel at **`main`** for production. Keep the staging services on `staging` so the
two never move together. Re-merge `staging` into `main` for each release, and re-check with:

```
git fetch origin && git rev-list --count origin/main..origin/staging   # 0 = main is current
```

---

## 9. Smoke test on production

Run these in order, with real (small) money:

1. Sign up as a new customer → welcome email arrives, from Immigroov not Supabase
2. Browse mentors → prices show in your local currency
3. Book a paid session → Razorpay live checkout → confirmation email with booking **and payment**
   reference, plus the price breakdown
4. Check the mentor received their own confirmation
5. Cancel it → both sides get the right wording, refund follows the policy
6. Confirm the admin inbox received the booking and cancellation notices
7. Wait for a reminder (or shorten a window temporarily) → reminders arrive
8. Sign in as a mentor → dashboard, availability, pricing all load

Only after all eight pass should the domain be advertised.
