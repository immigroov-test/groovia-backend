-- testing_db_reset.sql
-- Resets the TEST database between test runs so every real email can be reused,
-- while keeping the dummy/seed mentors intact. Do NOT run on production.
--
-- What this DELETES:
--   - ALL bookings + their children: answers, reminders, offers, requests, and every
--     payment row (customer_payments, mentor_payouts, booking_pricing, payment_refunds,
--     booking_ledger) - these all reference bookings ON DELETE CASCADE
--     - so admin and the respective mentors see a clean slate
--   - Payment artifacts with no booking FK: pricing_quotes, payment_events,
--     payment_reconciliation_log (so the same Razorpay test-mode events can be replayed)
--   - Self-signup mentors (those linked to a real account: profile_id IS NOT NULL)
--     and their services / availability
--   - All mentee / guest test accounts
--   - webhook_events, ai_events, LangGraph checkpoints
--   Net effect: every real email used during testing (mentee OR mentor) is freed.
--
-- What this PRESERVES (never deleted):
--   - The dummy/seed mentors (profile_id IS NULL) with their services,
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

-- 6. Clear payment artifacts NOT tied to a booking FK (guarded: skipped if the
--    payments schema hasn't been applied). The per-booking payment, payout,
--    pricing, refund and ledger rows already CASCADE-deleted with the bookings
--    delete in step 1, so a mentee/mentor can rebook + repay cleanly. These three
--    have no booking FK to ride on:
--      - pricing_quotes             : one-time binding price offers
--      - payment_events             : Razorpay webhook dedup log (clearing lets the
--                                     same test-mode events be replayed)
--      - payment_reconciliation_log : mismatch / fetch-failed audit rows
--    KEPT on purpose: fx_rates, fx_refresh_log and ppp_factors are reference data
--    (clearing them would break pricing until the dispatcher re-fetches FX);
--    dispatcher_locks and job_run_history are operational and left as-is.
DO $$ BEGIN
  IF to_regclass('public.pricing_quotes') IS NOT NULL THEN
    TRUNCATE pricing_quotes, payment_events, payment_reconciliation_log;
  END IF;
END $$;
