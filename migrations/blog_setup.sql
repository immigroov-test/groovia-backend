-- Editorial content, contributor access, review workflow, and first-party analytics.
CREATE TABLE IF NOT EXISTS content_contributors (
  profile_id uuid PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
  active boolean NOT NULL DEFAULT true,
  created_by uuid REFERENCES profiles(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS blog_posts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text NOT NULL UNIQUE,
  title text NOT NULL CHECK (char_length(title) BETWEEN 4 AND 160),
  excerpt text NOT NULL DEFAULT '' CHECK (char_length(excerpt) <= 400),
  content_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(content_json) = 'array'),
  content_html text NOT NULL DEFAULT '',
  cover_image_url text,
  author_id uuid NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted','changes_requested','published','archived')),
  category text NOT NULL CHECK (category IN ('visas_immigration','jobs_careers','study_abroad','housing','banking_taxes','healthcare','family_relocation','settling_in','culture_lifestyle','stories')),
  country_codes text[] NOT NULL DEFAULT '{}',
  seo_title text CHECK (char_length(seo_title) <= 70),
  seo_description text CHECK (char_length(seo_description) <= 170),
  sources text[] NOT NULL DEFAULT '{}',
  image_prompts jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(image_prompts) = 'array'),
  ctas jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(ctas) = 'array'),
  last_verified_at date,
  review_note text,
  reviewed_by uuid REFERENCES profiles(id) ON DELETE SET NULL,
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS blog_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  post_id uuid NOT NULL REFERENCES blog_posts(id) ON DELETE CASCADE,
  event_type text NOT NULL CHECK (event_type IN ('view','cta_click')),
  cta_kind text CHECK (cta_kind IN ('mentors','webinars','country')),
  occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS blog_posts_public_idx ON blog_posts(status, published_at DESC);
CREATE INDEX IF NOT EXISTS blog_posts_countries_idx ON blog_posts USING gin(country_codes);
CREATE INDEX IF NOT EXISTS blog_events_post_idx ON blog_events(post_id, occurred_at DESC);

ALTER TABLE content_contributors ENABLE ROW LEVEL SECURITY;
ALTER TABLE blog_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE blog_events ENABLE ROW LEVEL SECURITY;

-- All access is mediated by FastAPI using the service role; no browser-direct policies.
REVOKE ALL ON content_contributors, blog_posts, blog_events FROM anon, authenticated;

-- Public article media is uploaded only by the authenticated FastAPI endpoint using
-- the service role. Public reads let search engines and social previews load images.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('blog-media', 'blog-media', true, 5242880, ARRAY['image/jpeg','image/png','image/webp','image/gif'])
ON CONFLICT (id) DO UPDATE SET
  public = EXCLUDED.public,
  file_size_limit = EXCLUDED.file_size_limit,
  allowed_mime_types = EXCLUDED.allowed_mime_types;
