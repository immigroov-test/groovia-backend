-- testing_db_reset.sql
-- Resets the TEST database between test runs so every real email can be reused,
-- while keeping the dummy/seed mentors intact. Do NOT run on production.
--
-- What this DELETES:
--   - ALL bookings + their children (answers, reminders, offers, requests, payments)
--     - so admin and the respective mentors see a clean slate
--   - Self-signup mentors (those linked to a real account: profile_id IS NOT NULL)
--     and their services / availability
--   - All mentee / guest test accounts
--   - webhook_events, ai_events, LangGraph checkpoints
--   Net effect: every real email used during testing (mentee OR mentor) is freed.
--
-- What this PRESERVES (never deleted):
--   - The 14 dummy/seed mentors (profile_id IS NULL) with their services,
--     availability and prices - the fixed test fixtures
--   - All admins and platform_settings
--
-- ============================================================================
-- !!! SAFETY GUARD - DO NOT REMOVE CASUALLY !!!
-- This DELETES every booking, every self-signup mentor, and every mentee/guest
-- account. The dummy/seed mentors and admins are preserved. TEST database ONLY,
-- never production. To run it: (1) take a backup (Supabase Dashboard -> Database
-- -> Backups), then (2) delete the DO $$ ... $$ guard block just below.
-- ============================================================================
DO $$ BEGIN
  RAISE EXCEPTION 'SAFETY STOP: testing_db_reset.sql deletes all bookings, all self-signup mentors, and all mentee/guest accounts (dummy/seed mentors + admins are kept). Back up first, then delete this guard block to proceed. NEVER run on production.';
END $$;

-- 0. Make sure the admin accounts are classified as 'admin' so they are preserved
--    below (a fresh signup defaults to 'candidate'). Idempotent.
UPDATE profiles SET role = 'admin'
  WHERE lower(email) IN ('yokeshmd99@gmail.com', 'immigroovtst@gmail.com');

-- 1. Delete ALL bookings + children FIRST. Every booking in a test DB is a test
--    booking, so clearing them all removes them from admin + mentor views. Doing this
--    first also unblocks the mentor delete below (bookings.mentor_id is ON DELETE
--    RESTRICT). reschedule_offers is removed explicitly in case its FK isn't CASCADE;
--    answers / reminders / requests / payment rows cascade from bookings.
DELETE FROM reschedule_offers;
DELETE FROM bookings;
TRUNCATE webhook_events;

-- 2. Delete self-signup mentors only (linked to a real account). This CASCADES their
--    services, weekly_availability and specific_availability. The dummy/seed mentors
--    have profile_id IS NULL, so they and their sessions/prices are untouched.
DELETE FROM mentors WHERE profile_id IS NOT NULL;

-- 3. Delete every non-admin auth account (mentees, guests, and the just-unlinked
--    self-signup mentor logins), freeing their emails for reuse. Cascades: profiles,
--    chat_threads, consent_log. Seed mentors have no auth account, so they're safe.
DELETE FROM auth.users u
USING profiles p
WHERE p.id = u.id
  AND p.role <> 'admin';

-- 4. Clear LangGraph checkpoint tables (created by AsyncPostgresSaver), if present.
DO $$ BEGIN
  IF to_regclass('public.checkpoints') IS NOT NULL THEN
    TRUNCATE checkpoints, checkpoint_writes, checkpoint_blobs CASCADE;
  END IF;
END $$;

-- 5. Clear the AI observability log.
TRUNCATE ai_events;
