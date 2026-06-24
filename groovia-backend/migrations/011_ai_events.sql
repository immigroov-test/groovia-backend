-- 011_ai_events.sql
-- Observability table for tracking per-request LLM quality metrics.

CREATE TABLE IF NOT EXISTS ai_events (
  id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id      UUID         REFERENCES chat_threads(id) ON DELETE SET NULL,
  intent         TEXT,
  revision_count INTEGER      NOT NULL DEFAULT 0,
  tool_calls     INTEGER      NOT NULL DEFAULT 0,
  latency_ms     INTEGER,
  model          TEXT,
  quality_failure BOOLEAN     NOT NULL DEFAULT false,
  created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_events_thread   ON ai_events(thread_id);
CREATE INDEX IF NOT EXISTS idx_ai_events_created  ON ai_events(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_events_intent   ON ai_events(intent, created_at);
