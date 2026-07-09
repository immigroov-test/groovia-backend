-- =============================================================================
-- production_clear_users.sql
-- Removes signed-up MENTEE (candidate) accounts from production, while preserving
-- every mentor account and all of their data.
--
-- DELETES:
--   - auth.users rows for users who are NOT mentors and NOT admins
--       → cascades to profiles, chat_threads, consent_log (FK ON DELETE CASCADE)
--       → any bookings they made: bookings.candidate_id is SET NULL, so the
--         mentor's calendar history stays intact (the row is kept, just unlinked)
--
-- PRESERVES:
--   - Every mentor account (any profile referenced by mentors.profile_id, and any
--     profile with role = 'mentor')
--   - The admin account(s) (role = 'admin', e.g. yokeshmd99@gmail.com)
--   - All mentors, services, weekly_availability, specific_availability, booking
--     rules, reschedule offers, and the platform settings
--
-- SAFETY: this is destructive. Run step 1 (preview) first and eyeball the list,
-- then run step 2 (delete). Both use the exact same WHERE clause.
-- =============================================================================

-- 0) SET ADMIN - promote the admin account BEFORE the delete below, so it is
--    classified as 'admin' and preserved (a fresh signup defaults to 'candidate',
--    which would otherwise be deleted). The admin must have signed up at least once.
--    Idempotent - safe to run every time.
UPDATE profiles SET role = 'admin'
WHERE email = 'yokeshmd99@gmail.com';

-- 1) PREVIEW - who will be deleted. Run this on its own first.
SELECT u.id, u.email, p.role, p.created_at
FROM auth.users u
JOIN profiles p ON p.id = u.id
WHERE p.role NOT IN ('mentor', 'admin')
  AND u.id NOT IN (SELECT profile_id FROM mentors WHERE profile_id IS NOT NULL)
ORDER BY p.created_at;

-- 2) DELETE - removes the mentee accounts. Cascades handle profiles / chats /
--    consent_log; bookings.candidate_id is SET NULL by the FK.
DELETE FROM auth.users u
USING profiles p
WHERE p.id = u.id
  AND p.role NOT IN ('mentor', 'admin')
  AND u.id NOT IN (SELECT profile_id FROM mentors WHERE profile_id IS NOT NULL);

-- 3) OPTIONAL - also delete the now-anonymised bookings those mentees had made
--    (rows whose candidate_id became NULL but still carry a candidate_email).
--    Commented out by default: leave it off to keep mentor calendar history.
-- DELETE FROM bookings
-- WHERE candidate_id IS NULL
--   AND candidate_email IS NOT NULL
--   AND status IN ('cancelled', 'no_show', 'completed');
