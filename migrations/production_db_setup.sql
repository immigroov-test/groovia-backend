-- production_db_setup.sql
-- Complete Groovia schema for fresh Supabase project setup.
-- Run this ONCE in Supabase SQL Editor on a new project.
-- For existing databases, run migrations 001-015 sequentially instead.

-- ============================================================================
-- Extensions
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ============================================================================
-- Enum types
-- ============================================================================
DO $$ BEGIN
  CREATE TYPE user_role AS ENUM ('candidate', 'mentor', 'admin', 'guest');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
-- Ensure 'guest' exists even when the type predates it (idempotent; makes re-runs safe).
ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'guest';

DO $$ BEGIN
  CREATE TYPE mentor_status AS ENUM ('pending_review', 'approved', 'rejected', 'suspended');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE booking_status AS ENUM
    ('pending_payment', 'confirmed', 'rescheduled', 'cancelled', 'completed', 'no_show', 'pending');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE service_type AS ENUM ('video', 'dm');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE question_type AS ENUM ('text', 'multiple_choice', 'yes_no');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================================================
-- profiles
-- ============================================================================
CREATE TABLE IF NOT EXISTS profiles (
  id                     UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  role                   user_role NOT NULL DEFAULT 'candidate',
  email                  TEXT UNIQUE NOT NULL,
  full_name              TEXT,
  display_name           TEXT,
  photo_url              TEXT,
  country_code           CHAR(2),
  city                   TEXT,
  timezone               TEXT DEFAULT 'UTC',
  target_country_code    CHAR(2),
  profession             TEXT,
  immigration_goal       TEXT,
  profile_summary        TEXT,
  phone                  TEXT,
  attribution_source     TEXT,
  attribution_medium     TEXT,
  attribution_campaign   TEXT,
  attribution_mentor_id  UUID,
  attribution_locked_at  TIMESTAMPTZ,
  attribution_expires_at TIMESTAMPTZ,
  credit_balance         INTEGER NOT NULL DEFAULT 0 CHECK (credit_balance >= 0),
  email_notifications    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at             TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_profiles_role
  ON profiles(role) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_profiles_attribution_expiry
  ON profiles(id, attribution_expires_at)
  WHERE attribution_locked_at IS NOT NULL;

-- ============================================================================
-- mentors
-- ============================================================================
CREATE TABLE IF NOT EXISTS mentors (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id              UUID UNIQUE REFERENCES profiles(id) ON DELETE SET NULL,
  slug                    TEXT NOT NULL UNIQUE,
  display_name            TEXT NOT NULL,
  headline                TEXT,
  bio                     TEXT,
  photo_url               TEXT,
  expertise_country_codes CHAR(2)[] NOT NULL DEFAULT '{}',
  expertise_categories    TEXT[]    NOT NULL DEFAULT '{}',
  languages               TEXT[]    NOT NULL DEFAULT '{}',
  professional_domains    TEXT[]    NOT NULL DEFAULT '{}',
  years_lived_experience  INTEGER,
  social_links            JSONB NOT NULL DEFAULT '[]',
  phone                   TEXT,
  city                    TEXT,
  country                 TEXT,
  public_notes            TEXT,
  booking_url             TEXT,
  email                   TEXT,  -- contact email for pre-approved / seed mentors with no linked account yet
  status                  mentor_status NOT NULL DEFAULT 'approved',
  is_active               BOOLEAN NOT NULL DEFAULT TRUE,
  availability_type       TEXT,
  submission_count        INTEGER NOT NULL DEFAULT 1,
  session_duration_minutes INTEGER NOT NULL DEFAULT 30,
  timezone                TEXT NOT NULL DEFAULT 'UTC',
  app_timezone            TEXT DEFAULT 'UTC',
  app_buffertime          INTERVAL DEFAULT '15 minutes',
  app_minimum_notice      INTERVAL DEFAULT '2 hours',
  app_booking_window      INTERVAL DEFAULT '30 days',
  cancel_notice_hours     INTEGER NOT NULL DEFAULT 24,
  currency                TEXT DEFAULT 'USD',
  app_cancellation_policy TEXT,
  app_reschedule_policy   TEXT,
  avg_rating              NUMERIC(3,2) NOT NULL DEFAULT 0,
  review_count            INTEGER NOT NULL DEFAULT 0,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mentors_status_active ON mentors(status, is_active);
-- Fast case-insensitive lookup for pre-approved mentor linking on login (link_mentor_by_email).
CREATE INDEX IF NOT EXISTS idx_mentors_email_lower ON mentors(lower(email)) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mentors_countries     ON mentors USING GIN (expertise_country_codes);
CREATE INDEX IF NOT EXISTS idx_mentors_categories    ON mentors USING GIN (expertise_categories);

-- ============================================================================
-- chat_threads
-- ============================================================================
CREATE TABLE IF NOT EXISTS chat_threads (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID REFERENCES profiles(id) ON DELETE CASCADE,
  title           TEXT,
  user_intent     TEXT,
  track           TEXT,
  last_message_at TIMESTAMPTZ,
  message_count   INTEGER NOT NULL DEFAULT 0,
  is_archived     BOOLEAN NOT NULL DEFAULT FALSE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_threads_user_recent
  ON chat_threads(user_id, last_message_at DESC NULLS LAST)
  WHERE is_archived = FALSE;

-- ============================================================================
-- consent_log
-- ============================================================================
CREATE TABLE IF NOT EXISTS consent_log (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  consent_type TEXT NOT NULL,
  version      TEXT NOT NULL,
  ip_address   INET,
  user_agent   TEXT,
  granted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  revoked_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_consent_log_user ON consent_log(user_id, consent_type);

-- ============================================================================
-- webhook_events
-- ============================================================================
CREATE TABLE IF NOT EXISTS webhook_events (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider     TEXT NOT NULL,
  event_type   TEXT NOT NULL,
  external_id  TEXT,
  signature_ok BOOLEAN NOT NULL DEFAULT FALSE,
  payload      JSONB NOT NULL,
  processed_at TIMESTAMPTZ,
  error        TEXT,
  received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_lookup
  ON webhook_events(provider, external_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_unprocessed
  ON webhook_events(received_at) WHERE processed_at IS NULL;

-- ============================================================================
-- services (before bookings — bookings references it)
-- ============================================================================
CREATE TABLE IF NOT EXISTS services (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id    UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  title        TEXT NOT NULL,
  description  TEXT,
  type         service_type NOT NULL DEFAULT 'video',
  duration     INTEGER NOT NULL CHECK (duration > 0),
  is_ppp       BOOLEAN NOT NULL DEFAULT FALSE,
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  set_price    NUMERIC(10,2) NOT NULL DEFAULT 0,
  set_currency TEXT NOT NULL DEFAULT 'USD',
  platform_fee NUMERIC(10,2) NOT NULL DEFAULT 0,
  category     TEXT,
  status       TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'approved' | 'rejected' (admin review)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE services ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';

CREATE INDEX IF NOT EXISTS idx_services_mentor_id ON services(mentor_id);
CREATE INDEX IF NOT EXISTS idx_services_active    ON services(mentor_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_services_pending   ON services(status) WHERE status = 'pending';

-- ============================================================================
-- specific_availability (before bookings — bookings references it)
-- ============================================================================
CREATE TABLE IF NOT EXISTS specific_availability (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id   UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  slot_date   DATE NOT NULL,
  start_time  TIME,
  end_time    TIME,
  timezone    TEXT NOT NULL DEFAULT 'UTC',
  is_booked   BOOLEAN NOT NULL DEFAULT FALSE,
  is_blackout BOOLEAN NOT NULL DEFAULT FALSE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (is_blackout OR (start_time IS NOT NULL AND end_time IS NOT NULL AND end_time > start_time))
);

CREATE INDEX IF NOT EXISTS idx_specific_avail_mentor_date
  ON specific_availability(mentor_id, slot_date);

-- ============================================================================
-- bookings
-- ============================================================================
CREATE TABLE IF NOT EXISTS bookings (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source                   TEXT NOT NULL DEFAULT 'direct',
  external_id              TEXT,
  mentor_id                UUID NOT NULL REFERENCES mentors(id) ON DELETE RESTRICT,
  candidate_id             UUID REFERENCES profiles(id) ON DELETE SET NULL,
  candidate_email          TEXT,
  candidate_name           TEXT,
  thread_id                UUID REFERENCES chat_threads(id) ON DELETE SET NULL,
  title                    TEXT,
  scheduled_start          TIMESTAMPTZ,
  scheduled_end            TIMESTAMPTZ,
  attendee_timezone        TEXT,
  meeting_url              TEXT,
  status                   booking_status NOT NULL DEFAULT 'confirmed',
  cancel_reason            TEXT,
  service_id               UUID REFERENCES services(id) ON DELETE RESTRICT,
  slot_time                TIMESTAMPTZ,
  slot_end                 TIMESTAMPTZ,
  specific_availability_id UUID REFERENCES specific_availability(id) ON DELETE SET NULL,
  mentor_confirmed_at      TIMESTAMPTZ,
  slot_range               TSTZRANGE GENERATED ALWAYS AS (
    CASE WHEN slot_time IS NOT NULL AND slot_end IS NOT NULL
         THEN tstzrange(slot_time, slot_end) ELSE NULL END
  ) STORED,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT bookings_source_external_id_key UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_bookings_mentor_time
  ON bookings(mentor_id, slot_time DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_bookings_candidate_time
  ON bookings(candidate_id, slot_time DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_bookings_thread
  ON bookings(thread_id);
CREATE INDEX IF NOT EXISTS idx_bookings_external_id
  ON bookings(external_id) WHERE external_id IS NOT NULL;

-- Idempotency: dedupes a retried booking request (dropped network response, double-click).
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS idempotency_key text;
CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_idempotency
  ON bookings(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- ============================================================================
-- mentor_availability (legacy manual fallback system — superseded by weekly_availability)
-- ============================================================================
CREATE TABLE IF NOT EXISTS mentor_availability (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id   UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
  start_time  TIME NOT NULL,
  end_time    TIME NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (mentor_id, day_of_week, start_time)
);

CREATE INDEX IF NOT EXISTS idx_mentor_availability_mentor
  ON mentor_availability(mentor_id, day_of_week);

-- ============================================================================
-- ai_events
-- ============================================================================
CREATE TABLE IF NOT EXISTS ai_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id       UUID REFERENCES chat_threads(id) ON DELETE SET NULL,
  intent          TEXT,
  revision_count  INTEGER NOT NULL DEFAULT 0,
  tool_calls      INTEGER NOT NULL DEFAULT 0,
  latency_ms      INTEGER,
  model           TEXT,
  quality_failure BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_events_thread  ON ai_events(thread_id);
CREATE INDEX IF NOT EXISTS idx_ai_events_created ON ai_events(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_events_intent  ON ai_events(intent, created_at);

-- ============================================================================
-- chat_messages
-- ============================================================================
CREATE TABLE IF NOT EXISTS chat_messages (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id  UUID NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
  role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content    TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_thread_time
  ON chat_messages(thread_id, created_at);

-- ============================================================================
-- platform_settings
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
-- service_questions
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
-- weekly_availability
-- ============================================================================
CREATE TABLE IF NOT EXISTS weekly_availability (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id  UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  weekday    TEXT NOT NULL
             CHECK (weekday IN ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')),
  start_time TIME NOT NULL,
  end_time   TIME NOT NULL,
  timezone   TEXT NOT NULL DEFAULT 'UTC',
  is_active  BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (end_time > start_time)
);

CREATE INDEX IF NOT EXISTS idx_weekly_avail_mentor
  ON weekly_availability(mentor_id) WHERE is_active;

-- ============================================================================
-- booking_question_answers
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
-- booking_reminders
-- ============================================================================
CREATE TABLE IF NOT EXISTS booking_reminders (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL,
  sent_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (booking_id, kind)
);

-- ============================================================================
-- mentor_cancellation_policy
-- ============================================================================
CREATE TABLE IF NOT EXISTS mentor_cancellation_policy (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id    UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  month_year   TEXT NOT NULL,
  cancel_count INTEGER NOT NULL DEFAULT 0,
  last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (mentor_id, month_year)
);

-- ============================================================================
-- reschedule_offers
-- ============================================================================
CREATE TABLE IF NOT EXISTS reschedule_offers (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id     UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  proposed_by    TEXT NOT NULL CHECK (proposed_by IN ('mentor', 'user')),
  offer_date     DATE,
  range_start    TIMESTAMPTZ,
  range_end      TIMESTAMPTZ,
  requested_date DATE,
  selected_time  TIMESTAMPTZ,
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','mentee_selected','accepted','declined','superseded')),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reschedule_offers_booking ON reschedule_offers(booking_id);

-- ============================================================================
-- GiST exclusion constraint (double-booking guard)
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
-- Triggers and functions
-- ============================================================================

-- set_updated_at
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_profiles_updated_at ON profiles;
CREATE TRIGGER trg_profiles_updated_at
  BEFORE UPDATE ON profiles
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_mentors_updated_at ON mentors;
CREATE TRIGGER trg_mentors_updated_at
  BEFORE UPDATE ON mentors
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_chat_threads_updated_at ON chat_threads;
CREATE TRIGGER trg_chat_threads_updated_at
  BEFORE UPDATE ON chat_threads
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_bookings_updated_at ON bookings;
CREATE TRIGGER trg_bookings_updated_at
  BEFORE UPDATE ON bookings
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- handle_new_user (latest version from 009)
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO profiles (id, email, full_name, photo_url, role)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(
      NEW.raw_user_meta_data->>'full_name',
      NEW.raw_user_meta_data->>'name'
    ),
    COALESCE(
      NEW.raw_user_meta_data->>'avatar_url',
      NEW.raw_user_meta_data->>'picture'
    ),
    (CASE WHEN lower(NEW.email) = 'yokeshmd99@gmail.com' THEN 'admin'
          ELSE COALESCE(NEW.raw_user_meta_data->>'role', 'candidate') END)::user_role
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

DROP TRIGGER IF EXISTS trg_on_auth_user_created ON auth.users;
CREATE TRIGGER trg_on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION handle_new_user();

-- Ops admin: promote the admin account if its profile already exists (idempotent).
UPDATE profiles SET role = 'admin' WHERE lower(email) = 'yokeshmd99@gmail.com' AND role <> 'admin';

-- Does this email already have a PASSWORD? signInWithOtp creates an auth.users row
-- immediately (unconfirmed, no password), so "row exists" ≠ "can log in with a password".
-- Returns one row (has_password) if the email exists, zero rows if not.
CREATE OR REPLACE FUNCTION email_account_status(p_email text)
RETURNS TABLE (has_password boolean)
LANGUAGE sql SECURITY DEFINER SET search_path = public, auth AS $$
  SELECT (u.encrypted_password IS NOT NULL AND length(u.encrypted_password) > 0)
  FROM auth.users u
  WHERE lower(u.email) = lower(p_email)
  LIMIT 1;
$$;
GRANT EXECUTE ON FUNCTION email_account_status(text) TO service_role;

-- sync_mentor_is_active (from 013)
CREATE OR REPLACE FUNCTION sync_mentor_is_active()
RETURNS TRIGGER AS $$
BEGIN
  NEW.is_active := (NEW.status = 'approved');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_mentor_status_sync_active ON mentors;
CREATE TRIGGER trg_mentor_status_sync_active
  BEFORE INSERT OR UPDATE OF status ON mentors
  FOR EACH ROW EXECUTE FUNCTION sync_mentor_is_active();

-- bookings_set_slot_end (from 015)
CREATE OR REPLACE FUNCTION bookings_set_slot_end()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.slot_time IS NOT NULL AND NEW.slot_end IS NULL AND NEW.service_id IS NOT NULL THEN
    SELECT NEW.slot_time + MAKE_INTERVAL(mins => s.duration)
      INTO NEW.slot_end
    FROM services s WHERE s.id = NEW.service_id;
  END IF;
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

-- bookings_sync_slot_lock (from 015)
CREATE OR REPLACE FUNCTION bookings_sync_slot_lock()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.specific_availability_id IS NOT NULL THEN
      UPDATE specific_availability SET is_booked = FALSE WHERE id = OLD.specific_availability_id;
    END IF;
    RETURN OLD;
  END IF;

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
-- Auth helpers (from 015)
-- ============================================================================
CREATE OR REPLACE FUNCTION current_mentor_id()
RETURNS UUID LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT id FROM mentors WHERE profile_id = auth.uid();
$$;

CREATE OR REPLACE FUNCTION current_candidate_id()
RETURNS UUID LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT auth.uid();
$$;

-- ============================================================================
-- get_available_slots (from 015)
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
    IF EXISTS (
      SELECT 1 FROM specific_availability
      WHERE mentor_id = p_mentor_id AND slot_date = d AND is_blackout
    ) THEN CONTINUE; END IF;

    FOR rec IN
      SELECT wa.start_time, wa.end_time
      FROM weekly_availability wa
      WHERE wa.mentor_id = p_mentor_id AND wa.is_active
        AND TRIM(wa.weekday) = TRIM(TO_CHAR(d, 'FMDay'))
        AND NOT EXISTS (
          SELECT 1 FROM specific_availability sa
          WHERE sa.mentor_id = p_mentor_id AND sa.slot_date = d
        )
      UNION ALL
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
-- is_slot_available (from 015)
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
-- bump_mentor_cancellation (from 015)
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
-- cancel_booking (from 015)
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
-- mentor_confirm_attendance (from 015)
-- ============================================================================
CREATE OR REPLACE FUNCTION mentor_confirm_attendance(p_booking_id UUID)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  UPDATE bookings SET mentor_confirmed_at = NOW() WHERE id = p_booking_id;
$$;

GRANT EXECUTE ON FUNCTION mentor_confirm_attendance(UUID) TO authenticated;

-- ============================================================================
-- Reschedule negotiation RPCs (from 015)
-- ============================================================================
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
-- mentor_sessions (from 015)
-- ============================================================================
-- DROP first: the lifecycle-v2 block further down redefines this with a different
-- TABLE return type. CREATE OR REPLACE cannot change a return type, so re-running
-- the setup on an existing DB needs an explicit drop here.
DROP FUNCTION IF EXISTS mentor_sessions(UUID);
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
-- booking_times_display (from 015)
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
-- Availability management RPCs (from 015)
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
-- Services management RPCs (from 015)
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
              set_price NUMERIC, set_currency TEXT, platform_fee NUMERIC, is_active BOOLEAN, is_ppp BOOLEAN,
              status TEXT)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT id, title, description, type::TEXT, duration, category,
         set_price, set_currency, platform_fee, is_active, is_ppp, status
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
  UPDATE services SET is_active = FALSE WHERE id = p_id;
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
-- book_session (from 015)
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
  p_candidate_id             UUID     DEFAULT NULL
) RETURNS TABLE(booking_id UUID, status TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_booking_id UUID;
BEGIN
  IF NOT is_slot_available(p_mentor_id, p_service_id, p_slot_time) THEN
    RAISE EXCEPTION 'That time is not available — please choose another slot';
  END IF;

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
-- Row Level Security
-- ============================================================================

-- profiles
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users read own profile" ON profiles;
CREATE POLICY "Users read own profile"
  ON profiles FOR SELECT USING (auth.uid() = id);

DROP POLICY IF EXISTS "Users update own profile" ON profiles;
CREATE POLICY "Users update own profile"
  ON profiles FOR UPDATE USING (auth.uid() = id);

-- mentors
ALTER TABLE mentors ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Anyone reads approved active mentors" ON mentors;
CREATE POLICY "Anyone reads approved active mentors"
  ON mentors FOR SELECT USING (status = 'approved' AND is_active = TRUE);

DROP POLICY IF EXISTS "Mentor reads own row" ON mentors;
CREATE POLICY "Mentor reads own row"
  ON mentors FOR SELECT USING (auth.uid() = profile_id);

DROP POLICY IF EXISTS "Mentor updates own row" ON mentors;
CREATE POLICY "Mentor updates own row"
  ON mentors FOR UPDATE USING (auth.uid() = profile_id);

-- chat_threads
ALTER TABLE chat_threads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users access own threads" ON chat_threads;
CREATE POLICY "Users access own threads"
  ON chat_threads FOR ALL USING (auth.uid() = user_id);

-- consent_log
ALTER TABLE consent_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users read own consent" ON consent_log;
CREATE POLICY "Users read own consent"
  ON consent_log FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users insert own consent" ON consent_log;
CREATE POLICY "Users insert own consent"
  ON consent_log FOR INSERT WITH CHECK (auth.uid() = user_id);

-- webhook_events (service-role only)
ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;

-- bookings
ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Candidates read own bookings" ON bookings;
CREATE POLICY "Candidates read own bookings"
  ON bookings FOR SELECT USING (auth.uid() = candidate_id);

DROP POLICY IF EXISTS "Mentors read own bookings" ON bookings;
CREATE POLICY "Mentors read own bookings"
  ON bookings FOR SELECT
  USING (EXISTS (
    SELECT 1 FROM mentors m WHERE m.id = bookings.mentor_id AND m.profile_id = auth.uid()
  ));

-- mentor_availability
ALTER TABLE mentor_availability ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Mentor manages own availability" ON mentor_availability;
CREATE POLICY "Mentor manages own availability"
  ON mentor_availability
  USING (mentor_id IN (SELECT id FROM mentors WHERE profile_id = auth.uid()));

-- chat_messages
ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users read own chat messages" ON chat_messages;
CREATE POLICY "Users read own chat messages"
  ON chat_messages FOR SELECT
  USING (thread_id IN (SELECT id FROM chat_threads WHERE user_id = auth.uid()));

-- ai_events (service-role only)
ALTER TABLE ai_events ENABLE ROW LEVEL SECURITY;

-- services
ALTER TABLE services ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS services_public_read ON services;
CREATE POLICY services_public_read ON services FOR SELECT USING (is_active);

-- service_questions
ALTER TABLE service_questions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS service_questions_read ON service_questions;
CREATE POLICY service_questions_read ON service_questions FOR SELECT USING (is_active);

-- weekly_availability
ALTER TABLE weekly_availability ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS weekly_avail_public_read ON weekly_availability;
CREATE POLICY weekly_avail_public_read ON weekly_availability FOR SELECT USING (TRUE);

-- specific_availability
ALTER TABLE specific_availability ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS specific_avail_public_read ON specific_availability;
CREATE POLICY specific_avail_public_read ON specific_availability FOR SELECT USING (TRUE);

-- booking_question_answers
ALTER TABLE booking_question_answers ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS bqa_self ON booking_question_answers;
CREATE POLICY bqa_self ON booking_question_answers FOR SELECT
  USING (booking_id IN (SELECT id FROM bookings WHERE candidate_id = auth.uid()));

-- reschedule_offers
ALTER TABLE reschedule_offers ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS reschedule_offers_read ON reschedule_offers;
CREATE POLICY reschedule_offers_read ON reschedule_offers FOR SELECT USING (TRUE);

-- booking_reminders / mentor_cancellation_policy (service-role only)
ALTER TABLE booking_reminders          ENABLE ROW LEVEL SECURITY;
ALTER TABLE mentor_cancellation_policy ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- is_valid_timezone (IANA name validation)
-- ============================================================================
CREATE OR REPLACE FUNCTION is_valid_timezone(p_tz TEXT)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = p_tz);
$$;
GRANT EXECUTE ON FUNCTION is_valid_timezone(TEXT) TO anon, authenticated;

-- ============================================================================
-- ppp_factors  (Purchasing Power Parity price adjustments per country)
-- Ported verbatim from immigroov/supabase/migrations/0023_ppp_pricing.sql —
-- the frozen business spec. Price-level factors (US = 1.00). PPP only applies
-- to services where the mentor enabled is_ppp.
-- ============================================================================
-- factor has NO upper-bound CHECK — immigroov's own source (0023_ppp_pricing.sql)
-- has no constraint on this column at all, and its seed data includes
-- higher-cost-of-living countries priced ABOVE the US baseline (NO=1.05,
-- CH=1.15), which a `factor <= 1` constraint (added during an earlier pass
-- of this port, not part of the source) would reject outright — confirmed
-- the hard way: applying this migration against a live database failed on
-- exactly that seed row before this fix. `factor > 0` is kept as a sane
-- floor (a non-positive multiplier is never valid), not something the
-- source enforces either, but it doesn't contradict any actual data.
CREATE TABLE IF NOT EXISTS ppp_factors (
  country_code  CHAR(2)      PRIMARY KEY,
  factor        NUMERIC(5,4) NOT NULL CHECK (factor > 0),
  updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

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

-- Global floor: PPP never discounts below this fraction of base price, even
-- for a country not in ppp_factors or seeded below the floor (e.g. IN=0.30 is
-- dominated by a 0.40 floor — by design, per the source migration's comment).
INSERT INTO platform_settings (key, value, description) VALUES
  ('ppp_floor', '0.40', 'Minimum PPP factor (never price below this fraction of base)')
ON CONFLICT (key) DO NOTHING;

-- ============================================================================
-- get_ppp_factor  — countries NOT in ppp_factors get 1.0 (no discount);
-- countries IN the table get GREATEST(their factor, ppp_floor) — the floor is
-- an admin-configurable platform_settings row, not hardcoded.
-- ============================================================================
CREATE OR REPLACE FUNCTION get_ppp_factor(p_country_code TEXT)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT GREATEST(
    COALESCE((SELECT factor FROM ppp_factors WHERE country_code = UPPER(p_country_code)), 1.0),
    COALESCE((SELECT value::numeric FROM platform_settings WHERE key = 'ppp_floor'), 0.40)
  );
$$;
GRANT EXECUTE ON FUNCTION get_ppp_factor(TEXT) TO anon, authenticated;

-- ============================================================================
-- FX rate infra — ported verbatim from immigroov's 0065_pricing_engine.sql.
-- fx_rates is a pivot model (base='EUR' -> every quote currency); one
-- Frankfurter API call refreshes the whole table. get_fx() is the STRICT
-- variant the pricing engine uses: it RAISES FX_UNAVAILABLE rather than
-- silently falling back to rate=1, which would corrupt the INR ledger.
-- get_fx_or_null() is the soft variant for display-only pricing.
-- ============================================================================
CREATE TABLE IF NOT EXISTS fx_rates (
  base        TEXT        NOT NULL,             -- always 'EUR'
  quote       TEXT        NOT NULL,              -- ISO currency code
  rate        NUMERIC     NOT NULL,               -- quote units per 1 base unit
  as_of       DATE,                               -- provider's published date
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

-- Cross-rate via the EUR pivot. Returns NULL if either leg is missing or
-- older than fx_max_age_minutes. No silent fallback to 1.
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

-- Country -> display currency. Only currencies Frankfurter supports; anything
-- else falls back to USD.
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

-- ============================================================================
-- Pricing engine — ported from immigroov's 0065_pricing_engine.sql, adapted
-- from bigint PKs to this schema's UUID PKs. Business math (PPP, FX, fee) is
-- verbatim; only the ID types and the services-approval filter differ:
--   - services.id / mentors.id / bookings.id are UUID here, not bigint.
--   - compute_booking_price additionally requires status = 'approved' — a
--     groovia-only rule (service admin-approval workflow) immigroov doesn't
--     have. This is an existing groovia business rule being preserved, not a
--     new one introduced during migration (see db.list_services active_only
--     in db/direct_booking.py, which applies the same is_active + approved
--     filter for the public/bookable service list).
-- book_session_guest is intentionally NOT touched here — wiring quotes into
-- booking creation is Payments-phase work, not Pricing+PPP.
-- ============================================================================

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
-- Raises FX_UNAVAILABLE if rates are missing/stale. ppp_floor (0.40)
-- intentionally governs IN/PK/NP/EG/etc via get_ppp_factor — the seeded 0.30
-- IN row is dominated by the floor (by design, matching the source spec).
CREATE OR REPLACE FUNCTION compute_booking_price(p_service_id UUID, p_customer_country TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_pricing_version CONSTANT INT := 1;
  v_ppp_version     CONSTANT INT := 1;
  v_provider        CONSTANT TEXT := 'frankfurter';
  v_mentor_id UUID; v_set NUMERIC; v_ment_ccy TEXT; v_is_ppp BOOLEAN; v_fee_pct NUMERIC;
  v_cust_ccy TEXT; v_ppp NUMERIC;
  v_fx_mc NUMERIC; v_fx_c_inr NUMERIC; v_fx_m_inr NUMERIC;
  v_gross NUMERIC; v_fee NUMERIC; v_net_cust NUMERIC; v_net_mentor NUMERIC;
BEGIN
  -- NOTE: services.platform_fee is an ABSOLUTE commission amount in the mentor's
  -- currency (e.g. set_price 2500 -> platform_fee 375 = 15%), NOT a percentage.
  -- We convert it to a percentage of set_price so the fee applies correctly to
  -- the PPP-adjusted, FX-converted customer gross. Falls back to the admin
  -- global pct (immigroov_commission_pct).
  SELECT s.mentor_id, s.set_price, COALESCE(s.set_currency, 'USD'), s.is_ppp,
         COALESCE(
           CASE WHEN s.set_price > 0 AND NULLIF(s.platform_fee, 0) IS NOT NULL
                THEN ROUND(s.platform_fee / s.set_price * 100.0, 4) END,
           (SELECT value::numeric FROM platform_settings WHERE key = 'immigroov_commission_pct'),
           15)
    INTO v_mentor_id, v_set, v_ment_ccy, v_is_ppp, v_fee_pct
  FROM services s WHERE s.id = p_service_id AND s.is_active AND s.status = 'approved';
  IF v_set IS NULL THEN RAISE EXCEPTION 'Service not available' USING errcode = 'P0001'; END IF;

  v_cust_ccy := currency_for_country(p_customer_country);
  v_ppp := CASE WHEN v_is_ppp THEN get_ppp_factor(p_customer_country) ELSE 1 END;

  v_fx_mc    := get_fx(v_ment_ccy, v_cust_ccy);   -- customer units per 1 mentor unit
  v_fx_c_inr := get_fx(v_cust_ccy, 'INR');
  v_fx_m_inr := get_fx(v_ment_ccy, 'INR');

  v_gross      := ROUND(v_set * v_ppp * v_fx_mc, 2);
  v_fee        := ROUND(v_gross * v_fee_pct / 100.0, 2);
  v_net_cust   := ROUND(v_gross - v_fee, 2);
  v_net_mentor := ROUND(v_net_cust / v_fx_mc, 2);  -- divide: customer-net -> mentor currency

  RETURN jsonb_build_object(
    'pricing_version', v_pricing_version, 'ppp_version', v_ppp_version, 'fx_provider', v_provider,
    'service_id', p_service_id, 'mentor_id', v_mentor_id, 'customer_country', UPPER(COALESCE(p_customer_country, '')),
    'mentor_currency', v_ment_ccy, 'customer_currency', v_cust_ccy,
    'set_price', v_set, 'ppp_multiplier', v_ppp,
    'fx_mentor_customer', v_fx_mc, 'fx_customer_inr', v_fx_c_inr, 'fx_mentor_inr', v_fx_m_inr,
    'gross_customer', v_gross, 'fee_pct', v_fee_pct, 'fee_amount', v_fee,
    'net_customer', v_net_cust, 'net_mentor', v_net_mentor);
END; $$;
GRANT EXECUTE ON FUNCTION compute_booking_price(UUID, TEXT) TO anon, authenticated;

-- Issue a binding 10-minute quote.
CREATE OR REPLACE FUNCTION get_booking_quote(p_service_id UUID, p_customer_country TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_snap JSONB; v_hash TEXT; v_id UUID; v_exp TIMESTAMPTZ;
BEGIN
  v_snap := compute_booking_price(p_service_id, p_customer_country);
  -- extensions.digest, schema-qualified: Supabase installs pgcrypto into the
  -- `extensions` schema, not `public`. A plain session (whose default
  -- search_path includes both) can call digest() unqualified, but this
  -- function's own `SET search_path = public` deliberately excludes
  -- `extensions` for security, so unqualified digest() was never resolvable
  -- in here at all — confirmed by actually running this against live
  -- Postgres for the first time (every digest() call in this file had the
  -- same latent bug, 6 call sites total). Matches immigroov's own source
  -- (0065_pricing_engine.sql), which already schema-qualifies this call —
  -- an inconsistency introduced during the port, not a source-spec question.
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

-- Read-only DISPLAY pricing (soft FX fallback) — for browsing (homepage cards,
-- service lists). No quote row, no fee. If FX is unavailable it shows the
-- mentor-currency price (fx_ok=false) rather than failing the page; the
-- binding quote/booking still enforces fresh FX via get_fx().
-- p_items: [{ "key": "<any>", "amount": <num>, "from": "<ccy>", "is_ppp": <bool> }]
CREATE OR REPLACE FUNCTION convert_prices(p_customer_country TEXT, p_items JSONB)
RETURNS TABLE(key TEXT, you NUMERIC, you0 NUMERIC, customer_currency TEXT, fx_ok BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE it JSONB; v_amt NUMERIC; v_from TEXT; v_ppp_on BOOLEAN; v_cust TEXT; v_ppp NUMERIC; v_rate NUMERIC;
BEGIN
  v_cust := currency_for_country(p_customer_country);
  FOR it IN SELECT * FROM jsonb_array_elements(COALESCE(p_items, '[]'::jsonb)) LOOP
    v_amt := COALESCE((it->>'amount')::numeric, 0);
    v_from := COALESCE(it->>'from', 'USD');
    v_ppp_on := COALESCE((it->>'is_ppp')::boolean, false);
    v_ppp := CASE WHEN v_ppp_on THEN get_ppp_factor(p_customer_country) ELSE 1 END;
    v_rate := get_fx_or_null(v_from, v_cust);
    IF v_rate IS NULL THEN
      key := it->>'key'; you0 := ROUND(v_amt, 2); you := ROUND(v_amt * v_ppp, 2);
      customer_currency := UPPER(v_from); fx_ok := false;
    ELSE
      key := it->>'key'; you0 := ROUND(v_amt * v_rate, 2); you := ROUND(v_amt * v_ppp * v_rate, 2);
      customer_currency := v_cust; fx_ok := true;
    END IF;
    RETURN NEXT;
  END LOOP;
END; $$;
GRANT EXECUTE ON FUNCTION convert_prices(TEXT, JSONB) TO anon, authenticated;

-- GC expired quotes (mirrors immigroov's daily 'pricing-quotes-gc' pg_cron job).
-- Pure housekeeping DELETE, no business logic, no external I/O — stays in
-- pg_cron per INFRASTRUCTURE_ARCHITECTURE_PLAN.md (a DB-native job has no
-- reason to wake the application). Guarded the same way as every other
-- pg_cron block in this file so the migration still succeeds where pg_cron
-- isn't enabled.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'pricing-quotes-gc') THEN
      PERFORM cron.unschedule('pricing-quotes-gc');
    END IF;
    PERFORM cron.schedule('pricing-quotes-gc', '30 3 * * *',
      $gc$ DELETE FROM pricing_quotes WHERE expires_at < NOW() - INTERVAL '1 day' $gc$);
  ELSE
    RAISE NOTICE 'pg_cron not enabled — skipping pricing-quotes-gc schedule. Enable it and re-run this block.';
  END IF;
END $$;


-- ###########################################################################
-- Booking lifecycle v2 (folded in from 017_booking_lifecycle_v2.sql)
-- The CREATE OR REPLACE definitions below override the earlier booking RPCs.
-- ###########################################################################
-- ── 1. New columns ───────────────────────────────────────────────────────────
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reschedule_count INT NOT NULL DEFAULT 0;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS no_show_by TEXT;          -- 'mentor' | 'user'
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS no_show_strikes INT NOT NULL DEFAULT 0;
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS last_no_show_at TIMESTAMPTZ;
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS rejection_reason TEXT;   -- admin's note shown to a rejected mentor
ALTER TABLE reschedule_offers ADD COLUMN IF NOT EXISTS was_late BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE reschedule_offers ADD COLUMN IF NOT EXISTS respond_by TIMESTAMPTZ;

-- widen the reschedule_offers status set for the v2 flow
ALTER TABLE reschedule_offers DROP CONSTRAINT IF EXISTS reschedule_offers_status_check;
ALTER TABLE reschedule_offers ADD CONSTRAINT reschedule_offers_status_check
  CHECK (status IN ('pending','mentee_selected','accepted','declined','rejected','superseded','expired'));

-- ── 2. booking_requests (cancel / reschedule approval workflow) ───────────────
CREATE TABLE IF NOT EXISTS booking_requests (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id   UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('cancel','reschedule')),
  initiated_by TEXT NOT NULL CHECK (initiated_by IN ('user','mentor')),
  status       TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','approved','rejected','auto_approved','expired','withdrawn','completed')),
  respond_by   TIMESTAMPTZ,
  note         TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_booking_requests_booking ON booking_requests(booking_id);
CREATE INDEX IF NOT EXISTS idx_booking_requests_open ON booking_requests(status) WHERE status = 'pending';
ALTER TABLE booking_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS booking_requests_read ON booking_requests;
CREATE POLICY booking_requests_read ON booking_requests FOR SELECT USING (TRUE);

-- ── 3. booking_events outbox (FastAPI reads this to send mail for cron events) ─
CREATE TABLE IF NOT EXISTS booking_events (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id  UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  event       TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sent_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_booking_events_unsent ON booking_events(created_at) WHERE sent_at IS NULL;

CREATE OR REPLACE FUNCTION notify_booking_event(p_booking_id UUID, p_event TEXT)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  INSERT INTO booking_events(booking_id, event) VALUES (p_booking_id, p_event);
$$;

-- ── 4. Deadline helpers ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION booking_deadline_state(p_slot TIMESTAMPTZ)
RETURNS TEXT LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN p_slot IS NULL                        THEN 'free'
    WHEN p_slot - NOW() < INTERVAL '2 hours'   THEN 'buffer'
    WHEN p_slot - NOW() < INTERVAL '24 hours'  THEN 'late'
    ELSE 'free'
  END;
$$;

CREATE OR REPLACE FUNCTION response_window(p_slot TIMESTAMPTZ)
RETURNS TIMESTAMPTZ LANGUAGE sql STABLE AS $$
  SELECT LEAST(NOW() + INTERVAL '48 hours', p_slot - INTERVAL '2 hours');
$$;
GRANT EXECUTE ON FUNCTION booking_deadline_state(TIMESTAMPTZ) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION response_window(TIMESTAMPTZ) TO anon, authenticated;

-- ── 4a. Money: customer_payments, mentor_payouts, refunds, webhook events ──────
-- Ported from immigroov's 0009_mock_payment.sql (base shape) + 0063_money_model.sql
-- (authoritative payout columns) + 0072_razorpay_payments.sql (provider/state
-- machine, payment_refunds, payment_events). These tables did not exist at all
-- in groovia-backend before this commit — booking creation wrote no payment
-- record of any kind.
--
-- Two intentional simplifications vs. immigroov's schema, safe because groovia
-- has no existing payment rows to stay backward-compatible with:
--   - customer_payments has ONE state column (`state`), not immigroov's
--     dual status-enum-plus-state-text (that duality existed purely to keep
--     immigroov's old readers working during ITS migration to Razorpay).
--   - mentor_payouts.payout_state is the only status column, same reasoning.
-- Everything that IS load-bearing business logic is kept verbatim, notably
-- mentor_payouts.amount — immigroov's lifecycle-v2 penalty/credit math (in the
-- next commit) reads this exact column as the PRE-FEE mentor-currency payout
-- basis (set_price × ppp_multiplier). It is NOT the same number as
-- net_amount_mentor_currency (which is POST-FEE) — conflating the two would
-- silently shrink every mentor penalty by roughly the platform fee %.

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
CREATE POLICY customer_payments_read ON customer_payments FOR SELECT USING (TRUE);

CREATE TABLE IF NOT EXISTS mentor_payouts (
  id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id                     UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  booking_id                    UUID NOT NULL UNIQUE REFERENCES bookings(id) ON DELETE CASCADE,
  amount                        NUMERIC,   -- LOAD-BEARING: pre-fee mentor-currency basis (set_price × ppp_multiplier); see note above
  gross_amount                  NUMERIC,   -- customer currency
  fee_pct                       NUMERIC,
  platform_fee_amount           NUMERIC,   -- customer currency
  net_amount_customer_currency  NUMERIC,
  net_amount_mentor_currency    NUMERIC,   -- what a real payout would actually transfer (post-fee, mentor currency)
  exchange_rate_used            NUMERIC,   -- customer units per 1 mentor unit
  customer_currency             TEXT,
  mentor_currency                TEXT,
  ppp_multiplier                 NUMERIC,
  method                          TEXT,     -- 'manual' | NULL (RazorpayX auto-payout is out of scope for this module)
  payout_reference                TEXT,
  payout_state                    TEXT NOT NULL DEFAULT 'pending'
                                    CHECK (payout_state IN ('pending','paid','void','blocked')),
  paid_date                       TIMESTAMPTZ,
  comments                        TEXT,
  created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mentor_payouts_mentor ON mentor_payouts(mentor_id);
ALTER TABLE mentor_payouts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mentor_payouts_read ON mentor_payouts;
CREATE POLICY mentor_payouts_read ON mentor_payouts FOR SELECT USING (TRUE);

CREATE TABLE IF NOT EXISTS payment_refunds (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id                  UUID NOT NULL REFERENCES customer_payments(id) ON DELETE CASCADE,
  booking_id                  UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  provider_refund_id          TEXT UNIQUE,
  amount_minor                INT NOT NULL,   -- MINOR units (paise/cents) — matches Razorpay's refund API, not customer_payments.amount's decimal
  currency                    TEXT NOT NULL,
  status                      TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created','processed','failed')),
  provider_payload             JSONB,
  provider_error_code         TEXT,
  provider_error_description  TEXT,
  ledger_version               INT,
  created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payment_refunds_booking ON payment_refunds(booking_id);

-- Razorpay webhook intake log. event_id is the dedup key (Razorpay retries
-- deliveries; processed_at set means "already handled, no-op on replay").
CREATE TABLE IF NOT EXISTS payment_events (
  event_id         TEXT PRIMARY KEY,
  type              TEXT,
  payload           JSONB,
  signature         TEXT,
  attempt_count     INT NOT NULL DEFAULT 0,
  last_attempt_at   TIMESTAMPTZ,
  next_retry_at     TIMESTAMPTZ,
  processed_at      TIMESTAMPTZ,
  error             TEXT,
  received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ports immigroov's reconcile-payments Edge Function's audit trail. Read-only
-- consumer (db.reconcile_payments) — no RPC needed, plain table access like
-- payment_events/payment_refunds already use from Python.
CREATE TABLE IF NOT EXISTS payment_reconciliation_log (
  id                    BIGSERIAL PRIMARY KEY,
  kind                  TEXT NOT NULL,   -- 'mismatch' | 'fetch_failed'
  provider_payment_id   TEXT,
  booking_id            UUID REFERENCES bookings(id) ON DELETE CASCADE,
  detail                JSONB,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payment_reconciliation_log_booking ON payment_reconciliation_log(booking_id);

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_hold_expires_at TIMESTAMPTZ;  -- 10-min reservation hold
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS customer_currency TEXT;

INSERT INTO platform_settings (key, value, description) VALUES
  ('payments_enabled', 'false', 'false = mock instant-confirm booking; true = real Razorpay reserve->pay->confirm flow')
ON CONFLICT (key) DO NOTHING;

-- ── 4b. Money: booking_ledger + add_ledger ─────────────────────────────────────
-- Ported from immigroov's 0040_lifecycle_v2_foundation.sql + 0063_money_model.sql.
-- Every penalty/refund/credit/charge from the lifecycle functions below is
-- recorded here — this table was the real gap: cancel_booking and friends were
-- ported with the state-machine logic but their `add_ledger(...)` calls were
-- left out (see the "would post to the ledger in Phase 2" comments they shipped
-- with). This commit adds the table + helper; the next commit wires the calls
-- back in, matching immigroov's 0071_lifecycle_consolidation.sql exactly
-- ("Money math is unchanged throughout" — that migration's own words).
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
CREATE POLICY booking_ledger_read ON booking_ledger FOR SELECT USING (TRUE);

-- INR-normalized reporting needs a per-booking FX snapshot. Not populated by
-- anything yet — the reserve_booking/confirm_booking_payment RPCs (Payments
-- module, later commit) will fill these in from the pricing quote. Until then
-- add_ledger's COALESCE(...,1) fallback means normalized_inr_amount == amount,
-- which is only correct for INR-denominated bookings — acceptable for now
-- since no non-mock payment flow exists yet to produce a real fx snapshot.
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS fx_customer_inr NUMERIC;  -- INR per 1 customer-currency unit
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS fx_mentor_inr   NUMERIC;  -- INR per 1 mentor-currency unit

-- add_ledger(booking, party, kind, amount, pct, reason): idempotent-by-call-site
-- money journal insert. Resolves the correct currency for the party (customer
-- vs mentor) from the latest customer_payments/mentor_payouts row for the
-- booking, and normalizes to INR using the booking's frozen FX snapshot.
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

-- ── 4c. Payment state machine + payout admin ops ───────────────────────────────
-- Ported from immigroov's 0072_razorpay_payments.sql. get_app_secret (Vault
-- secret retrieval) is deliberately NOT ported — groovia-backend keeps secrets
-- in config.py/.env, not Supabase Vault. That's an architecture difference
-- (Source of architecture: groovia-backend), not a business-rule change; the
-- FastAPI layer reads RAZORPAY_* env vars directly (see the Payments F commit).

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
    OR p_new_state = 'failed'                                                        -- any -> failed
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

-- Stamp the Razorpay order id onto the 'created' payment row for a booking.
CREATE OR REPLACE FUNCTION set_provider_order(p_booking_id UUID, p_order_id TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE customer_payments SET provider = 'razorpay', provider_order_id = p_order_id
    WHERE booking_id = p_booking_id AND state = 'created';
END;
$$;
REVOKE ALL ON FUNCTION set_provider_order(UUID, TEXT) FROM PUBLIC, anon, authenticated;

-- How much (in MINOR units — paise/cents, matching Razorpay's refund API) is
-- still owed on a booking: sum of ledger 'refund' rows minus refunds already
-- issued. Used by the refund-issuing endpoint so a booking is never refunded
-- twice for the same ledger entry.
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

-- Admin marks a payout as actually paid (manual transfer — RazorpayX auto-payout
-- is out of scope for this module). Guard: only a completed booking's payout,
-- and only if it isn't already void/blocked.
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

-- Admin blocks a payout (compliance hold, dispute, etc). Never overrides an
-- already-paid payout.
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

-- A cancelled/no-show booking's payout is automatically voided (unless it's
-- already paid or blocked) — no manual step needed. Idempotent: only fires
-- when status actually CHANGES into cancelled/no_show.
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

-- Whitelisted public read of a platform_settings value — only 'payments_enabled'
-- is ever returned; anything else raises rather than silently leaking a value
-- that might later be added to platform_settings for internal use only.
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

-- Cheap read-only status poll for checkout pages (webhook-independent
-- confirmation UX: the frontend polls this after redirecting back from Razorpay).
CREATE OR REPLACE FUNCTION booking_status(p_booking_id UUID)
RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT status::text FROM bookings WHERE id = p_booking_id;
$$;
GRANT EXECUTE ON FUNCTION booking_status(UUID) TO anon, authenticated;

-- ── 4d. Quote-based booking creation ────────────────────────────────────────────
-- Ported from immigroov's 0069_payment_reservation_flow.sql + 0072's extension,
-- stripped of the referral params 0086_referral_checkout_wiring.sql later added
-- (referral attribution is a separate, later migration module — out of scope
-- here). This REPLACES book_session's direct-to-confirmed booking creation:
-- every booking now starts 'pending' (a 10-minute payment hold) and only
-- becomes 'confirmed' via confirm_booking_payment, exactly like immigroov's
-- spec. book_session itself is left in place (not dropped) but is no longer
-- called by the FastAPI layer after the Payments G commit — see the note
-- there on why it isn't destructively removed.
--
-- Identity resolution mirrors book_session's existing pattern exactly: if no
-- candidate_id is given, look up profiles by email but do NOT create one —
-- profiles is Supabase-Auth-owned (populated by the handle_new_user trigger),
-- unlike immigroov's users table which book_session_guest could insert into
-- freely. A guest who never signs up simply has candidate_id = NULL and their
-- identity lives in candidate_email/candidate_name.
-- Discount design (per immigroov's 0086_referral_checkout_wiring.sql):
-- discount_pct lives on the referral_codes row (not a global setting), so
-- each affiliate code carries its own predictable, fixed rate. Immigroov
-- absorbs the discount: only the customer-facing figures (customer_payments,
-- booking_pricing's informational gross/fee/net) are reduced. The mentor's
-- actual payout basis (mentor_payouts) is UNTOUCHED — computed from the
-- original quote snapshot exactly as before, so the mentor is paid in full
-- regardless of any discount applied. redemption_count only increments at
-- CONFIRMATION time (inside resolve_referral_attribution, called from
-- confirm_booking_payment) — an abandoned, never-paid reservation must not
-- consume a code's redemption cap.
CREATE OR REPLACE FUNCTION reserve_booking(
  p_quote_id UUID, p_mentor_id UUID, p_service_id UUID, p_slot_time TIMESTAMPTZ,
  p_email TEXT, p_name TEXT DEFAULT NULL, p_timezone TEXT DEFAULT 'UTC',
  p_answers JSONB DEFAULT '[]', p_specific_availability_id UUID DEFAULT NULL,
  p_candidate_id UUID DEFAULT NULL,
  p_referral_session_token TEXT DEFAULT NULL, p_referral_code TEXT DEFAULT NULL
) RETURNS TABLE(booking_id UUID, amount NUMERIC, currency TEXT, hold_expires_at TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  q pricing_quotes%ROWTYPE;
  s JSONB;
  v_booking_id UUID;
  v_hold_expires_at TIMESTAMPTZ := NOW() + INTERVAL '10 minutes';
  v_discount_pct NUMERIC := 0; v_gross NUMERIC; v_fee_amount NUMERIC; v_net_customer NUMERIC;
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

  IF p_referral_code IS NOT NULL AND TRIM(p_referral_code) <> '' THEN
    SELECT discount_pct INTO v_discount_pct FROM referral_codes
      WHERE code_string = UPPER(TRIM(p_referral_code)) AND expires_at > NOW() AND redemption_count < redemption_cap;
    v_discount_pct := COALESCE(v_discount_pct, 0);
  END IF;
  v_gross := ROUND((s->>'gross_customer')::numeric * (1 - v_discount_pct / 100.0), 2);
  v_fee_amount := ROUND(v_gross * (s->>'fee_pct')::numeric / 100.0, 2);
  v_net_customer := v_gross - v_fee_amount;

  BEGIN
    INSERT INTO bookings(
      mentor_id, candidate_id, candidate_email, candidate_name,
      service_id, slot_time, status, attendee_timezone,
      specific_availability_id, source,
      customer_currency, fx_customer_inr, fx_mentor_inr, payment_hold_expires_at,
      referral_session_token, referral_code, referral_discount_applied_pct
    ) VALUES (
      p_mentor_id, p_candidate_id, LOWER(p_email), p_name,
      p_service_id, p_slot_time, 'pending', p_timezone,
      p_specific_availability_id, 'direct',
      s->>'customer_currency', NULLIF((s->>'fx_customer_inr')::numeric, 0), NULLIF((s->>'fx_mentor_inr')::numeric, 0),
      v_hold_expires_at,
      NULLIF(TRIM(COALESCE(p_referral_session_token, '')), ''), NULLIF(TRIM(COALESCE(p_referral_code, '')), ''), v_discount_pct
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

  -- Mentor payout basis is computed from the ORIGINAL quote snapshot only —
  -- the discount never reaches here (see the function-level note above).
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
GRANT EXECUTE ON FUNCTION reserve_booking(UUID, UUID, UUID, TIMESTAMPTZ, TEXT, TEXT, TEXT, JSONB, UUID, UUID, TEXT, TEXT) TO anon, authenticated;

-- Finalize a payment hold into a confirmed booking. Idempotent (a webhook
-- retry or a duplicate confirm call is a no-op once already confirmed).
-- Guards against confirming a hold that already expired (the janitor / a
-- reconciliation pass may have moved it on) — callers must treat HOLD_EXPIRED
-- as "issue a refund", not retry. Resolves referral attribution here (not in
-- reserve_booking) so it fires exactly once, only for bookings that were
-- genuinely paid — matching immigroov's confirm_booking_payment exactly.
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

  IF b.referral_session_token IS NOT NULL OR b.referral_code IS NOT NULL THEN
    IF b.candidate_email IS NOT NULL THEN
      PERFORM resolve_referral_attribution(b.referral_session_token, b.candidate_email, b.referral_code);
    END IF;
  END IF;

  RETURN 'confirmed';
END;
$$;
REVOKE ALL ON FUNCTION confirm_booking_payment(UUID, TEXT) FROM PUBLIC, anon, authenticated;

-- Ports immigroov's expire_stale_holds (0069) — the janitor for reserve_booking's
-- 10-min payment hold. Without something calling this, a 'pending' hold whose
-- payer never completes checkout occupies the slot forever (bookings_no_overlap
-- blocks every other customer from that slot indefinitely) and the orphaned
-- customer_payments row sits at 'created' forever instead of 'failed'. Service-role
-- only, same as the source — never grant to anon/authenticated.
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

-- ── 5. Cancel flow (REPLACES the old block-on-late cancel_booking) ────────────
-- >=24h: cancelled immediately · 2–24h (user): opens a cancel request for mentor
-- approval · <2h: blocked. Mentor cancel is always allowed (>=2h) and is free
-- >=24h, bumps the cancellation counter when late. Auth is enforced in FastAPI.
CREATE OR REPLACE FUNCTION cancel_booking(p_booking_id UUID, p_cancelled_by TEXT DEFAULT 'user')
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_state TEXT; v_cost NUMERIC; v_payout NUMERIC;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking % not found', p_booking_id; END IF;
  IF b.status IN ('cancelled','completed','no_show') THEN
    RAISE EXCEPTION 'Booking % is already %', p_booking_id, b.status;
  END IF;

  v_state := booking_deadline_state(b.slot_time);
  IF v_state = 'buffer' THEN
    RAISE EXCEPTION 'Within 2 hours of the session — it can no longer be cancelled here. Please contact the other party.';
  END IF;

  SELECT amount INTO v_cost   FROM customer_payments WHERE booking_id = p_booking_id ORDER BY created_at DESC LIMIT 1;
  SELECT amount INTO v_payout FROM mentor_payouts    WHERE booking_id = p_booking_id ORDER BY created_at DESC LIMIT 1;

  IF p_cancelled_by = 'mentor' THEN
    UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id RETURNING * INTO b;
    PERFORM add_ledger(p_booking_id, 'customer', 'refund', v_cost, 100, 'Mentor cancelled — full refund');
    IF v_state = 'late' THEN
      PERFORM add_ledger(p_booking_id, 'mentor', 'penalty', v_payout * 0.25, 25, 'Late mentor cancel (<24h)');
      PERFORM bump_mentor_cancellation(b.mentor_id);
    END IF;
    UPDATE reschedule_offers SET status = 'superseded'
      WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
    PERFORM notify_booking_event(p_booking_id, 'cancelled');
    RETURN b;
  END IF;

  IF v_state = 'free' THEN
    UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id RETURNING * INTO b;
    PERFORM add_ledger(p_booking_id, 'customer', 'refund', v_cost, 100, 'Customer cancelled (>=24h) — full refund');
    UPDATE reschedule_offers SET status = 'superseded'
      WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
    PERFORM notify_booking_event(p_booking_id, 'cancelled');
    RETURN b;
  ELSE
    -- late: booking stays confirmed; open a cancel request for the mentor. No
    -- ledger write here — the outcome (and its ledger entry) is decided by
    -- respond_booking_request once the mentor answers (or the cron auto-approves).
    UPDATE booking_requests SET status = 'withdrawn', resolved_at = NOW()
      WHERE booking_id = p_booking_id AND status = 'pending';
    INSERT INTO booking_requests(booking_id, kind, initiated_by, status, respond_by, note)
      VALUES (p_booking_id, 'cancel', 'user', 'pending', response_window(b.slot_time),
              'User requested late cancellation');
    PERFORM notify_booking_event(p_booking_id, 'cancel_requested');
    RETURN b;
  END IF;
END;
$$;
GRANT EXECUTE ON FUNCTION cancel_booking(UUID, TEXT) TO authenticated;

-- ── 6. Respond to a cancel / reschedule request (mentor side) ─────────────────
CREATE OR REPLACE FUNCTION respond_booking_request(p_request_id UUID, p_accept BOOLEAN)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r booking_requests; v_cost NUMERIC;
BEGIN
  SELECT * INTO r FROM booking_requests WHERE id = p_request_id;
  IF NOT FOUND OR r.status <> 'pending' THEN RAISE EXCEPTION 'This request is no longer open'; END IF;
  SELECT amount INTO v_cost FROM customer_payments WHERE booking_id = r.booking_id ORDER BY created_at DESC LIMIT 1;

  IF r.kind = 'cancel' THEN
    -- A late cancel resolves to cancelled either way. Accept = mentor's
    -- goodwill, full refund. Reject = customer keeps half, forfeits half.
    UPDATE bookings SET status = 'cancelled' WHERE id = r.booking_id;
    IF p_accept THEN
      PERFORM add_ledger(r.booking_id, 'customer', 'refund', v_cost, 100, 'Late cancel approved — full refund');
    ELSE
      PERFORM add_ledger(r.booking_id, 'customer', 'charge', v_cost * 0.5, 50, 'Late cancel rejected — 50% fee kept');
      PERFORM add_ledger(r.booking_id, 'customer', 'refund', v_cost * 0.5, 50, 'Late cancel rejected — 50% refunded');
    END IF;
    UPDATE booking_requests
       SET status = CASE WHEN p_accept THEN 'approved' ELSE 'rejected' END, resolved_at = NOW()
     WHERE id = p_request_id;
    UPDATE reschedule_offers SET status = 'superseded'
      WHERE booking_id = r.booking_id AND status IN ('pending','mentee_selected');
    PERFORM notify_booking_event(r.booking_id, 'cancelled');
  ELSIF r.kind = 'reschedule' THEN
    IF p_accept THEN
      UPDATE booking_requests SET status = 'approved', resolved_at = NOW() WHERE id = p_request_id;
      PERFORM notify_booking_event(r.booking_id, 'reschedule_approved');
    ELSE
      UPDATE booking_requests SET status = 'rejected', resolved_at = NOW() WHERE id = p_request_id;
      PERFORM notify_booking_event(r.booking_id, 'reschedule_rejected');
    END IF;
  END IF;
END;
$$;
GRANT EXECUTE ON FUNCTION respond_booking_request(UUID, BOOLEAN) TO authenticated;

-- ── 7. force_autocancel (3rd reschedule attempt) — state only ─────────────────
CREATE OR REPLACE FUNCTION force_autocancel(p_booking_id UUID, p_initiator TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_status TEXT; v_cost NUMERIC; v_payout NUMERIC;
BEGIN
  SELECT status INTO v_status FROM bookings WHERE id = p_booking_id;
  IF v_status IS NULL OR v_status IN ('cancelled','completed','no_show') THEN RETURN; END IF;  -- idempotent
  SELECT amount INTO v_cost   FROM customer_payments WHERE booking_id = p_booking_id ORDER BY created_at DESC LIMIT 1;
  SELECT amount INTO v_payout FROM mentor_payouts    WHERE booking_id = p_booking_id ORDER BY created_at DESC LIMIT 1;

  UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id;
  PERFORM add_ledger(p_booking_id, 'customer', 'refund', v_cost, 100, '3rd reschedule attempt — auto-cancel, full refund');
  IF p_initiator = 'mentor' THEN
    PERFORM add_ledger(p_booking_id, 'mentor', 'penalty', v_payout, 100, '3rd reschedule attempt — 100% penalty');
  ELSE
    PERFORM add_ledger(p_booking_id, 'customer', 'penalty', v_cost, 100, '3rd reschedule attempt — 100% penalty');
  END IF;
  UPDATE reschedule_offers SET status = 'superseded'
    WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
  PERFORM notify_booking_event(p_booking_id, 'cancelled');
END;
$$;
GRANT EXECUTE ON FUNCTION force_autocancel(UUID, TEXT) TO authenticated;

-- ── 8. Reschedule — mentor proposes a date + range ────────────────────────────
CREATE OR REPLACE FUNCTION mentor_propose_reschedule(
  p_booking_id UUID, p_date DATE, p_start TIMESTAMPTZ, p_end TIMESTAMPTZ
) RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_id UUID; b bookings;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking not found'; END IF;
  IF b.status IN ('cancelled','completed','no_show') THEN
    RAISE EXCEPTION 'Cannot reschedule booking with status %', b.status;
  END IF;
  IF b.reschedule_count >= 2 THEN
    PERFORM force_autocancel(p_booking_id, 'mentor');
    RETURN NULL;
  END IF;
  IF p_end <= p_start THEN RAISE EXCEPTION 'Range end must be after start'; END IF;
  UPDATE reschedule_offers SET status = 'superseded'
    WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
  INSERT INTO reschedule_offers(booking_id, proposed_by, offer_date, range_start, range_end,
                                status, was_late, respond_by)
    VALUES (p_booking_id, 'mentor', p_date, p_start, p_end, 'pending',
            booking_deadline_state(b.slot_time) = 'late', response_window(b.slot_time))
    RETURNING id INTO v_id;
  PERFORM notify_booking_event(p_booking_id, 'proposed');
  RETURN v_id;
END;
$$;

-- ── 9. Mentee accepts a slot in the proposed range — finalises directly ───────
CREATE OR REPLACE FUNCTION mentee_accept_reschedule(p_offer_id UUID, p_slot_time TIMESTAMPTZ)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE o reschedule_offers; b bookings; v_payout NUMERIC;
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
    RAISE EXCEPTION 'That time is no longer available — pick another slot inside the range.';
  END IF;
  UPDATE reschedule_offers SET status = 'accepted', selected_time = p_slot_time WHERE id = p_offer_id;
  UPDATE bookings SET slot_time = p_slot_time, slot_end = NULL, status = 'rescheduled',
                      reschedule_count = reschedule_count + 1
    WHERE id = o.booking_id RETURNING * INTO b;
  DELETE FROM booking_reminders WHERE booking_id = o.booking_id;
  IF o.was_late THEN
    SELECT amount INTO v_payout FROM mentor_payouts WHERE booking_id = o.booking_id ORDER BY created_at DESC LIMIT 1;
    PERFORM add_ledger(o.booking_id, 'mentor', 'penalty', v_payout * 0.25, 25, 'Late mentor reschedule (<24h)');
  END IF;
  PERFORM notify_booking_event(o.booking_id, 'rescheduled');
  RETURN b;
END;
$$;

-- ── 10. Mentee rejects the mentor's proposal — booking cancelled ──────────────
CREATE OR REPLACE FUNCTION mentee_reject_reschedule(p_offer_id UUID)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE o reschedule_offers; b bookings; v_cost NUMERIC; v_payout NUMERIC;
BEGIN
  SELECT * INTO o FROM reschedule_offers WHERE id = p_offer_id;
  IF NOT FOUND OR o.status NOT IN ('pending','mentee_selected') OR o.proposed_by <> 'mentor' THEN
    RAISE EXCEPTION 'This proposal is no longer open';
  END IF;
  SELECT amount INTO v_cost   FROM customer_payments WHERE booking_id = o.booking_id ORDER BY created_at DESC LIMIT 1;
  SELECT amount INTO v_payout FROM mentor_payouts    WHERE booking_id = o.booking_id ORDER BY created_at DESC LIMIT 1;
  UPDATE reschedule_offers SET status = 'rejected' WHERE id = p_offer_id;
  UPDATE bookings SET status = 'cancelled' WHERE id = o.booking_id RETURNING * INTO b;
  IF o.was_late THEN
    PERFORM add_ledger(o.booking_id, 'customer', 'refund', v_cost, 100, 'Rejected late mentor reschedule — full refund');
    PERFORM add_ledger(o.booking_id, 'mentor', 'penalty', v_payout * 0.25, 25, 'Customer rejected late reschedule');
  ELSE
    PERFORM add_ledger(o.booking_id, 'customer', 'credit', v_cost, 100, 'Rejected reschedule — credit for a future booking');
  END IF;
  PERFORM notify_booking_event(o.booking_id, 'cancelled');
  RETURN b;
END;
$$;

-- ── 11. Customer-initiated reschedule (free path picks a slot directly) ───────
CREATE OR REPLACE FUNCTION customer_reschedule(p_booking_id UUID, p_slot_time TIMESTAMPTZ)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_state TEXT; v_approved BOOLEAN;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking not found'; END IF;
  IF b.status IN ('cancelled','completed','no_show') THEN
    RAISE EXCEPTION 'Cannot reschedule booking with status %', b.status;
  END IF;
  IF b.reschedule_count >= 2 THEN
    PERFORM force_autocancel(p_booking_id, 'user');
    RETURN 'autocancelled';
  END IF;
  v_state := booking_deadline_state(b.slot_time);
  IF v_state = 'buffer' THEN RAISE EXCEPTION 'Within 2 hours of the session — cannot reschedule.'; END IF;
  v_approved := EXISTS (SELECT 1 FROM booking_requests
                        WHERE booking_id = p_booking_id AND kind = 'reschedule'
                          AND status IN ('approved','auto_approved'));
  IF v_state <> 'free' AND NOT v_approved THEN
    RAISE EXCEPTION 'A late reschedule needs mentor approval first — send a request.';
  END IF;
  IF NOT is_slot_available(b.mentor_id, b.service_id, p_slot_time) THEN
    RAISE EXCEPTION 'That time is not available — pick another slot.';
  END IF;
  UPDATE bookings SET slot_time = p_slot_time, slot_end = NULL, status = 'rescheduled',
                      reschedule_count = reschedule_count + 1
    WHERE id = p_booking_id;
  DELETE FROM booking_reminders WHERE booking_id = p_booking_id;
  UPDATE booking_requests SET status = 'completed', resolved_at = NOW()
    WHERE booking_id = p_booking_id AND kind = 'reschedule' AND status IN ('approved','auto_approved');
  PERFORM notify_booking_event(p_booking_id, 'rescheduled');
  RETURN 'rescheduled';
END;
$$;

-- ── 12. Customer requests a late reschedule (needs mentor approval) ───────────
CREATE OR REPLACE FUNCTION request_reschedule(p_booking_id UUID)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_id UUID;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking not found'; END IF;
  IF b.status IN ('cancelled','completed','no_show') THEN
    RAISE EXCEPTION 'Cannot reschedule booking with status %', b.status;
  END IF;
  IF b.reschedule_count >= 2 THEN
    PERFORM force_autocancel(p_booking_id, 'user');
    RETURN NULL;
  END IF;
  IF booking_deadline_state(b.slot_time) = 'buffer' THEN
    RAISE EXCEPTION 'Within 2 hours of the session — cannot reschedule.';
  END IF;
  UPDATE booking_requests SET status = 'withdrawn', resolved_at = NOW()
    WHERE booking_id = p_booking_id AND status = 'pending';
  INSERT INTO booking_requests(booking_id, kind, initiated_by, status, respond_by, note)
    VALUES (p_booking_id, 'reschedule', 'user', 'pending', response_window(b.slot_time),
            'User requested reschedule')
    RETURNING id INTO v_id;
  PERFORM notify_booking_event(p_booking_id, 'reschedule_requested');
  RETURN v_id;
END;
$$;

GRANT EXECUTE ON FUNCTION mentor_propose_reschedule(UUID, DATE, TIMESTAMPTZ, TIMESTAMPTZ) TO authenticated;
GRANT EXECUTE ON FUNCTION mentee_accept_reschedule(UUID, TIMESTAMPTZ) TO authenticated;
GRANT EXECUTE ON FUNCTION mentee_reject_reschedule(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION customer_reschedule(UUID, TIMESTAMPTZ) TO authenticated;
GRANT EXECUTE ON FUNCTION request_reschedule(UUID) TO authenticated;

-- ── 13. No-show: strike ladder (counting only — no payout penalty) ────────────
CREATE OR REPLACE FUNCTION apply_mentor_strike(p_mentor_id UUID)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_str INT; v_last TIMESTAMPTZ;
BEGIN
  SELECT no_show_strikes, last_no_show_at INTO v_str, v_last FROM mentors WHERE id = p_mentor_id;
  IF v_last IS NULL OR v_last < NOW() - INTERVAL '90 days' THEN v_str := 0; END IF;
  v_str := COALESCE(v_str, 0) + 1;
  UPDATE mentors SET no_show_strikes = v_str, last_no_show_at = NOW() WHERE id = p_mentor_id;
  RETURN v_str;
END;
$$;

-- Report a no-show (allowed only 10 min after the start). p_no_show_party: 'mentor'|'user'.
CREATE OR REPLACE FUNCTION flag_no_show(p_booking_id UUID, p_no_show_party TEXT)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings;
BEGIN
  IF p_no_show_party NOT IN ('mentor','user') THEN
    RAISE EXCEPTION 'no_show_party must be mentor or user';
  END IF;
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking not found'; END IF;
  IF b.status NOT IN ('confirmed','rescheduled') THEN
    RAISE EXCEPTION 'Only an active session can be reported as a no-show';
  END IF;
  IF b.slot_time IS NULL OR NOW() < b.slot_time + INTERVAL '10 minutes' THEN
    RAISE EXCEPTION 'No-shows can only be reported 10 minutes after the start time';
  END IF;
  UPDATE bookings SET status = 'no_show', no_show_by = p_no_show_party
    WHERE id = p_booking_id RETURNING * INTO b;
  UPDATE reschedule_offers SET status = 'superseded'
    WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
  UPDATE booking_requests SET status = 'withdrawn', resolved_at = NOW()
    WHERE booking_id = p_booking_id AND status = 'pending';
  PERFORM notify_booking_event(p_booking_id, 'no_show');
  IF p_no_show_party = 'mentor' THEN
    PERFORM freeze_referral_attribution(p_booking_id);  -- referral system hook (immigroov 0078)
  END IF;
  RETURN b;
END;
$$;

-- Mentor no-showed → the user picks one of three outcomes.
CREATE OR REPLACE FUNCTION resolve_mentor_no_show(p_booking_id UUID, p_choice TEXT)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_cost NUMERIC;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF b.no_show_by IS DISTINCT FROM 'mentor' OR b.status <> 'no_show' THEN
    RAISE EXCEPTION 'Not a mentor no-show awaiting resolution';
  END IF;
  SELECT amount INTO v_cost FROM customer_payments WHERE booking_id = p_booking_id ORDER BY created_at DESC LIMIT 1;
  IF p_choice = 'rebook_same' THEN
    UPDATE bookings SET status = 'confirmed', no_show_by = NULL WHERE id = p_booking_id RETURNING * INTO b;
  ELSIF p_choice = 'rebook_different' THEN
    UPDATE bookings SET status = 'cancelled', no_show_by = NULL WHERE id = p_booking_id RETURNING * INTO b;  -- close the window
    PERFORM apply_mentor_strike(b.mentor_id);
    PERFORM add_ledger(p_booking_id, 'customer', 'credit', v_cost, 100, 'Mentor no-show — credit to rebook another mentor');
  ELSIF p_choice = 'refund' THEN
    UPDATE bookings SET status = 'cancelled', no_show_by = NULL WHERE id = p_booking_id RETURNING * INTO b;  -- close the window
    PERFORM apply_mentor_strike(b.mentor_id);
    PERFORM add_ledger(p_booking_id, 'customer', 'refund', v_cost, 100, 'Mentor no-show — full refund');
  ELSE
    RAISE EXCEPTION 'Unknown choice %', p_choice;
  END IF;
  PERFORM unfreeze_referral_attribution(p_booking_id);  -- referral system hook (immigroov 0078); fires for all 3 choices
  RETURN b;
END;
$$;

-- User no-showed → the mentor picks one of two outcomes.
CREATE OR REPLACE FUNCTION resolve_customer_no_show(p_booking_id UUID, p_choice TEXT)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_payout NUMERIC;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF b.no_show_by IS DISTINCT FROM 'user' OR b.status <> 'no_show' THEN
    RAISE EXCEPTION 'Not a user no-show awaiting resolution';
  END IF;
  IF p_choice = 'accept_rebook' THEN
    UPDATE bookings SET status = 'confirmed', no_show_by = NULL WHERE id = p_booking_id RETURNING * INTO b;
  ELSIF p_choice = 'reject' THEN
    SELECT amount INTO v_payout FROM mentor_payouts WHERE booking_id = p_booking_id ORDER BY created_at DESC LIMIT 1;
    UPDATE bookings SET status = 'completed' WHERE id = p_booking_id RETURNING * INTO b;
    PERFORM add_ledger(p_booking_id, 'mentor', 'credit', v_payout, 100, 'Customer no-show — session closed, mentor paid in full');
  ELSE
    RAISE EXCEPTION 'Unknown choice %', p_choice;
  END IF;
  RETURN b;
END;
$$;

GRANT EXECUTE ON FUNCTION flag_no_show(UUID, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION resolve_mentor_no_show(UUID, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION resolve_customer_no_show(UUID, TEXT) TO authenticated;

-- ── 14. Cron-driven transitions ──────────────────────────────────────────────
CREATE OR REPLACE FUNCTION resolve_expired_requests()
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r RECORD; n INT := 0;
BEGIN
  FOR r IN SELECT * FROM booking_requests WHERE status = 'pending' AND respond_by < NOW() LOOP
    PERFORM respond_booking_request(r.id, TRUE);     -- no response = auto-approve
    UPDATE booking_requests SET status = 'auto_approved' WHERE id = r.id;
    n := n + 1;
  END LOOP;
  -- stale mentor proposals with no customer response expire silently
  UPDATE reschedule_offers SET status = 'expired'
    WHERE status IN ('pending','mentee_selected') AND proposed_by = 'mentor'
      AND respond_by IS NOT NULL AND respond_by < NOW();
  RETURN n;
END;
$$;

INSERT INTO platform_settings (key, value, description) VALUES
  ('review_token_expiry_days', '90', 'How many days a post-session review link stays valid')
ON CONFLICT (key) DO NOTHING;

-- Reviews hook (added when the Reviews module landed): every booking that
-- transitions to 'completed' here gets a one-per-booking review token,
-- matching immigroov's integration point in its own mark_past_bookings_completed
-- (0099). NOT ported: the review-request EMAIL immigroov sends in the same
-- pass via pg_net (app_send_email) — groovia has no equivalent SQL-side HTTP
-- capability, and this function runs on a pg_cron schedule with no FastAPI
-- request to hang a BackgroundTask off of. The token is generated and ready;
-- wiring up the nudge email is a follow-up (candidates: a periodic Python
-- poller, or surfacing "Leave a review" directly in the sessions dashboard
-- so the email isn't the only path to it).
CREATE OR REPLACE FUNCTION mark_past_bookings_completed()
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE n INT := 0; v_token_days INT; r RECORD;
BEGIN
  v_token_days := COALESCE((SELECT value::int FROM platform_settings WHERE key = 'review_token_expiry_days'), 90);

  FOR r IN
    UPDATE bookings SET status = 'completed'
     WHERE status IN ('confirmed','rescheduled')
       AND slot_end IS NOT NULL AND slot_end < NOW()
     RETURNING *
  LOOP
    n := n + 1;
    INSERT INTO review_email_tokens (booking_id, expires_at)
      VALUES (r.id, NOW() + (v_token_days || ' days')::interval)
      ON CONFLICT (booking_id) DO NOTHING;
  END LOOP;

  RETURN n;
END;
$$;

-- Session reminder emails (immigroov: process_due_reminders / process_mentor_reminders,
-- 4 pg_cron jobs — reminders-24h, reminders-1h, mentor-reminders-1h, mentor-reminders-10m).
-- Sends email, so this is FastAPI/dispatcher-managed, not pg_cron — see
-- INFRASTRUCTURE_ARCHITECTURE_PLAN.md §1. No scheduler exists yet; these back
-- admin-triggerable endpoints only until the dispatcher lands.
--
-- Built claim-then-send from day one (not a retrofit): booking_reminders'
-- existing UNIQUE(booking_id, kind) is used as the atomic claim, not just a
-- post-send dedup record. The INSERT ... ON CONFLICT DO NOTHING ... RETURNING
-- IS the claim — Postgres's row-level locking on that INSERT guarantees a
-- given (booking, kind) is returned to at most one caller, so the FastAPI
-- layer only sends for bookings this specific call actually claimed. This is
-- the same pattern claim_due_webinar_reminders() uses, and the reason
-- DISPATCHER_SAFETY_CHECKLIST.md classifies this job as safe-by-construction
-- rather than "needs a fix before porting" — there is no separate mark step
-- to forget.
CREATE OR REPLACE FUNCTION claim_due_customer_reminders(p_kind TEXT, p_lo_minutes INT, p_hi_minutes INT)
RETURNS TABLE(
  booking_id UUID, email TEXT, first_name TEXT, slot_utc TIMESTAMPTZ,
  customer_tz TEXT, other_party_name TEXT, meeting_url TEXT
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  RETURN QUERY
  WITH claimed AS (
    INSERT INTO booking_reminders(booking_id, kind)
    SELECT b.id, p_kind
    FROM bookings b
    WHERE b.status IN ('confirmed','rescheduled')
      AND b.slot_time BETWEEN NOW() + (p_lo_minutes || ' minutes')::interval
                           AND NOW() + (p_hi_minutes || ' minutes')::interval
    ON CONFLICT (booking_id, kind) DO NOTHING
    RETURNING booking_id
  )
  SELECT b.id, COALESCE(b.candidate_email, p.email), COALESCE(p.display_name, p.full_name, b.candidate_name),
         b.slot_time, COALESCE(b.attendee_timezone, p.timezone, 'UTC'), m.display_name, b.meeting_url
  FROM claimed c
  JOIN bookings b ON b.id = c.booking_id
  LEFT JOIN profiles p ON p.id = b.candidate_id
  JOIN mentors m ON m.id = b.mentor_id
  WHERE COALESCE(b.candidate_email, p.email) IS NOT NULL;
END;
$$;
GRANT EXECUTE ON FUNCTION claim_due_customer_reminders(TEXT, INT, INT) TO authenticated;

CREATE OR REPLACE FUNCTION claim_due_mentor_reminders(p_kind TEXT, p_lo_minutes INT, p_hi_minutes INT)
RETURNS TABLE(
  booking_id UUID, email TEXT, first_name TEXT, slot_utc TIMESTAMPTZ,
  mentor_tz TEXT, other_party_name TEXT, meeting_url TEXT
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  RETURN QUERY
  WITH claimed AS (
    INSERT INTO booking_reminders(booking_id, kind)
    SELECT b.id, p_kind
    FROM bookings b
    WHERE b.status IN ('confirmed','rescheduled')
      AND b.slot_time BETWEEN NOW() + (p_lo_minutes || ' minutes')::interval
                           AND NOW() + (p_hi_minutes || ' minutes')::interval
    ON CONFLICT (booking_id, kind) DO NOTHING
    RETURNING booking_id
  )
  SELECT b.id, COALESCE(p.email, m.email), COALESCE(p.display_name, p.full_name, m.display_name),
         b.slot_time, COALESCE(m.app_timezone, 'UTC'), COALESCE(b.candidate_name, b.candidate_email), b.meeting_url
  FROM claimed c
  JOIN bookings b ON b.id = c.booking_id
  JOIN mentors m ON m.id = b.mentor_id
  LEFT JOIN profiles p ON p.id = m.profile_id
  WHERE COALESCE(p.email, m.email) IS NOT NULL;
END;
$$;
GRANT EXECUTE ON FUNCTION claim_due_mentor_reminders(TEXT, INT, INT) TO authenticated;

-- ── Dispatcher-wide lease lock ────────────────────────────────────────────────
-- Render Cron Jobs have no built-in mutex: if a dispatcher tick's runtime ever
-- exceeds its schedule interval (plausible once process_refunds/
-- reconcile_payments/sweep_verify_payments are wired in — each loops calling
-- Razorpay once per row), Render can start the next tick's container while the
-- previous one is still running, and every job in this file would then risk
-- running twice concurrently. See DISPATCHER_SAFETY_CHECKLIST.md's headline
-- finding #2.
--
-- NOT implemented as a literal pg_advisory_lock: this codebase talks to
-- Postgres exclusively through PostgREST/supabase-py's stateless RPC calls
-- (see MIGRATION_STATUS.md's architecture-decision note — the one persistent
-- raw connection, SUPABASE_DB_URL, is reserved for LangGraph's checkpointer).
-- A session-level advisory lock acquired in one RPC call has no guarantee of
-- surviving to the matching unlock call on a connection-pooled interface like
-- PostgREST — it can silently release the moment that request's connection
-- returns to the pool, which defeats the purpose. A TTL-based lease row is the
-- correct equivalent for this connection model: self-expiring (so a crashed
-- dispatcher doesn't wedge the lock forever), and atomic under concurrency via
-- the UPDATE-if-expired-else-INSERT pattern below (a UNIQUE constraint
-- collision on the INSERT is what actually prevents two concurrent acquirers
-- from both succeeding — the "TOCTOU" gap a naive check-then-insert would have
-- is closed by letting Postgres's own constraint be the arbiter, not app code).
CREATE TABLE IF NOT EXISTS dispatcher_locks (
  lock_name   TEXT PRIMARY KEY,
  locked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at  TIMESTAMPTZ NOT NULL
);

CREATE OR REPLACE FUNCTION try_acquire_dispatcher_lock(p_lock_name TEXT, p_ttl_seconds INT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  -- Fast path: an expired lease for this name already exists — reclaim it in place.
  UPDATE dispatcher_locks
    SET locked_at = NOW(), expires_at = NOW() + (p_ttl_seconds || ' seconds')::interval
    WHERE lock_name = p_lock_name AND expires_at < NOW();
  IF FOUND THEN RETURN TRUE; END IF;

  -- No row yet (first-ever acquire) — INSERT. If a concurrent caller's INSERT
  -- wins the race, ours raises unique_violation and we correctly report
  -- "not acquired" rather than silently overwriting a live lease.
  BEGIN
    INSERT INTO dispatcher_locks(lock_name, locked_at, expires_at)
      VALUES (p_lock_name, NOW(), NOW() + (p_ttl_seconds || ' seconds')::interval);
    RETURN TRUE;
  EXCEPTION WHEN unique_violation THEN
    RETURN FALSE;   -- someone else holds a live (non-expired) lease
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

-- pg_cron schedules — guarded so the migration still succeeds where pg_cron is
-- not enabled. Enable it in Supabase (Database → Extensions → pg_cron), then
-- re-run just this block to activate the jobs.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'resolve-requests') THEN
      PERFORM cron.unschedule('resolve-requests');
    END IF;
    PERFORM cron.schedule('resolve-requests', '*/10 * * * *', 'SELECT resolve_expired_requests()');

    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'auto-complete') THEN
      PERFORM cron.unschedule('auto-complete');
    END IF;
    PERFORM cron.schedule('auto-complete', '*/15 * * * *', 'SELECT mark_past_bookings_completed()');

    -- expire_stale_holds: pure SQL, zero external I/O, 1-minute cadence — the
    -- tightest latency requirement of any scheduled job in this codebase.
    -- Belongs in pg_cron, not the application scheduler, per
    -- INFRASTRUCTURE_ARCHITECTURE_PLAN.md §1 (reclassified there from the
    -- admin-endpoint stand-in shipped in an earlier pass — that endpoint,
    -- POST /admin/payments/expire-holds, still exists as a manual "expire now"
    -- ops tool, but this cron entry is the primary trigger going forward).
    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'expire-payment-holds') THEN
      PERFORM cron.unschedule('expire-payment-holds');
    END IF;
    PERFORM cron.schedule('expire-payment-holds', '* * * * *', 'SELECT expire_stale_holds()');
  ELSE
    RAISE NOTICE 'pg_cron not enabled — skipping booking cron schedules. Enable it and re-run the cron block.';
  END IF;
END $$;


-- ###########################################################################
-- Booking read RPCs (folded in from 018_booking_reads.sql)
-- ###########################################################################
-- =============================================================================
-- 018 — Booking read RPCs for the lifecycle-v2 management UI
--
-- Both RPCs return one row per booking with everything the BookingManager needs
-- to render deadline-aware actions: status, the live deadline_state, the latest
-- open reschedule offer, the latest open cancel/reschedule request, no_show_by,
-- and reschedule_count. No money columns (Phase 2).
--
--   my_bookings(candidate_id)  -> the mentee's bookings (other party = mentor)
--   mentor_sessions(mentor_id) -> the mentor's bookings (other party = mentee)
--
-- mentor_sessions REPLACES the 015 version (which dropped no_show/cancelled and
-- lacked v2 fields). Run after 017.
-- =============================================================================

-- ── Mentee view ───────────────────────────────────────────────────────────────
DROP FUNCTION IF EXISTS my_bookings(UUID);
CREATE OR REPLACE FUNCTION my_bookings(p_candidate_id UUID)
RETURNS TABLE (
  id               UUID,
  status           TEXT,
  slot_time        TIMESTAMPTZ,
  slot_end         TIMESTAMPTZ,
  meeting_url      TEXT,
  service_title    TEXT,
  service_duration INTEGER,
  other_name       TEXT,          -- the mentor
  mentor_slug      TEXT,
  mentor_tz        TEXT,
  attendee_tz      TEXT,
  reschedule_count INTEGER,
  no_show_by       TEXT,
  deadline_state   TEXT,
  offer_id         UUID,
  offer_by         TEXT,
  offer_status     TEXT,
  offer_date       DATE,
  range_start      TIMESTAMPTZ,
  range_end        TIMESTAMPTZ,
  selected_time    TIMESTAMPTZ,
  requested_date   DATE,
  req_id           UUID,
  req_kind         TEXT,
  req_initiated_by TEXT,
  req_status       TEXT,
  req_respond_by   TIMESTAMPTZ
) LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT
    b.id, b.status::TEXT, b.slot_time, b.slot_end, b.meeting_url,
    s.title, s.duration,
    m.display_name, m.slug,
    COALESCE(m.app_timezone, 'UTC'),
    COALESCE(b.attendee_timezone, p.timezone, 'UTC'),
    b.reschedule_count, b.no_show_by, booking_deadline_state(b.slot_time),
    ro.id, ro.proposed_by, ro.status, ro.offer_date, ro.range_start, ro.range_end,
    ro.selected_time, ro.requested_date,
    rq.id, rq.kind, rq.initiated_by, rq.status, rq.respond_by
  FROM bookings b
  JOIN  mentors  m ON m.id = b.mentor_id
  LEFT JOIN services s ON s.id = b.service_id
  LEFT JOIN profiles p ON p.id = b.candidate_id
  LEFT JOIN LATERAL (
    SELECT * FROM reschedule_offers
    WHERE booking_id = b.id AND status IN ('pending','mentee_selected')
    ORDER BY created_at DESC LIMIT 1
  ) ro ON TRUE
  LEFT JOIN LATERAL (
    SELECT * FROM booking_requests
    WHERE booking_id = b.id AND status IN ('pending','approved','auto_approved')
    ORDER BY created_at DESC LIMIT 1
  ) rq ON TRUE
  WHERE b.candidate_id = p_candidate_id
  ORDER BY b.slot_time DESC NULLS LAST;
$$;
GRANT EXECUTE ON FUNCTION my_bookings(UUID) TO authenticated;

-- ── Mentor view ───────────────────────────────────────────────────────────────
DROP FUNCTION IF EXISTS mentor_sessions(UUID);
CREATE OR REPLACE FUNCTION mentor_sessions(p_mentor_id UUID)
RETURNS TABLE (
  id                  UUID,
  status              TEXT,
  slot_time           TIMESTAMPTZ,
  slot_end            TIMESTAMPTZ,
  meeting_url         TEXT,
  service_title       TEXT,
  service_duration    INTEGER,
  other_name          TEXT,        -- the mentee
  other_email         TEXT,
  mentor_tz           TEXT,
  attendee_tz         TEXT,
  mentor_confirmed_at TIMESTAMPTZ,
  reschedule_count    INTEGER,
  no_show_by          TEXT,
  deadline_state      TEXT,
  offer_id            UUID,
  offer_by            TEXT,
  offer_status        TEXT,
  offer_date          DATE,
  range_start         TIMESTAMPTZ,
  range_end           TIMESTAMPTZ,
  selected_time       TIMESTAMPTZ,
  requested_date      DATE,
  req_id              UUID,
  req_kind            TEXT,
  req_initiated_by    TEXT,
  req_status          TEXT,
  req_respond_by      TIMESTAMPTZ
) LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT
    b.id, b.status::TEXT, b.slot_time, b.slot_end, b.meeting_url,
    s.title, s.duration,
    COALESCE(p.display_name, p.full_name, b.candidate_name, b.candidate_email),
    COALESCE(b.candidate_email, p.email),
    COALESCE(m.app_timezone, 'UTC'),
    COALESCE(b.attendee_timezone, p.timezone, 'UTC'),
    b.mentor_confirmed_at, b.reschedule_count, b.no_show_by,
    booking_deadline_state(b.slot_time),
    ro.id, ro.proposed_by, ro.status, ro.offer_date, ro.range_start, ro.range_end,
    ro.selected_time, ro.requested_date,
    rq.id, rq.kind, rq.initiated_by, rq.status, rq.respond_by
  FROM bookings b
  JOIN  mentors  m ON m.id = b.mentor_id
  LEFT JOIN services s ON s.id = b.service_id
  LEFT JOIN profiles p ON p.id = b.candidate_id
  LEFT JOIN LATERAL (
    SELECT * FROM reschedule_offers
    WHERE booking_id = b.id AND status IN ('pending','mentee_selected')
    ORDER BY created_at DESC LIMIT 1
  ) ro ON TRUE
  LEFT JOIN LATERAL (
    SELECT * FROM booking_requests
    WHERE booking_id = b.id AND status IN ('pending','approved','auto_approved')
    ORDER BY created_at DESC LIMIT 1
  ) rq ON TRUE
  WHERE b.mentor_id = p_mentor_id
    AND b.slot_time IS NOT NULL
  ORDER BY b.slot_time DESC NULLS LAST;
$$;
GRANT EXECUTE ON FUNCTION mentor_sessions(UUID) TO authenticated;


-- ###########################################################################
-- Reviews — ported from immigroov's 0001 (base table) + 0013 (rating rollup
-- trigger + integrity guard trigger) + 0099 (token-gated submission, star-
-- based moderation gating) + 0101 (FINAL state: edit_review removed —
-- reviews are final on submit; get_review_token_info simplified).
--
-- mentors.avg_rating / mentors.review_count already existed as columns
-- (provisioned ahead of time, never wired to anything) — this is the same
-- "gap" pattern as Payments: columns anticipated, feature never built.
--
-- One intentional simplification vs. immigroov's live schema: no `edited_at`
-- column. immigroov's table still carries it because ALTER TABLE ADD COLUMN
-- was never reverted after 0101 dropped edit_review — but nothing writes to
-- it in the final spec, and groovia has no existing rows to stay compatible
-- with, so it's simply not created. If review editing is ever reintroduced,
-- add it back then.
-- ###########################################################################

CREATE TABLE IF NOT EXISTS reviews (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
  mentor_id    UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  service_id   UUID REFERENCES services(id) ON DELETE SET NULL,
  booking_id   UUID NOT NULL UNIQUE REFERENCES bookings(id) ON DELETE CASCADE,
  rating       INT CHECK (rating BETWEEN 1 AND 5),
  title        TEXT,
  comment      TEXT,
  status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'published', 'rejected')),
  published_at TIMESTAMPTZ,
  reviewed_by  UUID REFERENCES profiles(id),
  review_token UUID,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reviews_mentor_published ON reviews(mentor_id) WHERE status = 'published';
CREATE INDEX IF NOT EXISTS idx_reviews_pending ON reviews(status) WHERE status = 'pending';

-- Public read must only expose PUBLISHED reviews — a pending/rejected review
-- must never leak via a direct table read (admin/customer flows go through
-- SECURITY DEFINER RPCs below, which bypass this and see everything they're
-- entitled to).
ALTER TABLE reviews ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS reviews_public_read ON reviews;
CREATE POLICY reviews_public_read ON reviews FOR SELECT USING (status = 'published');

CREATE TABLE IF NOT EXISTS review_email_tokens (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id UUID NOT NULL UNIQUE REFERENCES bookings(id) ON DELETE CASCADE,
  token      UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
  expires_at TIMESTAMPTZ NOT NULL,
  used_at    TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- No direct policies — reachable only via the SECURITY DEFINER token RPCs
-- below, same masking pattern as booking_ledger/customer_payments.
ALTER TABLE review_email_tokens ENABLE ROW LEVEL SECURITY;

-- Rating rollup must only count PUBLISHED reviews — a pending/rejected
-- review must never move the public average (business rule, from 0099).
CREATE OR REPLACE FUNCTION recompute_mentor_rating(p_mentor_id UUID)
RETURNS VOID LANGUAGE sql AS $$
  UPDATE mentors m SET
    avg_rating = COALESCE((SELECT ROUND(AVG(rating)::numeric, 2) FROM reviews WHERE mentor_id = p_mentor_id AND status = 'published'), 0),
    review_count = (SELECT COUNT(*) FROM reviews WHERE mentor_id = p_mentor_id AND status = 'published')
  WHERE m.id = p_mentor_id;
$$;

-- Trigger helper (must stay in Postgres, not a Python service): any write to
-- reviews, from ANY code path, keeps mentors.avg_rating/review_count in
-- sync. Same reasoning as trg_void_payout_fn in the Payments module.
CREATE OR REPLACE FUNCTION trg_reviews_rollup()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM recompute_mentor_rating(OLD.mentor_id);
    RETURN OLD;
  END IF;
  PERFORM recompute_mentor_rating(NEW.mentor_id);
  IF TG_OP = 'UPDATE' AND OLD.mentor_id IS DISTINCT FROM NEW.mentor_id THEN
    PERFORM recompute_mentor_rating(OLD.mentor_id);
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS reviews_rollup ON reviews;
CREATE TRIGGER reviews_rollup
  AFTER INSERT OR UPDATE OR DELETE ON reviews
  FOR EACH ROW EXECUTE FUNCTION trg_reviews_rollup();

-- Review integrity guard (must stay in Postgres — defense in depth against
-- ANY insert path, not just submit_review below): only a completed booking
-- can be reviewed, the reviewer must match the booking's own candidate
-- (NULL = NULL passes for guest bookings), and mentor_id must match.
-- Adapted from immigroov's trg_review_guard: user_id -> candidate_id.
CREATE OR REPLACE FUNCTION trg_review_guard()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE b bookings;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = NEW.booking_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Booking % not found', NEW.booking_id;
  END IF;
  IF b.status <> 'completed' THEN
    RAISE EXCEPTION 'You can only review a completed session (booking status is %)', b.status;
  END IF;
  IF b.candidate_id IS DISTINCT FROM NEW.candidate_id THEN
    RAISE EXCEPTION 'You can only review your own booking';
  END IF;
  IF b.mentor_id <> NEW.mentor_id THEN
    RAISE EXCEPTION 'mentor_id does not match the booking';
  END IF;
  IF NEW.service_id IS NULL THEN
    NEW.service_id := b.service_id;
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS review_guard ON reviews;
CREATE TRIGGER review_guard
  BEFORE INSERT ON reviews
  FOR EACH ROW EXECUTE FUNCTION trg_review_guard();

-- Public token lookup — feeds the /review/:token page. FINAL (0101) shape:
-- no title/comment/status/editable in the response (edit_review is gone) —
-- just enough to render the form, or the fact a review already exists (with
-- its star count, so a confirmation screen can redisplay it) so the page
-- can't be resubmitted.
CREATE OR REPLACE FUNCTION get_review_token_info(p_token UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE t review_email_tokens; b bookings; v_mentor_name TEXT; v_service_title TEXT; v_rating INT;
BEGIN
  SELECT * INTO t FROM review_email_tokens WHERE token = p_token;
  IF NOT FOUND THEN RAISE EXCEPTION 'Invalid review link'; END IF;
  SELECT * INTO b FROM bookings WHERE id = t.booking_id;
  SELECT display_name INTO v_mentor_name FROM mentors WHERE id = b.mentor_id;
  SELECT title INTO v_service_title FROM services WHERE id = b.service_id;
  SELECT rating INTO v_rating FROM reviews WHERE booking_id = t.booking_id;

  RETURN jsonb_build_object(
    'booking_id', b.id, 'mentor_name', COALESCE(v_mentor_name, 'your mentor'), 'service_title', v_service_title,
    'expired', t.expires_at < NOW(),
    'already_submitted', t.used_at IS NOT NULL,
    'rating', v_rating
  );
END;
$$;
GRANT EXECUTE ON FUNCTION get_review_token_info(UUID) TO anon, authenticated;

-- Submit a review via token. Rating gates publication immediately:
-- 4-5* -> published (visible right away); 1-3* -> pending, held for admin
-- moderation, never counted in avg_rating until approved (the rollup
-- trigger already filters on status). Final on submit — no edit path
-- (immigroov 0101: "reviews are final once submitted").
-- Email dispatch (5* -> notify mentor) is NOT done here — unlike immigroov's
-- pg_net-based app_send_email, groovia sends transactional email from the
-- FastAPI layer via a BackgroundTask, so `rating` is returned to let the
-- caller decide whether to fire that email.
CREATE OR REPLACE FUNCTION submit_review(p_token UUID, p_rating INT, p_title TEXT, p_review TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE t review_email_tokens; b bookings; v_status TEXT; v_review_id UUID;
BEGIN
  IF p_rating NOT BETWEEN 1 AND 5 THEN RAISE EXCEPTION 'Rating must be between 1 and 5'; END IF;
  SELECT * INTO t FROM review_email_tokens WHERE token = p_token FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Invalid review link'; END IF;
  IF t.expires_at < NOW() THEN RAISE EXCEPTION 'This review link has expired'; END IF;
  IF t.used_at IS NOT NULL THEN RAISE EXCEPTION 'A review was already submitted for this session'; END IF;

  SELECT * INTO b FROM bookings WHERE id = t.booking_id;
  IF b.status <> 'completed' THEN RAISE EXCEPTION 'You can only review a completed session'; END IF;

  v_status := CASE WHEN p_rating >= 4 THEN 'published' ELSE 'pending' END;

  INSERT INTO reviews (candidate_id, mentor_id, service_id, booking_id, rating, title, comment, status, published_at, review_token)
    VALUES (b.candidate_id, b.mentor_id, b.service_id, b.id, p_rating, NULLIF(TRIM(COALESCE(p_title, '')), ''), p_review,
            v_status, CASE WHEN v_status = 'published' THEN NOW() ELSE NULL END, p_token)
    RETURNING id INTO v_review_id;

  UPDATE review_email_tokens SET used_at = NOW() WHERE id = t.id;

  RETURN jsonb_build_object('review_id', v_review_id, 'status', v_status, 'rating', p_rating);
END;
$$;
GRANT EXECUTE ON FUNCTION submit_review(UUID, INT, TEXT, TEXT) TO anon, authenticated;

-- Mentor-profile-facing published reviews + rating breakdown histogram.
CREATE OR REPLACE FUNCTION mentor_reviews_public(p_mentor_id UUID, p_limit INT DEFAULT 10, p_offset INT DEFAULT 0)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT jsonb_build_object(
    'breakdown', (
      SELECT jsonb_build_object(
        '5', COUNT(*) FILTER (WHERE rating = 5), '4', COUNT(*) FILTER (WHERE rating = 4),
        '3', COUNT(*) FILTER (WHERE rating = 3), '2', COUNT(*) FILTER (WHERE rating = 2),
        '1', COUNT(*) FILTER (WHERE rating = 1))
      FROM reviews WHERE mentor_id = p_mentor_id AND status = 'published'
    ),
    'reviews', (
      SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'rating', r.rating, 'title', r.title, 'review', r.comment,
        'created_at', r.created_at, 'booking_slot_time', b.slot_time, 'verified_session', true
      ) ORDER BY r.created_at DESC), '[]'::jsonb)
      FROM (
        SELECT * FROM reviews WHERE mentor_id = p_mentor_id AND status = 'published'
        ORDER BY created_at DESC LIMIT p_limit OFFSET p_offset
      ) r
      JOIN bookings b ON b.id = r.booking_id
    )
  );
$$;
GRANT EXECUTE ON FUNCTION mentor_reviews_public(UUID, INT, INT) TO anon, authenticated;

-- Admin moderation queue (1-3* holds) + approve/reject.
CREATE OR REPLACE FUNCTION admin_reviews_queue()
RETURNS TABLE (
  review_id UUID, booking_id UUID, rating INT, title TEXT, review TEXT,
  candidate_email TEXT, mentor_name TEXT, created_at TIMESTAMPTZ
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT r.id, r.booking_id, r.rating, r.title, r.comment,
    COALESCE(b.candidate_email, p.email), COALESCE(m.display_name, 'Mentor'), r.created_at
  FROM reviews r
  JOIN bookings b ON b.id = r.booking_id
  LEFT JOIN profiles p ON p.id = b.candidate_id
  JOIN mentors m ON m.id = r.mentor_id
  WHERE r.status = 'pending'
  ORDER BY r.created_at ASC;
$$;
GRANT EXECUTE ON FUNCTION admin_reviews_queue() TO authenticated;

CREATE OR REPLACE FUNCTION admin_moderate_review(p_review_id UUID, p_decision TEXT, p_admin_user_id UUID DEFAULT NULL)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE rv reviews;
BEGIN
  IF p_decision NOT IN ('approve', 'reject') THEN RAISE EXCEPTION 'decision must be approve or reject'; END IF;
  SELECT * INTO rv FROM reviews WHERE id = p_review_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Review not found'; END IF;
  IF rv.status <> 'pending' THEN RAISE EXCEPTION 'Review is not awaiting moderation (status %)', rv.status; END IF;

  UPDATE reviews SET
    status = CASE WHEN p_decision = 'approve' THEN 'published' ELSE 'rejected' END,
    published_at = CASE WHEN p_decision = 'approve' THEN NOW() ELSE NULL END,
    reviewed_by = p_admin_user_id
  WHERE id = p_review_id;
END;
$$;
GRANT EXECUTE ON FUNCTION admin_moderate_review(UUID, TEXT, UUID) TO authenticated;


-- ###########################################################################
-- Referral / affiliate commission system — ported from immigroov's
-- 0078_referral_system.sql (main build-out) + fix-up migrations 0084, 0085,
-- 0086 (checkout wiring — booking columns only, functions handled in a later
-- commit), 0088, 0089-0091 (admin overview bugfixes), 0092-0098. The LATEST
-- definition of every function/table is what's ported — e.g. commission_ledger
-- amounts include the 0093 FX-conversion fix and process_referral_commissions
-- includes the 0096 booking-matching fix, from the start, not as a later patch.
--
-- immigroov's ORIGINAL referral_links table (0001_initial_schema.sql:
-- referrer_mentor_id, referred_user_id, type) is confirmed dead — nothing in
-- 0078+ reads or writes it. NOT ported; the tables below are an entirely
-- parallel system, not an evolution of that one.
--
-- Naming adaptation (architecture, not business rule): immigroov's
-- affiliates.user_id -> profile_id here, matching groovia's existing FK
-- naming convention (mentors.profile_id, etc.) for a UUID reference to
-- profiles(id). All UUID-vs-bigint PK adaptations follow the same pattern
-- used in every prior module.
-- ###########################################################################

-- ── 1. Core affiliate identity ─────────────────────────────────────────────

-- profile_id is nullable + email is a fallback column (fixed below, right
-- after creation) so an admin can onboard an affiliate BEFORE they've signed
-- up — mirroring the exact pattern already established for mentors
-- (mentors.profile_id nullable + mentors.email fallback + link_mentor_by_email
-- links the account on first login). immigroov doesn't have this problem
-- (its users table is freely insertable), but groovia's profiles table is
-- auth-trigger-owned, so this adaptation is required, not optional.
CREATE TABLE IF NOT EXISTS affiliates (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id         UUID UNIQUE REFERENCES profiles(id) ON DELETE SET NULL,
  mentor_id          UUID REFERENCES mentors(id) ON DELETE SET NULL,
  type               TEXT NOT NULL CHECK (type IN ('mentor', 'non_mentor')),
  payout_details     JSONB,
  audience_corridor  TEXT,
  status             TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'frozen')),
  agreed_terms_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT affiliates_mentor_type_chk CHECK (type <> 'mentor' OR mentor_id IS NOT NULL)
);
-- Idempotent fix for a DB where this table was already created with the
-- earlier (NOT NULL profile_id, no email) shape.
ALTER TABLE affiliates ALTER COLUMN profile_id DROP NOT NULL;
ALTER TABLE affiliates ADD COLUMN IF NOT EXISTS email TEXT;  -- contact email for a not-yet-signed-up affiliate
CREATE UNIQUE INDEX IF NOT EXISTS idx_affiliates_email_lower ON affiliates(LOWER(email)) WHERE email IS NOT NULL;
ALTER TABLE affiliates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS affiliates_self_read ON affiliates;
CREATE POLICY affiliates_self_read ON affiliates FOR SELECT USING (profile_id = auth.uid());

CREATE TABLE IF NOT EXISTS affiliate_links (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  affiliate_id      UUID NOT NULL UNIQUE REFERENCES affiliates(id) ON DELETE CASCADE,
  slug              TEXT NOT NULL UNIQUE,
  is_house_channel  BOOLEAN NOT NULL DEFAULT FALSE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE affiliate_links ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS referral_codes (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  affiliate_id      UUID NOT NULL UNIQUE REFERENCES affiliates(id) ON DELETE CASCADE,
  code_string       TEXT NOT NULL UNIQUE,
  redemption_cap    INT NOT NULL,
  expires_at        TIMESTAMPTZ NOT NULL,
  redemption_count  INT NOT NULL DEFAULT 0,
  discount_pct      NUMERIC NOT NULL DEFAULT 0,  -- added 0085: per-code customer discount
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE referral_codes ENABLE ROW LEVEL SECURITY;

-- ── 2. Attribution pipeline (click -> durable per-email record) ────────────

-- Stage 1: ephemeral click log, keyed by browser session token.
CREATE TABLE IF NOT EXISTS referral_click_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  affiliate_id  UUID NOT NULL REFERENCES affiliates(id) ON DELETE CASCADE,
  session_token TEXT NOT NULL,
  clicked_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_referral_click_events_session ON referral_click_events(session_token, clicked_at DESC);
ALTER TABLE referral_click_events ENABLE ROW LEVEL SECURITY;

-- Stage 2: durable, email-keyed attribution. One active row per customer
-- email — overwritten per the precedence rules in resolve_referral_attribution,
-- not versioned.
CREATE TABLE IF NOT EXISTS attribution_records (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email_hash    TEXT NOT NULL UNIQUE,
  affiliate_id  UUID REFERENCES affiliates(id) ON DELETE SET NULL,
  source_type   TEXT NOT NULL CHECK (source_type IN ('link', 'code')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at    TIMESTAMPTZ NOT NULL,
  frozen        BOOLEAN NOT NULL DEFAULT FALSE,  -- paused while a mentor no-show rebooking decision is pending
  frozen_at     TIMESTAMPTZ,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE attribution_records ENABLE ROW LEVEL SECURITY;  -- no select policies — internal (RPC-only), same masking as booking_ledger

-- ── 3. Commission ledger + payout batching ──────────────────────────────────

CREATE TABLE IF NOT EXISTS commission_ledger (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id             UUID NOT NULL UNIQUE REFERENCES bookings(id) ON DELETE CASCADE,
  session_completed_at   TIMESTAMPTZ NOT NULL,
  mentor_id              UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  affiliate_id           UUID NOT NULL REFERENCES affiliates(id) ON DELETE CASCADE,
  split_snapshot         JSONB NOT NULL,  -- {mentor_pct, immigroov_pct, promoter_pct} at calculation time
  commission_amount_inr  NUMERIC(12,2) NOT NULL,
  status                 TEXT NOT NULL DEFAULT 'pending_review' CHECK (status IN ('pending_review', 'approved', 'paid')),
  payout_batch_id        UUID,  -- FK added below, after payout_batches exists
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE commission_ledger ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS commission_ledger_self_read ON commission_ledger;
CREATE POLICY commission_ledger_self_read ON commission_ledger FOR SELECT USING (
  affiliate_id IN (SELECT id FROM affiliates WHERE profile_id = auth.uid())
);

CREATE TABLE IF NOT EXISTS payout_batches (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_date  DATE NOT NULL UNIQUE,
  status      TEXT NOT NULL DEFAULT 'preview' CHECK (status IN ('preview', 'finalized')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE payout_batches ENABLE ROW LEVEL SECURITY;

ALTER TABLE commission_ledger
  ADD CONSTRAINT commission_ledger_payout_batch_fkey
  FOREIGN KEY (payout_batch_id) REFERENCES payout_batches(id) ON DELETE SET NULL;

-- ── 4. Fraud review + admin audit trail ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS fraud_flags (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  affiliate_id                UUID NOT NULL REFERENCES affiliates(id) ON DELETE CASCADE,
  booking_id                  UUID REFERENCES bookings(id) ON DELETE SET NULL,
  vector_type                 TEXT NOT NULL CHECK (vector_type IN (
                                 'duplicate_person', 'volume_spike', 'geography_mismatch',
                                 'code_speed', 'cancel_rebook_cycling', 'mentor_steering', 'chargeback'
                               )),
  status                      TEXT NOT NULL DEFAULT 'escalated' CHECK (status IN ('auto_cleared', 'escalated', 'resolved')),
  reviewer                    UUID REFERENCES profiles(id),
  decision                    TEXT CHECK (decision IN ('approve', 'approve_with_note', 'reject_and_hold')),
  note                        TEXT,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at                 TIMESTAMPTZ,
  escalated_to_cofounder_at   TIMESTAMPTZ
);
ALTER TABLE fraud_flags ENABLE ROW LEVEL SECURITY;  -- no select policies — admin RPCs only

CREATE TABLE IF NOT EXISTS referral_admin_actions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  action      TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id   UUID NOT NULL,
  note        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE referral_admin_actions ENABLE ROW LEVEL SECURITY;

-- ── 5. Booking + ledger schema additions ────────────────────────────────────

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS referral_session_token TEXT;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS referral_code TEXT;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS referral_discount_applied_pct NUMERIC;

-- booking_ledger gains a 4th party ('promoter') and a 5th kind ('commission')
-- for affiliate commission entries — same table Payments already uses, just
-- a wider CHECK. Auto-generated constraint names (Postgres default naming
-- for an unnamed column CHECK): <table>_<column>_check.
ALTER TABLE booking_ledger DROP CONSTRAINT IF EXISTS booking_ledger_party_check;
ALTER TABLE booking_ledger ADD CONSTRAINT booking_ledger_party_check
  CHECK (party IN ('customer', 'mentor', 'platform', 'promoter'));
ALTER TABLE booking_ledger DROP CONSTRAINT IF EXISTS booking_ledger_kind_check;
ALTER TABLE booking_ledger ADD CONSTRAINT booking_ledger_kind_check
  CHECK (kind IN ('penalty', 'refund', 'credit', 'charge', 'commission'));

-- ── 6. Platform settings — all tunable, none hardcoded (immigroov's own design) ─

INSERT INTO platform_settings (key, value, description) VALUES
  ('referral_tier_starter_max', '4', 'Non-mentor affiliate: max referrals/month to stay Starter tier'),
  ('referral_tier_growth_max', '14', 'Non-mentor affiliate: max referrals/month to stay Growth tier (above = Partner)'),
  ('referral_volume_spike_autoapprove_multiplier', '3', 'Auto-approve today''s commission count up to this multiple of the 30-day daily average'),
  ('referral_volume_spike_escalate_multiplier', '5', 'Escalate for manual review above this multiple of the 30-day daily average'),
  ('referral_code_redemption_speed_minutes', '30', 'Minutes; a code used faster than this is a potential speed-fraud signal'),
  ('referral_code_speed_high_value_inr', '', 'INR threshold above which fast code redemption escalates; empty = check inactive'),
  ('referral_mentor_steering_threshold_pct', '', '% concentration to a single mentor; empty = dashboard-only, no auto-escalation'),
  ('referral_manual_review_escalation_days', '5', 'Working days a flagged case can sit before auto-escalating to the co-founder'),
  ('referral_attribution_window_days', '60', 'Days an attribution_records row stays valid before expiring'),
  ('referral_payout_min_working_days', '5', 'Minimum working days after session completion before a commission is payout-eligible')
ON CONFLICT (key) DO NOTHING;

CREATE OR REPLACE FUNCTION referral_setting(p_key TEXT)
RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT value FROM platform_settings WHERE key = p_key;
$$;

-- ── 7. Affiliate onboarding (admin-created, invite-only) ────────────────────
-- Admin-only in immigroov too (its RPCs are ungated, "a known, pre-existing
-- gap, not something newly introduced here" per 0078's own comment) — here,
-- require_admin is enforced at the FastAPI layer (Referral 8 commit), same
-- as every other admin_* RPC already in this file.

CREATE OR REPLACE FUNCTION admin_create_affiliate(
  p_email TEXT, p_type TEXT, p_mentor_id UUID DEFAULT NULL,
  p_payout_details JSONB DEFAULT NULL, p_audience_corridor TEXT DEFAULT NULL
) RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_id UUID; v_email TEXT := LOWER(TRIM(COALESCE(p_email, ''))); v_profile_id UUID;
BEGIN
  IF v_email = '' OR POSITION('@' IN v_email) = 0 THEN
    RAISE EXCEPTION 'A valid email is required';
  END IF;
  IF p_type NOT IN ('mentor', 'non_mentor') THEN
    RAISE EXCEPTION 'Affiliate type must be mentor or non_mentor';
  END IF;
  IF p_type = 'mentor' AND p_mentor_id IS NULL THEN
    RAISE EXCEPTION 'A mentor affiliate must reference an existing mentor_id';
  END IF;

  -- Link immediately if this email already has an account; otherwise the
  -- affiliate row carries just the email until link_affiliate_by_email()
  -- attaches it on first login — same pattern as mentors.
  SELECT id INTO v_profile_id FROM profiles WHERE LOWER(email) = v_email;

  INSERT INTO affiliates (profile_id, email, type, mentor_id, payout_details, audience_corridor, agreed_terms_at)
    VALUES (v_profile_id, v_email, p_type, p_mentor_id, p_payout_details, p_audience_corridor, NOW())
    RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;
GRANT EXECUTE ON FUNCTION admin_create_affiliate(TEXT, TEXT, UUID, JSONB, TEXT) TO authenticated;

CREATE OR REPLACE FUNCTION admin_create_referral_link(p_affiliate_id UUID, p_slug TEXT, p_is_house_channel BOOLEAN DEFAULT FALSE)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_id UUID; v_clean TEXT := LOWER(TRIM(p_slug));
BEGIN
  IF v_clean !~ '^[a-z0-9-]{3,40}$' THEN
    RAISE EXCEPTION 'Slug must be 3-40 characters, lowercase letters/numbers/hyphens only';
  END IF;
  INSERT INTO affiliate_links (affiliate_id, slug, is_house_channel)
    VALUES (p_affiliate_id, v_clean, p_is_house_channel)
    RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;
GRANT EXECUTE ON FUNCTION admin_create_referral_link(UUID, TEXT, BOOLEAN) TO authenticated;

-- Denylist ported verbatim from immigroov's 0078/0085 (admin_create_referral_code).
CREATE OR REPLACE FUNCTION admin_create_referral_code(
  p_affiliate_id UUID, p_code TEXT, p_redemption_cap INT, p_expires_at TIMESTAMPTZ, p_discount_pct NUMERIC
) RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_id UUID; v_clean TEXT := UPPER(TRIM(p_code));
  v_denylist TEXT[] := ARRAY['FUCK','SHIT','BITCH','ASS','CUNT','DAMN'];
BEGIN
  IF v_clean !~ '^[A-Z0-9_-]{3,20}$' THEN
    RAISE EXCEPTION 'Code must be 3-20 characters, letters/numbers/hyphen/underscore only';
  END IF;
  IF EXISTS (SELECT 1 FROM UNNEST(v_denylist) w WHERE v_clean LIKE '%'||w||'%') THEN
    RAISE EXCEPTION 'Code failed the profanity check — choose another';
  END IF;
  IF p_redemption_cap IS NULL OR p_redemption_cap < 1 THEN
    RAISE EXCEPTION 'Redemption cap must be at least 1';
  END IF;
  IF p_expires_at IS NULL OR p_expires_at <= NOW() THEN
    RAISE EXCEPTION 'Expiry date must be in the future';
  END IF;
  IF p_discount_pct IS NULL OR p_discount_pct < 0 OR p_discount_pct > 100 THEN
    RAISE EXCEPTION 'Discount percent must be between 0 and 100';
  END IF;
  INSERT INTO referral_codes (affiliate_id, code_string, redemption_cap, expires_at, discount_pct)
    VALUES (p_affiliate_id, v_clean, p_redemption_cap, p_expires_at, p_discount_pct)
    RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;
GRANT EXECUTE ON FUNCTION admin_create_referral_code(UUID, TEXT, INT, TIMESTAMPTZ, NUMERIC) TO authenticated;

-- Consolidates onboarding into one call: create the affiliate + their one
-- link + their one code, matching "the admin creates the affiliate account
-- directly" as a single action. Reuses the three building blocks above
-- rather than duplicating their validation.
-- NOT ported: immigroov's p_first_name param (stored on the freely-insertable
-- users row it creates). There is no profile to set a name on here until the
-- affiliate signs up and link_affiliate_by_email() attaches their account —
-- architecture difference, not a dropped business rule.
CREATE OR REPLACE FUNCTION admin_onboard_affiliate(
  p_email TEXT, p_type TEXT, p_slug TEXT, p_code TEXT,
  p_redemption_cap INT, p_expires_at TIMESTAMPTZ, p_discount_pct NUMERIC,
  p_mentor_id UUID DEFAULT NULL, p_payout_details JSONB DEFAULT NULL,
  p_audience_corridor TEXT DEFAULT NULL, p_is_house_channel BOOLEAN DEFAULT FALSE
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_affiliate_id UUID; v_link_id UUID; v_code_id UUID;
BEGIN
  v_affiliate_id := admin_create_affiliate(p_email, p_type, p_mentor_id, p_payout_details, p_audience_corridor);
  v_link_id := admin_create_referral_link(v_affiliate_id, p_slug, p_is_house_channel);
  v_code_id := admin_create_referral_code(v_affiliate_id, p_code, p_redemption_cap, p_expires_at, p_discount_pct);
  RETURN jsonb_build_object('affiliate_id', v_affiliate_id, 'link_id', v_link_id, 'code_id', v_code_id);
END;
$$;
GRANT EXECUTE ON FUNCTION admin_onboard_affiliate(TEXT, TEXT, TEXT, TEXT, INT, TIMESTAMPTZ, NUMERIC, UUID, JSONB, TEXT, BOOLEAN) TO authenticated;

-- Public, read-only code check for the checkout UI. referral_codes itself is
-- locked to the owning affiliate only, so guests/customers need a narrow
-- RPC that reveals just enough to show "code applied — X% off" before
-- payment, without exposing affiliate_id, redemption counts, or any other
-- row data.
CREATE OR REPLACE FUNCTION validate_referral_code(p_code TEXT)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT CASE WHEN EXISTS (
      SELECT 1 FROM referral_codes
      WHERE code_string = UPPER(TRIM(COALESCE(p_code, '')))
        AND expires_at > NOW() AND redemption_count < redemption_cap
    )
    THEN jsonb_build_object('valid', true, 'discount_pct',
      (SELECT discount_pct FROM referral_codes
        WHERE code_string = UPPER(TRIM(p_code)) AND expires_at > NOW() AND redemption_count < redemption_cap))
    ELSE jsonb_build_object('valid', false, 'discount_pct', 0)
  END;
$$;
GRANT EXECUTE ON FUNCTION validate_referral_code(TEXT) TO anon, authenticated;

-- ── 8. Attribution pipeline (click -> durable per-email record) ────────────
-- Ported from immigroov's 0078 (base logic) reconciled with 0098 (email
-- hooks). The email hooks themselves are NOT ported — immigroov sends them
-- via pg_net (app_send_email) directly from SQL; groovia has no SQL-side
-- HTTP capability. Same gap as the Reviews module's review-request email:
-- flagged, not silently dropped. The FastAPI layer (Referral 8 commit) can
-- send the "referral tracked" email as a BackgroundTask after calling
-- resolve_referral_attribution, since that call always happens inside a real
-- request (confirm_booking_payment / confirm-mock), unlike the cron-driven
-- review-token case. "Commission approved" (from run_referral_fraud_checks)
-- has no such request context — it fires from process_referral_commissions,
-- a cron job — so that one has the same architecture gap as the review
-- nudge email and is deferred the same way.

CREATE OR REPLACE FUNCTION log_referral_click(p_slug TEXT, p_session_token TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_affiliate_id UUID;
BEGIN
  SELECT affiliate_id INTO v_affiliate_id FROM affiliate_links WHERE slug = LOWER(TRIM(p_slug));
  IF NOT FOUND THEN RETURN; END IF;  -- unknown slug: fail silently, don't break the visitor's page load
  INSERT INTO referral_click_events (affiliate_id, session_token) VALUES (v_affiliate_id, p_session_token);
END;
$$;
GRANT EXECUTE ON FUNCTION log_referral_click(TEXT, TEXT) TO anon, authenticated;

CREATE OR REPLACE FUNCTION referral_email_for_booking(p_booking_id UUID)
RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(b.candidate_email, p.email)
  FROM bookings b LEFT JOIN profiles p ON p.id = b.candidate_id
  WHERE b.id = p_booking_id;
$$;

-- Called at checkout. Precedence: a code entered THIS checkout always wins;
-- otherwise a link click logged against THIS session overwrites any prior
-- attribution; if neither happened this checkout, whatever attribution
-- already existed for this email is left as-is (this is what makes
-- "return via a later generic link" a no-op).
CREATE OR REPLACE FUNCTION resolve_referral_attribution(p_session_token TEXT, p_email TEXT, p_code TEXT DEFAULT NULL)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_hash TEXT; v_click_affiliate UUID; v_code_affiliate UUID; v_window_days INT;
  v_existing attribution_records;
BEGIN
  v_hash := encode(extensions.digest(LOWER(TRIM(p_email)), 'sha256'), 'hex');
  v_window_days := COALESCE(referral_setting('referral_attribution_window_days')::int, 60);
  SELECT * INTO v_existing FROM attribution_records WHERE email_hash = v_hash;

  IF p_code IS NOT NULL AND TRIM(p_code) <> '' THEN
    SELECT affiliate_id INTO v_code_affiliate FROM referral_codes
      WHERE code_string = UPPER(TRIM(p_code)) AND expires_at > NOW() AND redemption_count < redemption_cap;
  END IF;

  SELECT affiliate_id INTO v_click_affiliate FROM referral_click_events
    WHERE session_token = p_session_token ORDER BY clicked_at DESC LIMIT 1;

  IF v_existing.frozen THEN
    RETURN;  -- a no-show rebooking decision is pending for this customer — leave attribution untouched
  END IF;

  IF v_code_affiliate IS NOT NULL THEN
    INSERT INTO attribution_records (email_hash, affiliate_id, source_type, created_at, expires_at)
      VALUES (v_hash, v_code_affiliate, 'code', NOW(), NOW() + (v_window_days || ' days')::interval)
      ON CONFLICT (email_hash) DO UPDATE SET
        affiliate_id = EXCLUDED.affiliate_id, source_type = 'code',
        created_at = NOW(), expires_at = NOW() + (v_window_days || ' days')::interval, updated_at = NOW();
    UPDATE referral_codes SET redemption_count = redemption_count + 1 WHERE affiliate_id = v_code_affiliate;
  ELSIF v_click_affiliate IS NOT NULL THEN
    INSERT INTO attribution_records (email_hash, affiliate_id, source_type, created_at, expires_at)
      VALUES (v_hash, v_click_affiliate, 'link', NOW(), NOW() + (v_window_days || ' days')::interval)
      ON CONFLICT (email_hash) DO UPDATE SET
        affiliate_id = EXCLUDED.affiliate_id, source_type = 'link',
        created_at = NOW(), expires_at = NOW() + (v_window_days || ' days')::interval, updated_at = NOW();
  END IF;
END;
$$;
GRANT EXECUTE ON FUNCTION resolve_referral_attribution(TEXT, TEXT, TEXT) TO authenticated, service_role;

-- Freezes the 60-day (default) clock the moment a mentor no-show is logged
-- against a referred booking. Called from flag_no_show below.
CREATE OR REPLACE FUNCTION freeze_referral_attribution(p_booking_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_email TEXT; v_hash TEXT;
BEGIN
  v_email := referral_email_for_booking(p_booking_id);
  IF v_email IS NULL THEN RETURN; END IF;
  v_hash := encode(extensions.digest(LOWER(TRIM(v_email)), 'sha256'), 'hex');
  UPDATE attribution_records SET frozen = TRUE, frozen_at = NOW() WHERE email_hash = v_hash AND NOT frozen;
END;
$$;

-- Unfreezes once a rebooking decision is made, extending expires_at by
-- however long it sat frozen so the customer doesn't lose attribution time
-- to the wait.
CREATE OR REPLACE FUNCTION unfreeze_referral_attribution(p_booking_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_email TEXT; v_hash TEXT;
BEGIN
  v_email := referral_email_for_booking(p_booking_id);
  IF v_email IS NULL THEN RETURN; END IF;
  v_hash := encode(extensions.digest(LOWER(TRIM(v_email)), 'sha256'), 'hex');
  UPDATE attribution_records
    SET frozen = FALSE, expires_at = expires_at + (NOW() - frozen_at), frozen_at = NULL
    WHERE email_hash = v_hash AND frozen;
END;
$$;

-- ── 9. Commission calculator + fraud engine ─────────────────────────────────
-- Ported from immigroov's 0078 (base) reconciled through 0084 (pct::int cast
-- fix — applied from the start here, not as a later patch), 0093 (FX
-- conversion fix — commission_amount_inr multiplies by fx_customer_inr, also
-- applied from the start), 0096 (booking-matching fix — only bookings that
-- themselves carry referral_code/referral_session_token are considered,
-- preventing retroactive claims on unrelated older sessions).
--
-- Two vectors from immigroov's fraud engine are explicit no-ops there too,
-- not something dropped in this port: self-referral via device/IP/payment
-- fingerprint (no such data captured anywhere), and geography mismatch (no
-- customer-geography column exists). Mentor-steering is deliberately
-- informational-only (dashboard report, no auto-escalation) per the
-- founder's original decision.
--
-- log_event(...) calls from the source are NOT ported — groovia has no
-- audit-log mechanism of that shape (its own booking_events table is an
-- email-outbox, a different thing entirely; see the Payments module's notes
-- on this exact naming collision). Money-relevant state (commission_ledger,
-- fraud_flags, booking_ledger) is unaffected; only the human-readable
-- timeline entry is missing, same gap noted throughout every prior module.

-- "Cannot advance a tier while flagged": the affiliate is held at whatever
-- tier they'd already reached THIS month before their first active flag
-- appeared — not reset to Starter. A flag never blocks tier progress already
-- earned; it only stops further advancement.
CREATE OR REPLACE FUNCTION current_affiliate_tier(p_affiliate_id UUID)
RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_type TEXT; v_starter_max INT; v_growth_max INT;
  v_count_now INT; v_count_at_flag INT; v_first_flag_at TIMESTAMPTZ;
  v_rank_now INT; v_rank_at_flag INT; v_rank INT;
BEGIN
  SELECT type INTO v_type FROM affiliates WHERE id = p_affiliate_id;
  IF v_type = 'mentor' THEN RETURN 'flat_peer_rate'; END IF;  -- mentor-to-mentor referrals never tier

  v_starter_max := COALESCE(referral_setting('referral_tier_starter_max')::int, 4);
  v_growth_max := COALESCE(referral_setting('referral_tier_growth_max')::int, 14);

  SELECT COUNT(*) INTO v_count_now FROM commission_ledger
    WHERE affiliate_id = p_affiliate_id AND status IN ('approved', 'paid')
      AND date_trunc('month', session_completed_at) = date_trunc('month', NOW());
  v_rank_now := CASE WHEN v_count_now <= v_starter_max THEN 1 WHEN v_count_now <= v_growth_max THEN 2 ELSE 3 END;

  SELECT MIN(created_at) INTO v_first_flag_at FROM fraud_flags
    WHERE affiliate_id = p_affiliate_id AND status = 'escalated'
      AND date_trunc('month', created_at) = date_trunc('month', NOW());

  IF v_first_flag_at IS NULL THEN
    v_rank := v_rank_now;  -- no active flag this month: free to advance normally
  ELSE
    SELECT COUNT(*) INTO v_count_at_flag FROM commission_ledger
      WHERE affiliate_id = p_affiliate_id AND status IN ('approved', 'paid')
        AND date_trunc('month', session_completed_at) = date_trunc('month', NOW())
        AND created_at < v_first_flag_at;
    v_rank_at_flag := CASE WHEN v_count_at_flag <= v_starter_max THEN 1 WHEN v_count_at_flag <= v_growth_max THEN 2 ELSE 3 END;
    v_rank := LEAST(v_rank_now, v_rank_at_flag);  -- capped at the tier already reached before the flag
  END IF;

  RETURN CASE v_rank WHEN 1 THEN 'starter' WHEN 2 THEN 'growth' ELSE 'partner' END;
END;
$$;

-- Deterministic fraud checks. If ANY vector fires, the ledger entry stays
-- pending_review; if none do, it's auto-approved.
CREATE OR REPLACE FUNCTION run_referral_fraud_checks(p_ledger_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  cl commission_ledger;
  v_avg30 NUMERIC; v_today_count INT; v_spike_escalate NUMERIC;
  v_email TEXT; v_hash TEXT; v_att attribution_records; v_code referral_codes;
  v_speed_minutes NUMERIC; v_speed_high_value NUMERIC;
  v_flagged BOOLEAN := FALSE;
BEGIN
  SELECT * INTO cl FROM commission_ledger WHERE id = p_ledger_id;

  -- Volume spike: today's count vs. the trailing 30-day daily average.
  v_spike_escalate := referral_setting('referral_volume_spike_escalate_multiplier')::numeric;
  SELECT COUNT(*) INTO v_today_count FROM commission_ledger
    WHERE affiliate_id = cl.affiliate_id AND session_completed_at::date = cl.session_completed_at::date;
  SELECT COUNT(*)::numeric / 30.0 INTO v_avg30 FROM commission_ledger
    WHERE affiliate_id = cl.affiliate_id
      AND session_completed_at >= cl.session_completed_at - INTERVAL '30 days'
      AND session_completed_at < cl.session_completed_at;
  IF v_avg30 > 0 AND v_today_count / v_avg30 > COALESCE(v_spike_escalate, 5) THEN
    INSERT INTO fraud_flags (affiliate_id, booking_id, vector_type, status) VALUES (cl.affiliate_id, cl.booking_id, 'volume_spike', 'escalated');
    v_flagged := TRUE;
  END IF;

  -- Code redemption speed (only relevant if this referral came from a code;
  -- inactive unless referral_code_speed_high_value_inr is set).
  v_email := referral_email_for_booking(cl.booking_id);
  v_hash := encode(extensions.digest(LOWER(TRIM(v_email)), 'sha256'), 'hex');
  SELECT * INTO v_att FROM attribution_records WHERE email_hash = v_hash;
  IF v_att.source_type = 'code' THEN
    SELECT * INTO v_code FROM referral_codes WHERE affiliate_id = cl.affiliate_id;
    v_speed_minutes := COALESCE(referral_setting('referral_code_redemption_speed_minutes')::numeric, 30);
    v_speed_high_value := NULLIF(referral_setting('referral_code_speed_high_value_inr'), '')::numeric;
    IF v_speed_high_value IS NOT NULL
       AND EXTRACT(EPOCH FROM (v_att.created_at - v_code.created_at)) / 60.0 <= v_speed_minutes
       AND cl.commission_amount_inr > v_speed_high_value THEN
      INSERT INTO fraud_flags (affiliate_id, booking_id, vector_type, status) VALUES (cl.affiliate_id, cl.booking_id, 'code_speed', 'escalated');
      v_flagged := TRUE;
    END IF;
  END IF;

  -- Cancel/rebook cycling — reuses the existing bookings.reschedule_count column.
  IF (SELECT reschedule_count FROM bookings WHERE id = cl.booking_id) >= 3 THEN
    INSERT INTO fraud_flags (affiliate_id, booking_id, vector_type, status) VALUES (cl.affiliate_id, cl.booking_id, 'cancel_rebook_cycling', 'escalated');
    v_flagged := TRUE;
  END IF;

  IF NOT v_flagged THEN
    UPDATE commission_ledger SET status = 'approved' WHERE id = p_ledger_id;
    -- "Commission approved" email deferred — see the module-level note above.
  END IF;
END;
$$;

-- Runs on a schedule (cron block below), not on booking creation — the
-- business rule is completion-only. Idempotent: only processes bookings
-- that don't already have a commission_ledger row.
CREATE OR REPLACE FUNCTION process_referral_commissions()
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  r RECORD;
  v_email TEXT; v_hash TEXT; v_att attribution_records; v_aff affiliates;
  v_gross NUMERIC; v_mentor_pct NUMERIC; v_immigroov_pct NUMERIC; v_promoter_pct NUMERIC;
  v_tier TEXT; v_ledger_id UUID; v_already_had_session BOOLEAN;
BEGIN
  FOR r IN
    SELECT b.* FROM bookings b
    WHERE b.status = 'completed'
      AND (b.referral_code IS NOT NULL OR b.referral_session_token IS NOT NULL)
      AND NOT EXISTS (SELECT 1 FROM commission_ledger cl WHERE cl.booking_id = b.id)
  LOOP
    v_email := r.candidate_email;
    IF v_email IS NULL THEN CONTINUE; END IF;
    v_hash := encode(extensions.digest(LOWER(TRIM(v_email)), 'sha256'), 'hex');

    SELECT * INTO v_att FROM attribution_records WHERE email_hash = v_hash;
    IF NOT FOUND OR v_att.affiliate_id IS NULL OR v_att.frozen
       OR v_att.expires_at < COALESCE(r.slot_end, r.slot_time, NOW()) THEN
      CONTINUE;  -- organic, lapsed, or still awaiting a no-show decision — no ledger entry
    END IF;

    -- Referral credit applies to the customer's lifetime first paid session only.
    SELECT EXISTS(
      SELECT 1 FROM bookings b2
      WHERE b2.id <> r.id AND b2.status = 'completed'
        AND b2.candidate_email = v_email
        AND b2.slot_time < r.slot_time
    ) INTO v_already_had_session;
    IF v_already_had_session THEN CONTINUE; END IF;

    SELECT * INTO v_aff FROM affiliates WHERE id = v_att.affiliate_id;
    IF v_aff.status <> 'active' THEN CONTINUE; END IF;

    -- The founder's own house channel always pays 0% promoter fee — owned
    -- marketing, not a paid acquisition channel. No commission_ledger row is
    -- created (equivalent to organic for payout purposes; click/attribution
    -- data is still captured for analytics).
    IF v_att.source_type = 'link' AND EXISTS (
      SELECT 1 FROM affiliate_links al WHERE al.affiliate_id = v_aff.id AND al.is_house_channel
    ) THEN
      CONTINUE;
    END IF;

    SELECT amount INTO v_gross FROM customer_payments WHERE booking_id = r.id ORDER BY created_at DESC LIMIT 1;
    IF v_gross IS NULL THEN CONTINUE; END IF;

    IF v_aff.mentor_id = r.mentor_id THEN
      -- Self-referral: the promoter is the mentor who delivered the session.
      v_mentor_pct := 90; v_immigroov_pct := 10; v_promoter_pct := 0;
    ELSIF v_aff.type = 'mentor' THEN
      -- Mentor-to-mentor peer referral — flat, never tiered.
      v_mentor_pct := 70; v_immigroov_pct := 20; v_promoter_pct := 10;
    ELSE
      -- Non-mentor influencer — tiered by this-month completed-referral count.
      v_tier := current_affiliate_tier(v_aff.id);
      v_mentor_pct := 70;
      CASE v_tier
        WHEN 'growth'  THEN v_immigroov_pct := 19; v_promoter_pct := 11;
        WHEN 'partner' THEN v_immigroov_pct := 15; v_promoter_pct := 15;
        ELSE                v_immigroov_pct := 22; v_promoter_pct := 8;  -- starter, and the fraud-gated fallback
      END CASE;
    END IF;

    INSERT INTO commission_ledger (booking_id, session_completed_at, mentor_id, affiliate_id, split_snapshot, commission_amount_inr, status)
      VALUES (r.id, COALESCE(r.slot_end, NOW()), r.mentor_id, v_aff.id,
              jsonb_build_object('mentor_pct', v_mentor_pct, 'immigroov_pct', v_immigroov_pct, 'promoter_pct', v_promoter_pct),
              round(v_gross * COALESCE(r.fx_customer_inr, 1) * v_promoter_pct / 100.0, 2), 'pending_review')
      RETURNING id INTO v_ledger_id;

    IF v_promoter_pct > 0 THEN
      PERFORM add_ledger(r.id, 'promoter', 'commission', round(v_gross * v_promoter_pct / 100.0, 2), v_promoter_pct::int,
                          'Referral commission — affiliate #'||v_aff.id);
    END IF;

    PERFORM run_referral_fraud_checks(v_ledger_id);
  END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION escalate_stale_fraud_flags()
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_days INT;
BEGIN
  v_days := COALESCE(referral_setting('referral_manual_review_escalation_days')::int, 5);
  UPDATE fraud_flags
    SET escalated_to_cofounder_at = NOW()
    WHERE status = 'escalated' AND escalated_to_cofounder_at IS NULL
      AND created_at < NOW() - (v_days || ' days')::interval;
END;
$$;

-- pg_cron schedules — same pattern as the existing resolve-requests/auto-complete
-- jobs (guarded so the migration still succeeds where pg_cron isn't enabled).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'referral-commissions') THEN
      PERFORM cron.unschedule('referral-commissions');
    END IF;
    PERFORM cron.schedule('referral-commissions', '*/15 * * * *', 'SELECT process_referral_commissions()');

    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'referral-escalations') THEN
      PERFORM cron.unschedule('referral-escalations');
    END IF;
    PERFORM cron.schedule('referral-escalations', '0 3 * * *', 'SELECT escalate_stale_fraud_flags()');
  ELSE
    RAISE NOTICE 'pg_cron not enabled — skipping referral cron schedules.';
  END IF;
END $$;

-- ── 10. Manual review queue + admin reporting ───────────────────────────────
-- Ported from immigroov's 0078 (review queue, resolve, freeze/unfreeze, void)
-- + 0089/0090/0091 (admin_affiliates_overview — built with the LAST, fixed
-- version directly: 0090 fixed a subquery column-shadowing bug, 0091 fixed a
-- varchar/text type mismatch; neither bug is reproducible here since UUID/
-- TEXT columns don't have the varchar(255) issue 0091 was working around,
-- but the final query SHAPE — explicit column qualification in every
-- subquery — is preserved) + 0097 (admin_referral_bookings_overview).

CREATE OR REPLACE FUNCTION admin_mentor_steering_report()
RETURNS TABLE(affiliate_id UUID, top_mentor_id UUID, concentration_pct NUMERIC)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT cl.affiliate_id, cl.mentor_id,
         round(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY cl.affiliate_id), 1)
  FROM commission_ledger cl
  WHERE cl.session_completed_at >= date_trunc('month', NOW())
  GROUP BY cl.affiliate_id, cl.mentor_id
  ORDER BY cl.affiliate_id, 3 DESC;
$$;
GRANT EXECUTE ON FUNCTION admin_mentor_steering_report() TO authenticated;

CREATE OR REPLACE FUNCTION admin_referral_review_queue()
RETURNS TABLE (
  flag_id UUID, affiliate_id UUID, booking_id UUID, vector_type TEXT,
  created_at TIMESTAMPTZ, escalated_to_cofounder_at TIMESTAMPTZ,
  commission_amount_inr NUMERIC, split_snapshot JSONB
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT ff.id, ff.affiliate_id, ff.booking_id, ff.vector_type, ff.created_at, ff.escalated_to_cofounder_at,
         cl.commission_amount_inr, cl.split_snapshot
  FROM fraud_flags ff
  LEFT JOIN commission_ledger cl ON cl.booking_id = ff.booking_id
  WHERE ff.status = 'escalated'
  ORDER BY ff.created_at ASC;
$$;
GRANT EXECUTE ON FUNCTION admin_referral_review_queue() TO authenticated;

CREATE OR REPLACE FUNCTION admin_resolve_fraud_flag(p_flag_id UUID, p_decision TEXT, p_note TEXT DEFAULT NULL)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE f fraud_flags;
BEGIN
  IF p_decision NOT IN ('approve', 'approve_with_note', 'reject_and_hold') THEN
    RAISE EXCEPTION 'Decision must be approve, approve_with_note, or reject_and_hold';
  END IF;
  SELECT * INTO f FROM fraud_flags WHERE id = p_flag_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Flag not found'; END IF;

  UPDATE fraud_flags SET status = 'resolved', decision = p_decision, note = p_note, resolved_at = NOW() WHERE id = p_flag_id;

  IF p_decision IN ('approve', 'approve_with_note') THEN
    UPDATE commission_ledger SET status = 'approved' WHERE booking_id = f.booking_id AND status = 'pending_review';
  END IF;
  -- reject_and_hold: the ledger entry simply stays pending_review forever —
  -- there is no separate "rejected" state, so it never becomes eligible for
  -- a payout batch. No clawback is ever needed since nothing was paid.
END;
$$;
GRANT EXECUTE ON FUNCTION admin_resolve_fraud_flag(UUID, TEXT, TEXT) TO authenticated;

CREATE OR REPLACE FUNCTION admin_freeze_affiliate(p_affiliate_id UUID, p_note TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_note IS NULL OR TRIM(p_note) = '' THEN RAISE EXCEPTION 'A note is required to freeze an affiliate'; END IF;
  UPDATE affiliates SET status = 'frozen' WHERE id = p_affiliate_id;
  INSERT INTO referral_admin_actions (action, target_type, target_id, note) VALUES ('freeze', 'affiliate', p_affiliate_id, p_note);
END;
$$;
GRANT EXECUTE ON FUNCTION admin_freeze_affiliate(UUID, TEXT) TO authenticated;

CREATE OR REPLACE FUNCTION admin_unfreeze_affiliate(p_affiliate_id UUID, p_note TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_note IS NULL OR TRIM(p_note) = '' THEN RAISE EXCEPTION 'A note is required to unfreeze an affiliate'; END IF;
  UPDATE affiliates SET status = 'active' WHERE id = p_affiliate_id;
  INSERT INTO referral_admin_actions (action, target_type, target_id, note) VALUES ('unfreeze', 'affiliate', p_affiliate_id, p_note);
END;
$$;
GRANT EXECUTE ON FUNCTION admin_unfreeze_affiliate(UUID, TEXT) TO authenticated;

CREATE OR REPLACE FUNCTION admin_void_commission_ledger_entry(p_ledger_id UUID, p_note TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_note IS NULL OR TRIM(p_note) = '' THEN RAISE EXCEPTION 'A note is required to void a ledger entry'; END IF;
  UPDATE commission_ledger SET status = 'pending_review', payout_batch_id = NULL WHERE id = p_ledger_id;
  INSERT INTO referral_admin_actions (action, target_type, target_id, note) VALUES ('void_ledger_entry', 'commission_ledger', p_ledger_id, p_note);
END;
$$;
GRANT EXECUTE ON FUNCTION admin_void_commission_ledger_entry(UUID, TEXT) TO authenticated;

-- Adapted for a nullable affiliates.profile_id: an affiliate onboarded but
-- not yet linked to an account (see the profile_id-nullable commit) still
-- needs to show up here, using their fallback email/no display name.
CREATE OR REPLACE FUNCTION admin_affiliates_overview()
RETURNS TABLE (
  affiliate_id UUID, email TEXT, first_name TEXT, type TEXT, status TEXT, tier TEXT,
  this_month_referrals BIGINT, lifetime_paid_inr NUMERIC, active_flag_count BIGINT, total_flag_count BIGINT,
  link_slug TEXT, code_string TEXT
) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
  RETURN QUERY
    SELECT a.id, COALESCE(p.email, a.email), COALESCE(p.display_name, p.full_name), a.type, a.status, current_affiliate_tier(a.id),
      (SELECT COUNT(*) FROM commission_ledger cl WHERE cl.affiliate_id = a.id AND cl.status IN ('approved','paid')
         AND date_trunc('month', cl.session_completed_at) = date_trunc('month', NOW())),
      (SELECT COALESCE(SUM(cl2.commission_amount_inr), 0) FROM commission_ledger cl2 WHERE cl2.affiliate_id = a.id AND cl2.status = 'paid'),
      (SELECT COUNT(*) FROM fraud_flags ff WHERE ff.affiliate_id = a.id AND ff.status = 'escalated'),
      (SELECT COUNT(*) FROM fraud_flags ff2 WHERE ff2.affiliate_id = a.id),
      al.slug, rc.code_string
    FROM affiliates a
    LEFT JOIN profiles p ON p.id = a.profile_id
    LEFT JOIN affiliate_links al ON al.affiliate_id = a.id
    LEFT JOIN referral_codes rc ON rc.affiliate_id = a.id
    ORDER BY a.created_at DESC;
END;
$$;
GRANT EXECUTE ON FUNCTION admin_affiliates_overview() TO authenticated;

-- Every referred booking (upcoming and settled) with the full money
-- breakdown: discount applied, commission owed to the affiliate, net amount
-- to the mentor for that same session.
CREATE OR REPLACE FUNCTION admin_referral_bookings_overview()
RETURNS TABLE (
  booking_id UUID, status TEXT, slot_time TIMESTAMPTZ, customer_email TEXT,
  affiliate_id UUID, affiliate_email TEXT, referral_code TEXT, discount_pct NUMERIC,
  customer_paid NUMERIC, customer_currency TEXT,
  commission_amount_inr NUMERIC, commission_status TEXT,
  mentor_net_amount NUMERIC, mentor_currency TEXT
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT
    b.id, b.status::text, b.slot_time, b.candidate_email,
    aff.id, COALESCE(p.email, aff.email),
    b.referral_code, b.referral_discount_applied_pct,
    cp.amount, cp.currency,
    cl.commission_amount_inr, cl.status,
    mp.net_amount_mentor_currency, mp.mentor_currency
  FROM bookings b
  LEFT JOIN commission_ledger cl ON cl.booking_id = b.id
  LEFT JOIN referral_codes rc ON rc.code_string = b.referral_code
  LEFT JOIN LATERAL (
    SELECT affiliate_id FROM referral_click_events WHERE session_token = b.referral_session_token LIMIT 1
  ) rce ON TRUE
  LEFT JOIN affiliates aff ON aff.id = COALESCE(cl.affiliate_id, rc.affiliate_id, rce.affiliate_id)
  LEFT JOIN profiles p ON p.id = aff.profile_id
  LEFT JOIN LATERAL (
    SELECT amount, currency FROM customer_payments WHERE booking_id = b.id ORDER BY created_at DESC LIMIT 1
  ) cp ON TRUE
  LEFT JOIN LATERAL (
    SELECT net_amount_mentor_currency, mentor_currency FROM mentor_payouts WHERE booking_id = b.id ORDER BY created_at DESC LIMIT 1
  ) mp ON TRUE
  WHERE b.referral_code IS NOT NULL OR b.referral_session_token IS NOT NULL
  ORDER BY b.slot_time DESC NULLS LAST;
$$;
GRANT EXECUTE ON FUNCTION admin_referral_bookings_overview() TO authenticated;

-- ── 11. Payout batching ──────────────────────────────────────────────────────
-- Ported from immigroov's 0078. SCOPE NOTE preserved from the source: this
-- only tracks eligibility and marks entries "paid" (swept into a finalized
-- batch). It does NOT move any money — the actual bank/PayPal/Razorpay
-- transfer is a manual step outside this system, same as immigroov's V1.

CREATE OR REPLACE FUNCTION add_working_days(p_start TIMESTAMPTZ, p_days INT)
RETURNS TIMESTAMPTZ LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_date DATE := p_start::date; v_added INT := 0;
BEGIN
  WHILE v_added < p_days LOOP
    v_date := v_date + 1;
    IF EXTRACT(isodow FROM v_date) < 6 THEN v_added := v_added + 1; END IF;  -- Mon-Fri only, no holiday calendar (locked V1 convention)
  END LOOP;
  RETURN v_date::timestamptz;
END;
$$;

CREATE OR REPLACE FUNCTION admin_payout_batch_preview(p_batch_date DATE)
RETURNS TABLE (commission_ledger_id UUID, affiliate_id UUID, amount_inr NUMERIC, booking_id UUID)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE v_min_days INT;
BEGIN
  v_min_days := COALESCE(referral_setting('referral_payout_min_working_days')::int, 5);
  RETURN QUERY
    SELECT cl.id, cl.affiliate_id, cl.commission_amount_inr, cl.booking_id
    FROM commission_ledger cl
    WHERE cl.status = 'approved'
      AND cl.payout_batch_id IS NULL
      AND add_working_days(cl.session_completed_at, v_min_days) <= p_batch_date::timestamptz;
END;
$$;
GRANT EXECUTE ON FUNCTION admin_payout_batch_preview(DATE) TO authenticated;

CREATE OR REPLACE FUNCTION admin_finalize_payout_batch(p_batch_date DATE)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_batch_id UUID;
BEGIN
  INSERT INTO payout_batches (batch_date, status) VALUES (p_batch_date, 'finalized')
    ON CONFLICT (batch_date) DO UPDATE SET status = 'finalized'
    RETURNING id INTO v_batch_id;
  UPDATE commission_ledger cl
    SET payout_batch_id = v_batch_id, status = 'paid'
    FROM admin_payout_batch_preview(p_batch_date) prev
    WHERE cl.id = prev.commission_ledger_id;
  RETURN v_batch_id;
END;
$$;
GRANT EXECUTE ON FUNCTION admin_finalize_payout_batch(DATE) TO authenticated;

-- ── 12. Affiliate-facing dashboard ──────────────────────────────────────────
-- Ported from immigroov's 0078 (base) + 0095 ("upcoming" in-flight referrals).
-- Deliberately hides fraud-flag reasoning — only ever exposes a plain
-- boolean "under_review", matching the source's product requirement.
--
-- Identity adaptation: immigroov's 0088 switched this to a p_email param
-- because immigroov has no real auth (current_user_id() is never populated).
-- groovia DOES have real Supabase Auth — the FastAPI layer resolves the
-- caller's identity from their JWT before calling this RPC, so it takes
-- p_profile_id directly rather than reintroducing the email-based workaround
-- immigroov needed. Same reasoning as the Auth module: don't revert a
-- security improvement to match the source.
CREATE OR REPLACE FUNCTION affiliate_dashboard_summary(p_profile_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_affiliate affiliates; v_link affiliate_links; v_code referral_codes; v_result JSONB;
BEGIN
  SELECT * INTO v_affiliate FROM affiliates WHERE profile_id = p_profile_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Not an affiliate account'; END IF;
  SELECT * INTO v_link FROM affiliate_links WHERE affiliate_id = v_affiliate.id;
  SELECT * INTO v_code FROM referral_codes WHERE affiliate_id = v_affiliate.id;

  SELECT jsonb_build_object(
    'affiliate', jsonb_build_object('id', v_affiliate.id, 'type', v_affiliate.type, 'status', v_affiliate.status),
    'link', jsonb_build_object('slug', v_link.slug, 'is_house_channel', v_link.is_house_channel),
    'code', jsonb_build_object('code', v_code.code_string, 'expires_at', v_code.expires_at,
                                'redemption_count', v_code.redemption_count, 'redemption_cap', v_code.redemption_cap,
                                'discount_pct', v_code.discount_pct),
    'tier', current_affiliate_tier(v_affiliate.id),
    'pending_commission_inr', (SELECT COALESCE(SUM(commission_amount_inr), 0) FROM commission_ledger WHERE affiliate_id = v_affiliate.id AND status IN ('pending_review', 'approved')),
    'paid_commission_inr', (SELECT COALESCE(SUM(commission_amount_inr), 0) FROM commission_ledger WHERE affiliate_id = v_affiliate.id AND status = 'paid'),
    'upcoming', (
      SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'booking_id', b.id, 'slot_time', b.slot_time, 'status', b.status
      ) ORDER BY b.slot_time ASC NULLS LAST), '[]'::jsonb)
      FROM bookings b
      WHERE b.status IN ('confirmed', 'rescheduled')
        AND NOT EXISTS (SELECT 1 FROM commission_ledger cl WHERE cl.booking_id = b.id)
        AND (
          (v_code.code_string IS NOT NULL AND b.referral_code = v_code.code_string)
          OR EXISTS (
            SELECT 1 FROM referral_click_events rce
            WHERE rce.affiliate_id = v_affiliate.id AND rce.session_token = b.referral_session_token
          )
        )
    ),
    'referrals', (
      SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'booking_id', cl.booking_id, 'status', cl.status, 'amount_inr', cl.commission_amount_inr,
        'created_at', cl.created_at,
        'under_review', EXISTS(SELECT 1 FROM fraud_flags f WHERE f.booking_id = cl.booking_id AND f.status = 'escalated')
      ) ORDER BY cl.created_at DESC), '[]'::jsonb)
      FROM commission_ledger cl WHERE cl.affiliate_id = v_affiliate.id
    ),
    'payouts', (
      SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'batch_date', pb.batch_date, 'amount_inr', s.total, 'entry_count', s.n
      ) ORDER BY pb.batch_date DESC), '[]'::jsonb)
      FROM payout_batches pb
      JOIN LATERAL (
        SELECT SUM(commission_amount_inr) AS total, COUNT(*) AS n
        FROM commission_ledger WHERE payout_batch_id = pb.id AND affiliate_id = v_affiliate.id
      ) s ON TRUE
      WHERE EXISTS (SELECT 1 FROM commission_ledger WHERE payout_batch_id = pb.id AND affiliate_id = v_affiliate.id)
    )
  ) INTO v_result;
  RETURN v_result;
END;
$$;
GRANT EXECUTE ON FUNCTION affiliate_dashboard_summary(UUID) TO authenticated;

-- ── 13. Admin financials: read-side reporting ───────────────────────────────
-- Ported from immigroov's admin_payouts/admin_ledger/admin_booking_detail,
-- reconciled through their full migration histories (admin_payouts: 0052 ->
-- 0063 -> 0064 -> 0066 -> 0074 FINAL; admin_ledger: 0049 -> 0063 FINAL;
-- admin_booking_detail: 0050 -> 0051 -> 0054 -> 0066 -> 0071 -> 0077 FINAL).
-- mark_payout_paid / set_payout_blocked already exist (ported in the
-- Payments module) — this module only adds the read-side reporting RPCs and
-- wires the write-side ones up at the FastAPI layer.
--
-- Grant posture: immigroov's own migration comments repeatedly flag that
-- these three read RPCs were left GRANTed to anon+authenticated in every
-- migration reviewed — an acknowledged, never-fixed gap in the source. That
-- is NOT reproduced here: every other admin_* RPC in this codebase (reviews
-- moderation, referral admin, etc.) is GRANTed to authenticated only and
-- gated by require_admin at the FastAPI layer — these three follow the same
-- established posture rather than reintroducing a known security hole.
--
-- mentor_earnings_summary (materialized view) / mentor_earnings(p_mentor_id)
-- are deliberately NOT ported as part of this module: the materialized view
-- is never queried anywhere in immigroov's own admin UI and its refresh cron
-- was never actually scheduled in the migrations reviewed (dead code); the
-- live function, mentor_earnings(p_mentor_id), is mentor-facing (a future
-- mentor earnings dashboard), not part of AdminManager.tsx's admin surface —
-- out of scope for "Admin financials" as drawn by this module's own RPC
-- boundary (admin_payouts/admin_ledger/admin_booking_detail/mark_payout_paid/
-- set_payout_blocked). Flagged here, not silently dropped.

-- Every payout-actionable booking (excludes pending/pending_payment/cancelled,
-- same set immigroov's FINAL 0074 body filters to), newest first. Prefers the
-- authoritative mentor_payouts snapshot; falls back to a computed figure only
-- for the (here, theoretically unreachable — reserve_booking always writes a
-- full mentor_payouts row) case where it's missing, matching immigroov's own
-- defensive fallback design.
CREATE OR REPLACE FUNCTION admin_payouts()
RETURNS TABLE(
  booking_id UUID, created_at TIMESTAMPTZ, status TEXT, slot_time TIMESTAMPTZ,
  service_title TEXT, mentor_name TEXT, mentee_email TEXT,
  gross NUMERIC, currency TEXT,
  fee_pct NUMERIC, deduction NUMERIC, net_payout NUMERIC,
  mentor_net NUMERIC, mentor_currency TEXT, fx_rate NUMERIC, ppp NUMERIC,
  method TEXT, payout_status TEXT
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT
    b.id, b.created_at, b.status::text, b.slot_time,
    s.title, m.display_name, b.candidate_email,
    COALESCE(mp.gross_amount, cp.amount),
    COALESCE(mp.customer_currency, cp.currency),
    COALESCE(mp.fee_pct, fee.pct),
    COALESCE(mp.platform_fee_amount, ROUND(cp.amount * fee.pct / 100.0, 2)),
    COALESCE(mp.net_amount_customer_currency, ROUND(cp.amount * (1 - fee.pct / 100.0), 2)),
    mp.net_amount_mentor_currency,
    mp.mentor_currency,
    mp.exchange_rate_used,
    mp.ppp_multiplier,
    -- Always 'manual': RazorpayX auto-payout is out of scope for this module
    -- (see mentor_payouts.method's own column comment) — immigroov's
    -- 'auto_inr' fallback describes an automated transfer path groovia has
    -- never built, so reproducing that label here would claim a capability
    -- that doesn't exist.
    COALESCE(mp.method, 'manual'),
    COALESCE(mp.payout_state, 'pending')
  FROM bookings b
  JOIN services s ON s.id = b.service_id
  JOIN mentors m ON m.id = b.mentor_id
  LEFT JOIN LATERAL (
    SELECT amount, currency FROM customer_payments WHERE booking_id = b.id ORDER BY created_at DESC LIMIT 1
  ) cp ON TRUE
  LEFT JOIN LATERAL (
    SELECT * FROM mentor_payouts WHERE booking_id = b.id ORDER BY created_at DESC LIMIT 1
  ) mp ON TRUE
  CROSS JOIN LATERAL (
    SELECT COALESCE(
      CASE WHEN s.set_price > 0 AND NULLIF(s.platform_fee, 0) IS NOT NULL
           THEN ROUND(s.platform_fee / s.set_price * 100.0, 4) END,
      (SELECT value::numeric FROM platform_settings WHERE key = 'immigroov_commission_pct'),
      15
    ) AS pct
  ) fee
  WHERE b.status IN ('confirmed', 'rescheduled', 'completed', 'no_show')
  ORDER BY b.created_at DESC, b.id DESC;
$$;
GRANT EXECUTE ON FUNCTION admin_payouts() TO authenticated;

-- Flat, unfiltered read of every booking_ledger row, decorated with booking
-- context — matches immigroov's FINAL (0063) body exactly: no date/status
-- filter, no pagination (the admin UI filters client-side). Ordered by
-- created_at (immigroov orders by its bigint id, which is chronological by
-- construction; groovia's booking_ledger.id is a UUID, so created_at is the
-- equivalent ordering key, with id as a tiebreaker for determinism).
CREATE OR REPLACE FUNCTION admin_ledger()
RETURNS TABLE (
  id UUID, created_at TIMESTAMPTZ, booking_id UUID, party TEXT, kind TEXT, pct INT,
  amount NUMERIC, currency TEXT, normalized_inr NUMERIC, reason TEXT,
  service_title TEXT, mentor_name TEXT, mentee_email TEXT, booking_status TEXT
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT
    l.id, l.created_at, l.booking_id, l.party, l.kind, l.pct,
    l.amount, l.currency, l.normalized_inr_amount, l.reason,
    s.title, m.display_name, b.candidate_email, b.status::text
  FROM booking_ledger l
  JOIN bookings b ON b.id = l.booking_id
  LEFT JOIN services s ON s.id = b.service_id
  LEFT JOIN mentors m ON m.id = b.mentor_id
  ORDER BY l.created_at DESC, l.id DESC;
$$;
GRANT EXECUTE ON FUNCTION admin_ledger() TO authenticated;

-- Full booking detail for the admin drill-down: booking/payment/payout facts,
-- a money-totals summary, and a reconstructed chronological timeline.
--
-- Timeline reconstruction: immigroov's FINAL body prefers a dedicated
-- booking_events AUDIT-LOG table (added in its 0051) and only falls back to
-- reconstructing from booking_requests/reschedule_offers for legacy
-- pre-0051 bookings. groovia has NO such audit log — its own booking_events
-- table is an EMAIL OUTBOX (see notify_booking_event above), a different
-- thing entirely, not a human-readable event history, so it is never read
-- here. Every booking's timeline is therefore reconstructed from
-- booking_requests + reschedule_offers + booking_ledger — immigroov's
-- "legacy fallback" path is groovia's only path. This is an architecture
-- gap (no equivalent to log_event's audit trail exists, same gap noted in
-- every prior module), not a business-rule change.
--
-- Known, faithfully-reproduced gap: immigroov's own FINAL (0077) body does
-- NOT return mentor_country/mentee_country in the `booking` object, even
-- though AdminManager.tsx's UI still reads both fields (they were present in
-- 0054, dropped in 0066, never restored — an unfixed bug in the source
-- itself). Per this migration's rule to port the final state exactly rather
-- than silently fixing source bugs, mentor_country/mentee_country are
-- likewise omitted here. Note groovia additionally has no candidate/customer
-- country column on bookings at all, so mentee_country could not be restored
-- even if this were fixed — mentor_country alone (mentors.country) could be
-- added trivially, but is intentionally left out to match the source.
--
-- Known, faithfully-reproduced gap: `totals` sums exactly six (party, kind)
-- ledger buckets (customer_refund/credit/charge/penalty, mentor_penalty/
-- credit) — the same six immigroov's FINAL body sums. Neither immigroov nor
-- this port adds buckets for the 'platform'/'promoter' parties or the
-- 'commission' kind that the referral module's widened CHECK constraints
-- allow — those rows appear in the raw `timeline` (via the ledger-entries
-- branch below) but are not aggregated into `totals`. Matches the source.
--
-- Known gap: groovia's reschedule_offers has no `was_late` column (unlike
-- immigroov's), so the "· past-deadline" timeline suffix immigroov shows is
-- not reproducible — architecture/data gap, not a dropped business rule.
CREATE OR REPLACE FUNCTION admin_booking_detail(p_booking_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_tz TEXT;
  v_result JSONB;
BEGIN
  SELECT COALESCE(b.attendee_timezone, p.timezone, 'UTC') INTO v_tz
  FROM bookings b LEFT JOIN profiles p ON p.id = b.candidate_id
  WHERE b.id = p_booking_id;
  IF v_tz IS NULL THEN RETURN NULL; END IF;  -- booking not found

  WITH b AS (
    SELECT * FROM bookings WHERE id = p_booking_id
  ), cp AS (
    SELECT amount, currency, state FROM customer_payments
    WHERE booking_id = p_booking_id ORDER BY created_at DESC LIMIT 1
  ), mp AS (
    SELECT * FROM mentor_payouts WHERE booking_id = p_booking_id ORDER BY created_at DESC LIMIT 1
  ), fee AS (
    SELECT COALESCE(mp.fee_pct,
      CASE WHEN s.set_price > 0 AND NULLIF(s.platform_fee, 0) IS NOT NULL
           THEN ROUND(s.platform_fee / s.set_price * 100.0, 4) END,
      (SELECT value::numeric FROM platform_settings WHERE key = 'immigroov_commission_pct'),
      15) AS pct
    FROM b JOIN services s ON s.id = b.service_id LEFT JOIN mp ON TRUE
  ), ev AS (
    -- (a) synthetic "created" event, payment-state-aware (matches immigroov's 0077)
    SELECT b.created_at AS at, 'system'::text AS actor,
      CASE
        WHEN cp.state = 'captured' THEN 'Booking created & paid'
        WHEN cp.state IN ('refunded', 'partially_refunded') THEN 'Booking created (later refunded)'
        WHEN cp.state = 'failed' THEN 'Booking created — awaiting payment'
        ELSE 'Booking created — awaiting payment'
      END AS title,
      CASE
        WHEN cp.state = 'captured' THEN 'Paid ' || cp.amount || ' ' || cp.currency
        WHEN cp.state = 'failed' THEN 'Payment not completed'
        WHEN cp.amount IS NOT NULL THEN 'Reserved ' || cp.amount || ' ' || cp.currency || ' — awaiting payment'
        ELSE NULL
      END AS detail
    FROM b LEFT JOIN cp ON TRUE

    UNION ALL
    -- (b) mentor confirmation
    SELECT b.mentor_confirmed_at, 'mentor', 'Mentor confirmed availability', NULL
    FROM b WHERE b.mentor_confirmed_at IS NOT NULL

    UNION ALL
    -- (d) booking_requests: creation
    SELECT r.created_at, r.initiated_by, INITCAP(r.kind) || ' requested', r.note
    FROM booking_requests r WHERE r.booking_id = p_booking_id

    UNION ALL
    -- (e) booking_requests: resolution
    SELECT r.resolved_at, 'system', INITCAP(r.kind) || ' request ' || r.status,
      CASE WHEN r.status = 'auto_approved' THEN 'No response in time — auto-approved' ELSE NULL END
    FROM booking_requests r WHERE r.booking_id = p_booking_id AND r.resolved_at IS NOT NULL

    UNION ALL
    -- (f) reschedule_offers
    SELECT o.created_at, o.proposed_by,
      CASE
        WHEN o.proposed_by = 'mentor' THEN 'Mentor proposed a new time window'
        WHEN o.requested_date IS NOT NULL THEN 'Customer asked for a different date'
        ELSE 'Reschedule offer'
      END,
      CASE
        WHEN o.requested_date IS NOT NULL THEN
          to_char(o.requested_date, 'YYYY-MM-DD') || ' · ' || o.status
        ELSE
          to_char(o.range_start AT TIME ZONE v_tz, 'YYYY-MM-DD HH24:MI') || '–' ||
          to_char(o.range_end AT TIME ZONE v_tz, 'HH24:MI') || ' (' || v_tz || ') · ' || o.status ||
          CASE WHEN o.selected_time IS NOT NULL
               THEN ' · picked ' || to_char(o.selected_time AT TIME ZONE v_tz, 'YYYY-MM-DD HH24:MI')
               ELSE '' END
      END
    FROM reschedule_offers o WHERE o.booking_id = p_booking_id

    UNION ALL
    -- (g) ledger entries — always included
    SELECT l.created_at, l.party,
      INITCAP(l.kind) || COALESCE(' ' || l.pct || '%', '') || COALESCE(' — ' || l.amount || ' ' || l.currency, ''),
      l.reason
    FROM booking_ledger l WHERE l.booking_id = p_booking_id
  )
  SELECT jsonb_build_object(
    'booking', jsonb_build_object(
      'id', b.id, 'status', b.status, 'created_at', b.created_at,
      'slot_time', b.slot_time, 'slot_end', b.slot_end,
      'reschedule_count', b.reschedule_count, 'no_show_by', b.no_show_by,
      'mentor_confirmed_at', b.mentor_confirmed_at, 'meeting_url', b.meeting_url,
      'service', s.title, 'duration', s.duration,
      'mentor', m.display_name, 'mentee', b.candidate_email,
      'mentee_tz', v_tz, 'mentor_tz', COALESCE(m.app_timezone, 'UTC')
    ),
    'payment', jsonb_build_object('amount', cp.amount, 'currency', cp.currency, 'status', cp.state),
    'payout', jsonb_build_object(
      'amount', mp.net_amount_mentor_currency, 'currency', mp.mentor_currency, 'status', mp.payout_state
    ),
    'totals', jsonb_build_object(
      'paid', cp.amount, 'currency', cp.currency, 'fee_pct', fee.pct,
      'platform_take', COALESCE(mp.platform_fee_amount, ROUND(cp.amount * fee.pct / 100.0, 2)),
      'net_to_mentor', mp.net_amount_mentor_currency,
      'mentor_currency', mp.mentor_currency, 'ppp_multiplier', mp.ppp_multiplier,
      'customer_refund', (SELECT COALESCE(SUM(amount), 0) FROM booking_ledger WHERE booking_id = p_booking_id AND party = 'customer' AND kind = 'refund'),
      'customer_credit', (SELECT COALESCE(SUM(amount), 0) FROM booking_ledger WHERE booking_id = p_booking_id AND party = 'customer' AND kind = 'credit'),
      'customer_charge', (SELECT COALESCE(SUM(amount), 0) FROM booking_ledger WHERE booking_id = p_booking_id AND party = 'customer' AND kind = 'charge'),
      'customer_penalty', (SELECT COALESCE(SUM(amount), 0) FROM booking_ledger WHERE booking_id = p_booking_id AND party = 'customer' AND kind = 'penalty'),
      'mentor_penalty', (SELECT COALESCE(SUM(amount), 0) FROM booking_ledger WHERE booking_id = p_booking_id AND party = 'mentor' AND kind = 'penalty'),
      'mentor_credit', (SELECT COALESCE(SUM(amount), 0) FROM booking_ledger WHERE booking_id = p_booking_id AND party = 'mentor' AND kind = 'credit')
    ),
    'timeline', (
      SELECT COALESCE(jsonb_agg(jsonb_build_object('at', ev.at, 'actor', ev.actor, 'title', ev.title, 'detail', ev.detail) ORDER BY ev.at), '[]'::jsonb)
      FROM ev WHERE ev.at IS NOT NULL
    )
  ) INTO v_result
  FROM b
  JOIN services s ON s.id = b.service_id
  JOIN mentors m ON m.id = b.mentor_id
  LEFT JOIN cp ON TRUE
  LEFT JOIN mp ON TRUE
  LEFT JOIN fee ON TRUE;

  RETURN v_result;
END;
$$;
GRANT EXECUTE ON FUNCTION admin_booking_detail(UUID) TO authenticated;

-- ── 14. Webinars (one-to-many sessions, separate from 1:1 bookings) ─────────
-- Ported from immigroov's 0055_webinars.sql (base), 0059_webinar_admin.sql
-- (admin_webinars, webinar_registrants), 0060_chat_inbox_webinar_share.sql
-- (webinar_public + two-stage reminders, replacing 0055's single-stage
-- version), 0062_webinar_no_double_register.sql (idempotent register_webinar
-- FINAL). No overlap with bookings/payment/payout machinery — webinars are
-- entirely free, registration is instant, and there is no FK to `bookings`
-- anywhere in the source. Confirmed no other webinar-named RPC exists beyond
-- the 7 originally scoped plus these 2 found during reconciliation.
--
-- NOT ported: webinars.reminder_sent — a dead column even in immigroov's own
-- final state (superseded by reminded_1d/reminded_1h in 0060, never written
-- to by any function after that). groovia has no legacy rows to stay
-- compatible with, so it's simply not created, same reasoning as Reviews'
-- edited_at omission.
--
-- Grant posture: immigroov leaves EVERY webinar RPC (including
-- create_webinar, cancel_webinar, admin_webinars, webinar_registrants)
-- GRANTed to anon+authenticated with ZERO server-side ownership checks —
-- p_mentor_id/p_webinar_id are trusted as passed, by the migration's own
-- design (no RLS anywhere on either table). That gap is NOT reproduced here:
-- mentor-scoped RPCs (create_webinar, cancel_webinar, mentor_webinars,
-- webinar_registrants) are GRANTed to authenticated only, with ownership
-- enforced at the FastAPI layer (resolve the caller's own mentor row via
-- get_mentor_by_profile_id, same pattern as every other mentor self-service
-- endpoint in routers/mentor.py) or admin bypass. admin_webinars follows the
-- existing admin_* convention (authenticated + require_admin). Public/
-- anonymous-by-design RPCs (list_webinars, webinar_public, register_webinar)
-- keep immigroov's anon grant — no ownership concept applies to them.

CREATE TABLE IF NOT EXISTS webinars (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id    UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  title        TEXT NOT NULL,
  description  TEXT,
  start_time   TIMESTAMPTZ NOT NULL,
  duration     INT NOT NULL DEFAULT 60,   -- minutes
  capacity     INT,                        -- NULL = unlimited
  visibility   TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'invite')),
  room_url     TEXT,
  status       TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'cancelled', 'ended')),
  reminded_1d  BOOLEAN NOT NULL DEFAULT FALSE,
  reminded_1h  BOOLEAN NOT NULL DEFAULT FALSE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_webinars_mentor ON webinars(mentor_id);
CREATE INDEX IF NOT EXISTS idx_webinars_public_upcoming ON webinars(start_time)
  WHERE visibility = 'public' AND status = 'scheduled';

CREATE TABLE IF NOT EXISTS webinar_registrations (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  webinar_id     UUID NOT NULL REFERENCES webinars(id) ON DELETE CASCADE,
  candidate_id   UUID REFERENCES profiles(id) ON DELETE SET NULL,
  email          TEXT NOT NULL,
  name           TEXT,
  registered_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_webinar_reg_unique ON webinar_registrations(webinar_id, LOWER(email));

-- p_mentor_id is NOT validated as the caller's own mentor row here (SQL
-- layer stays faithful to immigroov's design) — ownership is enforced by the
-- FastAPI endpoint before this RPC is ever called (see grant posture note).
CREATE OR REPLACE FUNCTION create_webinar(
  p_mentor_id UUID, p_title TEXT, p_description TEXT, p_start TIMESTAMPTZ,
  p_duration INT DEFAULT 60, p_capacity INT DEFAULT NULL, p_visibility TEXT DEFAULT 'public'
) RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_id UUID; v_title TEXT := TRIM(COALESCE(p_title, ''));
BEGIN
  IF v_title = '' THEN RAISE EXCEPTION 'Title is required'; END IF;
  IF p_start IS NULL OR p_start <= NOW() THEN RAISE EXCEPTION 'Start time must be in the future'; END IF;

  INSERT INTO webinars (mentor_id, title, description, start_time, duration, capacity, visibility, room_url)
    VALUES (
      p_mentor_id, v_title, NULLIF(TRIM(COALESCE(p_description, '')), ''), p_start,
      COALESCE(p_duration, 60), p_capacity, COALESCE(NULLIF(p_visibility, ''), 'public'),
      -- Deterministic string construction, no external API call — matches immigroov exactly.
      'https://meet.jit.si/ImmigroovWebinar-' || REPLACE(gen_random_uuid()::text, '-', '')
    ) RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;
GRANT EXECUTE ON FUNCTION create_webinar(UUID, TEXT, TEXT, TIMESTAMPTZ, INT, INT, TEXT) TO authenticated;

-- No existence/ownership check, no notification — matches immigroov exactly
-- (a no-op on an unknown id, silently, by design).
CREATE OR REPLACE FUNCTION cancel_webinar(p_webinar_id UUID)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  UPDATE webinars SET status = 'cancelled' WHERE id = p_webinar_id;
$$;
GRANT EXECUTE ON FUNCTION cancel_webinar(UUID) TO authenticated;

-- FINAL (0062) idempotent version. Capacity is only checked for a genuinely
-- NEW registrant — an already-registered email can always re-call this
-- (e.g. to update their name) even once the webinar is full. Confirmation
-- email is NOT sent from here (no pg_net in groovia) — the FastAPI layer
-- sends it as a BackgroundTask when the returned 'already' flag is false,
-- exactly mirroring the Reviews module's 5-star-notification pattern.
CREATE OR REPLACE FUNCTION register_webinar(p_webinar_id UUID, p_email TEXT, p_name TEXT DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  w webinars%ROWTYPE;
  v_email TEXT := LOWER(NULLIF(TRIM(COALESCE(p_email, '')), ''));
  v_already BOOLEAN;
  v_count INT;
  v_candidate_id UUID;
BEGIN
  IF v_email IS NULL OR POSITION('@' IN v_email) = 0 THEN
    RAISE EXCEPTION 'A valid email is required';
  END IF;

  SELECT * INTO w FROM webinars WHERE id = p_webinar_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Webinar not found'; END IF;
  IF w.status <> 'scheduled' THEN RAISE EXCEPTION 'This webinar is no longer open'; END IF;
  IF w.start_time <= NOW() THEN RAISE EXCEPTION 'This webinar has already started'; END IF;

  SELECT EXISTS(
    SELECT 1 FROM webinar_registrations WHERE webinar_id = p_webinar_id AND LOWER(email) = v_email
  ) INTO v_already;

  IF NOT v_already AND w.capacity IS NOT NULL THEN
    SELECT COUNT(*) INTO v_count FROM webinar_registrations WHERE webinar_id = p_webinar_id;
    IF v_count >= w.capacity THEN RAISE EXCEPTION 'This webinar is full'; END IF;
  END IF;

  SELECT id INTO v_candidate_id FROM profiles WHERE LOWER(email) = v_email;

  INSERT INTO webinar_registrations (webinar_id, candidate_id, email, name)
    VALUES (p_webinar_id, v_candidate_id, v_email, NULLIF(TRIM(COALESCE(p_name, '')), ''))
  ON CONFLICT (webinar_id, (LOWER(email))) DO UPDATE
    SET name = COALESCE(EXCLUDED.name, webinar_registrations.name);

  RETURN jsonb_build_object(
    'ok', true, 'already', v_already, 'room_url', w.room_url, 'title', w.title, 'start_time', w.start_time
  );
END;
$$;
GRANT EXECUTE ON FUNCTION register_webinar(UUID, TEXT, TEXT) TO anon, authenticated;

-- Public browse: upcoming, public, still-open webinars only.
CREATE OR REPLACE FUNCTION list_webinars()
RETURNS TABLE(
  id UUID, title TEXT, description TEXT, start_time TIMESTAMPTZ, duration INT,
  capacity INT, mentor_name TEXT, registrations INT
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT w.id, w.title, w.description, w.start_time, w.duration, w.capacity, m.display_name,
    (SELECT COUNT(*)::int FROM webinar_registrations r WHERE r.webinar_id = w.id)
  FROM webinars w JOIN mentors m ON m.id = w.mentor_id
  WHERE w.visibility = 'public' AND w.status = 'scheduled' AND w.start_time > NOW()
  ORDER BY w.start_time ASC;
$$;
GRANT EXECUTE ON FUNCTION list_webinars() TO anon, authenticated;

-- Share-link page: any visibility (invite-only included), any status —
-- "having the link is the gate", matching immigroov's own comment verbatim.
-- The frontend does its own client-side closed check (status/start_time).
CREATE OR REPLACE FUNCTION webinar_public(p_id UUID)
RETURNS TABLE(
  id UUID, title TEXT, description TEXT, start_time TIMESTAMPTZ, duration INT,
  capacity INT, status TEXT, mentor_name TEXT, registrations INT
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT w.id, w.title, w.description, w.start_time, w.duration, w.capacity, w.status, m.display_name,
    (SELECT COUNT(*)::int FROM webinar_registrations r WHERE r.webinar_id = w.id)
  FROM webinars w JOIN mentors m ON m.id = w.mentor_id
  WHERE w.id = p_id;
$$;
GRANT EXECUTE ON FUNCTION webinar_public(UUID) TO anon, authenticated;

-- Mentor's own dashboard: every one of their webinars, any status/visibility
-- (includes cancelled + invite-only) — p_mentor_id ownership enforced by the
-- FastAPI caller, see grant posture note above.
CREATE OR REPLACE FUNCTION mentor_webinars(p_mentor_id UUID)
RETURNS TABLE(
  id UUID, title TEXT, description TEXT, start_time TIMESTAMPTZ, duration INT, capacity INT,
  visibility TEXT, status TEXT, room_url TEXT, registrations INT
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT w.id, w.title, w.description, w.start_time, w.duration, w.capacity, w.visibility, w.status, w.room_url,
    (SELECT COUNT(*)::int FROM webinar_registrations r WHERE r.webinar_id = w.id)
  FROM webinars w
  WHERE w.mentor_id = p_mentor_id
  ORDER BY w.start_time DESC;
$$;
GRANT EXECUTE ON FUNCTION mentor_webinars(UUID) TO authenticated;

-- Serves both the mentor's own dashboard and the admin console — same RPC,
-- matching immigroov exactly. Ownership (mentor owns this webinar, or
-- caller is admin) is enforced by the FastAPI layer.
CREATE OR REPLACE FUNCTION webinar_registrants(p_webinar_id UUID)
RETURNS TABLE(name TEXT, email TEXT, registered_at TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(name, '—'), email, registered_at
  FROM webinar_registrations WHERE webinar_id = p_webinar_id
  ORDER BY registered_at ASC;
$$;
GRANT EXECUTE ON FUNCTION webinar_registrants(UUID) TO authenticated;

-- Cross-mentor platform view for the admin console — every webinar, any
-- status/visibility, matching immigroov's admin_webinars exactly.
CREATE OR REPLACE FUNCTION admin_webinars()
RETURNS TABLE(
  id UUID, title TEXT, mentor_name TEXT, start_time TIMESTAMPTZ, duration INT, capacity INT,
  visibility TEXT, status TEXT, registrations INT, created_at TIMESTAMPTZ
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT w.id, w.title, m.display_name, w.start_time, w.duration, w.capacity, w.visibility, w.status,
    (SELECT COUNT(*)::int FROM webinar_registrations r WHERE r.webinar_id = w.id), w.created_at
  FROM webinars w JOIN mentors m ON m.id = w.mentor_id
  ORDER BY w.start_time DESC;
$$;
GRANT EXECUTE ON FUNCTION admin_webinars() TO authenticated;

-- Reminders: immigroov's send_webinar_reminders (0060 two-stage FINAL) both
-- selects due webinars AND sends the batched email in one plpgsql function,
-- via pg_net — impossible here (no SQL-side HTTP in groovia). The actual
-- email send happens at the FastAPI layer.
--
-- GENUINELY DEFERRED (the scheduler part), not silently dropped: immigroov
-- drives this from pg_cron on a 10-minute timer with no request context —
-- groovia has no dispatcher yet (see INFRASTRUCTURE_ARCHITECTURE_PLAN.md).
-- Until it exists, this backs an admin-triggerable endpoint only.
--
-- claim_due_webinar_reminders() replaces an earlier two-step version of this
-- (due_webinar_reminders() read + a separate mark_webinar_reminded() write,
-- called after sending). That shape was NOT concurrency-safe: two overlapping
-- calls could both read the same "not yet reminded" webinar before either
-- wrote its flag, so both would send — the DB constraint dedupes the flag,
-- not the email. See DISPATCHER_SAFETY_CHECKLIST.md's send_webinar_reminders
-- finding. This version claims each (webinar, stage) FIRST via an UPDATE ...
-- WHERE NOT already-reminded ... RETURNING — Postgres's own row-level locking
-- on that UPDATE guarantees at most one caller ever gets a given (webinar,
-- stage) back — then joins registrants only for what it actually claimed.
-- A caller that sends nothing for a row it didn't get back is safe.
CREATE OR REPLACE FUNCTION claim_due_webinar_reminders()
RETURNS TABLE(
  webinar_id UUID, stage TEXT, title TEXT, start_time TIMESTAMPTZ, duration INT, room_url TEXT,
  registrant_email TEXT, registrant_name TEXT
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  RETURN QUERY
  WITH claimed_1d AS (
    UPDATE webinars
    SET reminded_1d = TRUE
    WHERE status = 'scheduled' AND NOT reminded_1d
      AND start_time BETWEEN NOW() + INTERVAL '23 hours' AND NOW() + INTERVAL '25 hours'
    RETURNING id, '1d'::TEXT AS stage, title, start_time, duration, room_url
  ), claimed_1h AS (
    UPDATE webinars
    SET reminded_1h = TRUE
    WHERE status = 'scheduled' AND NOT reminded_1h
      AND start_time BETWEEN NOW() AND NOW() + INTERVAL '70 minutes'
    RETURNING id, '1h'::TEXT AS stage, title, start_time, duration, room_url
  ), claimed AS (
    SELECT * FROM claimed_1d
    UNION ALL
    SELECT * FROM claimed_1h
  )
  SELECT c.id, c.stage, c.title, c.start_time, c.duration, c.room_url, r.email, r.name
  FROM claimed c
  JOIN webinar_registrations r ON r.webinar_id = c.id;
END;
$$;
GRANT EXECUTE ON FUNCTION claim_due_webinar_reminders() TO authenticated;
