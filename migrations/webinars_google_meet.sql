-- Replace automatic Jitsi room generation with organizer-supplied Google Meet links.
-- Legacy rows remain readable so this migration can be deployed before they are edited.
ALTER TABLE webinars ALTER COLUMN meeting_provider SET DEFAULT 'google_meet';
ALTER TABLE webinar_registrations ADD COLUMN IF NOT EXISTS reminder_sent_at timestamptz;

ALTER TABLE webinars DROP CONSTRAINT IF EXISTS webinars_meeting_provider_check;
ALTER TABLE webinars ADD CONSTRAINT webinars_meeting_provider_check
  CHECK (meeting_provider IN ('jitsi_public', 'google_meet'));

ALTER TABLE webinars DROP CONSTRAINT IF EXISTS webinars_google_meet_url_check;
ALTER TABLE webinars ADD CONSTRAINT webinars_google_meet_url_check
  CHECK (
    meeting_provider <> 'google_meet'
    OR meeting_url LIKE 'https://meet.google.com/%'
  );
