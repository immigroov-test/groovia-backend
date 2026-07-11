-- ============================================================================
-- bugfixes_phone_hourly_rate.sql
-- Run this once in the Supabase SQL editor (staging + production).
-- Additive and idempotent (safe to re-run).
--
-- Adds the two columns the July bug-fix batch needs:
--   * mentors.hourly_rate     - the mentor's hourly rate; per-session prices are
--                               prorated from it (rate x duration), editable per
--                               session. Currency reuses the existing
--                               mentors.currency column.
--   * bookings.candidate_phone - the mentee's phone (mandatory at booking, used
--                               for session coordination), prefilled from their
--                               profile when signed in.
--
-- The other bug fixes in this batch are code-only and need no schema change.
-- ============================================================================

ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS hourly_rate NUMERIC;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS candidate_phone TEXT;
