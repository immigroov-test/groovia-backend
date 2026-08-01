# Payments (Razorpay) — setup & go-live

The booking flow supports two paths, chosen at runtime by the
`platform_settings.payments_enabled` flag:

- **`payments_enabled = false` (default):** bookings confirm instantly with no
  charge (the existing direct-confirm path). This is the safe pre-launch state.
- **`payments_enabled = true`:** the frontend runs quote → reserve (10-min hold)
  → Razorpay Checkout → verify, and the booking only confirms after payment.

Everything below is what it takes to turn real payments on.

---

## 1. Apply the database migration

The pricing engine (PPP + FX + per-country commission/tax), the payment tables,
the reserve/confirm/expire RPCs, and the dispatcher lease lock are now folded
directly into `migrations/testing_db_setup.sql` (and `production_db_setup.sql`),
each a single self-contained script. Just run the one setup script:

```
psql "$SUPABASE_DB_URL" -f migrations/testing_db_setup.sql
```

`migrations/payments_setup.sql` is **deprecated** and must not be run separately;
it is kept only as a reference copy. It is safe to re-run the setup script.

Requires `pgcrypto` in the `extensions` schema (Supabase provides this by
default; the base migration already enables it).

## 2. Environment variables (already on Render)

| Var | Where to get it |
|---|---|
| `RAZORPAY_KEY_ID` | Razorpay Dashboard → Settings → API Keys |
| `RAZORPAY_KEY_SECRET` | shown once when you generate the key |
| `RAZORPAY_WEBHOOK_SECRET` | **you choose this string**; set the identical value in the webhook config below |

These are optional at startup (a warning is logged if unset). With
`MOCK_SERVICES=true`, webhook signature checks are skipped for local testing.

## 3. Razorpay webhook

Dashboard → Settings → Webhooks → Add:

- **URL:** `https://groovia-4bet.onrender.com/payments/razorpay/webhook`
- **Secret:** the same string as `RAZORPAY_WEBHOOK_SECRET`
- **Active events:** `payment.captured`, `order.paid`, `payment.failed`,
  `refund.created`, `refund.processed`

The handler verifies the HMAC signature, dedupes on event id, re-fetches the
payment from Razorpay (never trusts the webhook body), and always returns 200
once the signature checks out so Razorpay doesn't retry on our own errors.

## 4. Render Cron Job (the dispatcher)

The reserve flow needs a scheduler for three money-correctness jobs. Create **one**
Render **Cron Job** (not a Web Service):

- **Repository / branch:** same as the backend (`groovia-backend`, `staging`)
- **Command:** `python -m jobs.run_due`
- **Schedule:** `*/5 * * * *` (every 5 minutes)
- **Environment:** copy the backend web service's env group (needs
  `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `RAZORPAY_*`)

Each tick runs, under a lease lock so two overlapping ticks can't double-run:

| Job | Cadence | Purpose |
|---|---|---|
| `refresh_fx_rates` | self-gated 6h | pull EUR-pivot rates from Frankfurter (pricing fails without fresh FX) |
| `expire_stale_holds` | every tick | release 10-min payment holds nobody paid → frees the slot |
| `sweep_verify_payments` | every tick | backstop for dropped/late webhooks |
| `process_refunds` | every tick | issue owed refunds (no-op until refund ledger is wired in the payouts phase) |
| `reconcile_payments` | self-gated 24h | cross-check local vs Razorpay records, log mismatches |

**Important:** FX rates start empty. Paid quotes fail with `FX_UNAVAILABLE` until
the first `refresh_fx_rates` runs. Either wait one dispatcher tick or run it once
manually: `python -m jobs.run_due`.

## 5. Go live

1. Confirm FX rates exist: `SELECT count(*) FROM fx_rates;` (should be ~30+).
2. Flip the toggle:
   ```sql
   UPDATE platform_settings SET value = 'true' WHERE key = 'payments_enabled';
   ```
3. Book a paid session in Razorpay **test mode** first (test key + a test card)
   to confirm the full quote → reserve → checkout → verify → confirmed path, then
   switch to live keys.

## Not yet built (deferred, by request)

- **Manual payouts UI/endpoints.** The schema and RPCs exist
  (`mentor_payouts`, `mark_payout_paid`, `set_payout_blocked`) and a payout row is
  created per booking, but there is no admin screen yet.
- **Refund-on-cancel ledger.** `cancel_booking` is unchanged; it does not write
  `booking_ledger` refund entries, so `process_refunds` has nothing to issue for
  cancellations. The HOLD_EXPIRED auto-refund (money captured after the hold
  lapsed) IS handled, in `db.finalize_captured_payment`.
- **Referrals / affiliate.** Intentionally stripped from this port.
