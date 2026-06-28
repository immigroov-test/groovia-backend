-- =============================================================================
-- 015_direct_booking_system.sql
-- Direct slot-booking: services, weekly/specific availability,
-- double-booking guard, reschedule negotiation, service questions.
--
-- All FKs use UUID to match our existing profiles / mentors / bookings schema.
-- Adapted from immigroov-main (the other dev's Supabase-edge-function project).
-- Run this ONCE in Supabase SQL Editor.
-- =============================================================================

-- ============================================================================
-- Extensions
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- GiST exclusion for double-booking guard

-- ============================================================================
-- Enum types
-- ============================================================================
DO $$ BEGIN
  CREATE TYPE service_type AS ENUM ('video', 'dm');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE question_type AS ENUM ('text', 'multiple_choice', 'yes_no');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Add 'pending' value to existing booking_status enum (direct booking before payment)
DO $$ BEGIN
  ALTER TYPE booking_status ADD VALUE IF NOT EXISTS 'pending' BEFORE 'pending_payment';
EXCEPTION WHEN others THEN NULL; END $$;

-- ============================================================================
-- Scheduling config on mentors (booking rules + display fields)
-- ============================================================================
ALTER TABLE mentors
  ADD COLUMN IF NOT EXISTS app_timezone            TEXT DEFAULT 'UTC',
  ADD COLUMN IF NOT EXISTS app_buffertime          INTERVAL DEFAULT '15 minutes',
  ADD COLUMN IF NOT EXISTS app_minimum_notice      INTERVAL DEFAULT '2 hours',
  ADD COLUMN IF NOT EXISTS app_booking_window      INTERVAL DEFAULT '30 days',
  ADD COLUMN IF NOT EXISTS cancel_notice_hours     INTEGER NOT NULL DEFAULT 24,
  ADD COLUMN IF NOT EXISTS currency                TEXT DEFAULT 'USD',
  ADD COLUMN IF NOT EXISTS app_cancellation_policy TEXT,
  ADD COLUMN IF NOT EXISTS app_reschedule_policy   TEXT,
  ADD COLUMN IF NOT EXISTS avg_rating              NUMERIC(3,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS review_count            INTEGER NOT NULL DEFAULT 0;

-- ============================================================================
-- Platform settings (commission, currency defaults)
-- ============================================================================
CREATE TABLE IF NOT EXISTS platform_settings (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key         TEXT NOT NULL UNIQUE,
  value       TEXT,
  description TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO platform_settings (key, value, description) VALUES
  ('immigroov_commission_pct', '15', 'Default Immigroov commission percentage'),
  ('default_currency', 'USD', 'Fallback currency for the platform')
ON CONFLICT (key) DO NOTHING;

-- ============================================================================
-- Services (mentor-defined session types)
-- ============================================================================
CREATE TABLE IF NOT EXISTS services (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id    UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  title        TEXT NOT NULL,
  description  TEXT,
  type         service_type NOT NULL DEFAULT 'video',
  duration     INTEGER NOT NULL CHECK (duration > 0),  -- minutes
  is_ppp       BOOLEAN NOT NULL DEFAULT FALSE,         -- purchasing-power parity pricing
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  set_price    NUMERIC(10,2) NOT NULL DEFAULT 0,       -- mentor's base price in their currency
  set_currency TEXT NOT NULL DEFAULT 'USD',
  platform_fee NUMERIC(10,2) NOT NULL DEFAULT 0,       -- computed commission
  category     TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_services_mentor_id ON services(mentor_id);
CREATE INDEX IF NOT EXISTS idx_services_active    ON services(mentor_id) WHERE is_active;

-- ============================================================================
-- Service questions (custom intake questions per service)
-- ============================================================================
CREATE TABLE IF NOT EXISTS service_questions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id    UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  question_text TEXT NOT NULL,
  is_required   BOOLEAN NOT NULL DEFAULT FALSE,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  question_type question_type NOT NULL DEFAULT 'text',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_service_questions_service ON service_questions(service_id);

-- ============================================================================
-- Weekly availability (recurring schedule by weekday)
-- ============================================================================
CREATE TABLE IF NOT EXISTS weekly_availability (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id   UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  weekday     TEXT NOT NULL
              CHECK (weekday IN ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')),
  start_time  TIME NOT NULL,
  end_time    TIME NOT NULL,
  timezone    TEXT NOT NULL DEFAULT 'UTC',
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (end_time > start_time)
);

CREATE INDEX IF NOT EXISTS idx_weekly_avail_mentor ON weekly_availability(mentor_id) WHERE is_active;

-- ============================================================================
-- Specific availability (one-off overrides and blackouts)
-- ============================================================================
CREATE TABLE IF NOT EXISTS specific_availability (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id   UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  slot_date   DATE NOT NULL,
  start_time  TIME,      -- NULL when is_blackout = TRUE
  end_time    TIME,
  timezone    TEXT NOT NULL DEFAULT 'UTC',
  is_booked   BOOLEAN NOT NULL DEFAULT FALSE,
  is_blackout BOOLEAN NOT NULL DEFAULT FALSE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (is_blackout OR (start_time IS NOT NULL AND end_time IS NOT NULL AND end_time > start_time))
);

CREATE INDEX IF NOT EXISTS idx_specific_avail_mentor_date ON specific_availability(mentor_id, slot_date);

-- ============================================================================
-- New columns on bookings for the direct booking system
-- (Cal.com bookings keep scheduled_start/scheduled_end; direct bookings use slot_time/slot_end)
-- ============================================================================
ALTER TABLE bookings
  ADD COLUMN IF NOT EXISTS service_id                UUID REFERENCES services(id) ON DELETE RESTRICT,
  ADD COLUMN IF NOT EXISTS slot_time                 TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS slot_end                  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS specific_availability_id  UUID REFERENCES specific_availability(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS mentor_confirmed_at        TIMESTAMPTZ;

-- Generated tstzrange for double-booking exclusion (only set for direct bookings)
ALTER TABLE bookings
  ADD COLUMN IF NOT EXISTS slot_range TSTZRANGE
  GENERATED ALWAYS AS (
    CASE WHEN slot_time IS NOT NULL AND slot_end IS NOT NULL
         THEN tstzrange(slot_time, slot_end)
         ELSE NULL END
  ) STORED;

-- Direct bookings don't set scheduled_start (a legacy webhook column); allow NULL.
ALTER TABLE bookings ALTER COLUMN scheduled_start DROP NOT NULL;

-- ============================================================================
-- Booking question answers
-- ============================================================================
CREATE TABLE IF NOT EXISTS booking_question_answers (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id  UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  question_id UUID NOT NULL REFERENCES service_questions(id) ON DELETE CASCADE,
  answer_text TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bqa_booking ON booking_question_answers(booking_id);

-- ============================================================================
-- Booking reminders log (idempotent: one row per booking+kind)
-- ============================================================================
CREATE TABLE IF NOT EXISTS booking_reminders (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL,
  sent_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (booking_id, kind)
);

-- ============================================================================
-- Mentor cancellation policy (monthly counter)
-- ============================================================================
CREATE TABLE IF NOT EXISTS mentor_cancellation_policy (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id    UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  month_year   TEXT NOT NULL,   -- 'YYYY-MM'
  cancel_count INTEGER NOT NULL DEFAULT 0,
  last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (mentor_id, month_year)
);

-- ============================================================================
-- Reschedule offers (back-and-forth negotiation table)
-- ============================================================================
CREATE TABLE IF NOT EXISTS reschedule_offers (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id     UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  proposed_by    TEXT NOT NULL CHECK (proposed_by IN ('mentor', 'user')),
  offer_date     DATE,          -- mentor's proposed day
  range_start    TIMESTAMPTZ,   -- mentor's free window start
  range_end      TIMESTAMPTZ,   -- mentor's free window end
  requested_date DATE,          -- mentee's counter-proposal date
  selected_time  TIMESTAMPTZ,   -- mentee's selected time within the range
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'mentee_selected', 'accepted', 'declined', 'superseded')),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reschedule_offers_booking ON reschedule_offers(booking_id);

ALTER TABLE reschedule_offers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS reschedule_offers_read ON reschedule_offers;
CREATE POLICY reschedule_offers_read ON reschedule_offers FOR SELECT USING (TRUE);

-- =============================================================================
-- POSTGRESQL FUNCTIONS
-- =============================================================================

-- ============================================================================
-- Auth helpers
-- ============================================================================
CREATE OR REPLACE FUNCTION current_mentor_id()
RETURNS UUID LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT id FROM mentors WHERE profile_id = auth.uid();
$$;

CREATE OR REPLACE FUNCTION current_candidate_id()
RETURNS UUID LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT auth.uid();   -- profiles.id = auth.users.id in our schema
$$;

-- ============================================================================
-- Trigger: auto-set slot_end from service duration + snapshot attendee timezone
-- ============================================================================
CREATE OR REPLACE FUNCTION bookings_set_slot_end()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.slot_time IS NOT NULL AND NEW.slot_end IS NULL AND NEW.service_id IS NOT NULL THEN
    SELECT NEW.slot_time + MAKE_INTERVAL(mins => s.duration)
      INTO NEW.slot_end
    FROM services s WHERE s.id = NEW.service_id;
  END IF;
  -- Snapshot the attendee's timezone at booking time
  IF NEW.attendee_timezone IS NULL AND NEW.candidate_id IS NOT NULL THEN
    SELECT COALESCE(timezone, 'UTC')
      INTO NEW.attendee_timezone
    FROM profiles WHERE id = NEW.candidate_id;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_bookings_set_slot_end ON bookings;
CREATE TRIGGER trg_bookings_set_slot_end
  BEFORE INSERT OR UPDATE OF slot_time, service_id, slot_end ON bookings
  FOR EACH ROW EXECUTE FUNCTION bookings_set_slot_end();

-- ============================================================================
-- Trigger: keep specific_availability.is_booked in sync with the booking
-- ============================================================================
CREATE OR REPLACE FUNCTION bookings_sync_slot_lock()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.specific_availability_id IS NOT NULL THEN
      UPDATE specific_availability SET is_booked = FALSE WHERE id = OLD.specific_availability_id;
    END IF;
    RETURN OLD;
  END IF;

  -- Release a slot that was un-linked
  IF TG_OP = 'UPDATE'
     AND OLD.specific_availability_id IS NOT NULL
     AND OLD.specific_availability_id IS DISTINCT FROM NEW.specific_availability_id THEN
    UPDATE specific_availability SET is_booked = FALSE WHERE id = OLD.specific_availability_id;
  END IF;

  IF NEW.specific_availability_id IS NOT NULL THEN
    UPDATE specific_availability
      SET is_booked = (NEW.status NOT IN ('cancelled', 'no_show'))
    WHERE id = NEW.specific_availability_id;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_bookings_sync_slot_lock ON bookings;
CREATE TRIGGER trg_bookings_sync_slot_lock
  AFTER INSERT OR UPDATE OR DELETE ON bookings
  FOR EACH ROW EXECUTE FUNCTION bookings_sync_slot_lock();

-- ============================================================================
-- Double-booking guard (GiST exclusion on tstzrange)
-- Only applies to direct bookings where slot_range IS NOT NULL.
-- ============================================================================
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'bookings_no_overlap'
  ) THEN
    ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap
      EXCLUDE USING GIST (mentor_id WITH =, slot_range WITH &&)
      WHERE (status NOT IN ('cancelled', 'no_show') AND slot_range IS NOT NULL);
  END IF;
END $$;

-- ============================================================================
-- get_available_slots — server-side slot generation
-- Honors: weekly schedule, specific overrides, blackouts, buffer, min notice,
--         booking window, and existing non-cancelled bookings.
-- ============================================================================
CREATE OR REPLACE FUNCTION get_available_slots(
  p_mentor_id  UUID,
  p_service_id UUID,
  p_from       DATE,
  p_to         DATE
)
RETURNS TABLE (slot_start TIMESTAMPTZ, slot_end TIMESTAMPTZ)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_tz         TEXT;
  v_buffer     INTERVAL;
  v_min_notice INTERVAL;
  v_window     INTERVAL;
  v_duration   INTERVAL;
  v_step       INTERVAL;
  d            DATE;
  rec          RECORD;
  s            TIMESTAMPTZ;
  e            TIMESTAMPTZ;
  win_end      TIMESTAMPTZ;
BEGIN
  SELECT COALESCE(app_timezone, 'UTC'),
         COALESCE(app_buffertime, INTERVAL '0'),
         COALESCE(app_minimum_notice, INTERVAL '0'),
         COALESCE(app_booking_window, INTERVAL '365 days')
    INTO v_tz, v_buffer, v_min_notice, v_window
  FROM mentors WHERE id = p_mentor_id;

  SELECT MAKE_INTERVAL(mins => duration) INTO v_duration
  FROM services WHERE id = p_service_id AND is_active;

  IF v_duration IS NULL THEN
    RAISE EXCEPTION 'Active service % not found or has no duration', p_service_id;
  END IF;

  v_step := v_duration + v_buffer;

  FOR d IN SELECT generate_series(p_from, p_to, INTERVAL '1 day')::DATE LOOP
    -- Skip blackout dates entirely
    IF EXISTS (
      SELECT 1 FROM specific_availability
      WHERE mentor_id = p_mentor_id AND slot_date = d AND is_blackout
    ) THEN CONTINUE; END IF;

    FOR rec IN
      -- Recurring weekly windows (skip days with specific overrides)
      SELECT wa.start_time, wa.end_time
      FROM weekly_availability wa
      WHERE wa.mentor_id = p_mentor_id AND wa.is_active
        AND TRIM(wa.weekday) = TRIM(TO_CHAR(d, 'FMDay'))
        AND NOT EXISTS (
          SELECT 1 FROM specific_availability sa
          WHERE sa.mentor_id = p_mentor_id AND sa.slot_date = d
        )
      UNION ALL
      -- One-off override windows (not blackouts, not booked)
      SELECT sa.start_time, sa.end_time
      FROM specific_availability sa
      WHERE sa.mentor_id = p_mentor_id
        AND sa.slot_date = d
        AND sa.is_booked = FALSE
        AND sa.is_blackout = FALSE
        AND sa.start_time IS NOT NULL
    LOOP
      s       := (d::TEXT || ' ' || rec.start_time::TEXT)::TIMESTAMP AT TIME ZONE v_tz;
      win_end := (d::TEXT || ' ' || rec.end_time::TEXT)::TIMESTAMP   AT TIME ZONE v_tz;

      WHILE (s + v_duration) <= win_end LOOP
        e := s + v_duration;
        IF s >= NOW() + v_min_notice
           AND s <= NOW() + v_window
           AND NOT EXISTS (
             SELECT 1 FROM bookings b
             WHERE b.mentor_id = p_mentor_id
               AND b.status NOT IN ('cancelled', 'no_show')
               AND b.slot_range IS NOT NULL
               AND b.slot_range && tstzrange(s, e)
           )
        THEN
          slot_start := s;
          slot_end   := e;
          RETURN NEXT;
        END IF;
        s := s + v_step;
      END LOOP;
    END LOOP;
  END LOOP;
END;
$$;

GRANT EXECUTE ON FUNCTION get_available_slots(UUID, UUID, DATE, DATE) TO anon, authenticated;

-- ============================================================================
-- is_slot_available — server-side validation at booking time
-- ============================================================================
CREATE OR REPLACE FUNCTION is_slot_available(
  p_mentor_id  UUID,
  p_service_id UUID,
  p_slot       TIMESTAMPTZ
) RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT EXISTS (
    SELECT 1 FROM get_available_slots(
      p_mentor_id, p_service_id,
      (p_slot AT TIME ZONE 'UTC')::DATE - 1,
      (p_slot AT TIME ZONE 'UTC')::DATE + 1
    )
    WHERE slot_start = p_slot
  );
$$;

GRANT EXECUTE ON FUNCTION is_slot_available(UUID, UUID, TIMESTAMPTZ) TO anon, authenticated;

-- ============================================================================
-- bump_mentor_cancellation — monthly counter upsert
-- ============================================================================
CREATE OR REPLACE FUNCTION bump_mentor_cancellation(p_mentor_id UUID)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO mentor_cancellation_policy (mentor_id, month_year, cancel_count, last_updated)
  VALUES (p_mentor_id, TO_CHAR(NOW(), 'YYYY-MM'), 1, NOW())
  ON CONFLICT (mentor_id, month_year)
  DO UPDATE SET cancel_count = mentor_cancellation_policy.cancel_count + 1,
                last_updated = NOW();
END;
$$;

-- ============================================================================
-- cancel_booking — enforces notice window, bumps mentor's monthly counter
-- ============================================================================
CREATE OR REPLACE FUNCTION cancel_booking(
  p_booking_id   UUID,
  p_cancelled_by TEXT DEFAULT 'user'
)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_notice INTEGER;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking % not found', p_booking_id; END IF;

  -- Service role (auth.uid() IS NULL) is always allowed; otherwise check ownership
  IF auth.uid() IS NOT NULL
     AND b.candidate_id IS DISTINCT FROM current_candidate_id()
     AND b.mentor_id    IS DISTINCT FROM current_mentor_id() THEN
    RAISE EXCEPTION 'Not authorized to cancel booking %', p_booking_id;
  END IF;

  IF b.status IN ('cancelled', 'completed') THEN
    RAISE EXCEPTION 'Booking % is already %', p_booking_id, b.status;
  END IF;

  SELECT cancel_notice_hours INTO v_notice FROM mentors WHERE id = b.mentor_id;
  IF b.slot_time IS NOT NULL
     AND NOW() > b.slot_time - MAKE_INTERVAL(hours => COALESCE(v_notice, 24)) THEN
    RAISE EXCEPTION 'Cancellations must be at least % hours before the session — please reschedule instead.',
      COALESCE(v_notice, 24);
  END IF;

  UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id RETURNING * INTO b;
  UPDATE reschedule_offers SET status = 'superseded'
    WHERE booking_id = p_booking_id AND status = 'pending';

  IF p_cancelled_by = 'mentor' THEN
    PERFORM bump_mentor_cancellation(b.mentor_id);
  END IF;
  RETURN b;
END;
$$;

GRANT EXECUTE ON FUNCTION cancel_booking(UUID, TEXT) TO authenticated;

-- ============================================================================
-- mentor_confirm_attendance — "yes, I'm available" ~1h before session
-- ============================================================================
CREATE OR REPLACE FUNCTION mentor_confirm_attendance(p_booking_id UUID)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  UPDATE bookings SET mentor_confirmed_at = NOW() WHERE id = p_booking_id;
$$;

GRANT EXECUTE ON FUNCTION mentor_confirm_attendance(UUID) TO authenticated;

-- ============================================================================
-- Reschedule negotiation RPCs
-- ============================================================================

-- Step 1: Mentor declines attendance → proposes a day + free time range
CREATE OR REPLACE FUNCTION mentor_propose_reschedule(
  p_booking_id UUID,
  p_date       DATE,
  p_start      TIMESTAMPTZ,
  p_end        TIMESTAMPTZ
) RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_id UUID; b bookings;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking not found'; END IF;
  IF b.status IN ('cancelled', 'completed', 'no_show') THEN
    RAISE EXCEPTION 'Cannot reschedule booking with status %', b.status;
  END IF;
  IF p_end <= p_start THEN RAISE EXCEPTION 'Range end must be after start'; END IF;
  UPDATE reschedule_offers SET status = 'superseded'
    WHERE booking_id = p_booking_id AND status IN ('pending', 'mentee_selected');
  INSERT INTO reschedule_offers(booking_id, proposed_by, offer_date, range_start, range_end, status)
    VALUES (p_booking_id, 'mentor', p_date, p_start, p_end, 'pending')
    RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

-- Step 2a: Mentee picks a time inside the range
CREATE OR REPLACE FUNCTION mentee_accept_reschedule(
  p_offer_id UUID,
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
  -- Move to mentee_selected; mentor must confirm (3rd step in diagram)
  UPDATE reschedule_offers
    SET status = 'mentee_selected', selected_time = p_slot_time
  WHERE id = p_offer_id;
  -- Tentatively update booking
  UPDATE bookings SET slot_time = p_slot_time, slot_end = NULL, status = 'rescheduled'
    WHERE id = o.booking_id RETURNING * INTO b;
  DELETE FROM booking_reminders WHERE booking_id = o.booking_id;
  RETURN b;
END;
$$;

-- Step 2b: Mentee can't do that day → requests a different date (mentor re-proposes)
CREATE OR REPLACE FUNCTION mentee_request_other_date(p_booking_id UUID, p_date DATE)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_id UUID;
BEGIN
  UPDATE reschedule_offers SET status = 'superseded'
    WHERE booking_id = p_booking_id AND status IN ('pending', 'mentee_selected');
  INSERT INTO reschedule_offers(booking_id, proposed_by, requested_date, status)
    VALUES (p_booking_id, 'user', p_date, 'pending')
    RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

-- Step 3: Mentor confirms the mentee's selected time (final confirmation)
CREATE OR REPLACE FUNCTION mentor_confirm_reschedule(p_offer_id UUID)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE o reschedule_offers; b bookings;
BEGIN
  SELECT * INTO o FROM reschedule_offers
    WHERE id = p_offer_id AND status = 'mentee_selected';
  IF NOT FOUND THEN RAISE EXCEPTION 'Offer not found or not awaiting mentor confirmation'; END IF;
  UPDATE reschedule_offers SET status = 'accepted' WHERE id = p_offer_id;
  UPDATE bookings SET slot_time = o.selected_time, slot_end = NULL, status = 'rescheduled'
    WHERE id = o.booking_id RETURNING * INTO b;
  RETURN b;
END;
$$;

GRANT EXECUTE ON FUNCTION
  mentor_propose_reschedule(UUID, DATE, TIMESTAMPTZ, TIMESTAMPTZ),
  mentee_accept_reschedule(UUID, TIMESTAMPTZ),
  mentee_request_other_date(UUID, DATE),
  mentor_confirm_reschedule(UUID)
TO authenticated;

-- ============================================================================
-- mentor_sessions — upcoming sessions for the mentor hub
-- ============================================================================
CREATE OR REPLACE FUNCTION mentor_sessions(p_mentor_id UUID)
RETURNS TABLE (
  id                  UUID,
  status              TEXT,
  slot_time           TIMESTAMPTZ,
  meeting_url         TEXT,
  service_title       TEXT,
  service_duration    INTEGER,
  mentee_name         TEXT,
  mentee_email        TEXT,
  mentor_tz           TEXT,
  mentor_confirmed_at TIMESTAMPTZ,
  offer_id            UUID,
  offer_by            TEXT,
  offer_date          DATE,
  range_start         TIMESTAMPTZ,
  range_end           TIMESTAMPTZ,
  requested_date      DATE
) LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT
    b.id, b.status::TEXT, b.slot_time, b.meeting_url,
    s.title, s.duration,
    COALESCE(p.display_name, p.full_name, b.candidate_email),
    COALESCE(b.candidate_email, p.email),
    COALESCE(m.app_timezone, 'UTC'), b.mentor_confirmed_at,
    ro.id, ro.proposed_by, ro.offer_date, ro.range_start, ro.range_end, ro.requested_date
  FROM bookings b
  LEFT JOIN services  s ON s.id = b.service_id
  JOIN  mentors       m ON m.id = b.mentor_id
  LEFT JOIN profiles  p ON p.id = b.candidate_id
  LEFT JOIN LATERAL (
    SELECT * FROM reschedule_offers
    WHERE booking_id = b.id AND status IN ('pending', 'mentee_selected')
    ORDER BY created_at DESC LIMIT 1
  ) ro ON TRUE
  WHERE b.mentor_id = p_mentor_id
    AND b.status NOT IN ('cancelled', 'completed', 'no_show')
    AND b.slot_time IS NOT NULL
  ORDER BY b.slot_time;
$$;

GRANT EXECUTE ON FUNCTION mentor_sessions(UUID) TO authenticated;

-- ============================================================================
-- booking_times_display — dual-timezone rendering (for emails / UI)
-- ============================================================================
CREATE OR REPLACE FUNCTION booking_times_display(p_booking_id UUID)
RETURNS TABLE (
  slot_utc       TIMESTAMPTZ,
  mentor_tz      TEXT,
  mentor_local   TIMESTAMP,
  customer_tz    TEXT,
  customer_local TIMESTAMP
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT
    b.slot_time,
    COALESCE(m.app_timezone, 'UTC'),
    b.slot_time AT TIME ZONE COALESCE(m.app_timezone, 'UTC'),
    COALESCE(b.attendee_timezone, p.timezone, 'UTC'),
    b.slot_time AT TIME ZONE COALESCE(b.attendee_timezone, p.timezone, 'UTC')
  FROM bookings b
  JOIN  mentors  m ON m.id = b.mentor_id
  LEFT JOIN profiles p ON p.id = b.candidate_id
  WHERE b.id = p_booking_id;
$$;

GRANT EXECUTE ON FUNCTION booking_times_display(UUID) TO authenticated;

-- ============================================================================
-- Availability management RPCs (for mentor hub — all secured by service role in backend)
-- ============================================================================
CREATE OR REPLACE FUNCTION avail_add_weekly(p_mentor_id UUID, p_day TEXT, p_start TIME, p_end TIME)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_end <= p_start THEN RAISE EXCEPTION 'End must be after start'; END IF;
  INSERT INTO weekly_availability(mentor_id, weekday, start_time, end_time, timezone, is_active)
  SELECT p_mentor_id, p_day, p_start, p_end, COALESCE(app_timezone, 'UTC'), TRUE
  FROM mentors WHERE id = p_mentor_id;
END;
$$;

CREATE OR REPLACE FUNCTION avail_remove_weekly(p_id UUID)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  DELETE FROM weekly_availability WHERE id = p_id;
$$;

CREATE OR REPLACE FUNCTION avail_list_weekly(p_mentor_id UUID)
RETURNS TABLE(id UUID, weekday TEXT, start_time TIME, end_time TIME, timezone TEXT)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT id, weekday, start_time, end_time, timezone
  FROM weekly_availability
  WHERE mentor_id = p_mentor_id AND is_active
  ORDER BY ARRAY_POSITION(
    ARRAY['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'],
    weekday
  ), start_time;
$$;

CREATE OR REPLACE FUNCTION avail_block_date(p_mentor_id UUID, p_date DATE)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  DELETE FROM specific_availability WHERE mentor_id = p_mentor_id AND slot_date = p_date;
  INSERT INTO specific_availability(mentor_id, slot_date, timezone, is_blackout)
  SELECT p_mentor_id, p_date, COALESCE(app_timezone, 'UTC'), TRUE
  FROM mentors WHERE id = p_mentor_id;
END;
$$;

CREATE OR REPLACE FUNCTION avail_override_date(p_mentor_id UUID, p_date DATE, p_start TIME, p_end TIME)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_end <= p_start THEN RAISE EXCEPTION 'End must be after start'; END IF;
  DELETE FROM specific_availability
    WHERE mentor_id = p_mentor_id AND slot_date = p_date;
  INSERT INTO specific_availability(mentor_id, slot_date, start_time, end_time, timezone, is_blackout)
  SELECT p_mentor_id, p_date, p_start, p_end, COALESCE(app_timezone, 'UTC'), FALSE
  FROM mentors WHERE id = p_mentor_id;
END;
$$;

CREATE OR REPLACE FUNCTION avail_remove_specific(p_id UUID)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  DELETE FROM specific_availability WHERE id = p_id;
$$;

CREATE OR REPLACE FUNCTION avail_list_specific(p_mentor_id UUID)
RETURNS TABLE(id UUID, slot_date DATE, start_time TIME, end_time TIME, timezone TEXT, is_booked BOOLEAN, is_blackout BOOLEAN)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT id, slot_date, start_time, end_time, timezone, is_booked, is_blackout
  FROM specific_availability
  WHERE mentor_id = p_mentor_id AND slot_date >= CURRENT_DATE
  ORDER BY slot_date, start_time NULLS FIRST;
$$;

CREATE OR REPLACE FUNCTION avail_set_rules(
  p_mentor_id        UUID,
  p_days_ahead       INTEGER,
  p_min_notice_hours NUMERIC,
  p_cancel_hours     INTEGER DEFAULT NULL
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE mentors SET
    app_booking_window = MAKE_INTERVAL(days => GREATEST(p_days_ahead, 1)),
    app_minimum_notice = MAKE_INTERVAL(mins => ROUND(GREATEST(p_min_notice_hours, 0) * 60)::INTEGER),
    cancel_notice_hours = COALESCE(p_cancel_hours, cancel_notice_hours)
  WHERE id = p_mentor_id;
END;
$$;

CREATE OR REPLACE FUNCTION avail_get_rules(p_mentor_id UUID)
RETURNS TABLE(days_ahead INTEGER, min_notice_hours NUMERIC, cancel_hours INTEGER, timezone TEXT)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT
    COALESCE(EXTRACT(day FROM app_booking_window)::INTEGER, 30),
    ROUND((COALESCE(EXTRACT(epoch FROM app_minimum_notice), 0) / 3600.0)::NUMERIC, 1),
    COALESCE(cancel_notice_hours, 24),
    COALESCE(app_timezone, 'UTC')
  FROM mentors WHERE id = p_mentor_id;
$$;

GRANT EXECUTE ON FUNCTION
  avail_add_weekly(UUID, TEXT, TIME, TIME),
  avail_remove_weekly(UUID),
  avail_list_weekly(UUID),
  avail_block_date(UUID, DATE),
  avail_override_date(UUID, DATE, TIME, TIME),
  avail_remove_specific(UUID),
  avail_list_specific(UUID),
  avail_set_rules(UUID, INTEGER, NUMERIC, INTEGER),
  avail_get_rules(UUID)
TO authenticated;

-- ============================================================================
-- Services management RPCs
-- ============================================================================
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
DECLARE v_cur TEXT; v_pct NUMERIC; v_id UUID;
BEGIN
  SELECT COALESCE(currency, 'USD') INTO v_cur FROM mentors WHERE id = p_mentor_id;
  SELECT COALESCE(value::NUMERIC, 15) INTO v_pct
    FROM platform_settings WHERE key = 'immigroov_commission_pct';
  INSERT INTO services(mentor_id, title, description, type, duration, category,
                       is_ppp, is_active, set_price, set_currency, platform_fee)
  VALUES (p_mentor_id, p_title, p_description, p_type::service_type, p_duration, p_category,
          p_ppp, p_active, p_set_price, v_cur, ROUND(p_set_price * v_pct / 100, 2))
  RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION service_list(p_mentor_id UUID)
RETURNS TABLE(id UUID, title TEXT, description TEXT, type TEXT, duration INTEGER, category TEXT,
              set_price NUMERIC, set_currency TEXT, platform_fee NUMERIC, is_active BOOLEAN, is_ppp BOOLEAN)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT id, title, description, type::TEXT, duration, category,
         set_price, set_currency, platform_fee, is_active, is_ppp
  FROM services WHERE mentor_id = p_mentor_id ORDER BY created_at;
$$;

CREATE OR REPLACE FUNCTION service_set_active(p_id UUID, p_active BOOLEAN)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  UPDATE services SET is_active = p_active WHERE id = p_id;
$$;

CREATE OR REPLACE FUNCTION service_delete(p_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  DELETE FROM services WHERE id = p_id;
EXCEPTION WHEN foreign_key_violation THEN
  UPDATE services SET is_active = FALSE WHERE id = p_id;  -- archive if bookings reference it
END;
$$;

CREATE OR REPLACE FUNCTION question_add(
  p_service_id UUID,
  p_text       TEXT,
  p_required   BOOLEAN DEFAULT FALSE,
  p_type       TEXT    DEFAULT 'text'
) RETURNS UUID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  INSERT INTO service_questions(service_id, question_text, is_required, question_type, is_active)
  VALUES (p_service_id, p_text, p_required, p_type::question_type, TRUE)
  RETURNING id;
$$;

CREATE OR REPLACE FUNCTION question_list(p_service_id UUID)
RETURNS TABLE(id UUID, question_text TEXT, is_required BOOLEAN, question_type TEXT)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT id, question_text, is_required, question_type::TEXT
  FROM service_questions
  WHERE service_id = p_service_id AND is_active ORDER BY created_at;
$$;

CREATE OR REPLACE FUNCTION question_remove(p_id UUID)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  DELETE FROM service_questions WHERE id = p_id;
$$;

GRANT EXECUTE ON FUNCTION
  service_create(UUID, TEXT, TEXT, TEXT, INTEGER, TEXT, NUMERIC, BOOLEAN, BOOLEAN),
  service_list(UUID),
  service_set_active(UUID, BOOLEAN),
  service_delete(UUID),
  question_add(UUID, TEXT, BOOLEAN, TEXT),
  question_list(UUID),
  question_remove(UUID)
TO authenticated;

-- ============================================================================
-- book_session — the main direct booking action
-- Validates slot availability, creates booking + records answers.
-- Called by FastAPI backend (service role); auth.uid() is NULL in that context.
-- ============================================================================
CREATE OR REPLACE FUNCTION book_session(
  p_mentor_id                UUID,
  p_service_id               UUID,
  p_slot_time                TIMESTAMPTZ,
  p_email                    TEXT,
  p_name                     TEXT     DEFAULT NULL,
  p_timezone                 TEXT     DEFAULT 'UTC',
  p_answers                  JSONB    DEFAULT '[]',
  p_specific_availability_id UUID     DEFAULT NULL,
  p_candidate_id             UUID     DEFAULT NULL   -- pre-resolved by backend
) RETURNS TABLE(booking_id UUID, status TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_booking_id UUID;
BEGIN
  IF NOT is_slot_available(p_mentor_id, p_service_id, p_slot_time) THEN
    RAISE EXCEPTION 'That time is not available — please choose another slot';
  END IF;

  -- If candidate_id not supplied, try to match by email
  IF p_candidate_id IS NULL THEN
    SELECT id INTO p_candidate_id FROM profiles WHERE LOWER(email) = LOWER(p_email);
  END IF;

  INSERT INTO bookings(
    mentor_id, candidate_id, candidate_email, candidate_name,
    service_id, slot_time, status, attendee_timezone,
    specific_availability_id, source
  ) VALUES (
    p_mentor_id, p_candidate_id, LOWER(p_email), p_name,
    p_service_id, p_slot_time, 'confirmed', p_timezone,
    p_specific_availability_id, 'direct'
  ) RETURNING id INTO v_booking_id;

  INSERT INTO booking_question_answers(booking_id, question_id, answer_text)
  SELECT v_booking_id,
         (a->>'question_id')::UUID,
         a->>'answer_text'
  FROM jsonb_array_elements(COALESCE(p_answers, '[]'::JSONB)) a
  WHERE a ? 'question_id' AND (a->>'answer_text') IS NOT NULL AND (a->>'answer_text') <> '';

  RETURN QUERY SELECT v_booking_id, 'confirmed'::TEXT;
END;
$$;

GRANT EXECUTE ON FUNCTION
  book_session(UUID, UUID, TIMESTAMPTZ, TEXT, TEXT, TEXT, JSONB, UUID, UUID)
TO anon, authenticated;

-- ============================================================================
-- RLS on new tables
-- Backend (service role) bypasses RLS; these policies are for frontend queries
-- ============================================================================
ALTER TABLE services                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_questions          ENABLE ROW LEVEL SECURITY;
ALTER TABLE weekly_availability        ENABLE ROW LEVEL SECURITY;
ALTER TABLE specific_availability      ENABLE ROW LEVEL SECURITY;
ALTER TABLE booking_question_answers   ENABLE ROW LEVEL SECURITY;
ALTER TABLE booking_reminders          ENABLE ROW LEVEL SECURITY;
ALTER TABLE mentor_cancellation_policy ENABLE ROW LEVEL SECURITY;

-- Public can see active services and their questions
DROP POLICY IF EXISTS services_public_read   ON services;
DROP POLICY IF EXISTS service_questions_read ON service_questions;

CREATE POLICY services_public_read   ON services        FOR SELECT USING (is_active);
CREATE POLICY service_questions_read ON service_questions FOR SELECT USING (is_active);

-- Public can see mentor availability records
DROP POLICY IF EXISTS weekly_avail_public_read   ON weekly_availability;
DROP POLICY IF EXISTS specific_avail_public_read ON specific_availability;

CREATE POLICY weekly_avail_public_read   ON weekly_availability   FOR SELECT USING (TRUE);
CREATE POLICY specific_avail_public_read ON specific_availability FOR SELECT USING (TRUE);

-- Candidates see their own booking question answers
DROP POLICY IF EXISTS bqa_self ON booking_question_answers;
CREATE POLICY bqa_self ON booking_question_answers FOR SELECT
  USING (booking_id IN (SELECT id FROM bookings WHERE candidate_id = auth.uid()));
