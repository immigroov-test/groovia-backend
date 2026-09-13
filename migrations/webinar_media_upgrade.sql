-- Add supporting media to webinar installations created before this feature.
-- Safe to run repeatedly in the Supabase SQL editor.
ALTER TABLE public.webinars
  ADD COLUMN IF NOT EXISTS media_url text;

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'webinar-media',
  'webinar-media',
  true,
  52428800,
  ARRAY['image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/webm', 'application/pdf']
)
ON CONFLICT (id) DO UPDATE SET
  public = EXCLUDED.public,
  file_size_limit = EXCLUDED.file_size_limit,
  allowed_mime_types = EXCLUDED.allowed_mime_types;

DROP POLICY IF EXISTS webinar_media_public_read ON storage.objects;
DROP POLICY IF EXISTS webinar_media_admin_insert ON storage.objects;
DROP POLICY IF EXISTS webinar_media_admin_update ON storage.objects;
DROP POLICY IF EXISTS webinar_media_admin_delete ON storage.objects;

CREATE POLICY webinar_media_public_read ON storage.objects
  FOR SELECT USING (bucket_id = 'webinar-media');

CREATE POLICY webinar_media_admin_insert ON storage.objects
  FOR INSERT TO authenticated
  WITH CHECK (
    bucket_id = 'webinar-media'
    AND EXISTS (SELECT 1 FROM public.profiles WHERE id = auth.uid() AND role = 'admin')
  );

CREATE POLICY webinar_media_admin_update ON storage.objects
  FOR UPDATE TO authenticated
  USING (
    bucket_id = 'webinar-media'
    AND EXISTS (SELECT 1 FROM public.profiles WHERE id = auth.uid() AND role = 'admin')
  );

CREATE POLICY webinar_media_admin_delete ON storage.objects
  FOR DELETE TO authenticated
  USING (
    bucket_id = 'webinar-media'
    AND EXISTS (SELECT 1 FROM public.profiles WHERE id = auth.uid() AND role = 'admin')
  );
