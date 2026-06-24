-- 013_schema_audit.sql
-- Comprehensive schema hardening pass.
-- Fixes issues identified during DB design review before Phase 1 feature work begins.
-- All changes are safe to run on an existing database.
--
-- Changes:
--   1. bookings.external_id: UNIQUE → UNIQUE(source, external_id)  — adds namespace
--   2. booking_status enum: add 'pending_payment'                   — needed for Stripe flow
--   3. mentors.is_active: add sync trigger from status              — removes dual-gate redundancy
--   4. profiles: add index on attribution_expires_at for fast re-attribution check
--   5. chat_messages: new table for readable chat history            — LangGraph tables are not queryable
--   6. ai_events: add RLS (service-role only)

-- ============================================================================
-- 1. bookings.external_id — namespace the uniqueness by source
--    Cal.com UIDs and Nylas event IDs live in different namespaces; (source, external_id)
--    is the correct unique key. Existing constraint name in Supabase is 'bookings_external_id_key'.
-- ============================================================================
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_external_id_key;

ALTER TABLE bookings
  ADD CONSTRAINT bookings_source_external_id_key UNIQUE (source, external_id);

-- Retain a single-column index for quick lookup by external_id alone (webhook routing).
CREATE INDEX IF NOT EXISTS idx_bookings_external_id ON bookings(external_id) WHERE external_id IS NOT NULL;

-- ============================================================================
-- 2. booking_status enum — add pending_payment
--    Flow for Phase 1 Stripe integration:
--      slot selected → booking created as 'pending_payment'
--      checkout.session.completed → status → 'confirmed' + Nylas event created
--      payment timeout / failure → booking row is left as 'pending_payment' (TTL cleanup later)
-- ============================================================================
DO $$ BEGIN
  ALTER TYPE booking_status ADD VALUE IF NOT EXISTS 'pending_payment' BEFORE 'confirmed';
EXCEPTION WHEN others THEN NULL; END $$;

-- ============================================================================
-- 3. mentors.is_active — derive automatically from status
--    Previously callers had to set both status AND is_active separately. This
--    trigger keeps them in sync: approved → is_active = true, anything else → false.
--    Existing queries that check BOTH columns continue to work correctly.
-- ============================================================================
CREATE OR REPLACE FUNCTION sync_mentor_is_active()
RETURNS TRIGGER AS $$
BEGIN
  NEW.is_active := (NEW.status = 'approved');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_mentor_status_sync_active ON mentors;
CREATE TRIGGER trg_mentor_status_sync_active
  BEFORE INSERT OR UPDATE OF status ON mentors
  FOR EACH ROW EXECUTE FUNCTION sync_mentor_is_active();

-- Back-fill: make sure existing rows are consistent.
UPDATE mentors SET is_active = (status = 'approved') WHERE is_active != (status = 'approved');

-- ============================================================================
-- 4. profiles — attribution lookup index
--    Used by the commission engine when deciding whether to overwrite attribution:
--    WHERE id = $1 AND (attribution_locked_at IS NULL OR attribution_expires_at < NOW())
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_profiles_attribution_expiry
  ON profiles(id, attribution_expires_at)
  WHERE attribution_locked_at IS NOT NULL;

-- ============================================================================
-- 5. chat_messages — readable message log alongside LangGraph checkpoints
--    LangGraph's langgraph_checkpoints / _writes tables store serialised Python
--    dicts — not queryable for a "view my chat history" UI. This table mirrors
--    each human + AI turn into a human-readable row.
--
--    How to populate: at the end of every /chat request in routers/chat.py,
--    after the agent returns, write:
--      - one row per HumanMessage (role='user')
--      - one row per AIMessage that is the final response (role='assistant')
--      - tool call results are skipped (internal detail, not shown to user)
--
--    This table is intentionally NOT populated yet — just reserved so future
--    activation does not require a schema migration at that point.
-- ============================================================================
CREATE TABLE IF NOT EXISTS chat_messages (
  id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id   UUID         NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
  role        TEXT         NOT NULL CHECK (role IN ('user', 'assistant')),
  content     TEXT         NOT NULL,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_thread_time
  ON chat_messages(thread_id, created_at);

ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users read own chat messages" ON chat_messages;
CREATE POLICY "Users read own chat messages"
  ON chat_messages FOR SELECT
  USING (
    thread_id IN (
      SELECT id FROM chat_threads WHERE user_id = auth.uid()
    )
  );

-- Service role does all writes via backend — no INSERT/UPDATE/DELETE policy needed.

-- ============================================================================
-- 6. ai_events — RLS (service-role only, no user-facing policies)
-- ============================================================================
ALTER TABLE ai_events ENABLE ROW LEVEL SECURITY;
-- No policies: only the backend service role writes/reads this table.

-- ============================================================================
-- Reference: full intended table list after all phases complete
--
-- Core auth:
--   auth.users (Supabase)  → profiles  → mentors  → mentor_availability
--                                      → bookings (mentor RESTRICT, candidate SET NULL)
--                                      → saved_mentors
--                                      → saved_sponsors
--                                      → consent_log
--                                      → deletion_requests (GDPR)
--
-- Session/chat:
--   chat_threads  → chat_messages
--   langgraph_checkpoints / _writes / _blobs / _migrations  (LangGraph-managed)
--
-- Commerce:
--   payments  → bookings, mentors, profiles
--   commissions  → payments, bookings  (uses profiles.attribution_* for source attribution)
--
-- Reviews:
--   reviews  → bookings (RESTRICT unique), mentors (CASCADE), profiles (SET NULL)
--
-- Sponsor radar:
--   sponsors
--   saved_sponsors  → profiles, sponsors
--
-- AI observability:
--   ai_events  → chat_threads (SET NULL)
--   knowledge_chunks  (pgvector, no FK — standalone RAG index)
--
-- Admin / audit:
--   webhook_events  (append-only)
--   gdpr_events  (append-only)
--
-- Group sessions (V3):
--   group_sessions  → mentors
--   group_session_attendees  → group_sessions, profiles, payments
-- ============================================================================
