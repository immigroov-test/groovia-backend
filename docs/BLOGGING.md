# Blogging deployment

Groovia's contributor studio uses BlockNote in the existing Next.js frontend,
FastAPI for permissions and AI generation, and Supabase for article data and
public media. It does not require a separate CMS or hosting service.

## Database and storage

- New environment: run `migrations/blog_setup.sql`.
- Environment where the original blog migration was already applied: run
  `migrations/blog_rich_editor.sql`.

The migration creates a public `blog-media` bucket with a 5 MB limit and allows
JPG, PNG, WebP, and GIF files. Uploads still go through the contributor-protected
FastAPI endpoint; public access is read-only through the asset URL.

## AI writer

The writer reuses `GROQ_API_KEY`. `BLOG_WRITER_MODEL_NAME` is optional and
defaults to the main Groovia model. The generation endpoint is contributor-only
and rate-limited to five requests per minute.

The model may use only contributor-provided source URLs and fixed Groovia routes.
All returned HTML is allowlist-sanitized, and an admin must approve an article
before it becomes public.

## Public delivery

Published posts are fetched on the server by Next.js, included in the sitemap,
and rendered as complete HTML with Article structured data, canonical metadata,
country links, and tracked CTA links. Drafts are never exposed through public
routes.
