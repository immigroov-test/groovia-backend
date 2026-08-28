-- Run on PRODUCTION **after** these three, in this order:
--     1. migrations/legal_documents_setup.sql
--     2. migrations/legal_documents_seed_content.sql
--     3. migrations/legal_consent_flow_setup.sql
--
-- Split out of PROD_APPLY_pricing_and_pending.sql on purpose. These two functions reference
-- legal_documents and legal_applicable_documents, so on a database without the Legal
-- Documents CMS they fail to create - and because the SQL editor runs a script as ONE
-- transaction, that single failure rolled back the pricing fix along with it. Keeping them
-- in a separate file means the pricing work can never be held hostage to the legal schema.

-- 7. FEAT-030: materiality signal on pending updates, and who to email

-- Expose whether a pending legal update is a MATERIAL change.
--
-- publish() already carries the signal: p_major bumps the major number and resets the
-- minor to 0, so version_minor = 0 marks a material revision and anything else is an
-- editorial one. The distinction matters because the correct response differs. A material
-- change to terms someone is already bound by needs their explicit acceptance before they
-- carry on; a typo fix needs a notice at most. Treating both the same is wrong in both
-- directions: it either nags people over punctuation or lets a real change through on a
-- dismissible toast.
--
-- Added by lookup rather than by widening legal_applicable_documents, so no function
-- signature changes and nothing else that reads it has to move.

CREATE OR REPLACE FUNCTION legal_pending_updates(p_user UUID)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
           'document_id', x.document_id, 'code', x.code, 'slug', x.slug,
           'title', x.title, 'version_id', x.version_id, 'version', x.version,
           'last_updated', x.published_at,
           'is_major', COALESCE((SELECT lv.version_minor = 0
                                   FROM legal_document_versions lv
                                  WHERE lv.id = x.version_id), FALSE)
         ) ORDER BY x.published_at DESC), '[]'::jsonb)
  FROM legal_applicable_documents(p_user) x
  WHERE NOT EXISTS (SELECT 1 FROM user_legal_acknowledgements ack
                     WHERE ack.user_id = p_user AND ack.version_id = x.version_id)
    AND x.published_at > COALESCE((SELECT created_at FROM profiles WHERE id = p_user), NOW());
$$;

CREATE OR REPLACE FUNCTION legal_pending_updates_full(p_user UUID)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
           'document_id', x.document_id, 'code', x.code, 'slug', x.slug,
           'title', x.title, 'summary', x.summary,
           'audience', x.audience, 'audience_label', x.audience_label,
           'version_id', x.version_id, 'version', x.version,
           'last_updated', x.published_at, 'content', x.content,
           'is_major', COALESCE((SELECT lv.version_minor = 0
                                   FROM legal_document_versions lv
                                  WHERE lv.id = x.version_id), FALSE)
         ) ORDER BY x.sort_order, x.code), '[]'::jsonb)
  FROM legal_applicable_documents(p_user) x
  WHERE NOT EXISTS (SELECT 1 FROM user_legal_acknowledgements ack
                     WHERE ack.user_id = p_user AND ack.version_id = x.version_id)
    AND x.published_at > COALESCE((SELECT created_at FROM profiles WHERE id = p_user), NOW());
$$;

-- Who should be told that a legal document changed.
--
-- The inverse of legal_applicable_documents(user): given a document, the people it binds.
-- Audience decides the roles; region_scope decides which of the two Customer T&C editions
-- reaches a given customer, so an Indian customer is never emailed about the Rest-of-World
-- terms and vice versa.
CREATE OR REPLACE FUNCTION legal_document_recipients(p_document_id UUID)
RETURNS TABLE (email TEXT, name TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT p.email, COALESCE(NULLIF(p.display_name, ''), NULLIF(p.full_name, ''), '')
    FROM legal_documents d
    JOIN legal_audiences a ON a.audience = d.audience
    JOIN profiles       p ON p.role = ANY(a.roles)
   WHERE d.id = p_document_id
     AND COALESCE(p.email, '') <> ''
     AND (d.region_scope = 'all'
          OR (d.region_scope = 'in'  AND UPPER(COALESCE(p.country_code, '')) = 'IN')
          OR (d.region_scope = 'row' AND UPPER(COALESCE(p.country_code, '')) <> 'IN'));
$$;
REVOKE ALL ON FUNCTION legal_document_recipients(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION legal_document_recipients(UUID) TO service_role;

-- 8. Terms & Policies UI: reader-facing group labels, and region on the public payload

-- Audience labels as a reader sees them, not as the schema names them.
-- "Everyone" reads like a category in a filing system; "General" reads like a section of a
-- terms page. "Customers" and "Mentors" become "For users" and "For mentors" so the list
-- answers the question a reader is actually asking, which is which of these bind me.
UPDATE legal_audiences SET label = 'General'      WHERE audience = 'everyone';
UPDATE legal_audiences SET label = 'For users'    WHERE audience = 'customers';
UPDATE legal_audiences SET label = 'For mentors'  WHERE audience = 'mentors';

-- Return region_scope with each public document.
--
-- Two of the fourteen are region-specific: a customer gets the India T&C or the
-- Rest-of-World one, never both. Without this column the public page has no way to know
-- that, so it listed both editions to everyone and left the reader to work out which one
-- binds them - which is the one thing a terms page must not make them guess.
CREATE OR REPLACE FUNCTION legal_public_documents()
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
           'document_id', d.id, 'code', d.code, 'slug', d.slug,
           'title', d.title, 'summary', d.summary,
           'audience', d.audience, 'audience_label', a.label,
           'region_scope', d.region_scope,
           'version', v.version, 'last_updated', v.published_at,
           'content', v.content)
         ORDER BY d.sort_order, d.code), '[]'::jsonb)
  FROM legal_documents d
  JOIN legal_audiences a         ON a.audience = d.audience
  JOIN legal_document_versions v ON v.id = d.current_version_id
  WHERE d.is_active AND d.is_public;
$$;
GRANT EXECUTE ON FUNCTION legal_public_documents() TO service_role, authenticated, anon;

-- 9. Scope the date-override delete to its owner (authorization fix)

-- Scope the date-override delete to the mentor who owns it.
--
-- avail_remove_specific(p_id) deleted by id alone, and it is SECURITY DEFINER so it runs
-- past RLS. The endpoint above it checked only that the caller IS a mentor, never that the
-- entry belonged to them, so any mentor holding another mentor's entry id could delete
-- their date override. Ids are UUIDs, which makes it unlikely rather than prevented.
--
-- The single-argument version is dropped, not left alongside: leaving it in place would
-- keep the unscoped path callable and the overload would simply be dead weight.
DROP FUNCTION IF EXISTS avail_remove_specific(UUID);

CREATE OR REPLACE FUNCTION avail_remove_specific(p_id UUID, p_mentor_id UUID)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  DELETE FROM specific_availability
   WHERE id = p_id AND mentor_id = p_mentor_id;
$$;
REVOKE ALL ON FUNCTION avail_remove_specific(UUID, UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION avail_remove_specific(UUID, UUID) TO service_role;
