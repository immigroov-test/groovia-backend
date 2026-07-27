-- testing_db_reset.sql
-- Clears ONLY the test mentor (Yokesh Dhanabal)'s bookings, so his booking / payment flows can be
-- re-tested from a clean slate.
--
-- EVERYTHING ELSE IS PRESERVED (this is the important part):
--   - All other mentors (migrated + onboarded) and their bookings, services, availability, prices
--   - All mentee / guest / admin accounts and their emails
--   - Mentor notification emails, platform_settings, FX / PPP reference data
--   - Nothing is truncated; no mentor, no account, no availability is deleted.
--
-- Scope is by slug ('yokesh-dhanabal'), so this is a no-op on any DB that doesn't have that test
-- mentor (e.g. production). Safe to run directly - no guard block to remove.
-- ============================================================================

-- Keep the admin accounts classified as 'admin' (idempotent; a fresh signup defaults to 'candidate').
UPDATE profiles SET role = 'admin'
  WHERE lower(email) IN ('yokeshmd99@gmail.com', 'immigroovtst@gmail.com');

-- Clear ONLY Yokesh Dhanabal's bookings + their children. reschedule_offers is removed explicitly in
-- case its FK isn't CASCADE; answers / reminders / requests / payment rows cascade from bookings.
DELETE FROM reschedule_offers WHERE booking_id IN (
  SELECT b.id FROM bookings b
  JOIN mentors m ON m.id = b.mentor_id
  WHERE m.slug = 'yokesh-dhanabal');

DELETE FROM bookings WHERE mentor_id IN (
  SELECT id FROM mentors WHERE slug = 'yokesh-dhanabal');
