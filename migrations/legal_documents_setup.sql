-- ============================================================================
-- legal_documents_setup.sql  —  Legal Documents CMS
-- ----------------------------------------------------------------------------
-- Versioned, role-targeted legal documents with an admin CMS, an immutable
-- publication history, and per-user acknowledgement tracking.
--
-- This file is the AUTHORED SOURCE and is safe to run on its own against an
-- EXISTING database (every statement is idempotent). The same schema is folded
-- into production_db_setup.sql / testing_db_setup.sql so a fresh project gets it
-- without running anything extra — keep the three in sync if this changes.
--
-- Depends on objects created by the base migration:
--   profiles, mentors, user_role, log_audit_event().
--
-- Design notes that are easy to get wrong later:
--   * A version row is IMMUTABLE. A trigger blocks UPDATE and DELETE outright,
--     so "never overwrite a published version" is enforced by the database
--     rather than by every caller remembering not to.
--   * The DRAFT lives on legal_documents, never in the version table. That is
--     what makes "Save Draft" structurally incapable of publishing or notifying:
--     there is no version row to notify against until publish_legal_document()
--     creates one.
--   * Targeting is DATA (legal_audiences.roles + legal_documents.region_scope),
--     not code. Re-pointing a document at a different audience is an UPDATE,
--     and no application code changes.
-- ============================================================================

SET check_function_bodies = off;

-- ============================================================================
-- Audiences — which profile roles each audience name covers.
-- ============================================================================
-- The targeting rule lives here as ROWS. 'everyone' lists the roles explicitly
-- rather than meaning "no filter", so an audience can never accidentally widen
-- to a role that should not receive a document (a future 'partner' role, say).
CREATE TABLE IF NOT EXISTS legal_audiences (
  audience    TEXT PRIMARY KEY,
  label       TEXT NOT NULL,
  roles       user_role[] NOT NULL,
  sort_order  INTEGER NOT NULL DEFAULT 0
);

INSERT INTO legal_audiences (audience, label, roles, sort_order) VALUES
  ('everyone',  'Everyone',  ARRAY['candidate','mentor','admin','guest']::user_role[], 1),
  ('customers', 'Customers', ARRAY['candidate','guest']::user_role[],                  2),
  ('mentors',   'Mentors',   ARRAY['mentor']::user_role[],                             3)
ON CONFLICT (audience) DO NOTHING;

-- ============================================================================
-- legal_documents — the catalogue. One row per document, 14 of them.
-- ============================================================================
CREATE TABLE IF NOT EXISTS legal_documents (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code               TEXT NOT NULL UNIQUE,      -- '01'..'14', the reference number in the doc set
  slug               TEXT NOT NULL UNIQUE,      -- URL segment: /legal/privacy-policy
  title              TEXT NOT NULL,
  summary            TEXT,                      -- one line, shown under the title on the user page
  audience           TEXT NOT NULL REFERENCES legal_audiences(audience),
  -- 'all' | 'in' | 'row'. Only 08/09 use anything but 'all': a customer sees the
  -- India T&C or the Rest-of-World one, never both.
  region_scope       TEXT NOT NULL DEFAULT 'all' CHECK (region_scope IN ('all', 'in', 'row')),
  sort_order         INTEGER NOT NULL DEFAULT 0,
  is_active          BOOLEAN NOT NULL DEFAULT TRUE,
  -- Readable WITHOUT signing in. Separate from `audience` on purpose: "applies to
  -- everyone" and "may be read by a stranger" are different questions. A visitor has
  -- to be able to read the Privacy Policy before they have an account - the cookie
  -- banner links to it on first visit - while the mentor and customer contracts stay
  -- behind sign-in. The Placement Guide's site-wide footer documents are the public set.
  is_public          BOOLEAN NOT NULL DEFAULT FALSE,

  -- The live version. NULL until the document is published for the first time.
  current_version_id UUID,                      -- FK added after the versions table exists

  -- The working copy. Never seen by users, never notified on, replaced freely.
  draft_content      TEXT,
  draft_updated_at   TIMESTAMPTZ,
  draft_updated_by   UUID REFERENCES profiles(id) ON DELETE SET NULL,

  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_legal_documents_audience
  ON legal_documents(audience, region_scope) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_legal_documents_sort
  ON legal_documents(sort_order);

-- Added to the CREATE TABLE above, but that is a no-op on a database where the table
-- already exists, so it is also applied explicitly here.
ALTER TABLE legal_documents ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE;

-- ============================================================================
-- legal_document_versions — the immutable publication history.
-- ============================================================================
CREATE TABLE IF NOT EXISTS legal_document_versions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id   UUID NOT NULL REFERENCES legal_documents(id) ON DELETE CASCADE,
  version_major INTEGER NOT NULL,
  version_minor INTEGER NOT NULL,
  version       TEXT NOT NULL,                  -- 'v1.0' — kept for display/export
  content       TEXT NOT NULL,
  change_note   TEXT,                           -- what changed, for the history list
  published_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published_by  UUID REFERENCES profiles(id) ON DELETE SET NULL,
  -- Denormalised on purpose: the audit trail has to stay readable after an admin
  -- account is deleted, and published_by would go NULL.
  published_by_email TEXT,
  published_by_name  TEXT,
  -- Targeting AS IT WAS at publication. The live rule can be re-pointed later;
  -- this records who the document was aimed at when it went out.
  audience      TEXT NOT NULL,
  region_scope  TEXT NOT NULL,
  UNIQUE (document_id, version_major, version_minor)
);

CREATE INDEX IF NOT EXISTS idx_legal_versions_doc
  ON legal_document_versions(document_id, version_major DESC, version_minor DESC);

-- The circular reference, added once both tables exist.
DO $$ BEGIN
  ALTER TABLE legal_documents
    ADD CONSTRAINT legal_documents_current_version_fk
    FOREIGN KEY (current_version_id) REFERENCES legal_document_versions(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Immutability ─────────────────────────────────────────────────────────────
-- "Never overwrite a published version" enforced where it cannot be bypassed by
-- a careless UPDATE, a PostgREST call, or a future code path that forgets.
CREATE OR REPLACE FUNCTION legal_versions_are_immutable() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Published legal versions are immutable (attempted % on version %)',
    TG_OP, COALESCE(OLD.version, '?')
    USING errcode = 'P0001';
END $$;

DROP TRIGGER IF EXISTS trg_legal_versions_immutable ON legal_document_versions;
CREATE TRIGGER trg_legal_versions_immutable
  BEFORE UPDATE OR DELETE ON legal_document_versions
  FOR EACH ROW EXECUTE FUNCTION legal_versions_are_immutable();

-- Worth being explicit about: this trigger also defeats the ON DELETE CASCADE above.
-- Deleting a legal_documents row would cascade into its versions, the trigger would
-- reject that, and the whole delete fails. That is the intended outcome — a document
-- with published history cannot be made to disappear, and destroying the evidence of
-- what users agreed to is precisely what must not be possible from the application.
-- To take a document out of circulation, set legal_documents.is_active = FALSE: it
-- stops being served to anyone while its history stays intact.

-- ============================================================================
-- user_legal_acknowledgements — who has read which exact version.
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_legal_acknowledgements (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  document_id     UUID NOT NULL REFERENCES legal_documents(id) ON DELETE CASCADE,
  version_id      UUID NOT NULL REFERENCES legal_document_versions(id) ON DELETE CASCADE,
  acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- One acknowledgement per user per VERSION (not per document): acknowledging
  -- v1.2 must not silently count as acknowledging v1.3.
  UNIQUE (user_id, version_id)
);

CREATE INDEX IF NOT EXISTS idx_legal_ack_user
  ON user_legal_acknowledgements(user_id, document_id);

-- ============================================================================
-- Row level security
-- ============================================================================
-- Reads normally arrive through the backend on the service role, which bypasses
-- RLS. These policies matter for anything reaching PostgREST with the anon key
-- straight from the browser: published content is public, drafts are not.
ALTER TABLE legal_audiences              ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_documents              ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_document_versions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_legal_acknowledgements  ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY legal_audiences_read ON legal_audiences FOR SELECT USING (TRUE);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Deliberately NO select policy on legal_documents: the row carries draft_content,
-- which is unpublished text nobody outside the admin CMS may read. Published content
-- is served from legal_document_versions and through the RPCs below.
DO $$ BEGIN
  CREATE POLICY legal_versions_read ON legal_document_versions FOR SELECT USING (TRUE);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE POLICY legal_ack_self_read ON user_legal_acknowledgements
    FOR SELECT USING (user_id = auth.uid());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================================================
-- Applicability — one definition of "does this document apply to this user",
-- used by the user page, the pending-updates check, and the admin preview.
-- ============================================================================
-- Region is taken from the user's PROFILE country, not from a request header:
-- which contract a customer is under should not change because they opened the
-- site from an airport in another country.
CREATE OR REPLACE FUNCTION legal_user_region(p_user UUID)
RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT CASE WHEN UPPER(COALESCE(country_code, '')) = 'IN' THEN 'in' ELSE 'row' END
  FROM profiles WHERE id = p_user;
$$;

-- Effective role for targeting. profiles.role is authoritative, but a mentors row
-- wins over a stale 'candidate' role so an approved mentor can never miss a
-- mentor document because their profile row lagged behind.
CREATE OR REPLACE FUNCTION legal_user_role(p_user UUID)
RETURNS user_role LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT CASE
           WHEN EXISTS (SELECT 1 FROM mentors m WHERE m.profile_id = p_user) THEN 'mentor'::user_role
           ELSE COALESCE((SELECT role FROM profiles WHERE id = p_user), 'candidate'::user_role)
         END;
$$;

-- Every ACTIVE, PUBLISHED document that applies to this user, newest version only.
CREATE OR REPLACE FUNCTION legal_applicable_documents(p_user UUID)
RETURNS TABLE (
  document_id UUID, code TEXT, slug TEXT, title TEXT, summary TEXT,
  audience TEXT, audience_label TEXT, region_scope TEXT, sort_order INTEGER,
  version_id UUID, version TEXT, published_at TIMESTAMPTZ, content TEXT
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT d.id, d.code, d.slug, d.title, d.summary,
         d.audience, a.label, d.region_scope, d.sort_order,
         v.id, v.version, v.published_at, v.content
  FROM legal_documents d
  JOIN legal_audiences a          ON a.audience = d.audience
  JOIN legal_document_versions v  ON v.id = d.current_version_id
  WHERE d.is_active
    AND legal_user_role(p_user) = ANY (a.roles)
    AND (d.region_scope = 'all' OR d.region_scope = legal_user_region(p_user))
  ORDER BY d.sort_order, d.code;
$$;

-- ============================================================================
-- Admin: read
-- ============================================================================
-- The CMS table. Draft status is derived, never stored as a flag that could drift
-- out of step with whether draft_content actually holds anything.
CREATE OR REPLACE FUNCTION legal_admin_documents()
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.sort_order, x.code), '[]'::jsonb) FROM (
    SELECT d.id, d.code, d.slug, d.title, d.summary,
           d.audience, a.label AS audience_label, d.region_scope,
           d.sort_order, d.is_active,
           v.id           AS current_version_id,
           v.version      AS current_version,
           v.published_at AS last_updated,
           COALESCE(v.published_by_name, v.published_by_email) AS last_published_by,
           (d.draft_content IS NOT NULL AND LENGTH(TRIM(d.draft_content)) > 0) AS has_draft,
           d.draft_updated_at,
           (SELECT COUNT(*) FROM legal_document_versions vv WHERE vv.document_id = d.id) AS version_count
    FROM legal_documents d
    JOIN legal_audiences a ON a.audience = d.audience
    LEFT JOIN legal_document_versions v ON v.id = d.current_version_id
  ) x;
$$;

-- One document for the editor: the text to edit (draft if there is one, else the
-- live version), the live version, and the full history.
CREATE OR REPLACE FUNCTION legal_admin_document(p_document_id UUID)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT jsonb_build_object(
    'id', d.id, 'code', d.code, 'slug', d.slug, 'title', d.title, 'summary', d.summary,
    'audience', d.audience, 'audience_label', a.label, 'region_scope', d.region_scope,
    'is_active', d.is_active,
    'current_version_id', v.id,
    'current_version', v.version,
    'last_updated', v.published_at,
    'published_content', v.content,
    'has_draft', (d.draft_content IS NOT NULL AND LENGTH(TRIM(d.draft_content)) > 0),
    'draft_updated_at', d.draft_updated_at,
    'editor_content', COALESCE(NULLIF(d.draft_content, ''), v.content, ''),
    'history', COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
               'id', h.id, 'version', h.version, 'published_at', h.published_at,
               'published_by', COALESCE(h.published_by_name, h.published_by_email, 'system'),
               'change_note', h.change_note,
               'is_current', (h.id = d.current_version_id))
             ORDER BY h.version_major DESC, h.version_minor DESC)
      FROM legal_document_versions h WHERE h.document_id = d.id), '[]'::jsonb)
  )
  FROM legal_documents d
  JOIN legal_audiences a ON a.audience = d.audience
  LEFT JOIN legal_document_versions v ON v.id = d.current_version_id
  WHERE d.id = p_document_id;
$$;

-- A single historical version, read-only.
CREATE OR REPLACE FUNCTION legal_admin_version(p_version_id UUID)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT jsonb_build_object(
    'id', v.id, 'document_id', v.document_id, 'document_title', d.title,
    'version', v.version, 'content', v.content, 'change_note', v.change_note,
    'published_at', v.published_at,
    'published_by', COALESCE(v.published_by_name, v.published_by_email, 'system'),
    'audience', v.audience, 'region_scope', v.region_scope,
    'is_current', (v.id = d.current_version_id))
  FROM legal_document_versions v
  JOIN legal_documents d ON d.id = v.document_id
  WHERE v.id = p_version_id;
$$;

-- ============================================================================
-- Admin: write
-- ============================================================================
-- Save Draft. Touches ONLY the draft columns — no version row, no current_version_id
-- change, no last-updated change, and therefore nothing that could notify anyone.
CREATE OR REPLACE FUNCTION legal_save_draft(
  p_document_id UUID, p_actor UUID, p_content TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE d legal_documents;
BEGIN
  SELECT * INTO d FROM legal_documents WHERE id = p_document_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Legal document not found' USING errcode = 'P0001';
  END IF;

  UPDATE legal_documents
     SET draft_content    = p_content,
         draft_updated_at = NOW(),
         draft_updated_by = p_actor,
         updated_at       = NOW()
   WHERE id = p_document_id;

  PERFORM log_audit_event('legal_document', p_document_id, NULL, 'draft_saved',
    'Draft saved for ' || d.title,
    jsonb_build_object('code', d.code, 'chars', LENGTH(COALESCE(p_content, ''))));

  RETURN jsonb_build_object('ok', TRUE, 'draft_updated_at', NOW());
END $$;

-- Discard the draft and go back to the published text.
CREATE OR REPLACE FUNCTION legal_discard_draft(p_document_id UUID, p_actor UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE d legal_documents;
BEGIN
  SELECT * INTO d FROM legal_documents WHERE id = p_document_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Legal document not found' USING errcode = 'P0001';
  END IF;

  UPDATE legal_documents
     SET draft_content = NULL, draft_updated_at = NULL, draft_updated_by = NULL, updated_at = NOW()
   WHERE id = p_document_id;

  PERFORM log_audit_event('legal_document', p_document_id, NULL, 'draft_discarded',
    'Draft discarded for ' || d.title, jsonb_build_object('code', d.code));

  RETURN jsonb_build_object('ok', TRUE);
END $$;

-- Publish Official Update.
--
-- Transactional by construction: a function body is one transaction, so the version
-- INSERT, the current_version_id UPDATE and the draft clear either all land or none
-- do. A half-published document — a version row that nothing points at, or a
-- current_version_id aimed at a row that was rolled back — cannot be observed.
--
-- Notification eligibility is DERIVED from those same rows rather than written into
-- a queue here, which is what keeps it consistent: there is no separate notification
-- state to get out of step with the version that was actually published.
CREATE OR REPLACE FUNCTION publish_legal_document(
  p_document_id UUID,
  p_actor       UUID,
  p_change_note TEXT DEFAULT NULL,
  p_major       BOOLEAN DEFAULT FALSE
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  d       legal_documents;
  cur     legal_document_versions;
  v_draft TEXT;
  v_major INTEGER;
  v_minor INTEGER;
  v_label TEXT;
  v_email TEXT;
  v_name  TEXT;
  v_new   legal_document_versions;
BEGIN
  -- FOR UPDATE serialises two admins hitting Publish at the same moment: the second
  -- waits, then re-reads and finds its draft already published (or gone) instead of
  -- racing to create a duplicate version.
  SELECT * INTO d FROM legal_documents WHERE id = p_document_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Legal document not found' USING errcode = 'P0001';
  END IF;

  v_draft := NULLIF(TRIM(COALESCE(d.draft_content, '')), '');
  IF v_draft IS NULL THEN
    RAISE EXCEPTION 'There is no draft to publish. Edit the document and save a draft first.'
      USING errcode = 'P0001';
  END IF;

  SELECT * INTO cur FROM legal_document_versions WHERE id = d.current_version_id;

  -- Accidental duplicate publishing: republishing byte-identical text would mint a
  -- new version and notify every applicable user about a document that did not change.
  IF cur.id IS NOT NULL AND TRIM(cur.content) = TRIM(d.draft_content) THEN
    RAISE EXCEPTION 'This draft is identical to the published % — nothing to publish.', cur.version
      USING errcode = 'P0001';
  END IF;

  IF cur.id IS NULL THEN
    v_major := 1; v_minor := 0;
  ELSIF p_major THEN
    v_major := cur.version_major + 1; v_minor := 0;
  ELSE
    v_major := cur.version_major; v_minor := cur.version_minor + 1;
  END IF;
  v_label := 'v' || v_major || '.' || v_minor;

  SELECT email, COALESCE(NULLIF(TRIM(display_name), ''), NULLIF(TRIM(full_name), ''))
    INTO v_email, v_name
    FROM profiles WHERE id = p_actor;

  INSERT INTO legal_document_versions (
    document_id, version_major, version_minor, version, content, change_note,
    published_by, published_by_email, published_by_name, audience, region_scope)
  VALUES (
    d.id, v_major, v_minor, v_label, d.draft_content, NULLIF(TRIM(COALESCE(p_change_note, '')), ''),
    p_actor, v_email, v_name, d.audience, d.region_scope)
  RETURNING * INTO v_new;

  UPDATE legal_documents
     SET current_version_id = v_new.id,
         draft_content      = NULL,     -- the draft became the version; keeping it
         draft_updated_at   = NULL,     -- would show a phantom "unpublished changes"
         draft_updated_by   = NULL,
         updated_at         = NOW()
   WHERE id = d.id;

  PERFORM log_audit_event('legal_document', d.id, NULL, 'published',
    d.title || ' published as ' || v_label,
    jsonb_build_object('code', d.code, 'version', v_label,
                       'previous_version', cur.version, 'audience', d.audience,
                       'region_scope', d.region_scope, 'change_note', p_change_note,
                       'published_by', COALESCE(v_name, v_email)));

  RETURN jsonb_build_object(
    'ok', TRUE, 'version_id', v_new.id, 'version', v_label,
    'published_at', v_new.published_at,
    'published_by', COALESCE(v_name, v_email, 'system'),
    'previous_version', cur.version);
END $$;

-- ============================================================================
-- User: read, pending updates, acknowledge
-- ============================================================================
-- The user-facing Legal Documents page: latest applicable version of each document,
-- plus whether this user has acknowledged it.
CREATE OR REPLACE FUNCTION legal_user_documents(p_user UUID)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
           'document_id', x.document_id, 'code', x.code, 'slug', x.slug,
           'title', x.title, 'summary', x.summary,
           'audience', x.audience, 'audience_label', x.audience_label,
           'version_id', x.version_id, 'version', x.version,
           'last_updated', x.published_at, 'content', x.content,
           'acknowledged', EXISTS (SELECT 1 FROM user_legal_acknowledgements ack
                                    WHERE ack.user_id = p_user AND ack.version_id = x.version_id)
         ) ORDER BY x.sort_order, x.code), '[]'::jsonb)
  FROM legal_applicable_documents(p_user) x;
$$;

-- Documents this user should be shown the "Legal document updated" notice for.
--
-- Two conditions, both necessary:
--   1. They have not acknowledged the CURRENT version. Acknowledging v1.2 does not
--      cover v1.3, so a later publication brings the notice back on its own.
--   2. The current version was published AFTER their account existed. Without this
--      every new signup would be met with ten "updated" notices for documents they
--      have never seen and which did not change — the notice announces an UPDATE,
--      and for a new account there has not been one. Acceptance of the terms in force
--      at signup is captured by the signup/onboarding flow, not by this notice.
CREATE OR REPLACE FUNCTION legal_pending_updates(p_user UUID)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
           'document_id', x.document_id, 'code', x.code, 'slug', x.slug,
           'title', x.title, 'version_id', x.version_id, 'version', x.version,
           'last_updated', x.published_at
         ) ORDER BY x.published_at DESC), '[]'::jsonb)
  FROM legal_applicable_documents(p_user) x
  WHERE NOT EXISTS (SELECT 1 FROM user_legal_acknowledgements ack
                     WHERE ack.user_id = p_user AND ack.version_id = x.version_id)
    AND x.published_at > COALESCE((SELECT created_at FROM profiles WHERE id = p_user), NOW());
$$;

-- Record that a user has reviewed a specific version.
-- Idempotent: clicking twice, or on two devices, is one row.
CREATE OR REPLACE FUNCTION legal_acknowledge(p_user UUID, p_version_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v legal_document_versions;
BEGIN
  SELECT * INTO v FROM legal_document_versions WHERE id = p_version_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Legal document version not found' USING errcode = 'P0001';
  END IF;

  -- Only the version that is CURRENTLY live can be acknowledged. Acknowledging an
  -- archived version would otherwise leave the user still owing the live one while
  -- looking, in the table, as though they had responded.
  IF NOT EXISTS (SELECT 1 FROM legal_documents d
                  WHERE d.id = v.document_id AND d.current_version_id = v.id) THEN
    RAISE EXCEPTION 'That version is no longer the current one. Please review the latest version.'
      USING errcode = 'P0001';
  END IF;

  INSERT INTO user_legal_acknowledgements (user_id, document_id, version_id)
  VALUES (p_user, v.document_id, v.id)
  ON CONFLICT (user_id, version_id) DO NOTHING;

  RETURN jsonb_build_object('ok', TRUE, 'document_id', v.document_id, 'version', v.version);
END $$;

-- One document by slug, for the read-and-acknowledge page. Returns NULL when the
-- document does not apply to this user, so the route can 404 rather than show a
-- customer the Mentor Agreement.
CREATE OR REPLACE FUNCTION legal_user_document(p_user UUID, p_slug TEXT)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT jsonb_build_object(
           'document_id', x.document_id, 'code', x.code, 'slug', x.slug,
           'title', x.title, 'summary', x.summary,
           'audience', x.audience, 'audience_label', x.audience_label,
           'version_id', x.version_id, 'version', x.version,
           'last_updated', x.published_at, 'content', x.content,
           'acknowledged', EXISTS (SELECT 1 FROM user_legal_acknowledgements ack
                                    WHERE ack.user_id = p_user AND ack.version_id = x.version_id))
  FROM legal_applicable_documents(p_user) x
  WHERE x.slug = p_slug;
$$;

-- ============================================================================
-- Public: no sign-in
-- ============================================================================
-- Backs the public legal page, which is linked from the cookie banner and the
-- signup form and is indexed by search engines - so it must render for someone
-- who has no account and never will.
--
-- The is_public gate is the whole security boundary here: this is the one legal
-- read path with no caller identity behind it, so it must never be able to return
-- a document aimed at a role. A slug that is not public returns NULL, which the
-- route turns into a 404 - the same answer as a slug that does not exist.
CREATE OR REPLACE FUNCTION legal_public_document(p_slug TEXT)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT jsonb_build_object(
           'document_id', d.id, 'code', d.code, 'slug', d.slug,
           'title', d.title, 'summary', d.summary,
           'version', v.version, 'last_updated', v.published_at,
           'content', v.content)
  FROM legal_documents d
  JOIN legal_document_versions v ON v.id = d.current_version_id
  WHERE d.slug = p_slug AND d.is_active AND d.is_public;
$$;

-- Every publicly readable document, in catalogue order, with its published content.
-- Backs the single public legal page. Returns full content rather than summaries
-- because that page renders all fourteen in place, and fourteen round-trips to open
-- sections would be slower than the one payload it already holds.
CREATE OR REPLACE FUNCTION legal_public_documents()
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
           'document_id', d.id, 'code', d.code, 'slug', d.slug,
           'title', d.title, 'summary', d.summary,
           'audience', d.audience, 'audience_label', a.label,
           'version', v.version, 'last_updated', v.published_at,
           'content', v.content)
         ORDER BY d.sort_order, d.code), '[]'::jsonb)
  FROM legal_documents d
  JOIN legal_audiences a         ON a.audience = d.audience
  JOIN legal_document_versions v ON v.id = d.current_version_id
  WHERE d.is_active AND d.is_public;
$$;

-- ============================================================================
-- Grants
-- ============================================================================
-- Everything is reached through the backend on the service role. authenticated is
-- granted only the read paths a signed-in user drives themselves, plus the
-- acknowledge write; no admin function is exposed to it.
REVOKE ALL ON FUNCTION legal_admin_documents()            FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION legal_admin_document(UUID)         FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION legal_admin_version(UUID)          FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION legal_save_draft(UUID, UUID, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION legal_discard_draft(UUID, UUID)    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION publish_legal_document(UUID, UUID, TEXT, BOOLEAN) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION legal_admin_documents()            TO service_role;
GRANT EXECUTE ON FUNCTION legal_admin_document(UUID)         TO service_role;
GRANT EXECUTE ON FUNCTION legal_admin_version(UUID)          TO service_role;
GRANT EXECUTE ON FUNCTION legal_save_draft(UUID, UUID, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION legal_discard_draft(UUID, UUID)    TO service_role;
GRANT EXECUTE ON FUNCTION publish_legal_document(UUID, UUID, TEXT, BOOLEAN) TO service_role;

-- Every function that takes a user id is service_role ONLY, and the REVOKEs are not
-- decorative: PostgreSQL grants EXECUTE to PUBLIC on a new function by default, so
-- without them any signed-in caller could reach PostgREST and pass somebody else's
-- UUID.
--
-- anon and authenticated are named explicitly, not left to PUBLIC. An earlier revision
-- of this file GRANTed these to authenticated directly, and a grant made to a named
-- role is not removed by revoking from PUBLIC - so a database that ran that revision
-- would keep the hole open through a re-run. Naming the roles is what makes re-running
-- this file actually repair it. These are all SECURITY DEFINER, so that would bypass RLS as well - reading
-- another person's role and country from legal_user_region/legal_user_role, or worse,
-- calling legal_acknowledge on their behalf and corrupting the record of who agreed
-- to what. The identity always comes from the backend's verified JWT, never from a
-- parameter a caller chose.
--
-- The helpers are revoked too. They are only ever called from inside the functions
-- above, which run as the definer and so do not need a grant of their own.
REVOKE ALL ON FUNCTION legal_user_documents(UUID)      FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION legal_user_document(UUID, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION legal_pending_updates(UUID)     FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION legal_acknowledge(UUID, UUID)   FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION legal_applicable_documents(UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION legal_user_region(UUID)          FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION legal_user_role(UUID)            FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION legal_user_documents(UUID)      TO service_role;
GRANT EXECUTE ON FUNCTION legal_user_document(UUID, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION legal_pending_updates(UUID)     TO service_role;
GRANT EXECUTE ON FUNCTION legal_acknowledge(UUID, UUID)   TO service_role;
GRANT EXECUTE ON FUNCTION legal_applicable_documents(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION legal_user_region(UUID)          TO service_role;
GRANT EXECUTE ON FUNCTION legal_user_role(UUID)            TO service_role;

-- The two public reads take no user identity and return only published, is_public
-- content, so anon may call them directly.
GRANT EXECUTE ON FUNCTION legal_public_documents()    TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION legal_public_document(TEXT) TO service_role, authenticated, anon;

-- ============================================================================
-- Catalogue seed — the 14 documents.
-- ============================================================================
-- Metadata only. The TEXT of each document is loaded by
-- scripts/seed_legal_documents.py, which publishes v1.0 from the .md files in
-- content/legal/ — keeping a 6,000-word contract out of a schema file.
--
-- ON CONFLICT updates the targeting columns but never touches content, drafts or
-- current_version_id, so re-running this script re-asserts the intended audience
-- without disturbing anything that has been published.
INSERT INTO legal_documents (code, slug, title, summary, audience, region_scope, sort_order) VALUES
  ('01', 'website-terms-of-use',        'Website Terms of Use',
   'The rules for using the Immigroov website.', 'everyone', 'all', 1),
  ('02', 'cookie-policy',               'Cookie Policy',
   'What we store on your device, and why.', 'everyone', 'all', 2),
  ('03', 'refund-cancellation-policy',  'Refund & Cancellation Policy',
   'Cancelling, rescheduling, refunds and mentor no-shows.', 'customers', 'all', 3),
  ('04', 'data-subject-rights',         'Data Subject Rights',
   'Your rights over your data, and how to exercise them.', 'everyone', 'all', 4),
  ('05', 'privacy-policy',              'Privacy Policy',
   'What we collect, why, and who controls it.', 'everyone', 'all', 5),
  ('06', 'ai-disclosure-notice',        'AI Disclosure Notice',
   'Groovia is an AI system. What that means for you.', 'everyone', 'all', 6),
  ('07', 'groovia-ai-terms',            'Groovia AI Terms',
   'Terms for using the Groovia AI assistant.', 'everyone', 'all', 7),
  ('08', 'customer-terms-india',        'Customer T&C - India',
   'Your contract with Immigroov Consulting India LLP.', 'customers', 'in', 8),
  ('09', 'customer-terms-row',          'Customer T&C - Rest of World',
   'Your contract with Immigroov Consulting VOF.', 'customers', 'row', 9),
  ('10', 'payment-terms',               'Payment Terms',
   'Pricing, currency, payment processing and refunds.', 'customers', 'all', 10),
  ('11', 'mentor-agreement',            'Mentor Agreement',
   'Your contract as an Immigroov mentor.', 'mentors', 'all', 11),
  ('12', 'mentor-commission-payout',    'Mentor Commission & Payout Terms',
   'Commission, payout schedule and when a session counts as complete.', 'mentors', 'all', 12),
  ('13', 'mentor-code-of-conduct',      'Mentor Code of Conduct',
   'The standards every mentor agrees to uphold.', 'mentors', 'all', 13),
  ('14', 'mentor-data-processing',      'Mentor Data Processing Addendum',
   'Handling customer data during and after a session.', 'mentors', 'all', 14)
ON CONFLICT (code) DO UPDATE SET
  slug         = EXCLUDED.slug,
  title        = EXCLUDED.title,
  summary      = EXCLUDED.summary,
  audience     = EXCLUDED.audience,
  region_scope = EXCLUDED.region_scope,
  sort_order   = EXCLUDED.sort_order,
  updated_at   = NOW();

-- Every document is readable without an account. These are published terms, not
-- secrets - a customer should be able to read the agreement their mentor works under -
-- and it is what makes a single public page listing all fourteen possible.
--
-- Note this is about READING. Targeting still governs everything that matters: which
-- documents a user is asked to acknowledge, and who is notified when one changes.
-- is_public stays a per-document switch so one can be pulled from public view without
-- touching its audience.
--
-- Re-asserted on every run, like the audience columns above, so this file remains the
-- statement of intended defaults. To take a document out of public view, set
-- is_public = FALSE after running this.
UPDATE legal_documents SET is_public = TRUE;
