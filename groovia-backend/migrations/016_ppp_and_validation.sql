-- =============================================================================
-- 016 — PPP pricing table, IANA timezone validation, reschedule slot guard
-- Run this on any DB that already has 015_direct_booking_system.sql applied.
-- =============================================================================

-- ============================================================================
-- 1. IANA timezone name validation
-- ============================================================================
CREATE OR REPLACE FUNCTION is_valid_timezone(p_tz TEXT)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = p_tz);
$$;
GRANT EXECUTE ON FUNCTION is_valid_timezone(TEXT) TO anon, authenticated;

-- ============================================================================
-- 2. Fix mentee_accept_reschedule: validate slot is still bookable before
--    committing the reschedule. Without this a mentee could accept a time that
--    another candidate booked in the window between the mentor's offer and the
--    mentee's acceptance.
-- ============================================================================
CREATE OR REPLACE FUNCTION mentee_accept_reschedule(
  p_offer_id  UUID,
  p_slot_time TIMESTAMPTZ
) RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE o reschedule_offers; b bookings;
BEGIN
  SELECT * INTO o FROM reschedule_offers WHERE id = p_offer_id;
  IF NOT FOUND OR o.status <> 'pending' OR o.proposed_by <> 'mentor' THEN
    RAISE EXCEPTION 'This proposal is no longer open';
  END IF;
  IF p_slot_time < o.range_start OR p_slot_time >= o.range_end THEN
    RAISE EXCEPTION 'Please pick a time inside the proposed range';
  END IF;
  IF p_slot_time <= NOW() THEN RAISE EXCEPTION 'Please pick a future time'; END IF;
  SELECT * INTO b FROM bookings WHERE id = o.booking_id;
  IF NOT is_slot_available(b.mentor_id, b.service_id, p_slot_time) THEN
    RAISE EXCEPTION 'That time slot is no longer available — please pick another';
  END IF;
  UPDATE reschedule_offers
    SET status = 'mentee_selected', selected_time = p_slot_time
  WHERE id = p_offer_id;
  UPDATE bookings SET slot_time = p_slot_time, slot_end = NULL, status = 'rescheduled'
    WHERE id = o.booking_id RETURNING * INTO b;
  DELETE FROM booking_reminders WHERE booking_id = o.booking_id;
  RETURN b;
END;
$$;

-- ============================================================================
-- 3. PPP factors table
-- ============================================================================
CREATE TABLE IF NOT EXISTS ppp_factors (
  country_code  CHAR(2)      PRIMARY KEY,
  factor        NUMERIC(5,4) NOT NULL CHECK (factor > 0 AND factor <= 1),
  updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO ppp_factors (country_code, factor) VALUES
  ('IN', 0.2500), ('PK', 0.2000), ('BD', 0.1800), ('NP', 0.1800),
  ('LK', 0.2200), ('MM', 0.2000), ('PH', 0.3000), ('VN', 0.2800),
  ('ID', 0.3000), ('TH', 0.4000), ('CN', 0.4500),
  ('NG', 0.2200), ('GH', 0.2200), ('KE', 0.2500), ('ET', 0.1500),
  ('TZ', 0.2000), ('UG', 0.2000), ('CM', 0.2200), ('ZA', 0.3800),
  ('EG', 0.2800), ('MA', 0.3000),
  ('BR', 0.4000), ('MX', 0.4500), ('CO', 0.3500), ('PE', 0.3800),
  ('AR', 0.3500), ('EC', 0.4000), ('CL', 0.5000),
  ('UA', 0.3000), ('RO', 0.5000), ('TR', 0.3200)
ON CONFLICT (country_code) DO NOTHING;

ALTER TABLE ppp_factors ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ppp_factors_read ON ppp_factors;
CREATE POLICY ppp_factors_read ON ppp_factors FOR SELECT USING (TRUE);

-- ============================================================================
-- 4. get_ppp_factor RPC — returns 1.0 (full price) for unlisted countries
-- ============================================================================
CREATE OR REPLACE FUNCTION get_ppp_factor(p_country_code TEXT)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT factor FROM ppp_factors WHERE country_code = UPPER(p_country_code)),
    1.0
  );
$$;
GRANT EXECUTE ON FUNCTION get_ppp_factor(TEXT) TO anon, authenticated;
