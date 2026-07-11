-- ============================================================================
-- bugfixes_phone_hourly_rate.sql
-- Run this once in the Supabase SQL editor (staging + production).
-- Additive and idempotent (safe to re-run).
--
-- Adds the columns the July bug-fix batch needs:
--   * mentors.hourly_rate    - the mentor's hourly rate; per-session prices are
--                              prorated from it (rate x duration), editable per
--                              session. Currency reuses mentors.currency.
--   * bookings.candidate_phone - the mentee's phone (mandatory at booking, used
--                              for coordination), prefilled from their profile.
--   * mentors.smart_pricing  - single per-mentor "smart pricing" (PPP) toggle.
--                              When on, the mentor's sessions are priced by
--                              purchasing-power parity for the customer's country.
--
-- It also redefines service_create so each new service inherits is_ppp from the
-- mentor's smart_pricing toggle (one control instead of a per-service checkbox).
-- ============================================================================

ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS hourly_rate NUMERIC;
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS smart_pricing BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS candidate_phone TEXT;

-- service_create now derives is_ppp from the mentor's smart_pricing toggle (the
-- p_ppp argument is kept for signature compatibility but ignored). Toggling
-- smart_pricing re-syncs existing services from the API layer.
CREATE OR REPLACE FUNCTION service_create(
  p_mentor_id   UUID,
  p_title       TEXT,
  p_description TEXT    DEFAULT NULL,
  p_type        TEXT    DEFAULT 'video',
  p_duration    INTEGER DEFAULT 30,
  p_category    TEXT    DEFAULT NULL,
  p_set_price   NUMERIC DEFAULT 0,
  p_active      BOOLEAN DEFAULT TRUE,
  p_ppp         BOOLEAN DEFAULT FALSE
) RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_cur TEXT; v_pct NUMERIC; v_id UUID; v_ppp BOOLEAN;
BEGIN
  SELECT COALESCE(currency, 'USD'), COALESCE(smart_pricing, FALSE)
    INTO v_cur, v_ppp FROM mentors WHERE id = p_mentor_id;
  SELECT COALESCE(value::NUMERIC, 15) INTO v_pct
    FROM platform_settings WHERE key = 'immigroov_commission_pct';
  INSERT INTO services(mentor_id, title, description, type, duration, category,
                       is_ppp, is_active, set_price, set_currency, platform_fee)
  VALUES (p_mentor_id, p_title, p_description, p_type::service_type, p_duration, p_category,
          v_ppp, p_active, p_set_price, v_cur, ROUND(p_set_price * v_pct / 100, 2))
  RETURNING id INTO v_id;
  RETURN v_id;
END; $$;
