-- testing_db_reset.sql
-- Clears test data from the local testing Supabase project, preserving seed mentors.
-- Run between test sessions. Do NOT run on production.
--
-- What this clears:
--   - All bookings + their children (answers, reminders) via CASCADE
--   - Reschedule offers
--   - Services, availability for test mentor accounts (those created via auth signup)
--   - All auth.users rows (cascades to profiles, chat_threads, consent_log)
--   - Mentor rows created via signup (profile_id becomes NULL after cascade, then deleted)
--   - webhook_events, ai_events, LangGraph checkpoints
--
-- What this PRESERVES:
--   - The 14 seed mentors (inserted in testing_db_setup.sql, profile_id IS NULL)
--   - platform_settings

-- 0. SET ADMIN - promote the admins so they get the 'admin' role and are preserved by the
--    wipe in step 3. Each admin must have signed up at least once. Idempotent.
UPDATE profiles SET role = 'admin'
  WHERE lower(email) IN ('yokeshmd99@gmail.com', 'immigroovtst@gmail.com');

-- 1. Clear booking-related data (CASCADE handles booking_question_answers, booking_reminders)
DELETE FROM reschedule_offers;
DELETE FROM bookings;
TRUNCATE webhook_events;

-- 2. Clear direct-booking system data for test mentor accounts (profile_id IS NOT NULL)
DELETE FROM weekly_availability   WHERE mentor_id IN (SELECT id FROM mentors WHERE profile_id IS NOT NULL);
DELETE FROM specific_availability WHERE mentor_id IN (SELECT id FROM mentors WHERE profile_id IS NOT NULL);
DELETE FROM services              WHERE mentor_id IN (SELECT id FROM mentors WHERE profile_id IS NOT NULL);

-- 3. Wipe test user accounts EXCEPT the admin (CASCADE: profiles → mentor.profile_id SET NULL,
--    chat_threads, consent_log). Keeping the admin avoids re-creating it after every reset.
DELETE FROM auth.users WHERE lower(email) NOT IN ('yokeshmd99@gmail.com', 'immigroovtst@gmail.com');

-- 4. Remove mentor rows whose accounts were just wiped (profile_id was SET NULL by cascade).
--    Seed mentors also have profile_id IS NULL, so exclude them by slug.
DELETE FROM mentors
WHERE profile_id IS NULL
  AND slug NOT IN (
    'maya-singh','priya-mehta','lars-jansen','rohan-kapoor','fatima-rahman',
    'arjun-iyer','sara-okonkwo','daniel-park','aditi-banerjee','james-okafor',
    'vivek-shah','emily-chen','ravi-pillai','sophie-laurent'
  );

-- 4b. Normalize seed mentor contact emails to a plus-addressed inbox, so a fresh signup
--     with your real email (yokeshd1999@gmail.com) is treated as a NEW user and is not
--     auto-linked to a seed mentor such as "Maya Singh". Mail still lands in your inbox.
UPDATE mentors SET email = 'yokeshd1999+seed@gmail.com' WHERE profile_id IS NULL;

-- 4c. Clear any pending profile-change revisions so mentors start with no edit under review.
UPDATE mentors SET pending_changes = NULL, pending_submitted_at = NULL
WHERE pending_changes IS NOT NULL OR pending_submitted_at IS NOT NULL;

-- 5. Clear LangGraph checkpoint tables (created by AsyncPostgresSaver)
DO $$ BEGIN
  IF to_regclass('public.checkpoints') IS NOT NULL THEN
    TRUNCATE checkpoints, checkpoint_writes, checkpoint_blobs CASCADE;
  END IF;
END $$;

-- 6. Clear AI observability log
TRUNCATE ai_events;
