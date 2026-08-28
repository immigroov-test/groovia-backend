-- Apply on PRODUCTION, in the Supabase SQL editor, in this order.
--
-- Every block is idempotent: functions are CREATE OR REPLACE, the table is IF NOT EXISTS, and the
-- two data blocks match nothing once the data is already correct, so a re-run is a no-op.
--
-- Measured on staging beforehand: 187 services x 15 customer countries, before and after.
-- 180 unchanged, 7 moved, all on the four legacy multi-currency mentors. Every legacy market price
-- is held exactly (India keeps 1499 / 1999 / 499 / 2699 INR, and fazil's India price recovers from
-- 485 to 1500). What moves is the outlier session coming into line with the mentor's own sessions of
-- the same length.

SET check_function_bodies = off;


-- 1. PPP anchors on the mentor's base currency, not the service's (both pricing functions)
CREATE OR REPLACE FUNCTION compute_booking_price(p_service_id UUID, p_customer_country TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_pricing_version CONSTANT INT := 5;   -- v5: customer platform fee added ON TOP + internal mentor commission
  v_ppp_version     CONSTANT INT := 1;
  v_provider        CONSTANT TEXT := 'frankfurter';
  v_mentor_id UUID; v_set NUMERIC; v_set_offer NUMERIC; v_ment_ccy TEXT; v_is_ppp BOOLEAN;
  v_prices JSONB; v_pfee_pct NUMERIC; v_tax_pct NUMERIC; v_comm_pct NUMERIC; v_mentor_country TEXT;
  v_base_ccy TEXT;   -- the MENTOR's base currency: what PPP anchors to, distinct from the service's
  v_cust_ccy TEXT; v_ppp NUMERIC := 1; v_source TEXT; v_explicit NUMERIC; v_base NUMERIC; v_mentor_amt NUMERIC;
  v_fx_mc NUMERIC; v_fx_c_inr NUMERIC; v_fx_m_inr NUMERIC;
  v_gross NUMERIC; v_platform_fee NUMERIC; v_commission NUMERIC; v_subtotal NUMERIC;
  v_tax_amt NUMERIC; v_net_cust NUMERIC; v_net_mentor NUMERIC; v_mentor_base NUMERIC;
BEGIN
  SELECT s.mentor_id, s.set_price, s.set_offer_price, COALESCE(s.set_currency, 'USD'), s.is_ppp,
         COALESCE(s.currency_prices, '[]'::jsonb), m.country, UPPER(COALESCE(m.currency, s.set_currency, 'USD'))
    INTO v_mentor_id, v_set, v_set_offer, v_ment_ccy, v_is_ppp, v_prices, v_mentor_country, v_base_ccy
  FROM services s JOIN mentors m ON m.id = s.mentor_id
  WHERE s.id = p_service_id AND s.is_active AND s.status = 'approved';
  IF v_set IS NULL THEN RAISE EXCEPTION 'Service not available' USING errcode = 'P0001'; END IF;

  -- Customer-facing platform fee % + tax % by the CUSTOMER's country; INTERNAL mentor commission %
  -- (per-mentor override wins) sizes the payout only.
  v_pfee_pct := country_platform_fee_pct(p_customer_country);
  v_tax_pct  := country_tax_pct(p_customer_country);
  v_comm_pct := mentor_commission_pct(v_mentor_id);
  v_cust_ccy := currency_for_country(p_customer_country);

  -- 1. Is there an explicit MENTOR rate for the customer's currency (primary, or a currency_prices row)?
  IF v_cust_ccy = v_ment_ccy THEN
    v_explicit := COALESCE(v_set_offer, v_set);
  ELSE
    SELECT COALESCE((e->>'offer_price')::numeric, (e->>'base_price')::numeric)
      INTO v_explicit
    FROM jsonb_array_elements(v_prices) e
    WHERE UPPER(e->>'currency') = v_cust_ccy AND COALESCE((e->>'base_price')::numeric, 0) > 0
    LIMIT 1;
  END IF;

  v_fx_c_inr := get_fx_or_null(v_cust_ccy, 'INR');
  v_fx_m_inr := get_fx_or_null(v_ment_ccy, 'INR');

  IF v_explicit IS NOT NULL THEN
    -- Mentor set this currency's rate directly -> no FX, no PPP on the rate.
    v_source     := 'explicit';
    v_fx_mc      := CASE WHEN v_cust_ccy = v_ment_ccy THEN 1 ELSE get_fx_or_null(v_ment_ccy, v_cust_ccy) END;
    v_mentor_amt := ROUND(v_explicit, 2);
    v_net_mentor := CASE WHEN v_fx_mc IS NOT NULL AND v_fx_mc > 0 THEN ROUND(v_explicit / v_fx_mc, 2) ELSE NULL END;
  ELSE
    -- Fallback: localise the primary rate to the customer currency (+ PPP).
    v_source     := 'converted';
    v_ppp        := CASE WHEN v_is_ppp THEN ppp_relative(p_customer_country, currency_anchor_country(v_base_ccy)) ELSE 1 END;
    v_fx_mc      := get_fx_or_null(v_ment_ccy, v_cust_ccy);   -- SOFT (see fallback below)
    v_base       := COALESCE(v_set_offer, v_set);
    IF v_fx_mc IS NULL THEN
      -- No fresh FX for this pair. Charge in the mentor's OWN currency - exactly what the card showed
      -- (display_service_prices returns fx_ok=false with the mentor-currency price) - so the displayed
      -- price equals the charge instead of the checkout hard-failing (F2). Fee/tax stay by customer country.
      v_cust_ccy := v_ment_ccy;
      v_fx_mc    := 1;
      v_fx_c_inr := v_fx_m_inr;
    END IF;
    v_mentor_amt := ROUND(v_base * v_ppp * v_fx_mc, 2);
    v_net_mentor := v_base * v_ppp;                    -- mentor payout basis (mentor ccy), PPP-adjusted; commission applied below (BUG-097)
  END IF;

  -- Markup model. v_mentor_amt (the mentor's localised rate) is the SESSION PRICE the customer sees.
  -- The platform fee is ADDED ON TOP; tax is charged on (session + platform fee); the three sum to the
  -- gross the customer is charged. Separately, the INTERNAL mentor commission comes OUT of the session
  -- price to size the payout (fee_pct / fee_amount / net_*), and is never shown to the customer.
  v_subtotal     := v_mentor_amt;                                              -- session price (customer-facing)
  v_platform_fee := ROUND(v_mentor_amt * v_pfee_pct / 100.0, 2);               -- platform fee, added on top (customer)
  v_tax_amt      := ROUND((v_mentor_amt + v_platform_fee) * v_tax_pct / 100.0, 2);  -- tax on session + fee
  v_gross        := ROUND(v_mentor_amt + v_platform_fee + v_tax_amt, 2);       -- total the customer pays
  v_commission   := ROUND(v_mentor_amt * v_comm_pct / 100.0, 2);               -- internal mentor commission (out)
  v_net_cust     := ROUND(v_mentor_amt - v_commission, 2);                     -- mentor take-home, customer currency
  v_mentor_base := v_net_mentor;                                               -- PRE-commission mentor-ccy payout basis (explicit: explicit/fx; converted: base x ppp)
  v_net_mentor := CASE WHEN v_net_mentor IS NOT NULL                           -- mentor take-home, mentor currency
                       THEN ROUND(v_net_mentor * (1 - v_comm_pct / 100.0), 2) ELSE NULL END;

  RETURN jsonb_build_object(
    'pricing_version', v_pricing_version, 'ppp_version', v_ppp_version, 'fx_provider', v_provider,
    'service_id', p_service_id, 'mentor_id', v_mentor_id, 'customer_country', UPPER(COALESCE(p_customer_country, '')),
    'mentor_currency', v_ment_ccy, 'customer_currency', v_cust_ccy,
    'pricing_source', v_source, 'set_price', v_set, 'ppp_multiplier', v_ppp,
    'markup_pct', v_pfee_pct, 'mentor_amount', v_mentor_amt,
    'fx_mentor_customer', v_fx_mc, 'fx_customer_inr', v_fx_c_inr, 'fx_mentor_inr', v_fx_m_inr,
    'gross_customer', v_gross, 'subtotal', v_subtotal,
    'platform_fee_pct', v_pfee_pct, 'platform_fee', v_platform_fee,
    'tax_pct', v_tax_pct, 'tax_amount', v_tax_amt,
    -- Internal (admin-only) mentor-commission side; fee_pct/fee_amount are consumed by the payout
    -- ledger + referral splits (net_customer + fee_amount = session price).
    'fee_pct', v_comm_pct, 'fee_amount', v_commission,
    'commission_pct', v_comm_pct, 'commission_amount', v_commission,
    'net_customer', v_net_cust, 'net_mentor', v_net_mentor, 'mentor_base', v_mentor_base);
END; $$;
GRANT EXECUTE ON FUNCTION compute_booking_price(UUID, TEXT) TO anon, authenticated;

-- Issue a binding 10-minute quote.
CREATE OR REPLACE FUNCTION get_booking_quote(p_service_id UUID, p_customer_country TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_snap JSONB; v_hash TEXT; v_id UUID; v_exp TIMESTAMPTZ;
BEGIN
  v_snap := compute_booking_price(p_service_id, p_customer_country);
  v_hash := encode(extensions.digest(v_snap::text, 'sha256'), 'hex');
  INSERT INTO pricing_quotes(service_id, mentor_id, customer_country, customer_currency,
      pricing_version, ppp_version, fx_provider, snapshot, pricing_hash)
    VALUES (p_service_id, (v_snap->>'mentor_id')::uuid, UPPER(COALESCE(p_customer_country, '')),
      v_snap->>'customer_currency', (v_snap->>'pricing_version')::int, (v_snap->>'ppp_version')::int,
      v_snap->>'fx_provider', v_snap, v_hash)
    RETURNING id, expires_at INTO v_id, v_exp;
  RETURN v_snap || jsonb_build_object('quote_id', v_id, 'expires_at', v_exp, 'pricing_hash', v_hash);
END; $$;
GRANT EXECUTE ON FUNCTION get_booking_quote(UUID, TEXT) TO anon, authenticated;

-- Read-only DISPLAY pricing (soft FX fallback) — for browse/list pages. No quote
-- row, no fee. If FX is unavailable it shows the mentor-currency price
-- (fx_ok=false) rather than failing the page.
-- p_items: [{ "key": "<any>", "amount": <num>, "from": "<ccy>", "is_ppp": <bool> }]
CREATE OR REPLACE FUNCTION convert_prices(p_customer_country TEXT, p_items JSONB)
RETURNS TABLE(key TEXT, you NUMERIC, you0 NUMERIC, customer_currency TEXT, fx_ok BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE it JSONB; v_amt NUMERIC; v_from TEXT; v_ppp_on BOOLEAN; v_cust TEXT; v_ppp NUMERIC; v_rate NUMERIC; v_mk NUMERIC;
BEGIN
  v_cust := currency_for_country(p_customer_country);
  FOR it IN SELECT * FROM jsonb_array_elements(COALESCE(p_items, '[]'::jsonb)) LOOP
    v_amt := COALESCE((it->>'amount')::numeric, 0);
    v_from := COALESCE(it->>'from', 'USD');
    v_ppp_on := COALESCE((it->>'is_ppp')::boolean, false);
    v_ppp := CASE WHEN v_ppp_on THEN ppp_relative(p_customer_country, currency_anchor_country(v_from)) ELSE 1 END;
    -- Revenue split: the mentor's rate IS the customer price; the commission is taken OUT of it, so
    -- it never changes what the customer sees. Only tax is added on top, so the displayed price
    -- (mentor rate + tax) equals what's charged.
    v_mk  := (1 + country_tax_pct(p_customer_country) / 100.0);
    v_rate := get_fx_or_null(v_from, v_cust);
    IF v_rate IS NULL THEN
      key := it->>'key'; you0 := ROUND(v_amt * v_mk, 2); you := ROUND(v_amt * v_ppp * v_mk, 2);
      customer_currency := UPPER(v_from); fx_ok := false;
    ELSE
      key := it->>'key'; you0 := ROUND(v_amt * v_rate * v_mk, 2); you := ROUND(v_amt * v_ppp * v_rate * v_mk, 2);
      customer_currency := v_cust; fx_ok := true;
    END IF;
    RETURN NEXT;
  END LOOP;
END; $$;
GRANT EXECUTE ON FUNCTION convert_prices(TEXT, JSONB) TO anon, authenticated;

-- Display SESSION price per service, using the SAME per-service engine as the binding quote
-- (compute_booking_price): explicit per-currency price when the mentor set one, else the base rate
-- localized with FX, with PPP gated on the mentor's fair-pricing flag. This is what the cards and the
-- booking page show, so the session price displayed EXACTLY equals the session line at checkout
-- (BUG-077) - only the platform fee + tax are added at checkout. No fee/tax here (session only).
-- Soft FX: when a rate is missing it shows the mentor-currency price (fx_ok=false) instead of failing.
--   you  = session price WITH PPP (what the customer is charged for the session)
--   you0 = session price WITHOUT PPP (for the struck-through "original" on fair-pricing cards)
CREATE OR REPLACE FUNCTION display_service_prices(p_customer_country TEXT, p_service_ids UUID[])
RETURNS TABLE(key TEXT, you NUMERIC, you0 NUMERIC, customer_currency TEXT, fx_ok BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
  sid UUID; v_mentor_id UUID; v_set NUMERIC; v_set_offer NUMERIC; v_ment_ccy TEXT; v_is_ppp BOOLEAN;
  v_prices JSONB; v_mentor_country TEXT; v_base_ccy TEXT; v_cust TEXT; v_explicit NUMERIC; v_ppp NUMERIC; v_fx NUMERIC; v_base NUMERIC;
BEGIN
  v_cust := currency_for_country(p_customer_country);
  FOREACH sid IN ARRAY COALESCE(p_service_ids, ARRAY[]::UUID[]) LOOP
    SELECT s.mentor_id, s.set_price, s.set_offer_price, COALESCE(s.set_currency, 'USD'), s.is_ppp,
           COALESCE(s.currency_prices, '[]'::jsonb), m.country, UPPER(COALESCE(m.currency, s.set_currency, 'USD'))
      INTO v_mentor_id, v_set, v_set_offer, v_ment_ccy, v_is_ppp, v_prices, v_mentor_country, v_base_ccy
    FROM services s JOIN mentors m ON m.id = s.mentor_id
    WHERE s.id = sid AND s.is_active AND s.status = 'approved';
    IF v_set IS NULL THEN CONTINUE; END IF;   -- unknown/inactive/unapproved: skip (card falls back)

    -- Explicit mentor rate for the customer's currency? (identical rule to compute_booking_price)
    IF v_cust = v_ment_ccy THEN
      v_explicit := COALESCE(v_set_offer, v_set);
    ELSE
      SELECT COALESCE((e->>'offer_price')::numeric, (e->>'base_price')::numeric)
        INTO v_explicit
      FROM jsonb_array_elements(v_prices) e
      WHERE UPPER(e->>'currency') = v_cust AND COALESCE((e->>'base_price')::numeric, 0) > 0
      LIMIT 1;
    END IF;

    IF v_explicit IS NOT NULL THEN
      -- Mentor set this currency's price directly: no FX, no PPP (matches checkout's explicit branch).
      key := sid::text; you0 := ROUND(v_explicit, 2); you := ROUND(v_explicit, 2);
      customer_currency := v_cust; fx_ok := true;
    ELSE
      v_base := COALESCE(v_set_offer, v_set);
      v_ppp  := CASE WHEN v_is_ppp THEN ppp_relative(p_customer_country, currency_anchor_country(v_base_ccy)) ELSE 1 END;
      v_fx   := get_fx_or_null(v_ment_ccy, v_cust);   -- SOFT (checkout uses the strict get_fx)
      IF v_fx IS NULL THEN
        key := sid::text; you0 := ROUND(v_base, 2); you := ROUND(v_base * v_ppp, 2);
        customer_currency := UPPER(v_ment_ccy); fx_ok := false;   -- show mentor currency, no charge here
      ELSE
        key := sid::text; you0 := ROUND(v_base * v_fx, 2); you := ROUND(v_base * v_ppp * v_fx, 2);
        customer_currency := v_cust; fx_ok := true;
      END IF;
    END IF;
    RETURN NEXT;
  END LOOP;
END; $$;
GRANT EXECUTE ON FUNCTION display_service_prices(TEXT, UUID[]) TO anon, authenticated;

-- Migrated mentors keep their imported per-SESSION prices (services.set_price = the price the customer
-- pays) exactly as the old portal had them. We do NOT re-derive them from a per-hour rate here - an
-- earlier version of this block did, and it overwrote real prices (a 399 INR session became ~10). Each
-- mentor's hourly_rate is seeded at import (scripts/migrate_mentors.py) purely as the first-login popup
-- default; their sessions are re-priced only when they confirm/change that rate in onboarding or on the
-- Profile tab, via reprice_mentor_services(). For an already-migrated DB whose set_price was corrupted
-- by the old block, restore the real prices from the raw export once with:
--     python -m scripts.reimport_migrated_prices

-- ── Payment tables ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS customer_payments (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id                  UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  amount                      NUMERIC NOT NULL,   -- decimal, customer currency, actually charged
  currency                    TEXT NOT NULL,
  state                       TEXT NOT NULL DEFAULT 'created'
                                 CHECK (state IN ('created','authorized','captured','partially_refunded','refunded','failed')),
  provider                    TEXT DEFAULT 'razorpay',
  provider_order_id           TEXT,
  provider_payment_id         TEXT,
  provider_payload            JSONB,
  provider_error_code         TEXT,
  provider_error_description  TEXT,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_customer_payments_booking ON customer_payments(booking_id);
CREATE INDEX IF NOT EXISTS idx_customer_payments_provider_order ON customer_payments(provider_order_id);
ALTER TABLE customer_payments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_payments_read ON customer_payments;
-- Owner-scoped: a signed-in user sees only payments on their own bookings. The
-- backend uses the service-role key (bypasses RLS) so all server logic is
-- unaffected; this only closes direct anon/authenticated reads of others' rows.
CREATE POLICY customer_payments_read ON customer_payments FOR SELECT
  USING (booking_id IN (SELECT id FROM bookings WHERE candidate_id = auth.uid()));

-- mentor_payouts.amount is LOAD-BEARING: the PRE-FEE mentor-currency payout basis
-- (set_price x ppp_multiplier), distinct from net_amount_mentor_currency (POST-fee).
CREATE TABLE IF NOT EXISTS mentor_payouts (
  id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id                     UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  booking_id                    UUID NOT NULL UNIQUE REFERENCES bookings(id) ON DELETE CASCADE,
  amount                        NUMERIC,
  gross_amount                  NUMERIC,
  fee_pct                       NUMERIC,
  platform_fee_amount           NUMERIC,
  net_amount_customer_currency  NUMERIC,
  net_amount_mentor_currency    NUMERIC,
  exchange_rate_used            NUMERIC,
  customer_currency             TEXT,
  mentor_currency               TEXT,
  ppp_multiplier                NUMERIC,
  method                        TEXT,     -- 'manual' | NULL
  payout_reference              TEXT,
  payout_state                  TEXT NOT NULL DEFAULT 'pending'
                                  CHECK (payout_state IN ('pending','paid','void','blocked')),
  paid_date                     TIMESTAMPTZ,
  comments                      TEXT,
  created_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mentor_payouts_mentor ON mentor_payouts(mentor_id);
ALTER TABLE mentor_payouts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mentor_payouts_read ON mentor_payouts;
-- Owner-scoped: a mentor sees only their own payout rows. Service-role bypasses RLS.
CREATE POLICY mentor_payouts_read ON mentor_payouts FOR SELECT
  USING (mentor_id IN (SELECT id FROM mentors WHERE profile_id = auth.uid()));

-- One-time correction (BUG-097): the mentor's net earning must be the PPP-adjusted base MINUS the
-- commission. Backfill it only where it was never stored (legacy/manual rows), deriving net = amount
-- x (1 - commission %). NULL-guarded on purpose: reserve_booking already stores the correct net for new
-- bookings, and a referred session's net is overwritten by process_referral_commissions to the referral
-- split - re-running this must NOT clobber those correct values with a plain amount x (1 - fee).
UPDATE mentor_payouts
SET net_amount_mentor_currency = ROUND(amount * (1 - COALESCE(fee_pct, 0) / 100.0), 2)
WHERE amount IS NOT NULL AND net_amount_mentor_currency IS NULL;

-- Correction: payout_state used to be written by three different functions, one of which only ever
-- voided, so a booking brought back out of cancelled/no_show kept a real earning recorded as 'void'.
-- Re-derives the column from bookings.status using exactly the rule trg_sync_payout_state_fn now
-- enforces, so any database this script runs against is brought onto the invariant in both
-- directions. Idempotent: it matches only rows that actually disagree. 'paid'/'blocked' are admin
-- settlement decisions and are never touched.
UPDATE mentor_payouts po
SET payout_state = CASE WHEN b.status IN ('cancelled','no_show') THEN 'void' ELSE 'pending' END
FROM bookings b
WHERE b.id = po.booking_id
  AND po.payout_state NOT IN ('paid','blocked')
  AND po.payout_state IS DISTINCT FROM
      (CASE WHEN b.status IN ('cancelled','no_show') THEN 'void' ELSE 'pending' END);

CREATE TABLE IF NOT EXISTS payment_refunds (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id                  UUID NOT NULL REFERENCES customer_payments(id) ON DELETE CASCADE,
  booking_id                  UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  provider_refund_id          TEXT UNIQUE,
  amount_minor                INT NOT NULL,   -- MINOR units (paise/cents), matches Razorpay's refund API
  currency                    TEXT NOT NULL,
  status                      TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created','processed','failed')),
  provider_payload            JSONB,
  provider_error_code         TEXT,
  provider_error_description  TEXT,
  ledger_version              INT,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payment_refunds_booking ON payment_refunds(booking_id);

-- Razorpay webhook intake log. event_id is the dedup key (Razorpay retries
-- deliveries; processed_at set means "already handled, no-op on replay").
CREATE TABLE IF NOT EXISTS payment_events (
  event_id         TEXT PRIMARY KEY,
  type             TEXT,
  payload          JSONB,
  signature        TEXT,
  attempt_count    INT NOT NULL DEFAULT 0,
  last_attempt_at  TIMESTAMPTZ,
  next_retry_at    TIMESTAMPTZ,
  processed_at     TIMESTAMPTZ,
  error            TEXT,
  received_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payment_reconciliation_log (
  id                    BIGSERIAL PRIMARY KEY,
  kind                  TEXT NOT NULL,   -- 'mismatch' | 'fetch_failed'
  provider_payment_id   TEXT,
  booking_id            UUID REFERENCES bookings(id) ON DELETE CASCADE,
  detail                JSONB,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payment_reconciliation_log_booking ON payment_reconciliation_log(booking_id);

-- Booking columns the reserve/confirm flow needs.
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_hold_expires_at TIMESTAMPTZ;  -- 10-min reservation hold
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS customer_currency TEXT;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS notes TEXT;  -- customer's "what should my mentor prepare" note (BUG-113)

-- BUG-147: one-shot marker for the new-customer welcome email. /auth/sync runs on EVERY login, so
-- without this the welcome would go out again on each sign-in.
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS welcome_sent_at TIMESTAMPTZ;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS fx_customer_inr NUMERIC;  -- INR per 1 customer-currency unit
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS fx_mentor_inr   NUMERIC;  -- INR per 1 mentor-currency unit

INSERT INTO platform_settings (key, value, description) VALUES
  ('payments_enabled', 'false', 'false = mock instant-confirm booking; true = real Razorpay reserve->pay->confirm flow')
ON CONFLICT (key) DO NOTHING;

-- ── Money journal (read by refund_owed_minor) ────────────────────────────────
-- Included so refund_owed_minor has a table to read and forward-compat with the
-- payouts phase. Nothing in THIS migration writes 'refund' rows (cancel-refund
-- ledger wiring is deferred), so refund_owed_minor returns 0 today and
-- process_refunds is a safe no-op until then.
CREATE TABLE IF NOT EXISTS booking_ledger (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id             UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  party                  TEXT NOT NULL CHECK (party IN ('customer','mentor','platform')),
  kind                   TEXT NOT NULL CHECK (kind IN ('penalty','refund','credit','charge')),
  amount                 NUMERIC(10,2),
  pct                    INT,
  currency               TEXT,
  reason                 TEXT,
  normalized_inr_amount  NUMERIC,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_booking_ledger_booking ON booking_ledger(booking_id);
ALTER TABLE booking_ledger ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS booking_ledger_read ON booking_ledger;
-- Owner-scoped: the candidate or the mentor on the booking can read its money
-- journal; no public reads. Service-role bypasses RLS.
CREATE POLICY booking_ledger_read ON booking_ledger FOR SELECT
  USING (booking_id IN (
    SELECT id FROM bookings
    WHERE candidate_id = auth.uid()
       OR mentor_id IN (SELECT id FROM mentors WHERE profile_id = auth.uid())
  ));

CREATE OR REPLACE FUNCTION add_ledger(
  p_booking UUID, p_party TEXT, p_kind TEXT, p_amount NUMERIC, p_pct INT, p_reason TEXT
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_cust_ccy TEXT; v_ment_ccy TEXT; v_fxc NUMERIC; v_fxm NUMERIC; v_ccy TEXT; v_fx NUMERIC;
BEGIN
  SELECT COALESCE(mp.customer_currency, cp.currency, 'INR'),
         COALESCE(mp.mentor_currency, 'INR'),
         b.fx_customer_inr, b.fx_mentor_inr
    INTO v_cust_ccy, v_ment_ccy, v_fxc, v_fxm
  FROM bookings b
  LEFT JOIN LATERAL (
    SELECT customer_currency, mentor_currency FROM mentor_payouts
    WHERE booking_id = b.id ORDER BY created_at DESC LIMIT 1
  ) mp ON TRUE
  LEFT JOIN LATERAL (
    SELECT currency FROM customer_payments
    WHERE booking_id = b.id ORDER BY created_at DESC LIMIT 1
  ) cp ON TRUE
  WHERE b.id = p_booking;

  IF p_party = 'mentor' THEN v_ccy := v_ment_ccy; v_fx := COALESCE(v_fxm, 1);
  ELSE                       v_ccy := v_cust_ccy; v_fx := COALESCE(v_fxc, 1); END IF;

  INSERT INTO booking_ledger(booking_id, party, kind, amount, pct, currency, reason, normalized_inr_amount)
    VALUES (p_booking, p_party, p_kind, ROUND(COALESCE(p_amount, 0), 2), p_pct, v_ccy, p_reason,
            ROUND(COALESCE(p_amount, 0) * v_fx, 2));
END;
$$;


CREATE OR REPLACE FUNCTION display_service_prices(p_customer_country TEXT, p_service_ids UUID[])
RETURNS TABLE(key TEXT, you NUMERIC, you0 NUMERIC, customer_currency TEXT, fx_ok BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
  sid UUID; v_mentor_id UUID; v_set NUMERIC; v_set_offer NUMERIC; v_ment_ccy TEXT; v_is_ppp BOOLEAN;
  v_prices JSONB; v_mentor_country TEXT; v_base_ccy TEXT; v_cust TEXT; v_explicit NUMERIC; v_ppp NUMERIC; v_fx NUMERIC; v_base NUMERIC;
BEGIN
  v_cust := currency_for_country(p_customer_country);
  FOREACH sid IN ARRAY COALESCE(p_service_ids, ARRAY[]::UUID[]) LOOP
    SELECT s.mentor_id, s.set_price, s.set_offer_price, COALESCE(s.set_currency, 'USD'), s.is_ppp,
           COALESCE(s.currency_prices, '[]'::jsonb), m.country, UPPER(COALESCE(m.currency, s.set_currency, 'USD'))
      INTO v_mentor_id, v_set, v_set_offer, v_ment_ccy, v_is_ppp, v_prices, v_mentor_country, v_base_ccy
    FROM services s JOIN mentors m ON m.id = s.mentor_id
    WHERE s.id = sid AND s.is_active AND s.status = 'approved';
    IF v_set IS NULL THEN CONTINUE; END IF;   -- unknown/inactive/unapproved: skip (card falls back)

    -- Explicit mentor rate for the customer's currency? (identical rule to compute_booking_price)
    IF v_cust = v_ment_ccy THEN
      v_explicit := COALESCE(v_set_offer, v_set);
    ELSE
      SELECT COALESCE((e->>'offer_price')::numeric, (e->>'base_price')::numeric)
        INTO v_explicit
      FROM jsonb_array_elements(v_prices) e
      WHERE UPPER(e->>'currency') = v_cust AND COALESCE((e->>'base_price')::numeric, 0) > 0
      LIMIT 1;
    END IF;

    IF v_explicit IS NOT NULL THEN
      -- Mentor set this currency's price directly: no FX, no PPP (matches checkout's explicit branch).
      key := sid::text; you0 := ROUND(v_explicit, 2); you := ROUND(v_explicit, 2);
      customer_currency := v_cust; fx_ok := true;
    ELSE
      v_base := COALESCE(v_set_offer, v_set);
      v_ppp  := CASE WHEN v_is_ppp THEN ppp_relative(p_customer_country, currency_anchor_country(v_base_ccy)) ELSE 1 END;
      v_fx   := get_fx_or_null(v_ment_ccy, v_cust);   -- SOFT (checkout uses the strict get_fx)
      IF v_fx IS NULL THEN
        key := sid::text; you0 := ROUND(v_base, 2); you := ROUND(v_base * v_ppp, 2);
        customer_currency := UPPER(v_ment_ccy); fx_ok := false;   -- show mentor currency, no charge here
      ELSE
        key := sid::text; you0 := ROUND(v_base * v_fx, 2); you := ROUND(v_base * v_ppp * v_fx, 2);
        customer_currency := v_cust; fx_ok := true;
      END IF;
    END IF;
    RETURN NEXT;
  END LOOP;
END; $$;
GRANT EXECUTE ON FUNCTION display_service_prices(TEXT, UUID[]) TO anon, authenticated;

-- Migrated mentors keep their imported per-SESSION prices (services.set_price = the price the customer
-- pays) exactly as the old portal had them. We do NOT re-derive them from a per-hour rate here - an
-- earlier version of this block did, and it overwrote real prices (a 399 INR session became ~10). Each
-- mentor's hourly_rate is seeded at import (scripts/migrate_mentors.py) purely as the first-login popup
-- default; their sessions are re-priced only when they confirm/change that rate in onboarding or on the
-- Profile tab, via reprice_mentor_services(). For an already-migrated DB whose set_price was corrupted
-- by the old block, restore the real prices from the raw export once with:
--     python -m scripts.reimport_migrated_prices

-- ── Payment tables ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS customer_payments (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id                  UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  amount                      NUMERIC NOT NULL,   -- decimal, customer currency, actually charged
  currency                    TEXT NOT NULL,
  state                       TEXT NOT NULL DEFAULT 'created'
                                 CHECK (state IN ('created','authorized','captured','partially_refunded','refunded','failed')),
  provider                    TEXT DEFAULT 'razorpay',
  provider_order_id           TEXT,
  provider_payment_id         TEXT,
  provider_payload            JSONB,
  provider_error_code         TEXT,
  provider_error_description  TEXT,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_customer_payments_booking ON customer_payments(booking_id);
CREATE INDEX IF NOT EXISTS idx_customer_payments_provider_order ON customer_payments(provider_order_id);
ALTER TABLE customer_payments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_payments_read ON customer_payments;
-- Owner-scoped: a signed-in user sees only payments on their own bookings. The
-- backend uses the service-role key (bypasses RLS) so all server logic is
-- unaffected; this only closes direct anon/authenticated reads of others' rows.
CREATE POLICY customer_payments_read ON customer_payments FOR SELECT
  USING (booking_id IN (SELECT id FROM bookings WHERE candidate_id = auth.uid()));

-- mentor_payouts.amount is LOAD-BEARING: the PRE-FEE mentor-currency payout basis
-- (set_price x ppp_multiplier), distinct from net_amount_mentor_currency (POST-fee).
CREATE TABLE IF NOT EXISTS mentor_payouts (
  id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id                     UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  booking_id                    UUID NOT NULL UNIQUE REFERENCES bookings(id) ON DELETE CASCADE,
  amount                        NUMERIC,
  gross_amount                  NUMERIC,
  fee_pct                       NUMERIC,
  platform_fee_amount           NUMERIC,
  net_amount_customer_currency  NUMERIC,
  net_amount_mentor_currency    NUMERIC,
  exchange_rate_used            NUMERIC,
  customer_currency             TEXT,
  mentor_currency               TEXT,
  ppp_multiplier                NUMERIC,
  method                        TEXT,     -- 'manual' | NULL
  payout_reference              TEXT,
  payout_state                  TEXT NOT NULL DEFAULT 'pending'
                                  CHECK (payout_state IN ('pending','paid','void','blocked')),
  paid_date                     TIMESTAMPTZ,
  comments                      TEXT,
  created_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mentor_payouts_mentor ON mentor_payouts(mentor_id);
ALTER TABLE mentor_payouts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mentor_payouts_read ON mentor_payouts;
-- Owner-scoped: a mentor sees only their own payout rows. Service-role bypasses RLS.
CREATE POLICY mentor_payouts_read ON mentor_payouts FOR SELECT
  USING (mentor_id IN (SELECT id FROM mentors WHERE profile_id = auth.uid()));

-- One-time correction (BUG-097): the mentor's net earning must be the PPP-adjusted base MINUS the
-- commission. Backfill it only where it was never stored (legacy/manual rows), deriving net = amount
-- x (1 - commission %). NULL-guarded on purpose: reserve_booking already stores the correct net for new
-- bookings, and a referred session's net is overwritten by process_referral_commissions to the referral
-- split - re-running this must NOT clobber those correct values with a plain amount x (1 - fee).
UPDATE mentor_payouts
SET net_amount_mentor_currency = ROUND(amount * (1 - COALESCE(fee_pct, 0) / 100.0), 2)
WHERE amount IS NOT NULL AND net_amount_mentor_currency IS NULL;

-- Correction: payout_state used to be written by three different functions, one of which only ever
-- voided, so a booking brought back out of cancelled/no_show kept a real earning recorded as 'void'.
-- Re-derives the column from bookings.status using exactly the rule trg_sync_payout_state_fn now
-- enforces, so any database this script runs against is brought onto the invariant in both
-- directions. Idempotent: it matches only rows that actually disagree. 'paid'/'blocked' are admin
-- settlement decisions and are never touched.
UPDATE mentor_payouts po
SET payout_state = CASE WHEN b.status IN ('cancelled','no_show') THEN 'void' ELSE 'pending' END
FROM bookings b
WHERE b.id = po.booking_id
  AND po.payout_state NOT IN ('paid','blocked')
  AND po.payout_state IS DISTINCT FROM
      (CASE WHEN b.status IN ('cancelled','no_show') THEN 'void' ELSE 'pending' END);

CREATE TABLE IF NOT EXISTS payment_refunds (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id                  UUID NOT NULL REFERENCES customer_payments(id) ON DELETE CASCADE,
  booking_id                  UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  provider_refund_id          TEXT UNIQUE,
  amount_minor                INT NOT NULL,   -- MINOR units (paise/cents), matches Razorpay's refund API
  currency                    TEXT NOT NULL,
  status                      TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created','processed','failed')),
  provider_payload            JSONB,
  provider_error_code         TEXT,
  provider_error_description  TEXT,
  ledger_version              INT,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payment_refunds_booking ON payment_refunds(booking_id);

-- Razorpay webhook intake log. event_id is the dedup key (Razorpay retries
-- deliveries; processed_at set means "already handled, no-op on replay").
CREATE TABLE IF NOT EXISTS payment_events (
  event_id         TEXT PRIMARY KEY,
  type             TEXT,
  payload          JSONB,
  signature        TEXT,
  attempt_count    INT NOT NULL DEFAULT 0,
  last_attempt_at  TIMESTAMPTZ,
  next_retry_at    TIMESTAMPTZ,
  processed_at     TIMESTAMPTZ,
  error            TEXT,
  received_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payment_reconciliation_log (
  id                    BIGSERIAL PRIMARY KEY,
  kind                  TEXT NOT NULL,   -- 'mismatch' | 'fetch_failed'
  provider_payment_id   TEXT,
  booking_id            UUID REFERENCES bookings(id) ON DELETE CASCADE,
  detail                JSONB,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payment_reconciliation_log_booking ON payment_reconciliation_log(booking_id);

-- Booking columns the reserve/confirm flow needs.
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_hold_expires_at TIMESTAMPTZ;  -- 10-min reservation hold
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS customer_currency TEXT;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS notes TEXT;  -- customer's "what should my mentor prepare" note (BUG-113)

-- BUG-147: one-shot marker for the new-customer welcome email. /auth/sync runs on EVERY login, so
-- without this the welcome would go out again on each sign-in.
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS welcome_sent_at TIMESTAMPTZ;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS fx_customer_inr NUMERIC;  -- INR per 1 customer-currency unit
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS fx_mentor_inr   NUMERIC;  -- INR per 1 mentor-currency unit

INSERT INTO platform_settings (key, value, description) VALUES
  ('payments_enabled', 'false', 'false = mock instant-confirm booking; true = real Razorpay reserve->pay->confirm flow')
ON CONFLICT (key) DO NOTHING;

-- ── Money journal (read by refund_owed_minor) ────────────────────────────────
-- Included so refund_owed_minor has a table to read and forward-compat with the
-- payouts phase. Nothing in THIS migration writes 'refund' rows (cancel-refund
-- ledger wiring is deferred), so refund_owed_minor returns 0 today and
-- process_refunds is a safe no-op until then.
CREATE TABLE IF NOT EXISTS booking_ledger (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id             UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  party                  TEXT NOT NULL CHECK (party IN ('customer','mentor','platform')),
  kind                   TEXT NOT NULL CHECK (kind IN ('penalty','refund','credit','charge')),
  amount                 NUMERIC(10,2),
  pct                    INT,
  currency               TEXT,
  reason                 TEXT,
  normalized_inr_amount  NUMERIC,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_booking_ledger_booking ON booking_ledger(booking_id);
ALTER TABLE booking_ledger ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS booking_ledger_read ON booking_ledger;
-- Owner-scoped: the candidate or the mentor on the booking can read its money
-- journal; no public reads. Service-role bypasses RLS.
CREATE POLICY booking_ledger_read ON booking_ledger FOR SELECT
  USING (booking_id IN (
    SELECT id FROM bookings
    WHERE candidate_id = auth.uid()
       OR mentor_id IN (SELECT id FROM mentors WHERE profile_id = auth.uid())
  ));

CREATE OR REPLACE FUNCTION add_ledger(
  p_booking UUID, p_party TEXT, p_kind TEXT, p_amount NUMERIC, p_pct INT, p_reason TEXT
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_cust_ccy TEXT; v_ment_ccy TEXT; v_fxc NUMERIC; v_fxm NUMERIC; v_ccy TEXT; v_fx NUMERIC;
BEGIN
  SELECT COALESCE(mp.customer_currency, cp.currency, 'INR'),
         COALESCE(mp.mentor_currency, 'INR'),
         b.fx_customer_inr, b.fx_mentor_inr
    INTO v_cust_ccy, v_ment_ccy, v_fxc, v_fxm
  FROM bookings b
  LEFT JOIN LATERAL (
    SELECT customer_currency, mentor_currency FROM mentor_payouts
    WHERE booking_id = b.id ORDER BY created_at DESC LIMIT 1
  ) mp ON TRUE
  LEFT JOIN LATERAL (
    SELECT currency FROM customer_payments
    WHERE booking_id = b.id ORDER BY created_at DESC LIMIT 1
  ) cp ON TRUE
  WHERE b.id = p_booking;

  IF p_party = 'mentor' THEN v_ccy := v_ment_ccy; v_fx := COALESCE(v_fxm, 1);
  ELSE                       v_ccy := v_cust_ccy; v_fx := COALESCE(v_fxc, 1); END IF;

  INSERT INTO booking_ledger(booking_id, party, kind, amount, pct, currency, reason, normalized_inr_amount)
    VALUES (p_booking, p_party, p_kind, ROUND(COALESCE(p_amount, 0), 2), p_pct, v_ccy, p_reason,
            ROUND(COALESCE(p_amount, 0) * v_fx, 2));
END;
$$;



-- 2. The invariant becomes unwritable: services follow the mentor row, whatever writes it
CREATE OR REPLACE FUNCTION trg_reprice_services_fn() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.currency       IS DISTINCT FROM OLD.currency
  OR NEW.hourly_rate    IS DISTINCT FROM OLD.hourly_rate
  OR NEW.currency_rates IS DISTINCT FROM OLD.currency_rates THEN
    -- Same guards as the backfill above: nothing to derive from without a positive rate, and a
    -- migrated mentor still carrying the seeded base has not chosen it (their real per-session
    -- prices are the legacy ones, and they stay untouched until first-login onboarding).
    IF COALESCE(NEW.hourly_rate, 0) > 0
       AND COALESCE(NEW.currency, '') <> ''
       AND NOT (NEW.legacy_id IS NOT NULL AND COALESCE(NEW.needs_onboarding, FALSE)) THEN
      UPDATE services s
         SET set_price       = ROUND(NEW.hourly_rate * s.duration / 60.0, 2),
             set_currency    = UPPER(NEW.currency),
             set_offer_price = NULL,
             currency_prices = COALESCE((
               SELECT jsonb_agg(jsonb_build_object(
                        'currency',   UPPER(r->>'currency'),
                        'base_price', ROUND((r->>'hourly_rate')::numeric * s.duration / 60.0, 2)))
                 FROM jsonb_array_elements(COALESCE(NEW.currency_rates, '[]'::jsonb)) r
                WHERE UPPER(COALESCE(r->>'currency','')) <> UPPER(NEW.currency)
                  AND COALESCE(NULLIF(r->>'hourly_rate','')::numeric, 0) > 0
             ), '[]'::jsonb)
       WHERE s.mentor_id = NEW.id
         AND COALESCE(s.set_price, 0) > 0    -- free intro stays free
         AND COALESCE(s.duration, 0)  > 0;
    END IF;
  END IF;
  RETURN NEW;
END; $$;

DROP TRIGGER IF EXISTS trg_reprice_services ON mentors;
CREATE TRIGGER trg_reprice_services AFTER UPDATE ON mentors
  FOR EACH ROW EXECUTE FUNCTION trg_reprice_services_fn();



-- 3. Re-anchor services for mentors who CHOSE their base (this is what fixes hari-csk)
-- ── Re-anchor services onto the mentor's base currency ────────────────────────────────────────
-- INVARIANT: services.set_currency == mentors.currency. set_price is "base price in the mentor's
-- PRIMARY currency"; anything in another currency belongs in currency_prices, never in set_currency.
-- Two defects broke it: (a) the legacy import seeded hourly_rate as max() across per-hour figures in
-- MIXED currencies (an INR number always beats an AUD one) and never updated mentors.currency, and
-- (b) the changes_requested/rejected profile-edit path wrote the new currency to the live mentor row
-- without repricing services. Either way the mentor row says one currency and the services say
-- another, so PPP anchors to a currency the mentor does not price in and the customer is charged
-- against the wrong base.
-- This is a no-op once the invariant holds (the WHERE matches nothing), so it is safe on every run.
-- Mirrors reprice_mentor_services() exactly: free intros (set_price <= 0) stay free, set_price is
-- duration x hourly rate, any stale set_offer_price is cleared, and currency_prices is rebuilt from
-- mentors.currency_rates.
WITH rebased AS (
  SELECT s.id,
         ROUND(m.hourly_rate * s.duration / 60.0, 2) AS new_price,
         UPPER(m.currency)                           AS new_currency,
         COALESCE((
           SELECT jsonb_agg(jsonb_build_object(
                    'currency',   UPPER(r->>'currency'),
                    'base_price', ROUND((r->>'hourly_rate')::numeric * s.duration / 60.0, 2)))
             FROM jsonb_array_elements(COALESCE(m.currency_rates, '[]'::jsonb)) r
            WHERE UPPER(COALESCE(r->>'currency','')) <> UPPER(m.currency)
              AND COALESCE(NULLIF(r->>'hourly_rate','')::numeric, 0) > 0
         ), '[]'::jsonb)                             AS new_currency_prices
    FROM services s
    JOIN mentors  m ON m.id = s.mentor_id
   WHERE UPPER(COALESCE(s.set_currency, '')) <> UPPER(COALESCE(m.currency, ''))
     AND COALESCE(m.currency, '')   <> ''
     AND COALESCE(m.hourly_rate, 0) > 0    -- no base rate to anchor to: leave the row untouched
     AND COALESCE(s.set_price, 0)   > 0    -- free intro stays free
     AND COALESCE(s.duration, 0)    > 0
     -- Only anchor to a base the MENTOR actually chose. A migrated mentor who has not yet been
     -- through first-login onboarding still carries the seeded base, and that seed is the highest
     -- per-hour figure across their legacy sessions -- which for a mentor who priced different
     -- sessions in different currencies can be a number from one currency stored under another
     -- (e.g. a base of "USD 4400" that is really an INR-magnitude figure). Anchoring live prices to
     -- a seed like that multiplies the error instead of fixing it, so those rows are left alone and
     -- settle when the mentor confirms their rate at first login.
     AND NOT (m.legacy_id IS NOT NULL AND COALESCE(m.needs_onboarding, FALSE))
)
UPDATE services s
   SET set_price       = r.new_price,
       set_currency    = r.new_currency,
       set_offer_price = NULL,
       currency_prices = r.new_currency_prices
  FROM rebased r
 WHERE s.id = r.id;



-- 4. Legacy multi-currency mentors: base currency and additional currencies put right
-- ── Legacy multi-currency mentors: put the base currency and the extra currencies where they belong
-- These mentors priced each session individually in the old portal, sometimes in two currencies. The
-- import kept every service's own currency and seeded the base from the highest session, so a mentor
-- can hold a base in one currency and sessions in another. PPP then measures purchasing power against
-- a currency the price is not written in.
--
-- Two distinct faults, fixed in order:
--   1. The mentor has NO priced session in their base currency -> the BASE CURRENCY is the error.
--      Move it to the currency of their highest-value session, compared via the EUR pivot so an INR
--      face value cannot beat an AUD one. The rate itself is unchanged.
--   2. The mentor DOES have sessions in their base currency -> those are right, and the odd-currency
--      ones are an additional-currency price. Put the legacy amount into currency_prices (so buyers in
--      that currency pay EXACTLY what they pay today) and set the base price in the base currency.
--
-- Scope is deliberately only migrated mentors who have not yet been through first-login onboarding:
-- everyone else either chose their base or is covered by the re-anchor block.
-- Both statements are no-ops once clean, so the script stays safe to re-run.

-- 1. base currency follows the mentor's own priciest session
WITH ranked AS (
  SELECT m.id AS mentor_id,
         UPPER(s.set_currency) AS ccy,
         ROW_NUMBER() OVER (
           PARTITION BY m.id
           ORDER BY (s.set_price * 60.0 / s.duration) / COALESCE(fx.rate, 1) DESC
         ) AS rn
    FROM mentors m
    JOIN services s  ON s.mentor_id = m.id
    LEFT JOIN fx_rates fx ON fx.base = 'EUR' AND fx.quote = UPPER(s.set_currency)
   WHERE m.legacy_id IS NOT NULL
     AND COALESCE(m.needs_onboarding, FALSE)
     AND COALESCE(s.set_price, 0) > 0
     AND COALESCE(s.duration, 0)  > 0
     AND NOT EXISTS (
           SELECT 1 FROM services s2
            WHERE s2.mentor_id = m.id
              AND COALESCE(s2.set_price, 0) > 0
              AND UPPER(s2.set_currency) = UPPER(COALESCE(m.currency, ''))
         )
)
UPDATE mentors m
   SET currency = r.ccy
  FROM ranked r
 WHERE m.id = r.mentor_id AND r.rn = 1
   AND UPPER(COALESCE(m.currency, '')) <> r.ccy;

-- 2. the odd-currency sessions become additional-currency prices against the base
WITH moved AS (
  SELECT s.id,
         ROUND(m.hourly_rate * s.duration / 60.0, 2) AS base_price,
         UPPER(m.currency)                           AS base_ccy,
         COALESCE((SELECT jsonb_agg(e)
                     FROM jsonb_array_elements(COALESCE(s.currency_prices, '[]'::jsonb)) e
                    WHERE UPPER(e->>'currency') <> UPPER(s.set_currency)), '[]'::jsonb)
           || jsonb_build_array(jsonb_build_object(
                'currency',   UPPER(s.set_currency),
                'base_price', s.set_price))          AS new_cprices
    FROM services s
    JOIN mentors  m ON m.id = s.mentor_id
   WHERE m.legacy_id IS NOT NULL
     AND COALESCE(m.needs_onboarding, FALSE)
     AND UPPER(COALESCE(s.set_currency, '')) <> UPPER(COALESCE(m.currency, ''))
     AND COALESCE(m.currency, '')   <> ''
     AND COALESCE(m.hourly_rate, 0) > 0
     AND COALESCE(s.set_price, 0)   > 0
     AND COALESCE(s.duration, 0)    > 0
)
UPDATE services s
   SET set_price       = mv.base_price,
       set_currency    = mv.base_ccy,
       set_offer_price = NULL,
       currency_prices = mv.new_cprices
  FROM moved mv
 WHERE s.id = mv.id;



-- 5. Still pending from earlier work: chat storage helpers
CREATE OR REPLACE FUNCTION append_chat_messages(p_thread_id UUID, p_messages JSONB)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_added INT;
BEGIN
  INSERT INTO chat_messages(thread_id, role, content)
  SELECT p_thread_id, m->>'role', m->>'content'
    FROM jsonb_array_elements(p_messages) m
   WHERE m->>'content' IS NOT NULL AND m->>'content' <> ''
     AND m->>'role' IN ('user','assistant');
  GET DIAGNOSTICS v_added = ROW_COUNT;

  UPDATE chat_threads
     SET message_count = message_count + v_added,
         last_message_at = NOW(),
         updated_at = NOW()
   WHERE id = p_thread_id;

  RETURN v_added;
END;
$$;


CREATE OR REPLACE FUNCTION prune_chat_history(p_guest_days INT DEFAULT 90, p_user_days INT DEFAULT 365)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_threads INT; v_msgs INT; v_ckpt INT := 0;
BEGIN
  WITH doomed AS (
    SELECT id FROM chat_threads
     WHERE (user_id IS NULL     AND last_message_at < NOW() - MAKE_INTERVAL(days => p_guest_days))
        OR (user_id IS NOT NULL AND last_message_at < NOW() - MAKE_INTERVAL(days => p_user_days))
  ), del_msg AS (
    DELETE FROM chat_messages WHERE thread_id IN (SELECT id FROM doomed) RETURNING 1
  ), del_thread AS (
    DELETE FROM chat_threads WHERE id IN (SELECT id FROM doomed) RETURNING 1
  )
  SELECT (SELECT count(*) FROM del_msg), (SELECT count(*) FROM del_thread) INTO v_msgs, v_threads;

  -- LangGraph keys checkpoints by thread_id as TEXT; a thread we just deleted can never be resumed.
  BEGIN
    DELETE FROM checkpoints
     WHERE thread_id NOT IN (SELECT id::text FROM chat_threads);
    GET DIAGNOSTICS v_ckpt = ROW_COUNT;
  EXCEPTION WHEN undefined_table OR undefined_column THEN
    v_ckpt := 0;   -- checkpointer not initialised yet on a fresh database
  END;

  RETURN jsonb_build_object('threads', v_threads, 'messages', v_msgs, 'checkpoints', v_ckpt);
END;
$$;



-- 6. Still pending from earlier work: consent record (BUG-143)
CREATE TABLE IF NOT EXISTS consent_events (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  thread_id   UUID,
  kind        TEXT NOT NULL,           -- e.g. 'resume_ai_analysis'
  granted     BOOLEAN NOT NULL DEFAULT TRUE,
  policy_version TEXT,                 -- which wording they agreed to, so a later change is provable
  ip          TEXT,
  user_agent  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_consent_events_user ON consent_events(user_id, kind, created_at DESC);
