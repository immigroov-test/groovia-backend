-- Webinar module: admin-created events, mentor proposals, registrations and payments.
-- Safe to run repeatedly on an existing Supabase project.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS webinars (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text NOT NULL UNIQUE,
  title text NOT NULL,
  description text NOT NULL DEFAULT '',
  banner_url text,
  media_url text,
  mentor_id uuid REFERENCES mentors(id) ON DELETE SET NULL,
  created_by uuid REFERENCES profiles(id) ON DELETE SET NULL,
  source text NOT NULL DEFAULT 'admin' CHECK (source IN ('admin','mentor_request')),
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','pending_review','changes_requested','approved','published','registration_closed','live','completed','cancelled','rejected')),
  starts_at timestamptz NOT NULL,
  duration_minutes integer NOT NULL CHECK (duration_minutes BETWEEN 15 AND 480),
  timezone text NOT NULL DEFAULT 'UTC',
  registration_deadline timestamptz,
  capacity integer NOT NULL DEFAULT 100 CHECK (capacity BETWEEN 1 AND 10000),
  is_paid boolean NOT NULL DEFAULT false,
  price numeric(12,2) NOT NULL DEFAULT 0 CHECK (price >= 0),
  currency char(3) NOT NULL DEFAULT 'INR',
  meeting_provider text NOT NULL DEFAULT 'google_meet' CHECK (meeting_provider IN ('google_meet')),
  meeting_room text,
  meeting_url text,
  admin_note text,
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((is_paid AND price > 0) OR (NOT is_paid AND price = 0))
);

-- Existing installations created before webinar media support.
ALTER TABLE webinars ADD COLUMN IF NOT EXISTS media_url text;

CREATE TABLE IF NOT EXISTS webinar_registrations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  webinar_id uuid NOT NULL REFERENCES webinars(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('pending_payment','confirmed','cancelled','refunded','attended','no_show')),
  amount numeric(12,2) NOT NULL DEFAULT 0,
  currency char(3) NOT NULL DEFAULT 'INR',
  provider text,
  provider_order_id text UNIQUE,
  provider_payment_id text UNIQUE,
  joined_at timestamptz,
  left_at timestamptz,
  reminder_sent_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (webinar_id, user_id)
);

CREATE INDEX IF NOT EXISTS webinars_public_idx ON webinars(status, starts_at);
CREATE INDEX IF NOT EXISTS webinars_mentor_idx ON webinars(mentor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS webinar_registrations_user_idx ON webinar_registrations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS webinar_registrations_webinar_idx ON webinar_registrations(webinar_id, status);

ALTER TABLE webinar_registrations ADD COLUMN IF NOT EXISTS reminder_sent_at timestamptz;

ALTER TABLE webinars ENABLE ROW LEVEL SECURITY;
ALTER TABLE webinar_registrations ENABLE ROW LEVEL SECURITY;

-- Public reads go through FastAPI's deliberately redacted projection. Do not grant a
-- direct anon SELECT policy: meeting_room lives on this row and must remain private.
DROP POLICY IF EXISTS webinars_public_read ON webinars;
DROP POLICY IF EXISTS webinar_registrations_self_read ON webinar_registrations;
CREATE POLICY webinar_registrations_self_read ON webinar_registrations FOR SELECT USING (user_id = auth.uid());

CREATE OR REPLACE FUNCTION webinar_confirm_free(p_webinar_id uuid, p_user_id uuid)
RETURNS webinar_registrations LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE w webinars; r webinar_registrations; seats integer;
BEGIN
  SELECT * INTO w FROM webinars WHERE id = p_webinar_id FOR UPDATE;
  IF NOT FOUND OR w.status <> 'published' THEN RAISE EXCEPTION 'WEBINAR_NOT_OPEN'; END IF;
  IF w.is_paid THEN RAISE EXCEPTION 'PAYMENT_REQUIRED'; END IF;
  IF w.starts_at <= now() OR (w.registration_deadline IS NOT NULL AND w.registration_deadline <= now()) THEN RAISE EXCEPTION 'REGISTRATION_CLOSED'; END IF;
  SELECT count(*) INTO seats FROM webinar_registrations WHERE webinar_id=w.id AND status IN ('confirmed','attended','pending_payment');
  IF seats >= w.capacity THEN RAISE EXCEPTION 'WEBINAR_FULL'; END IF;
  INSERT INTO webinar_registrations(webinar_id,user_id,status,amount,currency)
  VALUES(w.id,p_user_id,'confirmed',0,w.currency)
  ON CONFLICT(webinar_id,user_id) DO UPDATE SET status=CASE WHEN webinar_registrations.status='cancelled' THEN 'confirmed' ELSE webinar_registrations.status END, updated_at=now()
  RETURNING * INTO r;
  RETURN r;
END $$;

CREATE OR REPLACE FUNCTION webinar_reserve_paid(p_webinar_id uuid, p_user_id uuid)
RETURNS webinar_registrations LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE w webinars; r webinar_registrations; seats integer;
BEGIN
  SELECT * INTO w FROM webinars WHERE id = p_webinar_id FOR UPDATE;
  IF NOT FOUND OR w.status <> 'published' THEN RAISE EXCEPTION 'WEBINAR_NOT_OPEN'; END IF;
  IF NOT w.is_paid THEN RAISE EXCEPTION 'WEBINAR_IS_FREE'; END IF;
  IF w.starts_at <= now() OR (w.registration_deadline IS NOT NULL AND w.registration_deadline <= now()) THEN RAISE EXCEPTION 'REGISTRATION_CLOSED'; END IF;
  -- Release abandoned checkout holds before counting capacity. Confirmed seats never expire.
  UPDATE webinar_registrations SET status='cancelled', updated_at=now()
    WHERE webinar_id=w.id AND status='pending_payment' AND updated_at <= now()-interval '15 minutes';
  SELECT count(*) INTO seats FROM webinar_registrations WHERE webinar_id=w.id AND status IN ('confirmed','attended','pending_payment');
  IF seats >= w.capacity THEN RAISE EXCEPTION 'WEBINAR_FULL'; END IF;
  INSERT INTO webinar_registrations(webinar_id,user_id,status,amount,currency)
  VALUES(w.id,p_user_id,'pending_payment',w.price,w.currency)
  ON CONFLICT(webinar_id,user_id) DO UPDATE SET status=CASE WHEN webinar_registrations.status IN ('cancelled','refunded') THEN 'pending_payment' ELSE webinar_registrations.status END, amount=w.price, currency=w.currency, updated_at=now()
  RETURNING * INTO r;
  RETURN r;
END $$;

REVOKE ALL ON FUNCTION webinar_confirm_free(uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION webinar_reserve_paid(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION webinar_confirm_free(uuid,uuid) TO service_role;
GRANT EXECUTE ON FUNCTION webinar_reserve_paid(uuid,uuid) TO service_role;
