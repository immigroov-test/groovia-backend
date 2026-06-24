-- 014_mentor_extended_fields.sql
-- Adds phone, city, country, social_links (JSONB), public_notes to mentors.
-- Migrates existing linkedin_url / youtube_url / instagram_url into social_links,
-- then drops the fixed columns.

BEGIN;

-- 1. Add new columns
ALTER TABLE mentors
  ADD COLUMN IF NOT EXISTS phone        text,
  ADD COLUMN IF NOT EXISTS city         text,
  ADD COLUMN IF NOT EXISTS country      text,
  ADD COLUMN IF NOT EXISTS social_links jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS public_notes text;

-- 2. Back-fill social_links from fixed URL columns (only where they have values)
UPDATE mentors
SET social_links = (
  SELECT jsonb_agg(entry ORDER BY ord)
  FROM (
    VALUES
      (1, 'linkedin',  linkedin_url),
      (2, 'youtube',   youtube_url),
      (3, 'instagram', instagram_url)
  ) AS t(ord, type, url)
  WHERE url IS NOT NULL AND url <> ''
)
WHERE (linkedin_url IS NOT NULL AND linkedin_url <> '')
   OR (youtube_url  IS NOT NULL AND youtube_url  <> '')
   OR (instagram_url IS NOT NULL AND instagram_url <> '');

-- Ensure no NULL entries leaked (rows that had no social links at all)
UPDATE mentors SET social_links = '[]'::jsonb WHERE social_links IS NULL;

-- 3. Drop obsolete fixed social URL columns
ALTER TABLE mentors
  DROP COLUMN IF EXISTS linkedin_url,
  DROP COLUMN IF EXISTS youtube_url,
  DROP COLUMN IF EXISTS instagram_url;

COMMIT;

-- NOTE: Supabase Storage bucket 'mentor-photos' must be created manually in the
-- Supabase Dashboard (Storage → New bucket → name: mentor-photos, public: true).
-- Or via the Supabase Management API / CLI:
--   supabase storage buckets create mentor-photos --public
