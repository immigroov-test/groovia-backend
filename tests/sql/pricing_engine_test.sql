-- =============================================================================
-- pgTAP acceptance tests for the pricing engine (ppp_factors/get_ppp_factor,
-- fx_rates/get_fx*, currency_for_country, compute_booking_price,
-- get_booking_quote, convert_prices).
--
-- Ported from immigroov/supabase/tests/pricing_test.sql — same scenarios,
-- same expected numbers (the business math is verbatim), adapted only for
-- this schema's UUID primary keys and its own mentor/service fixture shape
-- (no `users` table here; mentors.profile_id is nullable, so a mentor+service
-- fixture doesn't need a linked auth identity).
--
-- NOT covered here (deliberately): book_session_guest's one-time-use +
-- expiry guard on a pricing_quotes row. book_session_guest still has its
-- pre-quote signature — wiring it to consume a quote_id is Payments-phase
-- work, not Pricing+PPP. Re-add that assertion when that lands.
--
-- Run with:  psql "$SUPABASE_DB_URL" -f tests/sql/pricing_engine_test.sql
-- (or `supabase test db` if pgTAP is wired into the project's test runner).
-- Everything runs in one transaction and is rolled back by finish() + rollback.
--
-- NOTE: not executed in this sandbox — there is no live Postgres/Supabase
-- connection available here (no .env, no reachable DATABASE_URL). This file
-- is ready to run against a real project; someone with DB access needs to
-- run it and report the result before this module is marked verified in
-- MIGRATION_STATUS.md.
-- =============================================================================
begin;
select plan(21);

create extension if not exists pgtap;

-- --- Fixtures ----------------------------------------------------------------
-- Fresh FX (EUR pivot): EUR->USD=1.08, EUR->INR=90  =>  USD->INR = 90/1.08 ≈ 83.3333
insert into fx_rates(base, quote, rate, as_of, fetched_at) values
  ('EUR','USD',1.08,current_date, now()),
  ('EUR','INR',90.0,current_date, now())
on conflict (base,quote) do update set rate=excluded.rate, fetched_at=excluded.fetched_at;

-- Commission = 15%, PPP floor = 0.40 (must match immigroov's frozen values).
insert into platform_settings(key,value,description) values ('immigroov_commission_pct','15','test')
  on conflict (key) do update set value='15';
insert into platform_settings(key,value,description) values ('ppp_floor','0.40','test')
  on conflict (key) do update set value='0.40';

-- A mentor (no linked profile needed — profile_id is nullable) + two services
-- (USD PPP, INR non-PPP), both pre-approved so compute_booking_price accepts them.
insert into mentors(id, slug, display_name, status, is_active)
  values ('00000000-0000-4000-8000-000000000901', 'pricing-test-mentor', 'Pricing Test Mentor', 'approved', true)
  on conflict (id) do nothing;
insert into services(id, mentor_id, title, type, duration, is_ppp, is_active, status, set_price, set_currency)
  values ('00000000-0000-4000-8000-000000000902', '00000000-0000-4000-8000-000000000901',
          'USD PPP svc', 'video', 60, true, true, 'approved', 100, 'USD')
  on conflict (id) do nothing;
insert into services(id, mentor_id, title, type, duration, is_ppp, is_active, status, set_price, set_currency)
  values ('00000000-0000-4000-8000-000000000903', '00000000-0000-4000-8000-000000000901',
          'INR flat svc', 'video', 60, false, true, 'approved', 5000, 'INR')
  on conflict (id) do nothing;

-- --- Helpers / PPP + currency ------------------------------------------------
select is( get_ppp_factor('IN'), 0.40, 'India PPP is floored to 0.40 (seeded 0.30 is dominated)');
select is( get_ppp_factor('US'), 1.00, 'US PPP factor is 1.00 (no discount)');
select is( currency_for_country('IN'), 'INR', 'IN -> INR');
select is( currency_for_country('ZZ'), 'USD', 'unknown country -> USD fallback');

-- --- FX cross-rate -----------------------------------------------------------
select is( get_fx('USD','USD'), 1::numeric, 'same-currency FX is 1');
select ok( abs(get_fx('USD','INR') - 83.3333) < 0.01, 'USD->INR cross-rate ≈ 83.33 via EUR pivot');

-- --- Engine: USD mentor -> IN customer (PPP on) ------------------------------
select is( (compute_booking_price('00000000-0000-4000-8000-000000000902','IN')->>'ppp_multiplier')::numeric, 0.40, 'USD/IN: PPP 0.40 applied');
select is( (compute_booking_price('00000000-0000-4000-8000-000000000902','IN')->>'gross_customer')::numeric, 3333.33, 'USD/IN: gross = 100*0.40*83.3333');
select is( (compute_booking_price('00000000-0000-4000-8000-000000000902','IN')->>'fee_amount')::numeric, 500.00, 'USD/IN: 15% fee on gross');
select is( (compute_booking_price('00000000-0000-4000-8000-000000000902','IN')->>'net_customer')::numeric, 2833.33, 'USD/IN: net customer = gross - fee');
-- RATE-DIRECTION GUARD: net_mentor = net_customer / fx_mentor_customer ≈ 100*0.40*0.85 = 34.
-- A '*' instead of '/' here would yield ~236k and fail loudly.
select ok( (compute_booking_price('00000000-0000-4000-8000-000000000902','IN')->>'net_mentor')::numeric between 33.9 and 34.1,
           'USD/IN: net_mentor DIVIDES by fx (≈34, not multiplied)');

-- --- Engine: USD mentor -> US customer (PPP=1, FX=1) -------------------------
select is( (compute_booking_price('00000000-0000-4000-8000-000000000902','US')->>'gross_customer')::numeric, 100.00, 'USD/US: gross = set_price (no PPP, no FX)');

-- --- Engine: INR mentor -> IN customer (flat, no PPP) ------------------------
select is( (compute_booking_price('00000000-0000-4000-8000-000000000903','IN')->>'gross_customer')::numeric, 5000.00, 'INR/IN: gross = set_price');
select is( (compute_booking_price('00000000-0000-4000-8000-000000000903','IN')->>'net_mentor')::numeric, 4250.00, 'INR/IN: mentor nets 85% (fx=1)');

-- --- Quote issuance ----------------------------------------------------------
select ok( (get_booking_quote('00000000-0000-4000-8000-000000000902','IN')->>'quote_id') is not null, 'get_booking_quote returns a quote_id');
select ok( (get_booking_quote('00000000-0000-4000-8000-000000000902','IN')->>'pricing_hash') ~ '^[0-9a-f]{64}$', 'pricing_hash is a SHA-256 hex digest');

-- --- Display pricing (convert_prices) -----------------------------------------
select is( (select you from convert_prices('IN', '[{"key":"x","amount":100,"from":"USD","is_ppp":true}]'::jsonb)), 3333.33,
           'convert_prices: PPP+FX applied to display price, matches the engine');
select is( (select fx_ok from convert_prices('ZZ', '[{"key":"x","amount":10,"from":"ZZZ","is_ppp":false}]'::jsonb)), false,
           'convert_prices: unknown currency pair -> fx_ok=false (soft fallback, not an error)');

-- --- FX unavailable / stale --> hard failure (never rate=1) -------------------
select is( get_fx_or_null('USD','ZZZ'), null, 'missing FX pair -> NULL (no fallback)');
update fx_rates set fetched_at = now() - interval '2 days';  -- age all rates past 24h
select throws_like( $$ select get_fx('USD','INR') $$, '%FX_UNAVAILABLE%', 'stale FX (>24h) raises FX_UNAVAILABLE');
select throws_like( $$ select compute_booking_price('00000000-0000-4000-8000-000000000902','IN') $$, '%FX_UNAVAILABLE%',
                     'compute_booking_price aborts (never silently prices at rate=1) when FX is stale');

select * from finish();
rollback;
