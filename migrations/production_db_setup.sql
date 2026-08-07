-- production_db_setup.sql
-- Complete Groovia schema for fresh Supabase project setup.
-- Run this ONCE in Supabase SQL Editor on a new project.
-- For existing databases, run migrations 001-015 sequentially instead.

-- Validate function bodies at RUNTIME, not at CREATE time (what pg_dump emits). This makes the
-- script order-independent: e.g. the my_bookings / mentor_sessions read RPCs reference
-- customer_payments / mentor_payouts, which are created later in this file.
SET check_function_bodies = off;

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
  CREATE TYPE mentor_status AS ENUM ('pending_review', 'approved', 'rejected', 'suspended', 'changes_requested');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
-- 'changes_requested': admin asked the applicant to revise (editable + can resubmit),
-- distinct from 'rejected' (declined). Idempotent add for DBs that predate it.
ALTER TYPE mentor_status ADD VALUE IF NOT EXISTS 'changes_requested';

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
  years_lived_experience  INTEGER,               -- years lived abroad (optional; 0/NULL for locals)
  years_professional_experience INTEGER,         -- years of professional experience (required at signup)
  social_links            JSONB NOT NULL DEFAULT '[]',
  phone                   TEXT,
  city                    TEXT,
  country                 TEXT,                  -- current country of residence (location display)
  home_country_code       CHAR(2),               -- home / origin country (shown to users as "from X")
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
  legacy_id               TEXT UNIQUE,           -- id from the old (AWS) immigroov system; makes migration idempotent
  legacy_data             JSONB,                 -- source fields with no column here yet (total_sessions, response_time, per-service is_ppp, ...)
  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mentors_status_active ON mentors(status, is_active);
-- Fast case-insensitive lookup for pre-approved mentor linking on login (link_mentor_by_email).
CREATE INDEX IF NOT EXISTS idx_mentors_email_lower ON mentors(lower(email)) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mentors_countries     ON mentors USING GIN (expertise_country_codes);
CREATE INDEX IF NOT EXISTS idx_mentors_categories    ON mentors USING GIN (expertise_categories);

-- ============================================================================
-- mentor_bank_accounts  (payout details; the founder pays mentors manually, outside Razorpay)
-- The sensitive numbers (account number / IBAN / routing / sort code / IFSC / SWIFT) live ONLY
-- inside details_enc, a Fernet ciphertext produced by the backend (services/bank_crypto.py) - the
-- DB never stores them in clear. The cleartext columns are display-safe metadata only: holder
-- name, bank name, country, scheme, and the last 4 digits for masked display.
-- ============================================================================
CREATE TABLE IF NOT EXISTS mentor_bank_accounts (
  mentor_id            UUID PRIMARY KEY REFERENCES mentors(id) ON DELETE CASCADE,
  country_code         CHAR(2) NOT NULL,
  scheme               TEXT NOT NULL,          -- iban | india | us | uk | swift
  account_holder_name  TEXT NOT NULL,
  bank_name            TEXT,
  account_last4        TEXT,
  details_enc          TEXT NOT NULL,          -- Fernet-encrypted JSON of the secret numbers
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
-- services (before bookings - bookings references it)
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
  set_price    NUMERIC(10,2) NOT NULL DEFAULT 0,        -- base price in the mentor's PRIMARY currency
  set_currency TEXT NOT NULL DEFAULT 'USD',             -- the primary currency (mentor payout currency)
  set_offer_price NUMERIC(10,2),                        -- optional discount price in the primary currency
  currency_prices JSONB NOT NULL DEFAULT '[]',          -- explicit extra prices [{currency, base_price, offer_price}]
  platform_fee NUMERIC(10,2) NOT NULL DEFAULT 0,
  category     TEXT,
  status       TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'approved' | 'rejected' (admin review)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE services ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE services ADD COLUMN IF NOT EXISTS set_offer_price NUMERIC(10,2);
ALTER TABLE services ADD COLUMN IF NOT EXISTS currency_prices JSONB NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_services_mentor_id ON services(mentor_id);
CREATE INDEX IF NOT EXISTS idx_services_active    ON services(mentor_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_services_pending   ON services(status) WHERE status = 'pending';

-- ============================================================================
-- specific_availability (before bookings - bookings references it)
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

-- July bug-fix batch: mentor hourly rate + smart pricing, mandatory booking phone,
-- and per-service search tags. service_create below derives is_ppp from the mentor's
-- smart_pricing toggle and stores tags.
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS hourly_rate NUMERIC;
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS smart_pricing BOOLEAN NOT NULL DEFAULT FALSE;
-- Additional-currency base rates [{currency, hourly_rate}]; the primary is (currency, hourly_rate).
-- Each service's currency_prices are derived from these by duration.
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS currency_rates JSONB NOT NULL DEFAULT '[]';
-- These were added to the CREATE TABLE above, but CREATE TABLE IF NOT EXISTS is a no-op on an
-- existing DB, so they MUST also be ALTERed in (or the mentor-list SELECT breaks on old databases).
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS home_country_code CHAR(2);
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS years_professional_experience INTEGER;
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS legacy_id TEXT UNIQUE;
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS legacy_data JSONB;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS candidate_phone TEXT;
ALTER TABLE services ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

-- Jitsi 1:1 video: an unguessable room name per booking (revealed only in-window),
-- plus attendance timestamps (first join / last leave per party) for no-show detection.
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS meeting_room        TEXT;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS candidate_joined_at TIMESTAMPTZ;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS mentor_joined_at    TIMESTAMPTZ;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS candidate_left_at   TIMESTAMPTZ;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS mentor_left_at      TIMESTAMPTZ;
-- Speeds up the periodic finalize scan (active bookings past their end).
CREATE INDEX IF NOT EXISTS idx_bookings_active_slot_end ON bookings(slot_end) WHERE status IN ('confirmed','rescheduled');

-- ============================================================================
-- mentor_availability (legacy manual fallback system - superseded by weekly_availability)
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
  ('immigroov_commission_pct', '15', 'Default Immigroov commission %; used at service_create to seed services.platform_fee'),
  ('mentor_commission_pct', '30', 'Default INTERNAL commission % taken OUT of the mentor''s session price (mentor nets the rest). Never shown to the customer; distinct from the customer-facing country_pricing.platform_fee_pct.'),
  ('default_currency', 'USD', 'Fallback currency for the platform')
ON CONFLICT (key) DO NOTHING;
-- Retire the legacy global-markup setting (superseded by country_pricing + the revenue-split model).
DELETE FROM platform_settings WHERE key = 'immigroov_markup_pct';

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

DROP TRIGGER IF EXISTS trg_mentor_bank_accounts_updated_at ON mentor_bank_accounts;
CREATE TRIGGER trg_mentor_bank_accounts_updated_at
  BEFORE UPDATE ON mentor_bank_accounts
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
    (CASE WHEN lower(NEW.email) IN ('yokeshmd99@gmail.com', 'immigroovtst@gmail.com') THEN 'admin'
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

-- Ops admins: promote the admin accounts if their profiles already exist (idempotent).
UPDATE profiles SET role = 'admin'
  WHERE lower(email) IN ('yokeshmd99@gmail.com', 'immigroovtst@gmail.com') AND role <> 'admin';

-- Does this email already have a PASSWORD? signInWithOtp creates an auth.users row
-- immediately (unconfirmed, no password), so "row exists" ≠ "can log in with a password".
-- Returns one row (has_password) if the email exists, zero rows if not.
-- Re-runnable guard: an existing DB may already hold older versions of these
-- functions with different result columns. CREATE OR REPLACE cannot change a
-- function's return type / OUT columns, so drop them first.
DROP FUNCTION IF EXISTS email_account_status(text);
DROP FUNCTION IF EXISTS get_available_slots(uuid, uuid, date, date);
DROP FUNCTION IF EXISTS booking_times_display(uuid);
DROP FUNCTION IF EXISTS avail_list_weekly(uuid);
DROP FUNCTION IF EXISTS avail_list_specific(uuid);
DROP FUNCTION IF EXISTS avail_get_rules(uuid);
DROP FUNCTION IF EXISTS service_list(uuid);
DROP FUNCTION IF EXISTS question_list(uuid);
DROP FUNCTION IF EXISTS book_session(uuid, uuid, timestamptz, text, text, text, jsonb, uuid, uuid);

CREATE OR REPLACE FUNCTION email_account_status(p_email text)
RETURNS TABLE (has_password boolean, providers text[])
LANGUAGE sql SECURITY DEFINER SET search_path = public, auth AS $$
  -- has_password lets the popup route real password accounts to the login screen;
  -- providers (e.g. ['google'], ['email'], ['email','google']) lets it detect an
  -- OAuth-created account with no password and say "continue with Google" instead of
  -- wrongly emailing an OTP link + forcing a password.
  SELECT
    (u.encrypted_password IS NOT NULL AND length(u.encrypted_password) > 0),
    COALESCE(ARRAY(SELECT jsonb_array_elements_text(u.raw_app_meta_data -> 'providers')), ARRAY[]::text[])
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
    RAISE EXCEPTION 'Cancellations must be at least % hours before the session - please reschedule instead.',
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
    RAISE EXCEPTION 'That time slot is no longer available - please pick another';
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
  -- Caps enforced here (single write path): book-ahead <= 90 days, min notice <= 24h,
  -- cancel/reschedule notice 2-48h (>= the 2h buffer so a 'late' window always exists).
  UPDATE mentors SET
    app_booking_window = MAKE_INTERVAL(days => LEAST(GREATEST(p_days_ahead, 1), 90)),
    app_minimum_notice = MAKE_INTERVAL(mins => ROUND(LEAST(GREATEST(p_min_notice_hours, 0), 24) * 60)::INTEGER),
    cancel_notice_hours = LEAST(GREATEST(COALESCE(p_cancel_hours, cancel_notice_hours), 2), 48)
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
DECLARE v_cur TEXT; v_pct NUMERIC; v_id UUID; v_ppp BOOLEAN;
BEGIN
  -- is_ppp is derived from the mentor's smart_pricing toggle (one control), not p_ppp.
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
    RAISE EXCEPTION 'That time is not available - please choose another slot';
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

-- mentor_bank_accounts: sensitive payout data. RLS ON with NO policies = deny-all to anon and
-- authenticated; only the backend's service-role key (which bypasses RLS) may read or write it.
ALTER TABLE mentor_bank_accounts ENABLE ROW LEVEL SECURITY;

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
-- ppp_factors, get_ppp_factor, the FX engine, the pricing engine and the whole
-- Razorpay payment/payout schema are consolidated at the END of this file
-- (folded in from payments_setup.sql), placed there so every dependency
-- (bookings, services, mentors, is_slot_available, is_valid_timezone,
-- booking_question_answers) already exists. See the section:
--   "Pricing (PPP + FX) + Razorpay payments" below.
-- ============================================================================


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
-- Profile-change review for APPROVED mentors: the proposed edit is held here while the live
-- mentors row stays visible/bookable. On approval the backend applies these to the live
-- columns and clears them; on reject it just clears them. NULL = no revision awaiting review.
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS pending_changes JSONB;
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS pending_submitted_at TIMESTAMPTZ;
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
-- The free/late boundary is the mentor's cancellation-and-reschedule notice (p_free_hours,
-- default 24h), so changing it in the availability rules changes when penalties apply. The 2h
-- buffer (too close to touch) stays a fixed platform safety rail, so p_free_hours is floored at 2.
DROP FUNCTION IF EXISTS booking_deadline_state(TIMESTAMPTZ);
CREATE OR REPLACE FUNCTION booking_deadline_state(p_slot TIMESTAMPTZ, p_free_hours NUMERIC DEFAULT 24)
RETURNS TEXT LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN p_slot IS NULL                        THEN 'free'
    WHEN p_slot - NOW() < INTERVAL '2 hours'   THEN 'buffer'
    WHEN p_slot - NOW() < MAKE_INTERVAL(mins => (GREATEST(p_free_hours, 2) * 60)::INTEGER) THEN 'late'
    ELSE 'free'
  END;
$$;

CREATE OR REPLACE FUNCTION response_window(p_slot TIMESTAMPTZ)
RETURNS TIMESTAMPTZ LANGUAGE sql STABLE AS $$
  SELECT LEAST(NOW() + INTERVAL '48 hours', p_slot - INTERVAL '2 hours');
$$;
GRANT EXECUTE ON FUNCTION booking_deadline_state(TIMESTAMPTZ, NUMERIC) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION response_window(TIMESTAMPTZ) TO anon, authenticated;

-- ── 5. Cancel flow (REPLACES the old block-on-late cancel_booking) ────────────
-- >=24h: cancelled immediately · 2-24h (user): opens a cancel request for mentor
-- approval · <2h: blocked. Mentor cancel is always allowed (>=2h) and is free
-- >=24h, bumps the cancellation counter when late. Auth is enforced in FastAPI.
CREATE OR REPLACE FUNCTION cancel_booking(p_booking_id UUID, p_cancelled_by TEXT DEFAULT 'user')
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_state TEXT; v_free_hours INTEGER;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking % not found', p_booking_id; END IF;
  IF b.status IN ('cancelled','completed','no_show') THEN
    RAISE EXCEPTION 'Booking % is already %', p_booking_id, b.status;
  END IF;

  SELECT cancel_notice_hours INTO v_free_hours FROM mentors WHERE id = b.mentor_id;
  v_state := booking_deadline_state(b.slot_time, COALESCE(v_free_hours, 24));
  IF v_state = 'buffer' THEN
    RAISE EXCEPTION 'Within 2 hours of the session - it can no longer be cancelled here. Please contact the other party.';
  END IF;

  IF p_cancelled_by = 'mentor' THEN
    UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id RETURNING * INTO b;
    UPDATE reschedule_offers SET status = 'superseded'
      WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
    -- Mentor cancel: customer always refunded 100%; a late cancel also penalizes the mentor 25%.
    PERFORM settle_booking(p_booking_id, 'Mentor cancelled', p_cust_refund_pct => 100,
                           p_mentor_penalty_pct => CASE WHEN v_state = 'late' THEN 25 ELSE 0 END);
    IF v_state = 'late' THEN PERFORM bump_mentor_cancellation(b.mentor_id); END IF;
    PERFORM notify_booking_event(p_booking_id, 'cancelled');
    RETURN b;
  END IF;

  IF v_state = 'free' THEN
    UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id RETURNING * INTO b;
    UPDATE reschedule_offers SET status = 'superseded'
      WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
    PERFORM settle_booking(p_booking_id, 'Customer cancelled (free)', p_cust_refund_pct => 100);
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
    -- A late cancel resolves to cancelled either way. Accept (or cron auto-approve) = goodwill,
    -- full refund. Reject = platform keeps 50%, customer refunded the other 50%.
    UPDATE bookings SET status = 'cancelled' WHERE id = r.booking_id;
    UPDATE booking_requests
       SET status = CASE WHEN p_accept THEN 'approved' ELSE 'rejected' END, resolved_at = NOW()
     WHERE id = p_request_id;
    UPDATE reschedule_offers SET status = 'superseded'
      WHERE booking_id = r.booking_id AND status IN ('pending','mentee_selected');
    IF p_accept THEN
      PERFORM settle_booking(r.booking_id, 'Late cancel approved', p_cust_refund_pct => 100);
    ELSE
      PERFORM settle_booking(r.booking_id, 'Late cancel rejected', p_cust_refund_pct => 50, p_cust_charge_pct => 50);
    END IF;
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

-- ── 7. force_autocancel (3rd reschedule attempt) - state only ─────────────────
CREATE OR REPLACE FUNCTION force_autocancel(p_booking_id UUID, p_initiator TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id;
  UPDATE reschedule_offers SET status = 'superseded'
    WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
  -- 3rd reschedule attempt: 100% penalty on the initiator. Customer-initiated -> they forfeit
  -- (net 0 back). Mentor-initiated -> customer fully refunded + mentor penalized 100%.
  IF p_initiator = 'mentor' THEN
    PERFORM settle_booking(p_booking_id, '3rd reschedule (mentor)', p_cust_refund_pct => 100, p_mentor_penalty_pct => 100);
  ELSE
    PERFORM settle_booking(p_booking_id, '3rd reschedule (customer)', p_cust_charge_pct => 100);
  END IF;
  PERFORM notify_booking_event(p_booking_id, 'cancelled');
END;
$$;
GRANT EXECUTE ON FUNCTION force_autocancel(UUID, TEXT) TO authenticated;

-- ── 8. Reschedule - mentor proposes a date + range ────────────────────────────
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
            booking_deadline_state(b.slot_time, COALESCE((SELECT cancel_notice_hours FROM mentors WHERE id = b.mentor_id), 24)) = 'late',
            response_window(b.slot_time))
    RETURNING id INTO v_id;
  PERFORM notify_booking_event(p_booking_id, 'proposed');
  RETURN v_id;
END;
$$;

-- ── 9. Mentee accepts a slot in the proposed range - finalises directly ───────
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
    RAISE EXCEPTION 'That time is no longer available - pick another slot inside the range.';
  END IF;
  UPDATE reschedule_offers SET status = 'accepted', selected_time = p_slot_time WHERE id = p_offer_id;
  UPDATE bookings SET slot_time = p_slot_time, slot_end = NULL, status = 'rescheduled',
                      reschedule_count = reschedule_count + 1
    WHERE id = o.booking_id RETURNING * INTO b;
  DELETE FROM booking_reminders WHERE booking_id = o.booking_id;
  -- A reschedule the mentor proposed late (was_late) penalizes the mentor 25%; no customer charge.
  IF o.was_late THEN PERFORM settle_booking(o.booking_id, 'Late reschedule accepted', p_mentor_penalty_pct => 25); END IF;
  PERFORM notify_booking_event(o.booking_id, 'rescheduled');
  RETURN b;
END;
$$;

-- ── 10. Mentee rejects the mentor's proposal - booking cancelled ──────────────
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
  -- Rejecting the mentor's proposal cancels the booking. Late proposal -> full cash refund +
  -- 25% mentor penalty; within-deadline -> wallet credit only (no cash back).
  IF o.was_late THEN
    PERFORM settle_booking(o.booking_id, 'Reschedule rejected (late)', p_cust_refund_pct => 100, p_mentor_penalty_pct => 25);
  ELSE
    PERFORM settle_booking(o.booking_id, 'Reschedule rejected (within)', p_cust_credit_pct => 100);
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
  v_state := booking_deadline_state(b.slot_time, COALESCE((SELECT cancel_notice_hours FROM mentors WHERE id = b.mentor_id), 24));
  IF v_state = 'buffer' THEN RAISE EXCEPTION 'Within 2 hours of the session - cannot reschedule.'; END IF;
  v_approved := EXISTS (SELECT 1 FROM booking_requests
                        WHERE booking_id = p_booking_id AND kind = 'reschedule'
                          AND status IN ('approved','auto_approved'));
  IF v_state <> 'free' AND NOT v_approved THEN
    RAISE EXCEPTION 'A late reschedule needs mentor approval first - send a request.';
  END IF;
  IF NOT is_slot_available(b.mentor_id, b.service_id, p_slot_time) THEN
    RAISE EXCEPTION 'That time is not available - pick another slot.';
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
    RAISE EXCEPTION 'Within 2 hours of the session - cannot reschedule.';
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

-- ── 13. No-show: strike ladder (counting only - no payout penalty) ────────────
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
DECLARE b bookings; v_strikes INT;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF b.no_show_by IS DISTINCT FROM 'mentor' OR b.status <> 'no_show' THEN
    RAISE EXCEPTION 'Not a mentor no-show awaiting resolution';
  END IF;
  IF p_choice = 'rebook_same' THEN
    -- Forgiven: no strike, no penalty, session reinstated.
    UPDATE bookings SET status = 'confirmed', no_show_by = NULL WHERE id = p_booking_id RETURNING * INTO b;
  ELSIF p_choice IN ('rebook_different','refund') THEN
    -- Strike the mentor here (once, at resolution). 25% payout penalty from the 3rd strike on.
    v_strikes := apply_mentor_strike(b.mentor_id);
    PERFORM settle_booking(p_booking_id,
      CASE WHEN p_choice = 'refund' THEN 'Mentor no-show - refund' ELSE 'Mentor no-show - rebook different' END,
      p_cust_refund_pct   => CASE WHEN p_choice = 'refund'          THEN 100 ELSE 0 END,
      p_cust_credit_pct   => CASE WHEN p_choice = 'rebook_different' THEN 100 ELSE 0 END,
      p_mentor_penalty_pct => CASE WHEN v_strikes >= 3 THEN 25 ELSE 0 END);
    -- status stays 'no_show'
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
    -- Customer no-showed and the mentor closes it: mentor is paid in full (credit 100%).
    PERFORM settle_booking(p_booking_id, 'Customer no-show - closed', p_mentor_credit_pct => 100);
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

-- Finalize past sessions from attendance (the video join/leave stamps). Both parties
-- joined -> completed; a party that never joined -> no-show on the missing side. A
-- 15-min grace lets late join events land first. FOR UPDATE SKIP LOCKED + the status
-- guard make it idempotent and safe under overlapping cron ticks / concurrency. Kept
-- the name so the existing 'auto-complete' pg_cron job picks up the new logic.
-- Fault rules are deliberately simple and easy to adjust: mentor-missing strikes the
-- mentor (mentee protected); neither-joined is left no_show_by=NULL for admin review.
CREATE OR REPLACE FUNCTION mark_past_bookings_completed()
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE n INT := 0; r RECORD;
BEGIN
  FOR r IN
    SELECT id, mentor_id, candidate_joined_at, mentor_joined_at
    FROM bookings
    WHERE status IN ('confirmed','rescheduled')
      AND slot_end IS NOT NULL
      AND slot_end < NOW() - INTERVAL '15 minutes'
    FOR UPDATE SKIP LOCKED
  LOOP
    IF r.candidate_joined_at IS NOT NULL AND r.mentor_joined_at IS NOT NULL THEN
      UPDATE bookings SET status = 'completed' WHERE id = r.id;
    ELSIF r.candidate_joined_at IS NOT NULL AND r.mentor_joined_at IS NULL THEN
      UPDATE bookings SET status = 'no_show', no_show_by = 'mentor' WHERE id = r.id;
      -- Strike + 25% penalty are applied at RESOLUTION (resolve_mentor_no_show) so "rebook same"
      -- waives them and the mentor is never struck twice for one no-show.
    ELSIF r.mentor_joined_at IS NOT NULL AND r.candidate_joined_at IS NULL THEN
      UPDATE bookings SET status = 'no_show', no_show_by = 'user' WHERE id = r.id;
    ELSE
      UPDATE bookings SET status = 'no_show', no_show_by = NULL WHERE id = r.id;
    END IF;
    n := n + 1;
  END LOOP;
  RETURN n;
END;
$$;

-- pg_cron schedules - guarded so the migration still succeeds where pg_cron is
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
    RAISE NOTICE 'pg_cron not enabled - skipping booking cron schedules. Enable it and re-run the cron block.';
  END IF;
END $$;


-- ###########################################################################
-- Booking read RPCs (folded in from 018_booking_reads.sql)
-- ###########################################################################
-- =============================================================================
-- 018 - Booking read RPCs for the lifecycle-v2 management UI
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
  mentor_country   TEXT,           -- lets the UI fall back to the mentor's city when app_timezone is a bare UTC
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
  req_respond_by   TIMESTAMPTZ,
  pay_amount       NUMERIC,       -- what the candidate was charged (their currency)
  pay_currency     TEXT,
  pay_state        TEXT           -- created|captured|refunded|... (NULL for free/mock)
) LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT
    b.id, b.status::TEXT, b.slot_time, b.slot_end, b.meeting_url,
    s.title, s.duration,
    m.display_name, m.slug,
    COALESCE(m.app_timezone, 'UTC'),
    m.country,
    COALESCE(b.attendee_timezone, p.timezone, 'UTC'),
    b.reschedule_count, b.no_show_by, booking_deadline_state(b.slot_time, COALESCE(m.cancel_notice_hours, 24)),
    ro.id, ro.proposed_by, ro.status, ro.offer_date, ro.range_start, ro.range_end,
    ro.selected_time, ro.requested_date,
    rq.id, rq.kind, rq.initiated_by, rq.status, rq.respond_by,
    cp.amount, cp.currency, cp.state
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
  LEFT JOIN LATERAL (
    SELECT amount, currency, state FROM customer_payments
    WHERE booking_id = b.id ORDER BY created_at DESC LIMIT 1
  ) cp ON TRUE
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
  req_respond_by      TIMESTAMPTZ,
  payout_amount       NUMERIC,      -- mentor's net earning for this session (mentor currency)
  payout_currency     TEXT,
  payout_state        TEXT          -- pending|paid|void|blocked (NULL for free/mock)
) LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT
    b.id, b.status::TEXT, b.slot_time, b.slot_end, b.meeting_url,
    s.title, s.duration,
    COALESCE(p.display_name, p.full_name, b.candidate_name, b.candidate_email),
    COALESCE(b.candidate_email, p.email),
    COALESCE(m.app_timezone, 'UTC'),
    COALESCE(b.attendee_timezone, p.timezone, 'UTC'),
    b.mentor_confirmed_at, b.reschedule_count, b.no_show_by,
    booking_deadline_state(b.slot_time, COALESCE(m.cancel_notice_hours, 24)),
    ro.id, ro.proposed_by, ro.status, ro.offer_date, ro.range_start, ro.range_end,
    ro.selected_time, ro.requested_date,
    rq.id, rq.kind, rq.initiated_by, rq.status, rq.respond_by,
    COALESCE(mp.net_amount_mentor_currency, mp.amount), mp.mentor_currency, mp.payout_state
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
  LEFT JOIN LATERAL (
    SELECT amount, net_amount_mentor_currency, mentor_currency, payout_state FROM mentor_payouts
    WHERE booking_id = b.id ORDER BY created_at DESC LIMIT 1
  ) mp ON TRUE
  WHERE b.mentor_id = p_mentor_id
    AND b.slot_time IS NOT NULL
    -- Unpaid payment holds ('pending') are hidden from the mentor: a slot isn't the
    -- mentor's session until the candidate has actually paid. It appears once confirmed.
    AND b.status <> 'pending'
  ORDER BY b.slot_time DESC NULLS LAST;
$$;
GRANT EXECUTE ON FUNCTION mentor_sessions(UUID) TO authenticated;


-- ============================================================================
-- STORAGE: mentor profile photos
-- The bucket must exist before the frontend can upload (a missing bucket gives
-- the "Bucket not found" error). Public read so photos render on public
-- profiles; writes are scoped by RLS to each user's own folder ({auth.uid}/...).
-- ============================================================================
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('mentor-photos', 'mentor-photos', true, 5242880,
        ARRAY['image/png','image/jpeg','image/webp'])
ON CONFLICT (id) DO UPDATE
  SET public = true,
      file_size_limit = 5242880,
      allowed_mime_types = ARRAY['image/png','image/jpeg','image/webp'];

DROP POLICY IF EXISTS "mentor-photos public read" ON storage.objects;
CREATE POLICY "mentor-photos public read"
  ON storage.objects FOR SELECT
  USING (bucket_id = 'mentor-photos');

DROP POLICY IF EXISTS "mentor-photos owner insert" ON storage.objects;
CREATE POLICY "mentor-photos owner insert"
  ON storage.objects FOR INSERT TO authenticated
  WITH CHECK (bucket_id = 'mentor-photos' AND (storage.foldername(name))[1] = auth.uid()::text);

DROP POLICY IF EXISTS "mentor-photos owner update" ON storage.objects;
CREATE POLICY "mentor-photos owner update"
  ON storage.objects FOR UPDATE TO authenticated
  USING (bucket_id = 'mentor-photos' AND (storage.foldername(name))[1] = auth.uid()::text)
  WITH CHECK (bucket_id = 'mentor-photos' AND (storage.foldername(name))[1] = auth.uid()::text);

DROP POLICY IF EXISTS "mentor-photos owner delete" ON storage.objects;
CREATE POLICY "mentor-photos owner delete"
  ON storage.objects FOR DELETE TO authenticated
  USING (bucket_id = 'mentor-photos' AND (storage.foldername(name))[1] = auth.uid()::text);


-- ============================================================================
-- Backend-only tables: enable RLS so anon/authenticated clients cannot read or
-- write them directly. The backend uses the service-role key (bypasses RLS), so
-- server access is unaffected. No policies means "deny all clients".
-- ============================================================================
ALTER TABLE booking_events    ENABLE ROW LEVEL SECURITY;
ALTER TABLE booking_reminders ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_settings ENABLE ROW LEVEL SECURITY;


-- ###########################################################################
-- Pricing (PPP + FX) + Razorpay payments  (folded in from payments_setup.sql)
-- ###########################################################################
-- Consolidated so a fresh run of this file also provisions the pricing +
-- payment engine. This is the SAME schema as migrations/payments_setup.sql.
-- Every statement is idempotent. Dependencies (bookings, services, mentors,
-- is_slot_available, is_valid_timezone, booking_question_answers,
-- platform_settings) are all defined earlier in this file. payments_enabled
-- seeds to 'false' - flip it on only when you're ready to charge.

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

-- Per-mentor commission override: the % taken OUT of the mentor's price (revenue-split). When set
-- and not expired it WINS over the country/DEFAULT commission (effective_platform_fee_pct); NULL =
-- use the customer country's commission from country_pricing.
ALTER TABLE mentors ADD COLUMN IF NOT EXISTS commission_pct        NUMERIC;
ALTER TABLE mentors ADD COLUMN IF NOT EXISTS commission_expires_at TIMESTAMPTZ;
ALTER TABLE mentors ADD COLUMN IF NOT EXISTS specializations       TEXT[] DEFAULT '{}';

-- First-login onboarding gate for migrated mentors. New self-service signups set their rate during
-- registration (FALSE). Imported mentors have no rate yet, so they're flagged TRUE and the hub blocks
-- them behind a welcome popup -> review profile -> set rate + confirm sessions, before it unlocks.
-- Cleared by /mentor/complete-onboarding. Server-derived, so the gate survives refresh/new device.
ALTER TABLE mentors ADD COLUMN IF NOT EXISTS needs_onboarding      BOOLEAN NOT NULL DEFAULT FALSE;
-- onboarded_at: durable "finished first-login onboarding" marker, stamped by
-- /mentor/complete-onboarding. The backfill keys on THIS (not hourly_rate - imported mentors can
-- carry a legacy rate, which previously skipped the gate and old mentors never saw the popup).
ALTER TABLE mentors ADD COLUMN IF NOT EXISTS onboarded_at          TIMESTAMPTZ;

-- Gate every imported mentor who has never completed the first-login flow (popup -> review profile
-- -> rate + PPP choice + sessions). Idempotent: a mentor who finished (onboarded_at set) is never
-- re-flagged by a re-run.
UPDATE mentors SET needs_onboarding = TRUE
 WHERE legacy_id IS NOT NULL AND onboarded_at IS NULL AND needs_onboarding = FALSE;

-- ============================================================================
-- Legacy session history (imported read-only from the old portal's /bookings)
-- ============================================================================
-- Old sessions carry only a customer NAME (no email) and a service TITLE (no id), so they can't be
-- real bookings (no account to link to, no live lifecycle). We keep them denormalized here as a
-- read-only track record, shown on the mentor's dashboard + public profile. Loaded by
-- scripts/migrate_mentors.py, idempotent on legacy_booking_id.
CREATE TABLE IF NOT EXISTS legacy_sessions (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mentor_id         UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  legacy_booking_id TEXT UNIQUE,
  status            TEXT,
  service_title     TEXT,
  service_type      TEXT,
  customer_name     TEXT,
  slot_start        TIMESTAMPTZ,
  slot_end          TIMESTAMPTZ,
  duration_min      INTEGER,
  mentor_timezone   TEXT,
  amount_total      NUMERIC,
  amount_currency   TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- RLS on, consistent with every other table: no direct table access. Reads flow through the
-- SECURITY DEFINER mentor_legacy_sessions() RPC; writes through the service-role migration loader.
ALTER TABLE legacy_sessions ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_legacy_sessions_mentor ON legacy_sessions(mentor_id, slot_start DESC);

CREATE OR REPLACE FUNCTION mentor_legacy_sessions(p_mentor_id UUID)
RETURNS TABLE (
  status TEXT, service_title TEXT, service_type TEXT, customer_name TEXT,
  slot_start TIMESTAMPTZ, duration_min INTEGER, amount_total NUMERIC, amount_currency TEXT
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT status, service_title, service_type, customer_name, slot_start, duration_min, amount_total, amount_currency
  FROM legacy_sessions WHERE mentor_id = p_mentor_id ORDER BY slot_start DESC NULLS LAST;
$$;
GRANT EXECUTE ON FUNCTION mentor_legacy_sessions(UUID) TO anon, authenticated;

-- Retired: the old global-markup helper. Superseded by effective_platform_fee_pct (revenue-split).
DROP FUNCTION IF EXISTS effective_markup_pct(UUID);

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

-- Bootstrap FX so prices localize before the FX dispatcher's first run (else convert_prices has no
-- rate and every price falls back to the mentor's own currency). Approximate EUR-based rates (quote
-- units per 1 EUR); DO NOTHING so the dispatcher's live ECB rates are never overwritten by this seed.
INSERT INTO fx_rates (base, quote, rate, as_of, fetched_at) VALUES
  ('EUR','USD',1.08,CURRENT_DATE,NOW()), ('EUR','GBP',0.85,CURRENT_DATE,NOW()),
  ('EUR','INR',90.0,CURRENT_DATE,NOW()), ('EUR','AUD',1.63,CURRENT_DATE,NOW()),
  ('EUR','CAD',1.47,CURRENT_DATE,NOW()), ('EUR','SGD',1.45,CURRENT_DATE,NOW()),
  ('EUR','AED',3.97,CURRENT_DATE,NOW()), ('EUR','JPY',162.0,CURRENT_DATE,NOW()),
  ('EUR','CHF',0.96,CURRENT_DATE,NOW()), ('EUR','CNY',7.8,CURRENT_DATE,NOW()),
  ('EUR','ZAR',19.8,CURRENT_DATE,NOW()), ('EUR','BRL',5.9,CURRENT_DATE,NOW()),
  ('EUR','NZD',1.78,CURRENT_DATE,NOW()), ('EUR','SEK',11.4,CURRENT_DATE,NOW()),
  ('EUR','NOK',11.6,CURRENT_DATE,NOW()), ('EUR','DKK',7.46,CURRENT_DATE,NOW()),
  ('EUR','PLN',4.3,CURRENT_DATE,NOW()), ('EUR','HKD',8.42,CURRENT_DATE,NOW()),
  ('EUR','MXN',19.5,CURRENT_DATE,NOW()), ('EUR','THB',39.5,CURRENT_DATE,NOW()),
  ('EUR','MYR',5.05,CURRENT_DATE,NOW()), ('EUR','IDR',17200.0,CURRENT_DATE,NOW()),
  ('EUR','PHP',61.0,CURRENT_DATE,NOW()), ('EUR','VND',27200.0,CURRENT_DATE,NOW()),
  ('EUR','KRW',1480.0,CURRENT_DATE,NOW()), ('EUR','TRY',35.0,CURRENT_DATE,NOW()),
  ('EUR','SAR',4.05,CURRENT_DATE,NOW()), ('EUR','BDT',118.0,CURRENT_DATE,NOW()),
  ('EUR','PKR',300.0,CURRENT_DATE,NOW()), ('EUR','LKR',325.0,CURRENT_DATE,NOW()),
  ('EUR','NPR',144.0,CURRENT_DATE,NOW()), ('EUR','NGN',1700.0,CURRENT_DATE,NOW()),
  ('EUR','KES',140.0,CURRENT_DATE,NOW()), ('EUR','EGP',53.0,CURRENT_DATE,NOW()),
  ('EUR','TWD',35.0,CURRENT_DATE,NOW()), ('EUR','ILS',4.0,CURRENT_DATE,NOW()),
  ('EUR','RON',4.97,CURRENT_DATE,NOW()), ('EUR','CZK',25.2,CURRENT_DATE,NOW()),
  ('EUR','HUF',395.0,CURRENT_DATE,NOW())
ON CONFLICT (base, quote) DO NOTHING;

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

-- ── Per-country platform fee + tax ───────────────────────────────────────────
-- MARKUP model: the customer pays  session price + platform fee + tax.
--   * platform_fee_pct = the CUSTOMER-FACING platform fee, ADDED ON TOP of the mentor's session
--     price and shown to the customer as its own line item.
--   * tax_pct is charged on the (session price + platform fee) base.
-- Both %s are keyed to the CUSTOMER's country and admin-editable. This is entirely separate from the
-- INTERNAL mentor commission (mentor_commission_pct / mentors.commission_pct), which is taken OUT of
-- the mentor's price to size their payout and is never shown to the customer. Default platform fee
-- 15%, tax 0 except where set (India GST 18%). 'DEFAULT' is the fallback row.
CREATE TABLE IF NOT EXISTS country_pricing (
  country_code     TEXT PRIMARY KEY,            -- ISO-2 (uppercase), or 'DEFAULT'
  platform_fee_pct NUMERIC NOT NULL DEFAULT 15, -- customer-facing platform fee %, ADDED ON TOP
  tax_pct          NUMERIC NOT NULL DEFAULT 0,
  tax_label        TEXT,                         -- e.g. 'GST', 'VAT'
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE country_pricing ENABLE ROW LEVEL SECURITY;
INSERT INTO country_pricing (country_code, platform_fee_pct, tax_pct, tax_label) VALUES
  ('DEFAULT', 15, 0,  NULL),
  ('IN',      15, 18, 'GST')
ON CONFLICT (country_code) DO NOTHING;

-- The CUSTOMER-FACING platform fee % for a customer country (added on top of the session price).
CREATE OR REPLACE FUNCTION country_platform_fee_pct(p_cc TEXT)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT platform_fee_pct FROM country_pricing WHERE country_code = UPPER(COALESCE(p_cc, ''))),
    (SELECT platform_fee_pct FROM country_pricing WHERE country_code = 'DEFAULT'),
    15);
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

-- The INTERNAL mentor commission % for a booking (taken OUT of the mentor's session price to size the
-- payout; never shown to the customer): a live per-mentor override wins (special deals), else the
-- global 'mentor_commission_pct' setting, else 30.
CREATE OR REPLACE FUNCTION mentor_commission_pct(p_mentor_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT commission_pct FROM mentors
       WHERE id = p_mentor_id AND commission_pct IS NOT NULL
         AND (commission_expires_at IS NULL OR commission_expires_at > NOW())),
    (SELECT NULLIF(value, '')::numeric FROM platform_settings WHERE key = 'mentor_commission_pct'),
    30);
$$;
GRANT EXECUTE ON FUNCTION mentor_commission_pct(UUID) TO anon, authenticated;

-- Back-compat shim: older callers of effective_platform_fee_pct(mentor, country) now get the
-- customer-facing platform fee (the mentor commission moved to mentor_commission_pct()).
CREATE OR REPLACE FUNCTION effective_platform_fee_pct(p_mentor_id UUID, p_customer_country TEXT)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT country_platform_fee_pct(p_customer_country);
$$;
GRANT EXECUTE ON FUNCTION effective_platform_fee_pct(UUID, TEXT) TO anon, authenticated;

-- The single pricing engine. Returns the canonical BookingPrice as jsonb.
-- Raises FX_UNAVAILABLE if rates are missing/stale.
-- MARKUP model (v5): the customer pays  session price + platform fee + tax.
--   * session price  = the mentor's localised rate (subtotal / mentor_amount) - customer-facing.
--   * platform fee   = country_platform_fee_pct of the session price, ADDED ON TOP - customer-facing
--                      (platform_fee / platform_fee_pct).
--   * tax            = country_tax_pct on (session price + platform fee) - customer-facing.
--   * gross_customer = session price + platform fee + tax  (the amount actually charged).
-- Separately and INTERNALLY, the mentor commission (mentor_commission_pct) is taken OUT of the
-- session price to size the mentor payout: fee_pct / fee_amount / net_customer / net_mentor. Those
-- commission fields are admin-only and never returned to the customer's browser (see
-- _PUBLIC_QUOTE_FIELDS); platform_fee / platform_fee_pct ARE customer-facing.
CREATE OR REPLACE FUNCTION compute_booking_price(p_service_id UUID, p_customer_country TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_pricing_version CONSTANT INT := 5;   -- v5: customer platform fee added ON TOP + internal mentor commission
  v_ppp_version     CONSTANT INT := 1;
  v_provider        CONSTANT TEXT := 'frankfurter';
  v_mentor_id UUID; v_set NUMERIC; v_set_offer NUMERIC; v_ment_ccy TEXT; v_is_ppp BOOLEAN;
  v_prices JSONB; v_pfee_pct NUMERIC; v_tax_pct NUMERIC; v_comm_pct NUMERIC; v_mentor_country TEXT;
  v_cust_ccy TEXT; v_ppp NUMERIC := 1; v_source TEXT; v_explicit NUMERIC; v_base NUMERIC; v_mentor_amt NUMERIC;
  v_fx_mc NUMERIC; v_fx_c_inr NUMERIC; v_fx_m_inr NUMERIC;
  v_gross NUMERIC; v_platform_fee NUMERIC; v_commission NUMERIC; v_subtotal NUMERIC;
  v_tax_amt NUMERIC; v_net_cust NUMERIC; v_net_mentor NUMERIC;
BEGIN
  SELECT s.mentor_id, s.set_price, s.set_offer_price, COALESCE(s.set_currency, 'USD'), s.is_ppp,
         COALESCE(s.currency_prices, '[]'::jsonb), m.country
    INTO v_mentor_id, v_set, v_set_offer, v_ment_ccy, v_is_ppp, v_prices, v_mentor_country
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
    v_ppp        := CASE WHEN v_is_ppp THEN ppp_relative(p_customer_country, v_mentor_country) ELSE 1 END;
    v_fx_mc      := get_fx(v_ment_ccy, v_cust_ccy);   -- hard-fails FX_UNAVAILABLE (the charge needs it)
    v_base       := COALESCE(v_set_offer, v_set);
    v_mentor_amt := ROUND(v_base * v_ppp * v_fx_mc, 2);
    v_net_mentor := v_base;                            -- mentor's full rate in mentor ccy; commission applied below
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

-- One settlement per terminal outcome. Posts the customer's NET cash refund plus any kept
-- charge / wallet credit, and the mentor's penalty / credit, all as booking_ledger rows.
-- Amounts derive from the ACTUALLY-charged customer payment and the mentor payout basis, so a
-- free/mock session (no captured payment) posts nothing. Only 'refund' rows drive real cash
-- (refund_owed_minor -> process_refunds); 'charge' / 'credit' / 'penalty' are records for the
-- payouts + wallet phases. Percentages come from the booking-workflow spec.
CREATE OR REPLACE FUNCTION settle_booking(
  p_booking UUID, p_reason TEXT,
  p_cust_refund_pct INT DEFAULT 0, p_cust_charge_pct INT DEFAULT 0, p_cust_credit_pct INT DEFAULT 0,
  p_mentor_penalty_pct INT DEFAULT 0, p_mentor_credit_pct INT DEFAULT 0
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_cust NUMERIC; v_ment NUMERIC;
BEGIN
  SELECT amount INTO v_cust FROM customer_payments
    WHERE booking_id = p_booking AND state IN ('captured','partially_refunded','refunded')
    ORDER BY created_at DESC LIMIT 1;
  SELECT amount INTO v_ment FROM mentor_payouts WHERE booking_id = p_booking LIMIT 1;
  v_cust := COALESCE(v_cust, 0); v_ment := COALESCE(v_ment, 0);
  IF v_cust > 0 THEN
    IF p_cust_refund_pct > 0 THEN PERFORM add_ledger(p_booking,'customer','refund', ROUND(v_cust*p_cust_refund_pct/100.0,2), p_cust_refund_pct, p_reason); END IF;
    IF p_cust_charge_pct > 0 THEN PERFORM add_ledger(p_booking,'customer','charge', ROUND(v_cust*p_cust_charge_pct/100.0,2), p_cust_charge_pct, p_reason); END IF;
    IF p_cust_credit_pct > 0 THEN PERFORM add_ledger(p_booking,'customer','credit', ROUND(v_cust*p_cust_credit_pct/100.0,2), p_cust_credit_pct, p_reason); END IF;
  END IF;
  IF v_ment > 0 THEN
    IF p_mentor_penalty_pct > 0 THEN PERFORM add_ledger(p_booking,'mentor','penalty', ROUND(v_ment*p_mentor_penalty_pct/100.0,2), p_mentor_penalty_pct, p_reason); END IF;
    IF p_mentor_credit_pct > 0 THEN PERFORM add_ledger(p_booking,'mentor','credit', ROUND(v_ment*p_mentor_credit_pct/100.0,2), p_mentor_credit_pct, p_reason); END IF;
  END IF;
END;
$$;
REVOKE ALL ON FUNCTION settle_booking(UUID, TEXT, INT, INT, INT, INT, INT) FROM PUBLIC, anon, authenticated;

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
DROP FUNCTION IF EXISTS reserve_booking(UUID, UUID, UUID, TIMESTAMPTZ, TEXT, TEXT, TEXT, JSONB, UUID, UUID);
CREATE OR REPLACE FUNCTION reserve_booking(
  p_quote_id UUID, p_mentor_id UUID, p_service_id UUID, p_slot_time TIMESTAMPTZ,
  p_email TEXT, p_name TEXT DEFAULT NULL, p_timezone TEXT DEFAULT 'UTC',
  p_answers JSONB DEFAULT '[]', p_specific_availability_id UUID DEFAULT NULL,
  p_candidate_id UUID DEFAULT NULL, p_referral_code TEXT DEFAULT NULL
) RETURNS TABLE(booking_id UUID, amount NUMERIC, currency TEXT, hold_expires_at TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  q pricing_quotes%ROWTYPE;
  s JSONB;
  v_booking_id UUID;
  v_hold_expires_at TIMESTAMPTZ := NOW() + INTERVAL '10 minutes';
  v_gross NUMERIC; v_fee_amount NUMERIC; v_net_customer NUMERIC; v_net_mentor NUMERIC;
  v_discount NUMERIC := 0; v_factor NUMERIC := 1; v_val JSONB;
  v_code_id UUID; v_aff_id UUID; v_code_norm TEXT; v_amount NUMERIC;
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
  v_net_mentor := (s->>'net_mentor')::numeric;
  v_amount := ROUND((s->>'set_price')::numeric * (s->>'ppp_multiplier')::numeric, 2);

  -- Referral code (optional): validated server-side; its discount scales what the customer pays.
  -- The commission SPLIT for a referred first session is applied later at completion.
  IF p_referral_code IS NOT NULL AND LENGTH(TRIM(p_referral_code)) > 0 THEN
    v_val := validate_referral_code(p_referral_code);
    IF (v_val->>'valid')::boolean THEN
      v_discount  := (v_val->>'discount_pct')::numeric;
      v_code_id   := (v_val->>'code_id')::uuid;
      v_aff_id    := (v_val->>'affiliate_id')::uuid;
      v_code_norm := v_val->>'code';
    END IF;
  END IF;
  v_factor := 1 - COALESCE(v_discount, 0) / 100.0;
  v_gross        := ROUND(v_gross * v_factor, 2);
  v_fee_amount   := ROUND(v_fee_amount * v_factor, 2);
  v_net_customer := ROUND(v_net_customer * v_factor, 2);
  v_amount       := ROUND(v_amount * v_factor, 2);
  v_net_mentor   := CASE WHEN v_net_mentor IS NOT NULL THEN ROUND(v_net_mentor * v_factor, 2) ELSE NULL END;

  BEGIN
    INSERT INTO bookings(
      mentor_id, candidate_id, candidate_email, candidate_name,
      service_id, slot_time, status, attendee_timezone,
      specific_availability_id, source,
      customer_currency, fx_customer_inr, fx_mentor_inr, payment_hold_expires_at,
      referral_code, referral_code_id, referral_affiliate_id, referral_discount_applied_pct
    ) VALUES (
      p_mentor_id, p_candidate_id, LOWER(p_email), p_name,
      p_service_id, p_slot_time, 'pending', v_timezone,
      p_specific_availability_id, 'direct',
      s->>'customer_currency', NULLIF((s->>'fx_customer_inr')::numeric, 0), NULLIF((s->>'fx_mentor_inr')::numeric, 0),
      v_hold_expires_at,
      v_code_norm, v_code_id, v_aff_id, NULLIF(v_discount, 0)
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
      v_amount,
      v_gross, (s->>'fee_pct')::numeric, v_fee_amount,
      v_net_customer, v_net_mentor, (s->>'fx_mentor_customer')::numeric,
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
      v_gross, (s->>'fee_pct')::numeric, v_fee_amount, v_net_customer, v_net_mentor
    );

  UPDATE pricing_quotes SET used = TRUE, booking_id = v_booking_id WHERE id = p_quote_id;  -- one-time use
  IF v_code_id IS NOT NULL THEN
    UPDATE referral_codes SET redemption_count = redemption_count + 1 WHERE id = v_code_id;
  END IF;

  RETURN QUERY SELECT v_booking_id, v_gross, UPPER(s->>'customer_currency'), v_hold_expires_at;
END;
$$;
GRANT EXECUTE ON FUNCTION reserve_booking(UUID, UUID, UUID, TIMESTAMPTZ, TEXT, TEXT, TEXT, JSONB, UUID, UUID, TEXT) TO anon, authenticated;

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

-- Dispatcher schedule (24h/1h reminders + T-60 mentor nudge + FX/refund/verify money jobs) is
-- scheduled MANUALLY, not here: Supabase forbids ALTER DATABASE SET, so the URL + token must be
-- pasted inline. This is intentionally NOT auto-run so a re-run never clobbers a working job.
-- One-time setup:
--   1. Enable pg_cron + pg_net (Dashboard -> Database -> Extensions).
--   2. Set DISPATCHER_TOKEN on the backend (Render env var).
--   3. Run once (swap in your backend URL + the same token):
--        do $$ begin
--          if exists (select 1 from cron.job where jobname='run-dispatcher') then
--            perform cron.unschedule('run-dispatcher'); end if;
--          perform cron.schedule('run-dispatcher','*/5 * * * *', $cron$
--            select net.http_post(
--              url     := 'https://<your-backend-host>/payments/run-dispatcher',
--              headers := jsonb_build_object('Content-Type','application/json',
--                           'X-Dispatcher-Token','<your DISPATCHER_TOKEN>'),
--              body    := '{}'::jsonb);
--          $cron$); end $$;


-- ===========================================================================
-- Referral + Reviews systems (ported from testing_db_setup.sql - keep in sync)
-- ===========================================================================
-- ###########################################################################
-- Referral / affiliate commission system (SCHEMA)
-- ---------------------------------------------------------------------------
-- Adapted from the Gautham fork + the referral logic doc. Revenue-split model:
-- on a referred customer's FIRST completed session the price splits mentor /
-- Immigroov / promoter (see process_referral_commissions, next section). All of
-- this is ADDITIVE (new tables, ADD COLUMN IF NOT EXISTS, widened CHECKs).
--
-- DIVERGENCE from the source: referral_codes has NO UNIQUE(affiliate_id) - an
-- affiliate (e.g. a mentor) may hold MANY codes over time (generate new ones,
-- keep expired ones in their history). cap + expiry are optional (NULL = none).
-- ###########################################################################

-- ── 1. Affiliate identity ───────────────────────────────────────────────────
-- profile_id nullable + email fallback so an admin can onboard an affiliate
-- before they sign up (same pattern as mentors.profile_id + link-on-first-login).
CREATE TABLE IF NOT EXISTS affiliates (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id         UUID UNIQUE REFERENCES profiles(id) ON DELETE SET NULL,
  mentor_id          UUID REFERENCES mentors(id) ON DELETE SET NULL,
  type               TEXT NOT NULL CHECK (type IN ('mentor', 'non_mentor')),
  email              TEXT,                          -- contact for a not-yet-signed-up affiliate
  display_name       TEXT,
  payout_details     JSONB,
  audience_corridor  TEXT,
  status             TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'frozen')),
  agreed_terms_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT affiliates_mentor_type_chk CHECK (type <> 'mentor' OR mentor_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_affiliates_email_lower ON affiliates(LOWER(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_affiliates_mentor ON affiliates(mentor_id) WHERE mentor_id IS NOT NULL;
ALTER TABLE affiliates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS affiliates_self_read ON affiliates;
CREATE POLICY affiliates_self_read ON affiliates FOR SELECT USING (profile_id = auth.uid());

-- Slug-based /r/<slug> link (link-attribution phase). One per affiliate.
CREATE TABLE IF NOT EXISTS affiliate_links (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  affiliate_id      UUID NOT NULL UNIQUE REFERENCES affiliates(id) ON DELETE CASCADE,
  slug              TEXT NOT NULL UNIQUE,
  is_house_channel  BOOLEAN NOT NULL DEFAULT FALSE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE affiliate_links ENABLE ROW LEVEL SECURITY;

-- Discount/attribution codes. MANY per affiliate (history + regenerate).
CREATE TABLE IF NOT EXISTS referral_codes (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  affiliate_id      UUID NOT NULL REFERENCES affiliates(id) ON DELETE CASCADE,
  code_string       TEXT NOT NULL UNIQUE,
  discount_pct      NUMERIC NOT NULL DEFAULT 0 CHECK (discount_pct >= 0 AND discount_pct <= 100),
  redemption_cap    INT,                            -- NULL = unlimited
  redemption_count  INT NOT NULL DEFAULT 0,
  expires_at        TIMESTAMPTZ,                    -- NULL = no expiry
  is_active         BOOLEAN NOT NULL DEFAULT TRUE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_referral_codes_affiliate ON referral_codes(affiliate_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_codes_string_lower ON referral_codes(LOWER(code_string));
ALTER TABLE referral_codes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS referral_codes_self_read ON referral_codes;
CREATE POLICY referral_codes_self_read ON referral_codes FOR SELECT USING (
  affiliate_id IN (SELECT id FROM affiliates WHERE profile_id = auth.uid())
);

-- ── 2. Attribution pipeline ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS referral_click_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  affiliate_id  UUID NOT NULL REFERENCES affiliates(id) ON DELETE CASCADE,
  session_token TEXT NOT NULL,
  clicked_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_referral_click_events_session ON referral_click_events(session_token, clicked_at DESC);
ALTER TABLE referral_click_events ENABLE ROW LEVEL SECURITY;

-- Durable per-customer-email attribution (60-day window; code wins over link).
CREATE TABLE IF NOT EXISTS attribution_records (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email_hash       TEXT NOT NULL UNIQUE,
  affiliate_id     UUID REFERENCES affiliates(id) ON DELETE SET NULL,
  referral_code_id UUID REFERENCES referral_codes(id) ON DELETE SET NULL,
  source_type      TEXT NOT NULL CHECK (source_type IN ('link', 'code')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at       TIMESTAMPTZ NOT NULL,
  frozen           BOOLEAN NOT NULL DEFAULT FALSE,   -- paused while a no-show rebooking decision is pending
  frozen_at        TIMESTAMPTZ,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE attribution_records ENABLE ROW LEVEL SECURITY;   -- internal (RPC-only), no select policy

-- ── 3. Commission ledger + payout batching ──────────────────────────────────
-- One row per referred first-session, written at completion. Enriched with
-- gross/discount/currency so admin + payout views need no extra joins.
-- commission_amount = the promoter's cut in the customer currency;
-- commission_amount_inr = the same normalised to INR (the payout basis).
CREATE TABLE IF NOT EXISTS commission_ledger (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id             UUID NOT NULL UNIQUE REFERENCES bookings(id) ON DELETE CASCADE,
  session_completed_at   TIMESTAMPTZ NOT NULL,
  mentor_id              UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  affiliate_id           UUID NOT NULL REFERENCES affiliates(id) ON DELETE CASCADE,
  referral_code          TEXT,
  split_snapshot         JSONB NOT NULL,            -- {mentor_pct, immigroov_pct, promoter_pct}
  gross_customer         NUMERIC(12,2),             -- what the customer paid (post-discount, pre-tax)
  customer_currency      TEXT,
  discount_pct           NUMERIC DEFAULT 0,
  commission_amount      NUMERIC(12,2),             -- promoter cut, customer currency
  commission_amount_inr  NUMERIC(12,2) NOT NULL,    -- promoter cut, normalised to INR (payout basis)
  status                 TEXT NOT NULL DEFAULT 'pending_review'
                           CHECK (status IN ('pending_review', 'approved', 'paid', 'rejected', 'void')),
  payout_batch_id        UUID,                      -- FK added below, after payout_batches exists
  notified_at            TIMESTAMPTZ,               -- claim marker for the "commission approved" email
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_commission_ledger_affiliate ON commission_ledger(affiliate_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_commission_ledger_status ON commission_ledger(status);
ALTER TABLE commission_ledger ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS commission_ledger_self_read ON commission_ledger;
CREATE POLICY commission_ledger_self_read ON commission_ledger FOR SELECT USING (
  affiliate_id IN (SELECT id FROM affiliates WHERE profile_id = auth.uid())
);

CREATE TABLE IF NOT EXISTS payout_batches (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_date  DATE NOT NULL UNIQUE,
  status      TEXT NOT NULL DEFAULT 'preview' CHECK (status IN ('preview', 'finalized', 'paid')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE payout_batches ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  ALTER TABLE commission_ledger
    ADD CONSTRAINT commission_ledger_payout_batch_fkey
    FOREIGN KEY (payout_batch_id) REFERENCES payout_batches(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── 4. Fraud review + admin audit trail ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS fraud_flags (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  affiliate_id                UUID NOT NULL REFERENCES affiliates(id) ON DELETE CASCADE,
  booking_id                  UUID REFERENCES bookings(id) ON DELETE SET NULL,
  commission_ledger_id        UUID REFERENCES commission_ledger(id) ON DELETE SET NULL,
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
ALTER TABLE fraud_flags ENABLE ROW LEVEL SECURITY;   -- admin RPCs only, no select policy

CREATE TABLE IF NOT EXISTS referral_admin_actions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  admin_id    UUID REFERENCES profiles(id),
  action      TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id   UUID NOT NULL,
  note        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE referral_admin_actions ENABLE ROW LEVEL SECURITY;

-- ── 5. Booking + ledger schema additions ────────────────────────────────────
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS referral_session_token        TEXT;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS referral_code                 TEXT;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS referral_code_id              UUID REFERENCES referral_codes(id) ON DELETE SET NULL;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS referral_affiliate_id         UUID REFERENCES affiliates(id) ON DELETE SET NULL;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS referral_discount_applied_pct NUMERIC;

-- booking_ledger gains a 'promoter' party + a 'commission' kind for affiliate entries.
ALTER TABLE booking_ledger DROP CONSTRAINT IF EXISTS booking_ledger_party_check;
ALTER TABLE booking_ledger ADD CONSTRAINT booking_ledger_party_check
  CHECK (party IN ('customer', 'mentor', 'platform', 'promoter'));
ALTER TABLE booking_ledger DROP CONSTRAINT IF EXISTS booking_ledger_kind_check;
ALTER TABLE booking_ledger ADD CONSTRAINT booking_ledger_kind_check
  CHECK (kind IN ('penalty', 'refund', 'credit', 'charge', 'commission'));

-- ── 6. Platform settings (all tunable) ──────────────────────────────────────
INSERT INTO platform_settings (key, value, description) VALUES
  ('referral_tier_starter_max', '4', 'Non-mentor affiliate: max referrals/month to stay Starter tier'),
  ('referral_tier_growth_max', '14', 'Non-mentor affiliate: max referrals/month to stay Growth tier (above = Partner)'),
  ('referral_volume_spike_autoapprove_multiplier', '3', 'Auto-approve today''s commission count up to this multiple of the 30-day daily average'),
  ('referral_volume_spike_escalate_multiplier', '5', 'Escalate for manual review above this multiple of the 30-day daily average'),
  ('referral_code_redemption_speed_minutes', '30', 'Minutes; a code used faster than this is a potential speed-fraud signal'),
  ('referral_manual_review_escalation_days', '5', 'Working days a flagged case can sit before auto-escalating to the co-founder'),
  ('referral_payout_min_working_days', '5', 'Minimum working days after session completion before a commission is payout-eligible'),
  ('referral_attribution_days', '60', 'Days a referral attribution stays valid after the click/code'),
  ('referral_max_discount_pct', '50', 'Maximum discount % a mentor may set on a referral code (margin-floor guard)'),
  ('referral_default_code_cap', '100', 'Redemption cap applied when a code is generated without an explicit one (codes are never unlimited)'),
  ('referral_default_code_expiry_days', '90', 'Codes expire this many days after creation when no expiry is given (anti-leakage)')
ON CONFLICT (key) DO NOTHING;

-- ###########################################################################
-- Referral system (FUNCTIONS - phase 1: codes + checkout validation)
-- The completion-time commission split (process_referral_commissions) and the
-- reserve-time discount wiring are added in the next section.
-- ###########################################################################

-- Ensure a mentor has an affiliate row (type='mentor'); returns its id.
CREATE OR REPLACE FUNCTION ensure_mentor_affiliate(p_mentor_id UUID)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_aff UUID;
BEGIN
  SELECT id INTO v_aff FROM affiliates WHERE mentor_id = p_mentor_id;
  IF v_aff IS NULL THEN
    INSERT INTO affiliates (profile_id, mentor_id, type, email, display_name)
    SELECT m.profile_id, m.id, 'mentor', m.email, m.display_name FROM mentors m WHERE m.id = p_mentor_id
    RETURNING id INTO v_aff;
  END IF;
  RETURN v_aff;
END; $$;
GRANT EXECUTE ON FUNCTION ensure_mentor_affiliate(UUID) TO authenticated;

-- Mentor generates a referral code (auto-created affiliate row). Returns the code_string.
-- Codes are ALWAYS system-generated (no user-supplied strings), discount is capped by
-- referral_max_discount_pct, and every code gets a FINITE usage cap + an expiry (anti-leakage):
-- a NULL cap/expiry falls back to referral_default_code_cap / referral_default_code_expiry_days.
DROP FUNCTION IF EXISTS generate_referral_code(UUID, NUMERIC, INT, TIMESTAMPTZ, TEXT);
CREATE OR REPLACE FUNCTION generate_referral_code(
  p_mentor_id UUID,
  p_discount_pct NUMERIC DEFAULT 0,
  p_redemption_cap INT DEFAULT NULL,
  p_expires_at TIMESTAMPTZ DEFAULT NULL
) RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_aff UUID; v_code TEXT; v_try INT := 0; v_max NUMERIC; v_cap INT; v_days INT;
BEGIN
  v_max := COALESCE((SELECT value::numeric FROM platform_settings WHERE key = 'referral_max_discount_pct'), 50);
  IF COALESCE(p_discount_pct, 0) < 0 OR COALESCE(p_discount_pct, 0) > v_max THEN
    RAISE EXCEPTION 'Discount must be between 0 and %', v_max USING errcode = 'P0001';
  END IF;
  v_aff := ensure_mentor_affiliate(p_mentor_id);
  IF v_aff IS NULL THEN RAISE EXCEPTION 'Mentor not found' USING errcode = 'P0001'; END IF;

  -- Usage cap is always finite (never unlimited); default from settings when not provided.
  v_cap := COALESCE(p_redemption_cap, (SELECT value::int FROM platform_settings WHERE key = 'referral_default_code_cap'), 100);
  IF v_cap < 1 THEN v_cap := 1; END IF;
  -- Codes always expire; default from settings when no expiry is given.
  v_days := COALESCE((SELECT value::int FROM platform_settings WHERE key = 'referral_default_code_expiry_days'), 90);
  IF p_expires_at IS NULL THEN p_expires_at := NOW() + MAKE_INTERVAL(days => v_days); END IF;

  -- Always system-generate a unique code.
  LOOP
    v_code := UPPER(SUBSTRING(MD5(random()::text || clock_timestamp()::text) FROM 1 FOR 8));
    EXIT WHEN NOT EXISTS (SELECT 1 FROM referral_codes WHERE LOWER(code_string) = LOWER(v_code));
    v_try := v_try + 1;
    IF v_try > 25 THEN RAISE EXCEPTION 'Could not generate a unique code, please retry'; END IF;
  END LOOP;

  INSERT INTO referral_codes (affiliate_id, code_string, discount_pct, redemption_cap, expires_at)
    VALUES (v_aff, v_code, COALESCE(p_discount_pct, 0), v_cap, p_expires_at);
  RETURN v_code;
END; $$;
GRANT EXECUTE ON FUNCTION generate_referral_code(UUID, NUMERIC, INT, TIMESTAMPTZ) TO authenticated;

-- Validate a code at checkout (read-only). Returns {valid, reason, discount_pct, code_id,
-- affiliate_id, code}. The discount % the customer sees comes from HERE (backend-checked), never
-- from the client. A booking still re-checks + records the code at reserve time.
CREATE OR REPLACE FUNCTION validate_referral_code(p_code TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE c referral_codes; v_aff affiliates;
BEGIN
  IF p_code IS NULL OR LENGTH(TRIM(p_code)) = 0 THEN
    RETURN jsonb_build_object('valid', false, 'reason', 'empty', 'discount_pct', 0);
  END IF;
  SELECT * INTO c FROM referral_codes WHERE LOWER(code_string) = LOWER(TRIM(p_code));
  IF NOT FOUND THEN
    RETURN jsonb_build_object('valid', false, 'reason', 'not_found', 'discount_pct', 0);
  END IF;
  IF NOT c.is_active THEN
    RETURN jsonb_build_object('valid', false, 'reason', 'inactive', 'discount_pct', 0);
  END IF;
  IF c.expires_at IS NOT NULL AND c.expires_at < NOW() THEN
    RETURN jsonb_build_object('valid', false, 'reason', 'expired', 'discount_pct', 0);
  END IF;
  IF c.redemption_cap IS NOT NULL AND c.redemption_count >= c.redemption_cap THEN
    RETURN jsonb_build_object('valid', false, 'reason', 'cap_reached', 'discount_pct', 0);
  END IF;
  SELECT * INTO v_aff FROM affiliates WHERE id = c.affiliate_id;
  IF v_aff.status <> 'active' THEN
    RETURN jsonb_build_object('valid', false, 'reason', 'affiliate_inactive', 'discount_pct', 0);
  END IF;
  RETURN jsonb_build_object(
    'valid', true, 'reason', 'ok', 'discount_pct', c.discount_pct,
    'code_id', c.id, 'affiliate_id', c.affiliate_id, 'code', c.code_string);
END; $$;
GRANT EXECUTE ON FUNCTION validate_referral_code(TEXT) TO anon, authenticated;

-- Non-mentor affiliate tier by this-month approved/paid referral count. Mentors split flat
-- (self 90/10/0, peer 70/20/10), so this is only consulted for type='non_mentor'.
CREATE OR REPLACE FUNCTION current_affiliate_tier(p_affiliate_id UUID)
RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE v_count INT; v_starter INT; v_growth INT;
BEGIN
  v_starter := COALESCE((SELECT value::int FROM platform_settings WHERE key = 'referral_tier_starter_max'), 4);
  v_growth  := COALESCE((SELECT value::int FROM platform_settings WHERE key = 'referral_tier_growth_max'), 14);
  SELECT COUNT(*) INTO v_count FROM commission_ledger
    WHERE affiliate_id = p_affiliate_id
      AND status IN ('approved', 'paid')
      AND session_completed_at >= date_trunc('month', NOW());
  IF v_count > v_growth THEN RETURN 'partner';
  ELSIF v_count > v_starter THEN RETURN 'growth';
  ELSE RETURN 'starter'; END IF;
END; $$;
GRANT EXECUTE ON FUNCTION current_affiliate_tier(UUID) TO authenticated;

-- ###########################################################################
-- Referral system (FUNCTIONS - phase 2: completion split + read views)
-- ###########################################################################

-- Completion-time commission. For each newly-completed referred booking that is the customer's
-- FIRST completed session, write the commission_ledger row + the promoter's booking_ledger entry,
-- and adjust the mentor payout to the referred split (doc: self 90/10/0, mentor-to-mentor 70/20/10,
-- influencer 70 + tiered). Idempotent: skips bookings that already have a commission_ledger row.
CREATE OR REPLACE FUNCTION process_referral_commissions()
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  r RECORD; v_aff affiliates;
  v_price NUMERIC; v_ccy TEXT; v_fx_c_inr NUMERIC; v_fx_mc NUMERIC;
  v_mentor_pct INT; v_immigroov_pct INT; v_promoter_pct INT; v_tier TEXT;
  v_prev BOOLEAN; v_comm NUMERIC; v_comm_inr NUMERIC;
BEGIN
  FOR r IN
    SELECT b.*, bp.net_customer AS bp_net, bp.fee_amount AS bp_fee, bp.customer_currency AS bp_ccy,
           bp.fx_mentor_customer AS bp_fxmc
    FROM bookings b
    JOIN booking_pricing bp ON bp.booking_id = b.id
    WHERE b.status = 'completed'
      AND b.referral_affiliate_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM commission_ledger cl WHERE cl.booking_id = b.id)
  LOOP
    IF r.candidate_email IS NULL THEN CONTINUE; END IF;
    SELECT * INTO v_aff FROM affiliates WHERE id = r.referral_affiliate_id;
    IF NOT FOUND OR v_aff.status <> 'active' THEN CONTINUE; END IF;

    -- Referral commission applies to the customer's lifetime FIRST completed session only.
    SELECT EXISTS(
      SELECT 1 FROM bookings b2
      WHERE b2.id <> r.id AND b2.status = 'completed'
        AND LOWER(b2.candidate_email) = LOWER(r.candidate_email)
        AND b2.slot_time < r.slot_time
    ) INTO v_prev;
    IF v_prev THEN CONTINUE; END IF;

    v_price    := ROUND(COALESCE(r.bp_net, 0) + COALESCE(r.bp_fee, 0), 2);  -- pre-tax, discounted price (customer ccy)
    v_ccy      := COALESCE(r.bp_ccy, r.customer_currency);
    v_fx_c_inr := COALESCE(r.fx_customer_inr, 1);
    v_fx_mc    := NULLIF(r.bp_fxmc, 0);

    IF v_aff.mentor_id = r.mentor_id THEN
      v_mentor_pct := 90; v_immigroov_pct := 10; v_promoter_pct := 0;    -- mentor referred their own client
    ELSIF v_aff.type = 'mentor' THEN
      v_mentor_pct := 70; v_immigroov_pct := 20; v_promoter_pct := 10;   -- mentor-to-mentor (Track A)
    ELSE
      v_tier := current_affiliate_tier(v_aff.id);                        -- influencer (Track B)
      v_mentor_pct := 70;
      CASE v_tier
        WHEN 'growth'  THEN v_immigroov_pct := 19; v_promoter_pct := 11;
        WHEN 'partner' THEN v_immigroov_pct := 15; v_promoter_pct := 15;
        ELSE                v_immigroov_pct := 22; v_promoter_pct := 8;   -- starter
      END CASE;
    END IF;

    v_comm     := ROUND(v_price * v_promoter_pct / 100.0, 2);            -- promoter cut, customer ccy
    v_comm_inr := ROUND(v_price * v_fx_c_inr * v_promoter_pct / 100.0, 2);

    INSERT INTO commission_ledger (
      booking_id, session_completed_at, mentor_id, affiliate_id, referral_code, split_snapshot,
      gross_customer, customer_currency, discount_pct, commission_amount, commission_amount_inr, status
    ) VALUES (
      r.id, COALESCE(r.slot_end, NOW()), r.mentor_id, v_aff.id, r.referral_code,
      jsonb_build_object('mentor_pct', v_mentor_pct, 'immigroov_pct', v_immigroov_pct, 'promoter_pct', v_promoter_pct),
      v_price, v_ccy, COALESCE(r.referral_discount_applied_pct, 0), v_comm, v_comm_inr, 'pending_review'
    );

    IF v_promoter_pct > 0 THEN
      PERFORM add_ledger(r.id, 'promoter', 'commission', v_comm, v_promoter_pct,
                         'Referral commission - affiliate ' || v_aff.id::text);
    END IF;

    -- Adjust the mentor payout to the referred split.
    UPDATE mentor_payouts SET
      net_amount_customer_currency = ROUND(v_price * v_mentor_pct / 100.0, 2),
      net_amount_mentor_currency   = CASE WHEN v_fx_mc IS NOT NULL
                                          THEN ROUND(v_price * v_mentor_pct / 100.0 / v_fx_mc, 2)
                                          ELSE net_amount_mentor_currency END
    WHERE booking_id = r.id;
  END LOOP;
END; $$;
GRANT EXECUTE ON FUNCTION process_referral_commissions() TO service_role;

-- Guarded cron: process referral commissions every 15 min (no-op if pg_cron absent).
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'referral-commissions') THEN
      PERFORM cron.unschedule('referral-commissions');
    END IF;
    PERFORM cron.schedule('referral-commissions', '*/15 * * * *', 'SELECT process_referral_commissions()');
  ELSE
    RAISE NOTICE 'pg_cron not enabled - skipping referral-commissions schedule.';
  END IF;
END $$;

-- Admin: one row per affiliate (mentor or non-mentor) with code + referral + money aggregates.
CREATE OR REPLACE FUNCTION admin_referrals_overview()
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(jsonb_agg(row ORDER BY (row->>'referrals')::int DESC, (row->>'redemptions')::int DESC), '[]'::jsonb)
  FROM (
    SELECT jsonb_build_object(
      'affiliate_id', a.id, 'type', a.type,
      'name', COALESCE(a.display_name, m.display_name, a.email, 'Affiliate'),
      'mentor_id', a.mentor_id, 'status', a.status,
      'codes', (SELECT COUNT(*) FROM referral_codes rc WHERE rc.affiliate_id = a.id),
      'active_codes', (SELECT COUNT(*) FROM referral_codes rc WHERE rc.affiliate_id = a.id
                         AND rc.is_active AND (rc.expires_at IS NULL OR rc.expires_at > NOW())),
      'redemptions', COALESCE((SELECT SUM(rc.redemption_count) FROM referral_codes rc WHERE rc.affiliate_id = a.id), 0),
      'referrals', (SELECT COUNT(*) FROM commission_ledger cl WHERE cl.affiliate_id = a.id),
      'commission_inr', COALESCE((SELECT SUM(cl.commission_amount_inr) FROM commission_ledger cl
                                    WHERE cl.affiliate_id = a.id AND cl.status IN ('approved','paid')), 0),
      'commission_pending_inr', COALESCE((SELECT SUM(cl.commission_amount_inr) FROM commission_ledger cl
                                    WHERE cl.affiliate_id = a.id AND cl.status = 'pending_review'), 0)
    ) AS row
    FROM affiliates a
    LEFT JOIN mentors m ON m.id = a.mentor_id
  ) t;
$$;
GRANT EXECUTE ON FUNCTION admin_referrals_overview() TO service_role, authenticated;

-- Admin drill-in / payouts: one row per referred (commission) booking - who gave the code, the
-- customer, service/booking, discount, split, and the final amount + commission.
CREATE OR REPLACE FUNCTION admin_referral_bookings(p_affiliate_id UUID DEFAULT NULL)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(jsonb_agg(row ORDER BY (row->>'completed_at') DESC), '[]'::jsonb)
  FROM (
    SELECT jsonb_build_object(
      'ledger_id', cl.id, 'booking_id', cl.booking_id, 'completed_at', cl.session_completed_at,
      'affiliate_id', cl.affiliate_id,
      'affiliate_name', COALESCE(a.display_name, m2.display_name, a.email),
      'referral_code', cl.referral_code,
      'customer_email', b.candidate_email, 'customer_name', b.candidate_name,
      'service_id', b.service_id, 'mentor_id', cl.mentor_id, 'mentor_name', m.display_name,
      'discount_pct', cl.discount_pct, 'gross_customer', cl.gross_customer,
      'customer_currency', cl.customer_currency, 'split', cl.split_snapshot,
      'commission_amount', cl.commission_amount, 'commission_amount_inr', cl.commission_amount_inr,
      'status', cl.status
    ) AS row
    FROM commission_ledger cl
    JOIN bookings b ON b.id = cl.booking_id
    JOIN mentors m ON m.id = cl.mentor_id
    JOIN affiliates a ON a.id = cl.affiliate_id
    LEFT JOIN mentors m2 ON m2.id = a.mentor_id
    WHERE p_affiliate_id IS NULL OR cl.affiliate_id = p_affiliate_id
  ) t;
$$;
GRANT EXECUTE ON FUNCTION admin_referral_bookings(UUID) TO service_role, authenticated;

-- Mentor dashboard: this mentor's own affiliate codes + promoter earnings.
CREATE OR REPLACE FUNCTION mentor_referral_overview(p_mentor_id UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE v_aff UUID; v_result JSONB;
BEGIN
  SELECT id INTO v_aff FROM affiliates WHERE mentor_id = p_mentor_id;
  IF v_aff IS NULL THEN
    RETURN jsonb_build_object('affiliate_id', NULL, 'codes', '[]'::jsonb,
      'referrals', 0, 'earnings_inr', 0, 'pending_inr', 0);
  END IF;
  SELECT jsonb_build_object(
    'affiliate_id', v_aff,
    'codes', COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'id', rc.id, 'code', rc.code_string, 'discount_pct', rc.discount_pct,
        'redemption_cap', rc.redemption_cap, 'redemption_count', rc.redemption_count,
        'expires_at', rc.expires_at, 'is_active', rc.is_active,
        'expired', (rc.expires_at IS NOT NULL AND rc.expires_at < NOW()),
        'created_at', rc.created_at
      ) ORDER BY rc.created_at DESC) FROM referral_codes rc WHERE rc.affiliate_id = v_aff), '[]'::jsonb),
    'referrals', (SELECT COUNT(*) FROM commission_ledger cl WHERE cl.affiliate_id = v_aff),
    'earnings_inr', COALESCE((SELECT SUM(commission_amount_inr) FROM commission_ledger
                                WHERE affiliate_id = v_aff AND status IN ('approved','paid')), 0),
    'pending_inr', COALESCE((SELECT SUM(commission_amount_inr) FROM commission_ledger
                                WHERE affiliate_id = v_aff AND status = 'pending_review'), 0)
  ) INTO v_result;
  RETURN v_result;
END; $$;
GRANT EXECUTE ON FUNCTION mentor_referral_overview(UUID) TO authenticated;

-- Admin: approve or reject a pending commission (simple review; the full fraud queue is a later phase).
CREATE OR REPLACE FUNCTION admin_set_commission_status(p_ledger_id UUID, p_status TEXT, p_admin UUID DEFAULT NULL, p_note TEXT DEFAULT NULL)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_status NOT IN ('approved', 'rejected', 'paid', 'void') THEN
    RAISE EXCEPTION 'Invalid status %', p_status USING errcode = 'P0001';
  END IF;
  UPDATE commission_ledger SET status = p_status,
     notified_at = CASE WHEN p_status = 'approved' THEN NULL ELSE notified_at END
   WHERE id = p_ledger_id;
  INSERT INTO referral_admin_actions (admin_id, action, target_type, target_id, note)
    VALUES (p_admin, 'commission_' || p_status, 'commission_ledger', p_ledger_id, COALESCE(p_note, ''));
END; $$;
GRANT EXECUTE ON FUNCTION admin_set_commission_status(UUID, TEXT, UUID, TEXT) TO service_role, authenticated;

-- ###########################################################################
-- Reviews: mentee -> mentor post-session ratings + written reviews + moderation.
-- One review per completed booking (editable within the review window; editing
-- re-enters moderation). PRE-MODERATION: a review is 'pending' until an admin
-- publishes it; only 'published' reviews are public + counted in the mentor's
-- avg_rating / review_count. Optional sub-ratings (knowledge/communication/help).
-- ###########################################################################

CREATE TABLE IF NOT EXISTS reviews (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id           UUID NOT NULL UNIQUE REFERENCES bookings(id) ON DELETE CASCADE,
  mentor_id            UUID NOT NULL REFERENCES mentors(id) ON DELETE CASCADE,
  reviewer_id          UUID REFERENCES profiles(id) ON DELETE SET NULL,
  reviewer_name        TEXT,                                    -- first-name snapshot for public display
  rating               INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
  rating_knowledge     INT CHECK (rating_knowledge BETWEEN 1 AND 5),
  rating_communication INT CHECK (rating_communication BETWEEN 1 AND 5),
  rating_helpfulness   INT CHECK (rating_helpfulness BETWEEN 1 AND 5),
  body                 TEXT,                                    -- sanitized rich-text HTML
  status               TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','published','rejected')),
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Idempotent upgrades if reviews was created by the earlier (v1) version.
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS rating_knowledge     INT;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS rating_communication INT;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS rating_helpfulness   INT;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE reviews DROP COLUMN IF EXISTS is_hidden;   -- retired: superseded by status
DO $$ BEGIN
  ALTER TABLE reviews ADD CONSTRAINT reviews_status_chk CHECK (status IN ('pending','published','rejected'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS idx_reviews_mentor ON reviews(mentor_id, created_at DESC);
ALTER TABLE reviews ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS reviews_public_read ON reviews;
CREATE POLICY reviews_public_read ON reviews FOR SELECT USING (status = 'published');

INSERT INTO platform_settings (key, value, description) VALUES
  ('review_window_days', '14', 'Days after a session completes during which the mentee can leave/edit a review')
ON CONFLICT (key) DO NOTHING;

-- Recompute a mentor's cached avg_rating + review_count from their PUBLISHED reviews.
CREATE OR REPLACE FUNCTION recompute_mentor_rating(p_mentor_id UUID)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  UPDATE mentors m SET
    avg_rating   = COALESCE((SELECT ROUND(AVG(rating)::numeric, 2) FROM reviews r WHERE r.mentor_id = p_mentor_id AND r.status = 'published'), 0),
    review_count = (SELECT COUNT(*) FROM reviews r WHERE r.mentor_id = p_mentor_id AND r.status = 'published')
  WHERE m.id = p_mentor_id;
$$;
GRANT EXECUTE ON FUNCTION recompute_mentor_rating(UUID) TO service_role, authenticated;

-- Mentee submits/edits their review for a COMPLETED session they attended, within the review window.
-- Editing re-enters moderation (status -> pending). One per booking.
DROP FUNCTION IF EXISTS submit_review(UUID, UUID, INT, TEXT);
CREATE OR REPLACE FUNCTION submit_review(
  p_booking_id UUID, p_reviewer UUID, p_rating INT, p_body TEXT DEFAULT NULL,
  p_knowledge INT DEFAULT NULL, p_communication INT DEFAULT NULL, p_helpfulness INT DEFAULT NULL
) RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_name TEXT; v_id UUID; v_window INT; v_end TIMESTAMPTZ;
BEGIN
  IF p_rating < 1 OR p_rating > 5 THEN RAISE EXCEPTION 'Rating must be between 1 and 5' USING errcode = 'P0001'; END IF;
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking not found' USING errcode = 'P0001'; END IF;
  IF b.candidate_id IS NULL OR b.candidate_id <> p_reviewer THEN
    RAISE EXCEPTION 'Only the mentee on this booking can review it' USING errcode = 'P0001';
  END IF;
  IF b.status <> 'completed' THEN
    RAISE EXCEPTION 'You can review only after the session is completed' USING errcode = 'P0001';
  END IF;
  v_window := COALESCE((SELECT value::int FROM platform_settings WHERE key = 'review_window_days'), 14);
  v_end    := COALESCE(b.slot_end, b.slot_time, NOW());
  IF NOW() > v_end + MAKE_INTERVAL(days => v_window) THEN
    RAISE EXCEPTION 'The review window for this session has closed' USING errcode = 'P0001';
  END IF;
  SELECT split_part(COALESCE(NULLIF(TRIM(display_name), ''), NULLIF(TRIM(full_name), ''), 'Member'), ' ', 1)
    INTO v_name FROM profiles WHERE id = p_reviewer;
  INSERT INTO reviews (booking_id, mentor_id, reviewer_id, reviewer_name, rating,
                       rating_knowledge, rating_communication, rating_helpfulness, body, status)
    VALUES (p_booking_id, b.mentor_id, p_reviewer, v_name, p_rating,
            p_knowledge, p_communication, p_helpfulness, NULLIF(TRIM(p_body), ''), 'pending')
  ON CONFLICT (booking_id) DO UPDATE
    SET rating = EXCLUDED.rating, rating_knowledge = EXCLUDED.rating_knowledge,
        rating_communication = EXCLUDED.rating_communication, rating_helpfulness = EXCLUDED.rating_helpfulness,
        body = EXCLUDED.body, status = 'pending', updated_at = NOW()
  RETURNING id INTO v_id;
  PERFORM recompute_mentor_rating(b.mentor_id);   -- an edit drops a previously-published review until re-approved
  RETURN v_id;
END; $$;
GRANT EXECUTE ON FUNCTION submit_review(UUID, UUID, INT, TEXT, INT, INT, INT) TO authenticated;

-- Public: a mentor's PUBLISHED reviews (+ sub-ratings). Admin passes p_include_hidden := true for all.
CREATE OR REPLACE FUNCTION mentor_reviews(p_mentor_id UUID, p_include_hidden BOOLEAN DEFAULT FALSE)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'id', r.id, 'rating', r.rating, 'body', r.body, 'reviewer_name', r.reviewer_name,
    'knowledge', r.rating_knowledge, 'communication', r.rating_communication, 'helpfulness', r.rating_helpfulness,
    'created_at', r.created_at, 'status', r.status, 'verified', true
  ) ORDER BY r.created_at DESC), '[]'::jsonb)
  FROM reviews r
  WHERE r.mentor_id = p_mentor_id AND (p_include_hidden OR r.status = 'published');
$$;
GRANT EXECUTE ON FUNCTION mentor_reviews(UUID, BOOLEAN) TO anon, authenticated;

-- Public: a mentor's rating summary (avg, count, 5..1 distribution, sub-rating averages).
CREATE OR REPLACE FUNCTION mentor_rating_summary(p_mentor_id UUID)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT jsonb_build_object(
    'avg',   COALESCE(ROUND(AVG(rating)::numeric, 2), 0),
    'count', COUNT(*),
    'distribution', jsonb_build_object(
      '5', COUNT(*) FILTER (WHERE rating = 5), '4', COUNT(*) FILTER (WHERE rating = 4),
      '3', COUNT(*) FILTER (WHERE rating = 3), '2', COUNT(*) FILTER (WHERE rating = 2),
      '1', COUNT(*) FILTER (WHERE rating = 1)),
    'knowledge',     ROUND(AVG(rating_knowledge)::numeric, 2),
    'communication', ROUND(AVG(rating_communication)::numeric, 2),
    'helpfulness',   ROUND(AVG(rating_helpfulness)::numeric, 2)
  )
  FROM reviews WHERE mentor_id = p_mentor_id AND status = 'published';
$$;
GRANT EXECUTE ON FUNCTION mentor_rating_summary(UUID) TO anon, authenticated;

-- The mentee's own review for a booking (to prefill the edit form). NULL if none.
CREATE OR REPLACE FUNCTION my_review_for_booking(p_booking_id UUID, p_reviewer UUID)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT to_jsonb(r) FROM reviews r WHERE r.booking_id = p_booking_id AND r.reviewer_id = p_reviewer;
$$;
GRANT EXECUTE ON FUNCTION my_review_for_booking(UUID, UUID) TO authenticated;

-- Admin: recent reviews for moderation, enriched with the meeting details.
CREATE OR REPLACE FUNCTION admin_reviews(p_limit INT DEFAULT 200)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(jsonb_agg(row ORDER BY (row->>'created_at') DESC), '[]'::jsonb) FROM (
    SELECT jsonb_build_object(
      'id', r.id, 'rating', r.rating, 'body', r.body, 'reviewer_name', r.reviewer_name,
      'knowledge', r.rating_knowledge, 'communication', r.rating_communication, 'helpfulness', r.rating_helpfulness,
      'mentor_id', r.mentor_id, 'mentor_name', m.display_name,
      'mentee_name', b.candidate_name, 'mentee_email', b.candidate_email,
      'booking_id', r.booking_id, 'slot_time', b.slot_time, 'slot_end', b.slot_end,
      'duration', s.duration, 'service_title', s.title,
      'status', r.status, 'created_at', r.created_at
    ) AS row
    FROM reviews r
    JOIN mentors m ON m.id = r.mentor_id
    JOIN bookings b ON b.id = r.booking_id
    LEFT JOIN services s ON s.id = b.service_id
    ORDER BY r.created_at DESC LIMIT GREATEST(p_limit, 1)
  ) t;
$$;
GRANT EXECUTE ON FUNCTION admin_reviews(INT) TO service_role, authenticated;

-- Admin: publish (enable) / reject (disable) / reset a review; re-syncs the mentor's rating.
DROP FUNCTION IF EXISTS admin_set_review_hidden(UUID, BOOLEAN);
CREATE OR REPLACE FUNCTION admin_set_review_status(p_review_id UUID, p_status TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_mentor UUID;
BEGIN
  IF p_status NOT IN ('pending','published','rejected') THEN
    RAISE EXCEPTION 'Invalid status %', p_status USING errcode = 'P0001';
  END IF;
  UPDATE reviews SET status = p_status, updated_at = NOW() WHERE id = p_review_id RETURNING mentor_id INTO v_mentor;
  IF v_mentor IS NOT NULL THEN PERFORM recompute_mentor_rating(v_mentor); END IF;
END; $$;
GRANT EXECUTE ON FUNCTION admin_set_review_status(UUID, TEXT) TO service_role, authenticated;

-- ── Security hardening: referral + review RPCs are BACKEND-ONLY ───────────────
-- The frontend never calls these directly - every request goes through the FastAPI backend using
-- the service role, which enforces the admin/mentor checks. So strip the default PUBLIC / anon /
-- authenticated EXECUTE: otherwise any logged-in user could call them straight through PostgREST
-- (with the public anon key + their JWT) and bypass those checks - reading other mentors' earnings,
-- customer emails and unpublished reviews, approving their own commissions, or submitting a review
-- as another user. Same lock-down pattern as confirm_booking_payment.
DO $$
DECLARE fn TEXT;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'ensure_mentor_affiliate(uuid)',
    'generate_referral_code(uuid,numeric,integer,timestamptz)',
    'validate_referral_code(text)',
    'current_affiliate_tier(uuid)',
    'mentor_referral_overview(uuid)',
    'admin_referrals_overview()',
    'admin_referral_bookings(uuid)',
    'admin_set_commission_status(uuid,text,uuid,text)',
    'process_referral_commissions()',
    'recompute_mentor_rating(uuid)',
    'submit_review(uuid,uuid,integer,text,integer,integer,integer)',
    'mentor_reviews(uuid,boolean)',
    'mentor_rating_summary(uuid)',
    'my_review_for_booking(uuid,uuid)',
    'admin_reviews(integer)',
    'admin_set_review_status(uuid,text)'
  ] LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated', fn);
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role', fn);
  END LOOP;
END $$;



-- ###########################################################################
-- Platform activity log (unified audit trail)
-- One admin-viewable timeline of everything that happens: bookings and their
-- status changes, customer payments, mentor payouts, money-ledger movements,
-- referral commissions, and pricing / commission config changes. Filled by
-- AFTER triggers, so capture is guaranteed no matter which code path made the
-- change. Every trigger body is wrapped in its own EXCEPTION guard, so a bug in
-- the auditing can never abort the underlying business write. Reads are admin /
-- service-role only (RLS on, no select policy).
-- ###########################################################################
CREATE TABLE IF NOT EXISTS audit_events (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  entity_type  TEXT NOT NULL,     -- booking | payment | payout | ledger | commission | pricing | settings
  entity_id    UUID,              -- the changed row's id (null for country/settings rows keyed by text)
  booking_id   UUID,              -- correlation key for anything tied to a booking
  action       TEXT NOT NULL,     -- created | updated | status_changed | deleted
  actor        TEXT,              -- app-set (SET LOCAL app.actor) when available, else 'system'
  summary      TEXT,              -- short human-readable line
  details      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_audit_events_booking ON audit_events(booking_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_entity  ON audit_events(entity_type, entity_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_time    ON audit_events(occurred_at DESC);
ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;   -- admin / service-role reads only, no select policy

-- The actor for the current statement, if the backend set one via  SET LOCAL app.actor = '...'.
CREATE OR REPLACE FUNCTION audit_actor()
RETURNS TEXT LANGUAGE sql STABLE AS $$
  SELECT COALESCE(NULLIF(current_setting('app.actor', true), ''), 'system');
$$;

-- Single insert path. SECURITY DEFINER so triggers running as any role can write; the EXCEPTION
-- guard makes the write itself best-effort. NOTE: callers ALSO guard their argument-building (enum
-- casts etc.), because those are evaluated before this function runs.
CREATE OR REPLACE FUNCTION log_audit_event(
  p_entity_type TEXT, p_entity_id UUID, p_booking_id UUID,
  p_action TEXT, p_summary TEXT, p_details JSONB
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  INSERT INTO audit_events(entity_type, entity_id, booking_id, action, actor, summary, details)
  VALUES (p_entity_type, p_entity_id, p_booking_id, p_action, audit_actor(), p_summary,
          COALESCE(p_details, '{}'::jsonb));
EXCEPTION WHEN OTHERS THEN
  NULL;   -- auditing is best-effort; never break the underlying write
END; $$;

-- ── bookings: creation + every status change (status is an ENUM -> cast to text) ──
CREATE OR REPLACE FUNCTION audit_bookings() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  BEGIN
    IF TG_OP = 'INSERT' THEN
      PERFORM log_audit_event('booking', NEW.id, NEW.id, 'created',
        'Booking created (' || COALESCE(NEW.status::text, '?') || ')',
        jsonb_build_object('status', NEW.status::text, 'service_id', NEW.service_id,
          'mentor_id', NEW.mentor_id, 'slot_time', NEW.slot_time,
          'candidate_email', NEW.candidate_email, 'source', NEW.source,
          'referral_code', NEW.referral_code,
          'referral_discount_applied_pct', NEW.referral_discount_applied_pct,
          'customer_currency', NEW.customer_currency));
    ELSIF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status THEN
      PERFORM log_audit_event('booking', NEW.id, NEW.id, 'status_changed',
        'Booking ' || COALESCE(OLD.status::text, '?') || ' -> ' || COALESCE(NEW.status::text, '?'),
        jsonb_build_object('from', OLD.status::text, 'to', NEW.status::text));
    END IF;
  EXCEPTION WHEN OTHERS THEN
    NULL;   -- auditing must never break the booking write
  END;
  RETURN NULL;
END; $$;
DROP TRIGGER IF EXISTS trg_audit_bookings ON bookings;
CREATE TRIGGER trg_audit_bookings AFTER INSERT OR UPDATE ON bookings
  FOR EACH ROW EXECUTE FUNCTION audit_bookings();

-- ── customer_payments: creation + payment state changes ──────────────────────
CREATE OR REPLACE FUNCTION audit_customer_payments() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  BEGIN
    IF TG_OP = 'INSERT' THEN
      PERFORM log_audit_event('payment', NEW.id, NEW.booking_id, 'created',
        'Payment ' || COALESCE(NEW.state, '?') || ' ' || COALESCE(NEW.amount::text, '') || ' ' || COALESCE(NEW.currency, ''),
        jsonb_build_object('state', NEW.state, 'amount', NEW.amount, 'currency', NEW.currency, 'provider', NEW.provider));
    ELSIF TG_OP = 'UPDATE' AND NEW.state IS DISTINCT FROM OLD.state THEN
      PERFORM log_audit_event('payment', NEW.id, NEW.booking_id, 'status_changed',
        'Payment ' || COALESCE(OLD.state, '?') || ' -> ' || COALESCE(NEW.state, '?'),
        jsonb_build_object('from', OLD.state, 'to', NEW.state, 'amount', NEW.amount, 'currency', NEW.currency,
          'provider_payment_id', NEW.provider_payment_id));
    END IF;
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;
  RETURN NULL;
END; $$;
DROP TRIGGER IF EXISTS trg_audit_customer_payments ON customer_payments;
CREATE TRIGGER trg_audit_customer_payments AFTER INSERT OR UPDATE ON customer_payments
  FOR EACH ROW EXECUTE FUNCTION audit_customer_payments();

-- ── mentor_payouts: creation + payout state changes ──────────────────────────
CREATE OR REPLACE FUNCTION audit_mentor_payouts() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  BEGIN
    IF TG_OP = 'INSERT' THEN
      PERFORM log_audit_event('payout', NEW.id, NEW.booking_id, 'created',
        'Payout created (' || COALESCE(NEW.payout_state, 'pending') || ')',
        jsonb_build_object('payout_state', NEW.payout_state,
          'net_amount_customer_currency', NEW.net_amount_customer_currency,
          'net_amount_mentor_currency', NEW.net_amount_mentor_currency,
          'customer_currency', NEW.customer_currency, 'mentor_currency', NEW.mentor_currency));
    ELSIF TG_OP = 'UPDATE' AND NEW.payout_state IS DISTINCT FROM OLD.payout_state THEN
      PERFORM log_audit_event('payout', NEW.id, NEW.booking_id, 'status_changed',
        'Payout ' || COALESCE(OLD.payout_state, '?') || ' -> ' || COALESCE(NEW.payout_state, '?'),
        jsonb_build_object('from', OLD.payout_state, 'to', NEW.payout_state,
          'payout_reference', NEW.payout_reference, 'paid_date', NEW.paid_date));
    END IF;
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;
  RETURN NULL;
END; $$;
DROP TRIGGER IF EXISTS trg_audit_mentor_payouts ON mentor_payouts;
CREATE TRIGGER trg_audit_mentor_payouts AFTER INSERT OR UPDATE ON mentor_payouts
  FOR EACH ROW EXECUTE FUNCTION audit_mentor_payouts();

-- ── booking_ledger: every money movement (charge / refund / credit / penalty / commission) ──
CREATE OR REPLACE FUNCTION audit_booking_ledger() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  BEGIN
    PERFORM log_audit_event('ledger', NEW.id, NEW.booking_id, 'created',
      initcap(NEW.party) || ' ' || NEW.kind || ' ' || COALESCE(NEW.amount::text, '') || ' ' || COALESCE(NEW.currency, ''),
      jsonb_build_object('party', NEW.party, 'kind', NEW.kind, 'amount', NEW.amount,
        'pct', NEW.pct, 'currency', NEW.currency, 'reason', NEW.reason));
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;
  RETURN NULL;
END; $$;
DROP TRIGGER IF EXISTS trg_audit_booking_ledger ON booking_ledger;
CREATE TRIGGER trg_audit_booking_ledger AFTER INSERT ON booking_ledger
  FOR EACH ROW EXECUTE FUNCTION audit_booking_ledger();

-- ── commission_ledger: referral commission recorded + status changes ─────────
CREATE OR REPLACE FUNCTION audit_commission_ledger() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  BEGIN
    IF TG_OP = 'INSERT' THEN
      PERFORM log_audit_event('commission', NEW.id, NEW.booking_id, 'created',
        'Referral commission ' || COALESCE(NEW.commission_amount::text, '') || ' ' || COALESCE(NEW.customer_currency, '') || ' (' || COALESCE(NEW.status, '') || ')',
        jsonb_build_object('affiliate_id', NEW.affiliate_id, 'referral_code', NEW.referral_code,
          'commission_amount', NEW.commission_amount, 'customer_currency', NEW.customer_currency,
          'split_snapshot', NEW.split_snapshot, 'status', NEW.status));
    ELSIF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status THEN
      PERFORM log_audit_event('commission', NEW.id, NEW.booking_id, 'status_changed',
        'Referral commission ' || COALESCE(OLD.status, '?') || ' -> ' || COALESCE(NEW.status, '?'),
        jsonb_build_object('from', OLD.status, 'to', NEW.status, 'affiliate_id', NEW.affiliate_id));
    END IF;
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;
  RETURN NULL;
END; $$;
DROP TRIGGER IF EXISTS trg_audit_commission_ledger ON commission_ledger;
CREATE TRIGGER trg_audit_commission_ledger AFTER INSERT OR UPDATE ON commission_ledger
  FOR EACH ROW EXECUTE FUNCTION audit_commission_ledger();

-- ── country_pricing: platform fee / tax config changes ───────────────────────
CREATE OR REPLACE FUNCTION audit_country_pricing() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.platform_fee_pct IS NOT DISTINCT FROM OLD.platform_fee_pct
       AND NEW.tax_pct IS NOT DISTINCT FROM OLD.tax_pct
       AND NEW.tax_label IS NOT DISTINCT FROM OLD.tax_label THEN
      RETURN NULL;   -- nothing pricing-relevant changed
    END IF;
    PERFORM log_audit_event('pricing', NULL, NULL, LOWER(TG_OP),
      'Pricing ' || NEW.country_code || ': fee ' || NEW.platform_fee_pct || '%, tax ' || NEW.tax_pct || '%',
      jsonb_build_object('country_code', NEW.country_code, 'platform_fee_pct', NEW.platform_fee_pct,
        'tax_pct', NEW.tax_pct, 'tax_label', NEW.tax_label));
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;
  RETURN NULL;
END; $$;
DROP TRIGGER IF EXISTS trg_audit_country_pricing ON country_pricing;
CREATE TRIGGER trg_audit_country_pricing AFTER INSERT OR UPDATE ON country_pricing
  FOR EACH ROW EXECUTE FUNCTION audit_country_pricing();

-- ── platform_settings: any tunable value change (incl. mentor commission %) ──
CREATE OR REPLACE FUNCTION audit_platform_settings() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  BEGIN
    IF TG_OP = 'UPDATE' AND NEW.value IS NOT DISTINCT FROM OLD.value THEN
      RETURN NULL;
    END IF;
    PERFORM log_audit_event('settings', NULL, NULL, LOWER(TG_OP),
      'Setting ' || NEW.key || ' = ' || COALESCE(NEW.value, ''),
      jsonb_build_object('key', NEW.key, 'from', CASE WHEN TG_OP = 'UPDATE' THEN OLD.value ELSE NULL END, 'to', NEW.value));
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;
  RETURN NULL;
END; $$;
DROP TRIGGER IF EXISTS trg_audit_platform_settings ON platform_settings;
CREATE TRIGGER trg_audit_platform_settings AFTER INSERT OR UPDATE ON platform_settings
  FOR EACH ROW EXECUTE FUNCTION audit_platform_settings();

-- Admin-facing reader: newest-first activity, optionally scoped to one booking or entity type.
CREATE OR REPLACE FUNCTION admin_audit_events(p_booking_id UUID DEFAULT NULL, p_entity_type TEXT DEFAULT NULL, p_limit INT DEFAULT 200)
RETURNS SETOF audit_events LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT * FROM audit_events
  WHERE (p_booking_id IS NULL OR booking_id = p_booking_id)
    AND (p_entity_type IS NULL OR entity_type = p_entity_type)
  ORDER BY occurred_at DESC, id DESC
  LIMIT GREATEST(1, LEAST(COALESCE(p_limit, 200), 1000));
$$;
REVOKE ALL ON FUNCTION admin_audit_events(UUID, TEXT, INT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION admin_audit_events(UUID, TEXT, INT) TO service_role;
