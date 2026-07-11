-- testing_db_reset.sql
-- Resets the TEST database between test runs so you can re-test with the same emails,
-- WITHOUT touching mentors. Do NOT run on production.
--
-- What this clears:
--   - ALL bookings + their children (answers, reminders, offers, requests) - so admin
--     and the respective mentors see a clean slate, no leftover test bookings
--   - All mentee / guest test accounts (anyone whose role is not mentor or admin),
--     freeing their email addresses to be reused for the next test
--   - webhook_events, ai_events, LangGraph checkpoints
--
-- What this PRESERVES (never deleted):
--   - ALL mentors (seed + self-signup) and their services / availability / profiles
--   - All admins and platform_settings
--
-- ============================================================================
-- !!! SAFETY GUARD - DO NOT REMOVE CASUALLY !!!
-- This DELETES every booking and every mentee/guest account. Mentors and admins are
-- preserved, but it is still for a TEST database ONLY, never production.
-- To run it: (1) take a backup (Supabase Dashboard -> Database -> Backups), then
-- (2) delete the DO $$ ... $$ guard block just below.
-- ============================================================================
DO $$ BEGIN
  RAISE EXCEPTION 'SAFETY STOP: testing_db_reset.sql deletes all bookings + all mentee/guest accounts (mentors + admins are kept). Back up first, then delete this guard block to proceed. NEVER run on production.';
END $$;

-- 0. Make sure the admin accounts are classified as 'admin' so they are preserved
--    below (a fresh signup defaults to 'candidate'). Idempotent.
UPDATE profiles SET role = 'admin'
  WHERE lower(email) IN ('yokeshmd99@gmail.com', 'immigroovtst@gmail.com');

-- 1. Delete ALL bookings and their children. In a test DB every booking is a test
--    booking, so clearing them all is what removes them from admin + mentor views.
--    reschedule_offers is deleted first in case its FK to bookings is not CASCADE;
--    booking_question_answers / booking_reminders / booking_requests / payment rows
--    cascade from bookings.
DELETE FROM reschedule_offers;
DELETE FROM bookings;
TRUNCATE webhook_events;

-- 2. Delete mentee / guest test accounts only (everyone who is NOT a mentor or admin).
--    Cascades: profiles, chat_threads, consent_log. Mentors + admins are untouched, so
--    their profiles, services and availability all remain intact. Bookings are already
--    gone (step 1), so nothing is left pointing at a deleted user.
DELETE FROM auth.users u
USING profiles p
WHERE p.id = u.id
  AND p.role NOT IN ('mentor', 'admin');

-- 3. Clear LangGraph checkpoint tables (created by AsyncPostgresSaver), if present.
DO $$ BEGIN
  IF to_regclass('public.checkpoints') IS NOT NULL THEN
    TRUNCATE checkpoints, checkpoint_writes, checkpoint_blobs CASCADE;
  END IF;
END $$;

-- 4. Clear the AI observability log.
TRUNCATE ai_events;
