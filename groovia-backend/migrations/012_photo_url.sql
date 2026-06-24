-- 012_photo_url.sql
-- Add photo_url column to mentors for profile photo upload (Phase 2 will add upload endpoint).

ALTER TABLE mentors ADD COLUMN IF NOT EXISTS photo_url TEXT;
