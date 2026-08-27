-- ============================================================================
-- legal_consent_flow_setup.sql  —  Active-consent capture (Consent Flow Spec)
-- ----------------------------------------------------------------------------
-- Implements the "Immigroov — Legal Document Placement & Consent Flow Spec":
-- one central table for every ACTIVE consent event (signup, guest checkout,
-- mentor onboarding, the Groovia AI Terms gate, the cookie banner), plus the
-- intake ticket for Data Subject Rights requests.
--
-- This file is the AUTHORED SOURCE and is safe to run on its own against an
-- EXISTING database (every statement is idempotent). The same schema is folded
-- into production_db_setup.sql / testing_db_setup.sql for a fresh install —
-- keep the three in sync. Depends on objects from legal_documents_setup.sql
-- (legal_documents, legal_document_versions) and the base migration (profiles,
-- bookings, log_audit_event()).
--
-- Design notes:
--   * legal_consent_events is keyed to a REAL legal_document_versions row
--     (version_id), resolved server-side at write time — never a client-
--     supplied version string. The spec's "document_type"/"document_version"
--     free-text fields are kept too, as an immutable snapshot taken at the
--     same moment, so an audit query never needs to join back through a
--     version history that itself keeps changing.
--   * Three identity anchors (user_id / session_id / booking_id), matching the
--     spec exactly: a guest has neither a user_id nor (before checkout) a
--     booking_id, so session_id is not optional decoration — it is the only
--     way a guest's Groovia AI Terms acceptance, or a guest's cookie-banner
--     choice, can be recorded at all.
--   * One write path (record_legal_consent) for every trigger point, so "one
--     click, N documents" (a bundle) always produces N rows the same way
--     legal_acknowledge_all already does for the CMS's re-review flow. This
--     table records INITIAL/transactional consent; user_legal_acknowledgements
--     records "re-reviewed after a later update" — genuinely different events,
--     both needed, neither a duplicate of the other.
-- ============================================================================

SET check_function_bodies = off;

-- ============================================================================
-- legal_consent_events — the central active-consent log.
-- ============================================================================
CREATE TABLE IF NOT EXISTS legal_consent_events (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id        UUID NOT NULL REFERENCES legal_document_versions(id),
  -- Snapshot at consent time, not a live join - what was agreed to must never
  -- appear to change because a later publish moved current_version_id elsewhere.
  document_code     TEXT NOT NULL,
  document_slug     TEXT NOT NULL,
  document_version  TEXT NOT NULL,

  user_id           UUID REFERENCES profiles(id) ON DELETE SET NULL,
  session_id        TEXT,                   -- guest identity before a booking exists
  booking_id        UUID REFERENCES bookings(id) ON DELETE SET NULL,

  consent_method    TEXT NOT NULL,           -- 'checkbox_signup' | 'checkbox_guest_checkout' |
                                              -- 'checkbox_mentor_onboarding_1' | '..._2' |
                                              -- 'modal_groovia_ai_terms' | 'cookie_banner'
  ip_address        INET,
  user_agent        TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CHECK (user_id IS NOT NULL OR session_id IS NOT NULL OR booking_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_legal_consent_events_user
  ON legal_consent_events(user_id, document_slug) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_legal_consent_events_session
  ON legal_consent_events(session_id, document_slug) WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_legal_consent_events_booking
  ON legal_consent_events(booking_id) WHERE booking_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_legal_consent_events_slug_created
  ON legal_consent_events(document_slug, created_at DESC);

ALTER TABLE legal_consent_events ENABLE ROW LEVEL SECURITY;
-- No SELECT policy: this is an audit/evidentiary table, read by admins and the
-- backend (service role) only. A user does not need to query their own consent
-- history through PostgREST; the applications that need it (support, disputes)
-- go through the backend.

-- Immutable, like legal_document_versions: a consent record is exactly the
-- kind of row that must never be edited after the fact - its entire purpose is
-- to prove what happened at a point in time.
CREATE OR REPLACE FUNCTION legal_consent_events_are_immutable() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Consent records are immutable (attempted % on legal_consent_events row %)',
    TG_OP, COALESCE(OLD.id::text, '?')
    USING errcode = 'P0001';
END $$;

DROP TRIGGER IF EXISTS trg_legal_consent_events_immutable ON legal_consent_events;
CREATE TRIGGER trg_legal_consent_events_immutable
  BEFORE UPDATE OR DELETE ON legal_consent_events
  FOR EACH ROW EXECUTE FUNCTION legal_consent_events_are_immutable();

-- ============================================================================
-- record_legal_consent — the one write path every trigger point calls.
-- ============================================================================
-- Resolves each slug to its CURRENT version_id server-side (never trusts a
-- caller-supplied version), and inserts one row per document - the same
-- "single action, one row per document" pattern legal_acknowledge_all already
-- uses for re-review. A slug with no published version, or that does not
-- exist, is silently skipped rather than failing the whole batch: a signup or
-- checkout must never be blocked because one document in the bundle happens
-- to have no live version yet - that is a content-ops problem, not a reason
-- to stop commerce.
CREATE OR REPLACE FUNCTION record_legal_consent(
  p_slugs          TEXT[],
  p_user           UUID DEFAULT NULL,
  p_session_id     TEXT DEFAULT NULL,
  p_booking_id     UUID DEFAULT NULL,
  p_consent_method TEXT DEFAULT 'checkbox',
  p_ip             TEXT DEFAULT NULL,
  p_user_agent     TEXT DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_count INT := 0;
BEGIN
  IF p_user IS NULL AND p_session_id IS NULL AND p_booking_id IS NULL THEN
    RAISE EXCEPTION 'record_legal_consent requires a user, session, or booking to attribute to'
      USING errcode = 'P0001';
  END IF;

  INSERT INTO legal_consent_events (
    version_id, document_code, document_slug, document_version,
    user_id, session_id, booking_id, consent_method, ip_address, user_agent)
  SELECT v.id, d.code, d.slug, v.version,
         p_user, p_session_id, p_booking_id, p_consent_method,
         NULLIF(p_ip, '')::INET, p_user_agent
  FROM legal_documents d
  JOIN legal_document_versions v ON v.id = d.current_version_id
  WHERE d.slug = ANY(p_slugs);

  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN jsonb_build_object('ok', TRUE, 'recorded_count', v_count);
END $$;

REVOKE ALL ON FUNCTION record_legal_consent(TEXT[], UUID, TEXT, UUID, TEXT, TEXT, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION record_legal_consent(TEXT[], UUID, TEXT, UUID, TEXT, TEXT, TEXT)
  TO service_role;

-- Has this identity already consented to a document's CURRENT version, under
-- any consent_method? Backs the Groovia AI Terms one-time gate: check before
-- showing the modal, so a returning user/guest is never re-prompted.
CREATE OR REPLACE FUNCTION legal_has_current_consent(
  p_slug TEXT, p_user UUID DEFAULT NULL, p_session_id TEXT DEFAULT NULL
) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT EXISTS (
    SELECT 1
    FROM legal_consent_events e
    JOIN legal_documents d ON d.slug = e.document_slug
    WHERE e.document_slug = p_slug
      AND e.version_id = d.current_version_id
      AND ((p_user IS NOT NULL AND e.user_id = p_user)
           OR (p_session_id IS NOT NULL AND e.session_id = p_session_id))
  );
$$;

REVOKE ALL ON FUNCTION legal_has_current_consent(TEXT, UUID, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION legal_has_current_consent(TEXT, UUID, TEXT) TO service_role;

-- ============================================================================
-- data_subject_requests — intake ticket for Section 7 (Data Subject Rights).
-- ============================================================================
-- Intake only, deliberately. Fulfillment (verifying identity, actually
-- deleting/exporting data) is an operational workflow this table does not
-- attempt to automate - the spec itself says to confirm that workflow exists
-- before this page goes live. This is the record that a request came in and
-- whether it has been actioned, not the actioning itself.
CREATE TABLE IF NOT EXISTS data_subject_requests (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name         TEXT NOT NULL,
  email        TEXT NOT NULL,
  request_type TEXT NOT NULL,   -- 'access' | 'rectification' | 'erasure' | 'portability' | 'other'
  details      TEXT,
  user_id      UUID REFERENCES profiles(id) ON DELETE SET NULL,   -- set when the requester is signed in
  status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'closed')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_data_subject_requests_status
  ON data_subject_requests(status, created_at DESC);

ALTER TABLE data_subject_requests ENABLE ROW LEVEL SECURITY;
-- No policies: service role (admin views, the intake endpoint) only. A
-- requester does not query this table directly - they get a confirmation at
-- submit time and are followed up with by email.

INSERT INTO platform_settings (key, value, description) VALUES
  ('data_subject_request_window_days', '90',
   'Target turnaround for a data subject request, per the published retention/deletion policy.')
ON CONFLICT (key) DO NOTHING;
