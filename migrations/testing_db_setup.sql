-- testing_db_setup.sql
-- Complete Groovia schema + seed data for a local testing Supabase project.
-- Run this ONCE on a fresh test project. Use testing_db_reset.sql to clear test data between runs.
-- Do NOT run on the production database.

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

-- For DBs created before the approval column existed.
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
CREATE TABLE IF NOT EXISTS ppp_factors (
  country_code  CHAR(2)      PRIMARY KEY,
  factor        NUMERIC(5,4) NOT NULL CHECK (factor > 0 AND factor <= 1),
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
  v_hash := encode(digest(v_snap::text, 'sha256'), 'hex');
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
-- Not scheduled here — groovia-backend has no pg_cron jobs registered yet;
-- run this manually or wire up a scheduler when the cron infra exists.
-- DELETE FROM pricing_quotes WHERE expires_at < NOW() - INTERVAL '1 day';

-- ============================================================================
-- Seed mentor data (testing only)
-- ============================================================================
INSERT INTO mentors (
  slug, display_name, headline, bio,
  expertise_country_codes, expertise_categories, languages, professional_domains,
  booking_url, years_lived_experience
) VALUES
  (
    'maya-singh',
    'Maya Singh',
    'Software Engineer who moved from India to the Netherlands (Blue Card)',
    'I navigated the Dutch Blue Card process while changing jobs. Happy to share what I learned about paperwork, timing, and avoiding pitfalls.',
    ARRAY['NL']::CHAR(2)[],
    ARRAY['job_career', 'visa_pr'],
    ARRAY['en', 'hi'],
    ARRAY['IT'],
    'maya-singh', 4
  ),
  (
    'priya-mehta',
    'Priya Mehta',
    'Masters at TU Eindhoven, now ML Engineer at ASML',
    'Study-abroad route: application strategy, scholarship hunting, and the post-study work visa (zoekjaar).',
    ARRAY['NL']::CHAR(2)[],
    ARRAY['study_abroad', 'job_career'],
    ARRAY['en', 'hi'],
    ARRAY['Engineering', 'AI/ML'],
    'priya-mehta', 3
  ),
  (
    'lars-jansen',
    'Lars Jansen',
    'Relocation specialist — settling into Amsterdam',
    'Housing in Amsterdam is brutal. I help newcomers find a place, register at the gemeente, set up DigiD, banking, healthcare.',
    ARRAY['NL']::CHAR(2)[],
    ARRAY['life_settling'],
    ARRAY['en', 'nl'],
    ARRAY['Operations'],
    'lars-jansen', 8
  ),
  (
    'rohan-kapoor',
    'Rohan Kapoor',
    'Product Manager in Berlin (Skilled Worker visa)',
    'Helped 50+ folks land PM roles in Berlin. Specialism: visa interviews + salary negotiation for non-EU PMs.',
    ARRAY['DE']::CHAR(2)[],
    ARRAY['job_career'],
    ARRAY['en', 'hi', 'de'],
    ARRAY['Product'],
    'rohan-kapoor', 6
  ),
  (
    'fatima-rahman',
    'Fatima Rahman',
    'Healthcare professional — Germany work visa route',
    'Nurses, doctors, allied health: the Approbation process, B2 German requirement, and how to land your first hospital role.',
    ARRAY['DE']::CHAR(2)[],
    ARRAY['job_career', 'visa_pr'],
    ARRAY['en', 'ur', 'de'],
    ARRAY['Healthcare'],
    'fatima-rahman', 5
  ),
  (
    'arjun-iyer',
    'Arjun Iyer',
    'Masters in Computer Science, TU Munich',
    'I went from a tier-2 Indian engineering college to TUM on scholarship. Application strategy, German B1, student life.',
    ARRAY['DE']::CHAR(2)[],
    ARRAY['study_abroad'],
    ARRAY['en', 'hi', 'ta', 'de'],
    ARRAY['Engineering'],
    'arjun-iyer', 2
  ),
  (
    'sara-okonkwo',
    'Sara Okonkwo',
    'PR in Canada via Express Entry — finance professional',
    'CRS score optimization, IELTS prep, document checklist for ECA + PR. Toronto-based.',
    ARRAY['CA']::CHAR(2)[],
    ARRAY['visa_pr', 'job_career'],
    ARRAY['en', 'fr'],
    ARRAY['Finance'],
    'sara-okonkwo', 5
  ),
  (
    'daniel-park',
    'Daniel Park',
    'Software Engineer — Vancouver tech scene + LMIA',
    'Moved from Seoul to Vancouver on LMIA, transitioned to PR. Tech job market reality, salaries, neighborhoods.',
    ARRAY['CA']::CHAR(2)[],
    ARRAY['job_career', 'life_settling'],
    ARRAY['en', 'ko'],
    ARRAY['IT'],
    'daniel-park', 4
  ),
  (
    'aditi-banerjee',
    'Aditi Banerjee',
    'Skilled Worker visa to UK — research scientist',
    'Sponsoring employers, salary thresholds, dependent visas, switching from Student to Skilled Worker.',
    ARRAY['GB']::CHAR(2)[],
    ARRAY['visa_pr', 'job_career'],
    ARRAY['en', 'bn', 'hi'],
    ARRAY['Research', 'Healthcare'],
    'aditi-banerjee', 7
  ),
  (
    'james-okafor',
    'James Okafor',
    'Global Talent visa holder — early-stage founder',
    'Got the UK Global Talent visa as a Nigerian tech founder. Endorsing bodies, evidence pack, founder visa alternatives.',
    ARRAY['GB']::CHAR(2)[],
    ARRAY['visa_pr'],
    ARRAY['en'],
    ARRAY['Product', 'Startups'],
    'james-okafor', 3
  ),
  (
    'vivek-shah',
    'Vivek Shah',
    'H1B lottery to Green Card — 12 years in the US',
    'I survived 6 H1B lotteries, an L1 transfer, EB2-NIW filing. Honest take on the wait + employer dependence.',
    ARRAY['US']::CHAR(2)[],
    ARRAY['visa_pr', 'job_career'],
    ARRAY['en', 'hi', 'gu'],
    ARRAY['IT'],
    'vivek-shah', 12
  ),
  (
    'emily-chen',
    'Emily Chen',
    'F1 student → OPT → STEM extension — Bay Area',
    'University selection for STEM grads aiming at US tech. OPT/CPT, employer red flags, internship hunting timeline.',
    ARRAY['US']::CHAR(2)[],
    ARRAY['study_abroad', 'job_career'],
    ARRAY['en', 'zh'],
    ARRAY['IT', 'Engineering'],
    'emily-chen', 4
  ),
  (
    'ravi-pillai',
    'Ravi Pillai',
    'Skilled Independent visa (189) — Melbourne',
    'PR via points-based system. Skills assessment, EOI, state nomination. Cost of moving from India to AU.',
    ARRAY['AU']::CHAR(2)[],
    ARRAY['visa_pr'],
    ARRAY['en', 'hi', 'ml'],
    ARRAY['Engineering'],
    'ravi-pillai', 6
  ),
  (
    'sophie-laurent',
    'Sophie Laurent',
    'Career across EU — France, Germany, Belgium',
    'I worked in 3 EU countries in 10 years. EU Blue Card, intra-EU moves, taxation, family relocations.',
    ARRAY['DE', 'FR', 'BE']::CHAR(2)[],
    ARRAY['job_career', 'visa_pr', 'life_settling'],
    ARRAY['en', 'fr', 'de'],
    ARRAY['Consulting'],
    'sophie-laurent', 10
  )
ON CONFLICT (slug) DO NOTHING;

-- Give seed mentors a contact email so booking-confirmation emails to mentors work in
-- testing (all point at one inbox you control). Idempotent — only fills blanks.
UPDATE mentors SET email = 'yokeshd1999@gmail.com' WHERE profile_id IS NULL AND email IS NULL;

-- ============================================================================
-- Seed bookable services + weekly availability for the seed mentors (testing only)
-- The mentor profile page only renders a "Book" widget when a mentor has at least
-- one service. Without this, every seed mentor falls back to "setting up their
-- calendar — check back soon" and there is no button to test the booking flow.
-- Each seed mentor gets one free 30-min video service and a *varied* weekly
-- schedule (different days/hours per mentor) so the booking calendar isn't
-- identical for everyone. Guarded so re-running the setup is idempotent.
-- ============================================================================
DO $$
DECLARE
  m RECORD;
  i INT := 0;
  v_start TIME;
  v_end   TIME;
  v_days  TEXT[];
BEGIN
  FOR m IN SELECT id FROM mentors WHERE profile_id IS NULL ORDER BY slug LOOP
    i := i + 1;
    -- vary start (08:00–11:00) and end (15:00–18:00) and the working days
    v_start := TIME '08:00' + ((i % 4) * INTERVAL '1 hour');
    v_end   := TIME '15:00' + ((i % 4) * INTERVAL '1 hour');
    v_days  := CASE (i % 3)
                 WHEN 0 THEN ARRAY['Monday','Tuesday','Wednesday','Thursday','Friday']
                 WHEN 1 THEN ARRAY['Monday','Wednesday','Friday']
                 ELSE        ARRAY['Tuesday','Thursday','Saturday']
               END;

    IF NOT EXISTS (SELECT 1 FROM services WHERE mentor_id = m.id) THEN
      INSERT INTO services (mentor_id, title, description, type, duration, category,
                            set_price, set_currency, is_active, status)
      VALUES (m.id, '1-on-1 Mentoring Session',
              'A 30-minute video call to talk through your visa and career questions.',
              'video', 30, 'job_career', 50, 'EUR', TRUE, 'approved');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM weekly_availability WHERE mentor_id = m.id) THEN
      INSERT INTO weekly_availability (mentor_id, weekday, start_time, end_time, timezone, is_active)
      SELECT m.id, d, v_start, v_end, 'UTC', TRUE FROM unnest(v_days) AS d;
    END IF;
  END LOOP;
END $$;

-- Correct any seed services still priced at 0 (idempotent; leaves real prices alone).
UPDATE services SET set_price = 50, set_currency = 'EUR'
  WHERE set_price = 0 AND mentor_id IN (SELECT id FROM mentors WHERE profile_id IS NULL);
-- Seed services belong to pre-approved mentors → mark them approved (bookable).
UPDATE services SET status = 'approved'
  WHERE status <> 'approved' AND mentor_id IN (SELECT id FROM mentors WHERE profile_id IS NULL);


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

-- ── 5. Cancel flow (REPLACES the old block-on-late cancel_booking) ────────────
-- >=24h: cancelled immediately · 2–24h (user): opens a cancel request for mentor
-- approval · <2h: blocked. Mentor cancel is always allowed (>=2h) and is free
-- >=24h, bumps the cancellation counter when late. Auth is enforced in FastAPI.
CREATE OR REPLACE FUNCTION cancel_booking(p_booking_id UUID, p_cancelled_by TEXT DEFAULT 'user')
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_state TEXT;
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

  IF p_cancelled_by = 'mentor' THEN
    UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id RETURNING * INTO b;
    UPDATE reschedule_offers SET status = 'superseded'
      WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
    IF v_state = 'late' THEN PERFORM bump_mentor_cancellation(b.mentor_id); END IF;
    PERFORM notify_booking_event(p_booking_id, 'cancelled');
    RETURN b;
  END IF;

  IF v_state = 'free' THEN
    UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id RETURNING * INTO b;
    UPDATE reschedule_offers SET status = 'superseded'
      WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
    PERFORM notify_booking_event(p_booking_id, 'cancelled');
    RETURN b;
  ELSE
    -- late: booking stays confirmed; open a cancel request for the mentor
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
DECLARE r booking_requests;
BEGIN
  SELECT * INTO r FROM booking_requests WHERE id = p_request_id;
  IF NOT FOUND OR r.status <> 'pending' THEN RAISE EXCEPTION 'This request is no longer open'; END IF;

  IF r.kind = 'cancel' THEN
    -- A late cancel resolves to cancelled either way (accept = goodwill, reject =
    -- 50% fee would apply once payments exist). State is identical without money.
    UPDATE bookings SET status = 'cancelled' WHERE id = r.booking_id;
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
BEGIN
  UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id;
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
    RAISE EXCEPTION 'That time is no longer available — pick another slot inside the range.';
  END IF;
  UPDATE reschedule_offers SET status = 'accepted', selected_time = p_slot_time WHERE id = p_offer_id;
  UPDATE bookings SET slot_time = p_slot_time, slot_end = NULL, status = 'rescheduled',
                      reschedule_count = reschedule_count + 1
    WHERE id = o.booking_id RETURNING * INTO b;
  DELETE FROM booking_reminders WHERE booking_id = o.booking_id;
  PERFORM notify_booking_event(o.booking_id, 'rescheduled');
  RETURN b;
END;
$$;

-- ── 10. Mentee rejects the mentor's proposal — booking cancelled ──────────────
CREATE OR REPLACE FUNCTION mentee_reject_reschedule(p_offer_id UUID)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE o reschedule_offers; b bookings;
BEGIN
  SELECT * INTO o FROM reschedule_offers WHERE id = p_offer_id;
  IF NOT FOUND OR o.status NOT IN ('pending','mentee_selected') OR o.proposed_by <> 'mentor' THEN
    RAISE EXCEPTION 'This proposal is no longer open';
  END IF;
  UPDATE reschedule_offers SET status = 'rejected' WHERE id = p_offer_id;
  UPDATE bookings SET status = 'cancelled' WHERE id = o.booking_id RETURNING * INTO b;
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
  RETURN b;
END;
$$;

-- Mentor no-showed → the user picks one of three outcomes.
CREATE OR REPLACE FUNCTION resolve_mentor_no_show(p_booking_id UUID, p_choice TEXT)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF b.no_show_by IS DISTINCT FROM 'mentor' OR b.status <> 'no_show' THEN
    RAISE EXCEPTION 'Not a mentor no-show awaiting resolution';
  END IF;
  IF p_choice = 'rebook_same' THEN
    UPDATE bookings SET status = 'confirmed', no_show_by = NULL WHERE id = p_booking_id RETURNING * INTO b;
  ELSIF p_choice IN ('rebook_different','refund') THEN
    PERFORM apply_mentor_strike(b.mentor_id);
    -- status stays 'no_show'; (credit/refund would post to the ledger in Phase 2)
  ELSE
    RAISE EXCEPTION 'Unknown choice %', p_choice;
  END IF;
  RETURN b;
END;
$$;

-- User no-showed → the mentor picks one of two outcomes.
CREATE OR REPLACE FUNCTION resolve_customer_no_show(p_booking_id UUID, p_choice TEXT)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF b.no_show_by IS DISTINCT FROM 'user' OR b.status <> 'no_show' THEN
    RAISE EXCEPTION 'Not a user no-show awaiting resolution';
  END IF;
  IF p_choice = 'accept_rebook' THEN
    UPDATE bookings SET status = 'confirmed', no_show_by = NULL WHERE id = p_booking_id RETURNING * INTO b;
  ELSIF p_choice = 'reject' THEN
    UPDATE bookings SET status = 'completed' WHERE id = p_booking_id RETURNING * INTO b;
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

CREATE OR REPLACE FUNCTION mark_past_bookings_completed()
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE n INT;
BEGIN
  UPDATE bookings SET status = 'completed'
   WHERE status IN ('confirmed','rescheduled')
     AND slot_end IS NOT NULL AND slot_end < NOW();
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END;
$$;

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
