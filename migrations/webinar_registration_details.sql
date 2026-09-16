-- Add the attendee details and separate optional marketing consent captured at registration.
-- Safe to run repeatedly on an existing webinar installation.
ALTER TABLE webinar_registrations
  ADD COLUMN IF NOT EXISTS attendee_full_name text,
  ADD COLUMN IF NOT EXISTS attendee_email text,
  ADD COLUMN IF NOT EXISTS attendee_phone text,
  ADD COLUMN IF NOT EXISTS marketing_consent boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS marketing_consent_at timestamptz;
