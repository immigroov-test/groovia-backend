-- ============================================================================
-- payments_setup.sql  —  Pricing (PPP + FX) + Razorpay payment flow
-- ----------------------------------------------------------------------------
-- Additive migration: run AFTER testing_db_setup.sql / production_db_setup.sql
-- against the same Supabase database. Every statement is idempotent
-- (IF NOT EXISTS / CREATE OR REPLACE / ON CONFLICT DO NOTHING), so it is safe
-- to re-run.
--
-- Ported from the Gautham fork (which itself ports immigroov's pricing +
-- Razorpay engine), with two deliberate scope reductions:
--   1. Referrals/affiliate are STRIPPED — no referral_codes lookup in
--      reserve_booking, no resolve_referral_attribution in confirm.
--   2. Our existing lifecycle cancel_booking / no-show / reschedule RPCs are
--      NOT replaced. This migration only ADDS the reserve->pay->confirm path
--      plus the payout/refund scaffolding. Wiring refund ledger entries into
--      cancellation is deferred to the payouts phase.
--
-- Depends on objects already created by the base migration:
--   bookings, services, profiles, platform_settings, mentors,
--   is_slot_available(), is_valid_timezone(), booking_question_answers,
--   pgcrypto (extensions.digest).
-- ============================================================================

-- ── PPP factors (Purchasing Power Parity per-country price adjustment) ────────
-- factor has no upper bound: higher-cost-of-living countries are priced ABOVE
-- the US baseline (NO=1.05, CH=1.15). PPP only applies to services where the
-- mentor enabled is_ppp.
CREATE TABLE IF NOT EXISTS ppp_factors (
  country_code  CHAR(2)      PRIMARY KEY,
  factor        NUMERIC(5,4) NOT NULL CHECK (factor > 0),
  updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
-- Normalize the factor CHECK for older DBs that used factor<=1 (which would reject
-- above-baseline rows like NO=1.05, CH=1.15). Drop+re-add so the rule is always factor>0.
ALTER TABLE ppp_factors DROP CONSTRAINT IF EXISTS ppp_factors_factor_check;
ALTER TABLE ppp_factors ADD  CONSTRAINT ppp_factors_factor_check CHECK (factor > 0);

INSERT INTO ppp_factors (country_code, factor) VALUES
  ('US',1.00),('CA',0.92),('GB',0.94),('IE',0.95),('DE',0.90),('FR',0.92),('NL',0.93),
  ('ES',0.78),('IT',0.80),('PT',0.72),('SE',0.97),('NO',1.05),('CH',1.15),('PL',0.55),
  ('RO',0.50),('AU',0.95),('NZ',0.90),('JP',0.85),('KR',0.78),('SG',0.85),('HK',0.90),
  ('AE',0.72),('SA',0.60),('QA',0.70),('IN',0.30),('PK',0.29),('BD',0.32),('LK',0.32),
  ('NP',0.30),('ID',0.38),('PH',0.40),('VN',0.37),('TH',0.45),('MY',0.45),('CN',0.55),
  ('BR',0.45),('MX',0.50),('AR',0.40),('CO',0.42),('CL',0.55),('PE',0.45),('ZA',0.45),
  ('NG',0.40),('KE',0.42),('EG',0.28),('MA',0.45),('TR',0.40),('RU',0.42),('UA',0.35)
ON CONFLICT (country_code) DO UPDATE SET factor = excluded.factor;

ALTER TABLE ppp_factors ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ppp_factors_read ON ppp_factors;
CREATE POLICY ppp_factors_read ON ppp_factors FOR SELECT USING (TRUE);

INSERT INTO platform_settings (key, value, description) VALUES
  ('ppp_floor', '0.40', 'Minimum PPP factor (never price below this fraction of base)')
ON CONFLICT (key) DO NOTHING;

-- get_ppp_factor: countries NOT in ppp_factors get 1.0; countries IN get
-- GREATEST(their factor, ppp_floor). The floor dominates seeded-low rows
-- (IN=0.30 -> effective 0.40) by design.
CREATE OR REPLACE FUNCTION get_ppp_factor(p_country_code TEXT)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT GREATEST(
    COALESCE((SELECT factor FROM ppp_factors WHERE country_code = UPPER(p_country_code)), 1.0),
    COALESCE((SELECT value::numeric FROM platform_settings WHERE key = 'ppp_floor'), 0.40)
  );
$$;
GRANT EXECUTE ON FUNCTION get_ppp_factor(TEXT) TO anon, authenticated;

-- Relative PPP: the CUSTOMER's purchasing-power factor divided by the MENTOR's, so a mentor's local
-- price is re-based to the customer's country - a NL customer viewing an India-priced mentor pays
-- MORE (uplift), an India customer viewing a US mentor pays less. Raw factors drive the ratio; the
-- result is floored at ppp_floor so the discount side never drops below that fraction of the FX
-- price. Unknown/absent countries default to factor 1.0 (no adjustment).
CREATE OR REPLACE FUNCTION ppp_relative(p_customer TEXT, p_mentor TEXT)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT GREATEST(
    COALESCE((SELECT factor FROM ppp_factors WHERE country_code = UPPER(p_customer)), 1.0)
    / COALESCE((SELECT factor FROM ppp_factors WHERE country_code = UPPER(p_mentor)), 1.0),
    COALESCE((SELECT value::numeric FROM platform_settings WHERE key = 'ppp_floor'), 0.40)
  );
$$;
GRANT EXECUTE ON FUNCTION ppp_relative(TEXT, TEXT) TO anon, authenticated;

-- Per-mentor commission override: the % added to the mentor rate to get the customer price. When
-- set and not expired it WINS over the global immigroov_markup_pct; NULL = use the global.
ALTER TABLE mentors ADD COLUMN IF NOT EXISTS commission_pct        NUMERIC;
ALTER TABLE mentors ADD COLUMN IF NOT EXISTS commission_expires_at TIMESTAMPTZ;
ALTER TABLE mentors ADD COLUMN IF NOT EXISTS specializations       TEXT[] DEFAULT '{}';

CREATE OR REPLACE FUNCTION effective_markup_pct(p_mentor_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT commission_pct FROM mentors
       WHERE id = p_mentor_id AND commission_pct IS NOT NULL
         AND (commission_expires_at IS NULL OR commission_expires_at > NOW())),
    (SELECT value::numeric FROM platform_settings WHERE key = 'immigroov_markup_pct'),
    10
  );
$$;
GRANT EXECUTE ON FUNCTION effective_markup_pct(UUID) TO anon, authenticated;

-- ── Per-country platform fee + tax ───────────────────────────────────────────
-- Customer price = mentor rate x (1 + platform_fee%) x (1 + tax%). Both are keyed to the CUSTOMER's
-- country and admin-editable. The platform fee (default 5%) replaces the old global markup; a
-- per-mentor commission override still wins for that mentor. Tax defaults to 0 except where set
-- (India GST 18%). 'DEFAULT' is the fallback row used when a country has no explicit entry.
CREATE TABLE IF NOT EXISTS country_pricing (
  country_code     TEXT PRIMARY KEY,           -- ISO-2 (uppercase), or 'DEFAULT'
  platform_fee_pct NUMERIC NOT NULL DEFAULT 5,
  tax_pct          NUMERIC NOT NULL DEFAULT 0,
  tax_label        TEXT,                        -- e.g. 'GST', 'VAT'
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE country_pricing ENABLE ROW LEVEL SECURITY;
INSERT INTO country_pricing (country_code, platform_fee_pct, tax_pct, tax_label) VALUES
  ('DEFAULT', 5, 0,  NULL),
  ('IN',      5, 18, 'GST')
ON CONFLICT (country_code) DO NOTHING;

CREATE OR REPLACE FUNCTION country_platform_fee_pct(p_cc TEXT)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT platform_fee_pct FROM country_pricing WHERE country_code = UPPER(COALESCE(p_cc, ''))),
    (SELECT platform_fee_pct FROM country_pricing WHERE country_code = 'DEFAULT'),
    5);
$$;
GRANT EXECUTE ON FUNCTION country_platform_fee_pct(TEXT) TO anon, authenticated;

CREATE OR REPLACE FUNCTION country_tax_pct(p_cc TEXT)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT tax_pct FROM country_pricing WHERE country_code = UPPER(COALESCE(p_cc, ''))),
    (SELECT tax_pct FROM country_pricing WHERE country_code = 'DEFAULT'),
    0);
$$;
GRANT EXECUTE ON FUNCTION country_tax_pct(TEXT) TO anon, authenticated;

-- The platform fee for a booking: a live per-mentor override wins (special deals), else the
-- customer country's fee.
CREATE OR REPLACE FUNCTION effective_platform_fee_pct(p_mentor_id UUID, p_customer_country TEXT)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT commission_pct FROM mentors
       WHERE id = p_mentor_id AND commission_pct IS NOT NULL
         AND (commission_expires_at IS NULL OR commission_expires_at > NOW())),
    country_platform_fee_pct(p_customer_country));
$$;
GRANT EXECUTE ON FUNCTION effective_platform_fee_pct(UUID, TEXT) TO anon, authenticated;

-- ── FX rate infrastructure (EUR-pivot model, Frankfurter/ECB) ────────────────
-- One API call refreshes the whole table. get_fx() is the STRICT variant the
-- booking engine uses: it RAISES FX_UNAVAILABLE rather than silently falling
-- back to rate=1 (which would corrupt the ledger). get_fx_or_null() is the
-- soft variant for display-only pricing.
CREATE TABLE IF NOT EXISTS fx_rates (
  base        TEXT        NOT NULL,             -- always 'EUR'
  quote       TEXT        NOT NULL,             -- ISO currency code
  rate        NUMERIC     NOT NULL,             -- quote units per 1 base unit
  as_of       DATE,                             -- provider's published date
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (base, quote)
);

CREATE TABLE IF NOT EXISTS fx_refresh_log (
  id          BIGSERIAL PRIMARY KEY,
  provider    TEXT DEFAULT 'frankfurter',
  as_of       DATE,
  raw_json    JSONB,
  success     BOOLEAN,
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO platform_settings (key, value, description) VALUES
  ('fx_max_age_minutes', '1440', 'Max age (minutes) of an FX rate before bookings fail with FX_UNAVAILABLE (default 24h)')
ON CONFLICT (key) DO NOTHING;

-- Cross-rate via the EUR pivot. NULL if either leg is missing or stale.
CREATE OR REPLACE FUNCTION get_fx_or_null(p_from TEXT, p_to TEXT)
RETURNS NUMERIC LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_from TEXT := UPPER(COALESCE(p_from, ''));
  v_to   TEXT := UPPER(COALESCE(p_to, ''));
  v_max  INT := COALESCE((SELECT value::numeric FROM platform_settings WHERE key = 'fx_max_age_minutes'), 1440);
  r_from NUMERIC; r_to NUMERIC; f_from TIMESTAMPTZ; f_to TIMESTAMPTZ;
BEGIN
  IF v_from = '' OR v_to = '' THEN RETURN NULL; END IF;
  IF v_from = v_to THEN RETURN 1; END IF;

  IF v_from = 'EUR' THEN r_from := 1; f_from := NOW();
  ELSE SELECT rate, fetched_at INTO r_from, f_from FROM fx_rates WHERE base = 'EUR' AND quote = v_from; END IF;

  IF v_to = 'EUR' THEN r_to := 1; f_to := NOW();
  ELSE SELECT rate, fetched_at INTO r_to, f_to FROM fx_rates WHERE base = 'EUR' AND quote = v_to; END IF;

  IF r_from IS NULL OR r_to IS NULL THEN RETURN NULL; END IF;                              -- missing
  IF LEAST(f_from, f_to) < NOW() - MAKE_INTERVAL(mins => v_max) THEN RETURN NULL; END IF;  -- stale
  RETURN r_to / r_from;
END; $$;
GRANT EXECUTE ON FUNCTION get_fx_or_null(TEXT, TEXT) TO anon, authenticated;

-- Strict variant used by the booking engine: aborts rather than mis-price.
CREATE OR REPLACE FUNCTION get_fx(p_from TEXT, p_to TEXT)
RETURNS NUMERIC LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE v NUMERIC := get_fx_or_null(p_from, p_to);
BEGIN
  IF v IS NULL THEN
    RAISE EXCEPTION 'FX_UNAVAILABLE: no fresh exchange rate for %->%', UPPER(p_from), UPPER(p_to)
      USING errcode = 'P0001';
  END IF;
  RETURN v;
END; $$;
GRANT EXECUTE ON FUNCTION get_fx(TEXT, TEXT) TO anon, authenticated;

-- Country -> display currency. Only Frankfurter-supported currencies; else USD.
CREATE OR REPLACE FUNCTION currency_for_country(p_cc TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE SET search_path = public AS $$
  SELECT COALESCE(
    (CASE UPPER(COALESCE(p_cc, ''))
      WHEN 'US' THEN 'USD' WHEN 'GB' THEN 'GBP' WHEN 'IN' THEN 'INR'
      WHEN 'DE' THEN 'EUR' WHEN 'FR' THEN 'EUR' WHEN 'NL' THEN 'EUR' WHEN 'IE' THEN 'EUR'
      WHEN 'ES' THEN 'EUR' WHEN 'IT' THEN 'EUR' WHEN 'PT' THEN 'EUR'
      WHEN 'CA' THEN 'CAD' WHEN 'AU' THEN 'AUD' WHEN 'NZ' THEN 'NZD' WHEN 'SG' THEN 'SGD'
      WHEN 'HK' THEN 'HKD' WHEN 'JP' THEN 'JPY' WHEN 'KR' THEN 'KRW' WHEN 'CN' THEN 'CNY'
      WHEN 'MX' THEN 'MXN' WHEN 'BR' THEN 'BRL' WHEN 'ZA' THEN 'ZAR' WHEN 'CH' THEN 'CHF'
      WHEN 'SE' THEN 'SEK' WHEN 'NO' THEN 'NOK' WHEN 'DK' THEN 'DKK' WHEN 'PL' THEN 'PLN'
      WHEN 'RO' THEN 'RON' WHEN 'CZ' THEN 'CZK' WHEN 'HU' THEN 'HUF' WHEN 'BG' THEN 'BGN'
      WHEN 'IL' THEN 'ILS' WHEN 'ID' THEN 'IDR' WHEN 'PH' THEN 'PHP' WHEN 'MY' THEN 'MYR'
      WHEN 'TH' THEN 'THB' WHEN 'TR' THEN 'TRY'
      ELSE NULL END), 'USD');
$$;
GRANT EXECUTE ON FUNCTION currency_for_country(TEXT) TO anon, authenticated;

-- ── Pricing engine ───────────────────────────────────────────────────────────
-- Binding price quotes. A quote is a 10-minute offer; booking commits it verbatim.
CREATE TABLE IF NOT EXISTS pricing_quotes (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id        UUID NOT NULL,
  mentor_id         UUID NOT NULL,
  customer_country  TEXT,
  customer_currency TEXT,
  pricing_version   INT,
  ppp_version       INT,
  fx_provider       TEXT,
  snapshot          JSONB NOT NULL,    -- full BookingPrice record (the contract)
  pricing_hash      TEXT NOT NULL,     -- SHA-256 of canonical snapshot JSON
  used              BOOLEAN NOT NULL DEFAULT FALSE,
  booking_id        UUID,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at        TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '10 minutes'
);
CREATE INDEX IF NOT EXISTS pricing_quotes_expires_idx ON pricing_quotes(expires_at);

-- Immutable per-booking pricing snapshot (1:1 with bookings).
CREATE TABLE IF NOT EXISTS booking_pricing (
  booking_id         UUID PRIMARY KEY REFERENCES bookings(id) ON DELETE CASCADE,
  pricing_version    INT NOT NULL,
  ppp_version        INT,
  fx_provider        TEXT,
  mentor_currency    TEXT,
  customer_currency  TEXT,
  set_price          NUMERIC,
  ppp_multiplier     NUMERIC,
  fx_mentor_customer NUMERIC,
  fx_customer_inr    NUMERIC,
  fx_mentor_inr      NUMERIC,
  gross_customer     NUMERIC,
  fee_pct            NUMERIC,
  fee_amount         NUMERIC,
  net_customer       NUMERIC,
  net_mentor         NUMERIC,
  calculated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The single pricing engine. Returns the canonical BookingPrice as jsonb.
-- Raises FX_UNAVAILABLE if rates are missing/stale.
-- NOTE: services.platform_fee is an ABSOLUTE commission amount in the mentor's
-- currency (e.g. set_price 2500 -> platform_fee 375 = 15%), converted here to a
-- percentage so it applies to the PPP-adjusted, FX-converted customer gross.
-- Falls back to the admin global pct (immigroov_commission_pct).
-- Multi-currency columns (v2) - added here too so this migration is safe to run standalone.
ALTER TABLE services ADD COLUMN IF NOT EXISTS set_offer_price NUMERIC(10,2);
ALTER TABLE services ADD COLUMN IF NOT EXISTS currency_prices JSONB NOT NULL DEFAULT '[]';
CREATE OR REPLACE FUNCTION compute_booking_price(p_service_id UUID, p_customer_country TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_pricing_version CONSTANT INT := 3;   -- v3: customer price = mentor rate + global markup (mentor rate hidden)
  v_ppp_version     CONSTANT INT := 1;
  v_provider        CONSTANT TEXT := 'frankfurter';
  v_mentor_id UUID; v_set NUMERIC; v_set_offer NUMERIC; v_ment_ccy TEXT; v_is_ppp BOOLEAN;
  v_prices JSONB; v_fee_pct NUMERIC; v_tax_pct NUMERIC; v_mentor_country TEXT;
  v_cust_ccy TEXT; v_ppp NUMERIC := 1; v_source TEXT; v_explicit NUMERIC; v_base NUMERIC; v_mentor_amt NUMERIC;
  v_fx_mc NUMERIC; v_fx_c_inr NUMERIC; v_fx_m_inr NUMERIC;
  v_gross NUMERIC; v_fee NUMERIC; v_subtotal NUMERIC; v_tax_amt NUMERIC; v_net_cust NUMERIC; v_net_mentor NUMERIC;
BEGIN
  SELECT s.mentor_id, s.set_price, s.set_offer_price, COALESCE(s.set_currency, 'USD'), s.is_ppp,
         COALESCE(s.currency_prices, '[]'::jsonb), m.country
    INTO v_mentor_id, v_set, v_set_offer, v_ment_ccy, v_is_ppp, v_prices, v_mentor_country
  FROM services s JOIN mentors m ON m.id = s.mentor_id
  WHERE s.id = p_service_id AND s.is_active AND s.status = 'approved';
  IF v_set IS NULL THEN RAISE EXCEPTION 'Service not available' USING errcode = 'P0001'; END IF;

  -- Platform fee + tax by the CUSTOMER's country (a live per-mentor override wins for the fee).
  -- Customer price = mentor amount x (1 + fee%) x (1 + tax%).
  v_fee_pct  := effective_platform_fee_pct(v_mentor_id, p_customer_country);
  v_tax_pct  := country_tax_pct(p_customer_country);
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
    v_ppp        := CASE WHEN v_is_ppp THEN ppp_relative(p_customer_country, v_mentor_country) ELSE 1 END;
    v_fx_mc      := get_fx(v_ment_ccy, v_cust_ccy);   -- hard-fails FX_UNAVAILABLE (the charge needs it)
    v_base       := COALESCE(v_set_offer, v_set);
    v_mentor_amt := ROUND(v_base * v_ppp * v_fx_mc, 2);
    v_net_mentor := v_base;                            -- mentor's filled rate (owner pays this manually)
  END IF;

  -- Customer pays: mentor rate + platform fee, then tax on that total. The mentor rate itself
  -- (set_price / mentor_amount / net_mentor) is for admin + owner only, never shown to the customer.
  -- Gross is a SINGLE combined round (fee x tax together), identical to convert_prices, so the
  -- displayed price and the charged price never drift by a rounding cent. The subtotal + fee + tax
  -- breakdown is derived from it for admin/receipt display.
  v_gross    := ROUND(v_mentor_amt * (1 + v_fee_pct / 100.0) * (1 + v_tax_pct / 100.0), 2);
  v_subtotal := ROUND(v_mentor_amt * (1 + v_fee_pct / 100.0), 2);   -- mentor rate + platform fee
  v_fee      := ROUND(v_subtotal - v_mentor_amt, 2);                -- platform fee amount
  v_tax_amt  := ROUND(v_gross - v_subtotal, 2);                     -- tax amount
  v_net_cust := v_mentor_amt;

  RETURN jsonb_build_object(
    'pricing_version', v_pricing_version, 'ppp_version', v_ppp_version, 'fx_provider', v_provider,
    'service_id', p_service_id, 'mentor_id', v_mentor_id, 'customer_country', UPPER(COALESCE(p_customer_country, '')),
    'mentor_currency', v_ment_ccy, 'customer_currency', v_cust_ccy,
    'pricing_source', v_source, 'set_price', v_set, 'ppp_multiplier', v_ppp,
    'markup_pct', v_fee_pct, 'mentor_amount', v_mentor_amt,
    'fx_mentor_customer', v_fx_mc, 'fx_customer_inr', v_fx_c_inr, 'fx_mentor_inr', v_fx_m_inr,
    'gross_customer', v_gross, 'fee_pct', v_fee_pct, 'fee_amount', v_fee,
    'subtotal', v_subtotal, 'tax_pct', v_tax_pct, 'tax_amount', v_tax_amt,
    'net_customer', v_net_cust, 'net_mentor', v_net_mentor);
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
    v_ppp := CASE WHEN v_ppp_on THEN ppp_relative(p_customer_country, it->>'mentor_country') ELSE 1 END;
    -- Same platform fee + tax the charge uses (per-mentor override or country fee, then country tax),
    -- so the displayed price equals what's charged.
    v_mk  := (1 + effective_platform_fee_pct(NULLIF(it->>'mentor_id','')::uuid, p_customer_country) / 100.0)
             * (1 + country_tax_pct(p_customer_country) / 100.0);
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

-- ── Payment state machine + payout admin ops ─────────────────────────────────
-- Legal customer_payments.state transitions. Called by the webhook handler and
-- confirm_booking_payment — never by anon/authenticated directly.
CREATE OR REPLACE FUNCTION set_payment_state(p_payment_id UUID, p_new_state TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_state TEXT;
BEGIN
  SELECT state INTO v_state FROM customer_payments WHERE id = p_payment_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Payment % not found', p_payment_id; END IF;
  IF NOT (
    p_new_state = v_state                                                            -- idempotent
    OR p_new_state = 'failed'                                                         -- any -> failed
    OR (v_state = 'created' AND p_new_state IN ('authorized','captured'))
    OR (v_state = 'authorized' AND p_new_state = 'captured')
    OR (v_state = 'captured' AND p_new_state IN ('partially_refunded','refunded'))
    OR (v_state = 'partially_refunded' AND p_new_state IN ('partially_refunded','refunded'))
  ) THEN
    RAISE EXCEPTION 'Illegal payment state transition % -> %', v_state, p_new_state;
  END IF;
  UPDATE customer_payments SET state = p_new_state WHERE id = p_payment_id;
END;
$$;
REVOKE ALL ON FUNCTION set_payment_state(UUID, TEXT) FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION set_provider_order(p_booking_id UUID, p_order_id TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE customer_payments SET provider = 'razorpay', provider_order_id = p_order_id
    WHERE booking_id = p_booking_id AND state = 'created';
END;
$$;
REVOKE ALL ON FUNCTION set_provider_order(UUID, TEXT) FROM PUBLIC, anon, authenticated;

-- How much (in MINOR units) is still owed on a booking: ledger 'refund' rows
-- minus refunds already issued. Never refund twice for the same ledger entry.
CREATE OR REPLACE FUNCTION refund_owed_minor(p_booking_id UUID)
RETURNS INT LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE v_owed NUMERIC; v_issued INT;
BEGIN
  SELECT COALESCE(SUM(amount), 0) INTO v_owed
    FROM booking_ledger WHERE booking_id = p_booking_id AND party = 'customer' AND kind = 'refund';
  SELECT COALESCE(SUM(amount_minor), 0) INTO v_issued
    FROM payment_refunds WHERE booking_id = p_booking_id AND status IN ('created','processed');
  RETURN GREATEST(0, ROUND(v_owed * 100)::int - v_issued);
END;
$$;
REVOKE ALL ON FUNCTION refund_owed_minor(UUID) FROM PUBLIC, anon, authenticated;

-- Admin marks a payout as actually paid (manual transfer). Only a completed
-- booking's payout, and only if not already void/blocked. (Payouts phase.)
CREATE OR REPLACE FUNCTION mark_payout_paid(p_booking_id UUID, p_reference TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_status TEXT;
BEGIN
  SELECT status INTO v_status FROM bookings WHERE id = p_booking_id;
  IF v_status IS DISTINCT FROM 'completed' THEN
    RAISE EXCEPTION 'Booking % is not completed — cannot mark payout paid', p_booking_id;
  END IF;
  UPDATE mentor_payouts SET payout_state = 'paid', paid_date = NOW(), payout_reference = p_reference
    WHERE booking_id = p_booking_id AND payout_state NOT IN ('void','blocked');
END;
$$;
GRANT EXECUTE ON FUNCTION mark_payout_paid(UUID, TEXT) TO authenticated;

CREATE OR REPLACE FUNCTION set_payout_blocked(p_booking_id UUID, p_reason TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE mentor_payouts
     SET payout_state = 'blocked',
         comments = COALESCE(comments || E'\n', '') || COALESCE(p_reason, '')
   WHERE booking_id = p_booking_id AND payout_state <> 'paid';
END;
$$;
GRANT EXECUTE ON FUNCTION set_payout_blocked(UUID, TEXT) TO authenticated;

-- A cancelled/no-show booking's payout is automatically voided (unless already
-- paid or blocked). Idempotent: only fires when status actually CHANGES.
CREATE OR REPLACE FUNCTION trg_void_payout_fn()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF NEW.status IN ('cancelled','no_show') AND NEW.status IS DISTINCT FROM OLD.status THEN
    UPDATE mentor_payouts SET payout_state = 'void'
      WHERE booking_id = NEW.id AND payout_state NOT IN ('paid','blocked');
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_void_payout ON bookings;
CREATE TRIGGER trg_void_payout AFTER UPDATE OF status ON bookings
  FOR EACH ROW EXECUTE FUNCTION trg_void_payout_fn();

-- Whitelisted public read of platform_settings — only 'payments_enabled'.
CREATE OR REPLACE FUNCTION public_setting(p_key TEXT)
RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_key <> 'payments_enabled' THEN
    RAISE EXCEPTION 'Setting % is not publicly readable', p_key;
  END IF;
  RETURN (SELECT value FROM platform_settings WHERE key = p_key);
END;
$$;
GRANT EXECUTE ON FUNCTION public_setting(TEXT) TO anon, authenticated;

-- Cheap read-only status poll for checkout pages (webhook-independent UX).
CREATE OR REPLACE FUNCTION booking_status(p_booking_id UUID)
RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT status::text FROM bookings WHERE id = p_booking_id;
$$;
GRANT EXECUTE ON FUNCTION booking_status(UUID) TO anon, authenticated;

-- ── Quote-based booking creation (reserve -> pay -> confirm) ──────────────────
-- REFERRAL-STRIPPED port of immigroov's reserve_booking. Every paid booking
-- starts 'pending' (a 10-minute payment hold) and only becomes 'confirmed' via
-- confirm_booking_payment. The existing book_session RPC (direct-to-confirmed)
-- is left in place for the free/mock path and is unaffected.
--
-- Identity: if no candidate_id is given, look up profiles by email but do NOT
-- create one (profiles is Supabase-Auth-owned). A guest who never signs up has
-- candidate_id = NULL; their identity lives in candidate_email/candidate_name.
CREATE OR REPLACE FUNCTION reserve_booking(
  p_quote_id UUID, p_mentor_id UUID, p_service_id UUID, p_slot_time TIMESTAMPTZ,
  p_email TEXT, p_name TEXT DEFAULT NULL, p_timezone TEXT DEFAULT 'UTC',
  p_answers JSONB DEFAULT '[]', p_specific_availability_id UUID DEFAULT NULL,
  p_candidate_id UUID DEFAULT NULL
) RETURNS TABLE(booking_id UUID, amount NUMERIC, currency TEXT, hold_expires_at TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  q pricing_quotes%ROWTYPE;
  s JSONB;
  v_booking_id UUID;
  v_hold_expires_at TIMESTAMPTZ := NOW() + INTERVAL '10 minutes';
  v_gross NUMERIC; v_fee_amount NUMERIC; v_net_customer NUMERIC;
  v_timezone TEXT := CASE WHEN is_valid_timezone(p_timezone) THEN p_timezone ELSE 'UTC' END;
BEGIN
  IF p_email IS NULL OR POSITION('@' IN p_email) = 0 THEN
    RAISE EXCEPTION 'A valid email is required';
  END IF;

  -- Load & lock the quote; validate it is a fresh, unused, matching offer.
  SELECT * INTO q FROM pricing_quotes WHERE id = p_quote_id FOR UPDATE;
  IF q.id IS NULL THEN RAISE EXCEPTION 'QUOTE_EXPIRED: quote not found' USING errcode = 'P0001'; END IF;
  IF q.used THEN RAISE EXCEPTION 'QUOTE_EXPIRED: quote already used' USING errcode = 'P0001'; END IF;
  IF q.expires_at < NOW() THEN
    RAISE EXCEPTION 'QUOTE_EXPIRED: quote has expired — please refresh the price' USING errcode = 'P0001';
  END IF;
  IF q.service_id <> p_service_id OR q.mentor_id <> p_mentor_id THEN
    RAISE EXCEPTION 'QUOTE_EXPIRED: quote does not match this booking' USING errcode = 'P0001';
  END IF;

  IF NOT is_slot_available(p_mentor_id, p_service_id, p_slot_time) THEN
    RAISE EXCEPTION 'That time is not available — please choose another slot';
  END IF;

  IF p_candidate_id IS NULL THEN
    SELECT id INTO p_candidate_id FROM profiles WHERE LOWER(email) = LOWER(p_email);
  END IF;

  s := q.snapshot;  -- the binding contract; committed verbatim, no recompute
  v_gross := (s->>'gross_customer')::numeric;
  v_fee_amount := (s->>'fee_amount')::numeric;
  v_net_customer := (s->>'net_customer')::numeric;

  BEGIN
    INSERT INTO bookings(
      mentor_id, candidate_id, candidate_email, candidate_name,
      service_id, slot_time, status, attendee_timezone,
      specific_availability_id, source,
      customer_currency, fx_customer_inr, fx_mentor_inr, payment_hold_expires_at
    ) VALUES (
      p_mentor_id, p_candidate_id, LOWER(p_email), p_name,
      p_service_id, p_slot_time, 'pending', v_timezone,
      p_specific_availability_id, 'direct',
      s->>'customer_currency', NULLIF((s->>'fx_customer_inr')::numeric, 0), NULLIF((s->>'fx_mentor_inr')::numeric, 0),
      v_hold_expires_at
    ) RETURNING id INTO v_booking_id;
  EXCEPTION WHEN exclusion_violation OR unique_violation THEN
    RAISE EXCEPTION 'That time was just taken — please choose another slot' USING errcode = 'P0001';
  END;

  INSERT INTO booking_question_answers(booking_id, question_id, answer_text)
  SELECT v_booking_id, (a->>'question_id')::UUID, a->>'answer_text'
  FROM jsonb_array_elements(COALESCE(p_answers, '[]'::JSONB)) a
  WHERE a ? 'question_id' AND (a->>'answer_text') IS NOT NULL AND (a->>'answer_text') <> '';

  INSERT INTO customer_payments(booking_id, amount, currency, state)
    VALUES (v_booking_id, v_gross, UPPER(s->>'customer_currency'), 'created');

  INSERT INTO mentor_payouts(
      mentor_id, booking_id, amount, gross_amount, fee_pct, platform_fee_amount,
      net_amount_customer_currency, net_amount_mentor_currency, exchange_rate_used,
      customer_currency, mentor_currency, ppp_multiplier, payout_state
    ) VALUES (
      p_mentor_id, v_booking_id,
      ROUND((s->>'set_price')::numeric * (s->>'ppp_multiplier')::numeric, 2),
      v_gross, (s->>'fee_pct')::numeric, v_fee_amount,
      v_net_customer, (s->>'net_mentor')::numeric, (s->>'fx_mentor_customer')::numeric,
      UPPER(s->>'customer_currency'), s->>'mentor_currency', (s->>'ppp_multiplier')::numeric, 'pending'
    );

  INSERT INTO booking_pricing(
      booking_id, pricing_version, ppp_version, fx_provider,
      mentor_currency, customer_currency, set_price, ppp_multiplier,
      fx_mentor_customer, fx_customer_inr, fx_mentor_inr,
      gross_customer, fee_pct, fee_amount, net_customer, net_mentor
    ) VALUES (
      v_booking_id, (s->>'pricing_version')::int, (s->>'ppp_version')::int, s->>'fx_provider',
      s->>'mentor_currency', s->>'customer_currency', (s->>'set_price')::numeric, (s->>'ppp_multiplier')::numeric,
      (s->>'fx_mentor_customer')::numeric, (s->>'fx_customer_inr')::numeric, (s->>'fx_mentor_inr')::numeric,
      v_gross, (s->>'fee_pct')::numeric, v_fee_amount, v_net_customer, (s->>'net_mentor')::numeric
    );

  UPDATE pricing_quotes SET used = TRUE, booking_id = v_booking_id WHERE id = p_quote_id;  -- one-time use

  RETURN QUERY SELECT v_booking_id, v_gross, UPPER(s->>'customer_currency'), v_hold_expires_at;
END;
$$;
GRANT EXECUTE ON FUNCTION reserve_booking(UUID, UUID, UUID, TIMESTAMPTZ, TEXT, TEXT, TEXT, JSONB, UUID, UUID) TO anon, authenticated;

-- Finalize a payment hold into a confirmed booking. Idempotent (a webhook retry
-- or duplicate confirm is a no-op once confirmed). HOLD_EXPIRED means the caller
-- must issue a refund (money captured for a slot we can no longer honour), not
-- retry. Referral attribution is NOT resolved here (referrals out of scope).
CREATE OR REPLACE FUNCTION confirm_booking_payment(p_booking_id UUID, p_provider_ref TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking % not found', p_booking_id; END IF;
  IF b.status = 'confirmed' THEN RETURN 'already_confirmed'; END IF;
  IF b.status <> 'pending' OR b.payment_hold_expires_at IS NULL THEN
    RAISE EXCEPTION 'HOLD_EXPIRED: booking % is no longer awaiting payment (status %)', p_booking_id, b.status
      USING errcode = 'P0001';
  END IF;

  UPDATE bookings SET status = 'confirmed', payment_hold_expires_at = NULL WHERE id = p_booking_id;
  UPDATE customer_payments SET state = 'captured', provider = 'razorpay', provider_payment_id = p_provider_ref
    WHERE booking_id = p_booking_id AND state = 'created';
  UPDATE mentor_payouts SET method = COALESCE(method, 'manual'), payout_state = COALESCE(payout_state, 'pending')
    WHERE booking_id = p_booking_id;

  RETURN 'confirmed';
END;
$$;
REVOKE ALL ON FUNCTION confirm_booking_payment(UUID, TEXT) FROM PUBLIC, anon, authenticated;

-- Janitor for reserve_booking's 10-min hold. Without something calling this, a
-- 'pending' hold whose payer never completes checkout occupies the slot forever
-- and the orphaned customer_payments row sits at 'created'. Service-role only.
CREATE OR REPLACE FUNCTION expire_stale_holds()
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_count INT;
BEGIN
  WITH expired AS (
    UPDATE bookings SET status = 'cancelled', payment_hold_expires_at = NULL
     WHERE status = 'pending' AND payment_hold_expires_at IS NOT NULL AND payment_hold_expires_at < NOW()
     RETURNING id
  )
  UPDATE customer_payments cp SET state = 'failed'
   FROM expired e WHERE cp.booking_id = e.id AND cp.state = 'created';
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;
REVOKE ALL ON FUNCTION expire_stale_holds() FROM PUBLIC, anon, authenticated;

-- ── Dispatcher lease lock + job self-gating (Render Cron Job has no mutex) ────
-- TTL-based lease row (not pg_advisory_lock — supabase-py's stateless RPC calls
-- can't hold a session-level lock across two calls). A UNIQUE collision on
-- INSERT is what actually arbitrates two concurrent acquirers.
CREATE TABLE IF NOT EXISTS dispatcher_locks (
  lock_name   TEXT PRIMARY KEY,
  locked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at  TIMESTAMPTZ NOT NULL
);

CREATE OR REPLACE FUNCTION try_acquire_dispatcher_lock(p_lock_name TEXT, p_ttl_seconds INT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE dispatcher_locks
    SET locked_at = NOW(), expires_at = NOW() + (p_ttl_seconds || ' seconds')::interval
    WHERE lock_name = p_lock_name AND expires_at < NOW();
  IF FOUND THEN RETURN TRUE; END IF;
  BEGIN
    INSERT INTO dispatcher_locks(lock_name, locked_at, expires_at)
      VALUES (p_lock_name, NOW(), NOW() + (p_ttl_seconds || ' seconds')::interval);
    RETURN TRUE;
  EXCEPTION WHEN unique_violation THEN
    RETURN FALSE;
  END;
END;
$$;
GRANT EXECUTE ON FUNCTION try_acquire_dispatcher_lock(TEXT, INT) TO authenticated;

CREATE OR REPLACE FUNCTION release_dispatcher_lock(p_lock_name TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  DELETE FROM dispatcher_locks WHERE lock_name = p_lock_name;
END;
$$;
GRANT EXECUTE ON FUNCTION release_dispatcher_lock(TEXT) TO authenticated;

-- Last-run tracker for dispatcher jobs that run less often than every tick
-- (reconcile_payments: 24h). Read/write directly via supabase-py.
CREATE TABLE IF NOT EXISTS job_run_history (
  job_name    TEXT PRIMARY KEY,
  last_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ── Auto-expire abandoned payment holds (pg_cron; free, no external dispatcher for
-- THIS job). Releases a slot whose 10-minute payment hold was never paid, so it can
-- be rebooked. Guarded so the setup still succeeds where pg_cron isn't enabled.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'expire-stale-holds') THEN
      PERFORM cron.unschedule('expire-stale-holds');
    END IF;
    PERFORM cron.schedule('expire-stale-holds', '*/5 * * * *', 'SELECT expire_stale_holds()');
  ELSE
    RAISE NOTICE 'pg_cron not enabled - skipping expire-stale-holds schedule. Enable pg_cron (Database -> Extensions) and re-run this block.';
  END IF;
END $$;
