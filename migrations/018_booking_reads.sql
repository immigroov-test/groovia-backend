-- =============================================================================
-- 018 — Booking read RPCs for the lifecycle-v2 management UI
--
-- Both RPCs return one row per booking with everything the BookingManager needs
-- to render deadline-aware actions: status, the live deadline_state, the latest
-- open reschedule offer, the latest open cancel/reschedule request, no_show_by,
-- and reschedule_count. No money columns (Phase 2).
--
--   my_bookings(candidate_id)  -> the mentee's bookings (other party = mentor)
--   mentor_sessions(mentor_id) -> the mentor's bookings (other party = mentee)
--
-- mentor_sessions REPLACES the 015 version (which dropped no_show/cancelled and
-- lacked v2 fields). Run after 017.
-- =============================================================================

-- ── Mentee view ───────────────────────────────────────────────────────────────
DROP FUNCTION IF EXISTS my_bookings(UUID);
CREATE OR REPLACE FUNCTION my_bookings(p_candidate_id UUID)
RETURNS TABLE (
  id               UUID,
  status           TEXT,
  slot_time        TIMESTAMPTZ,
  slot_end         TIMESTAMPTZ,
  meeting_url      TEXT,
  service_title    TEXT,
  service_duration INTEGER,
  other_name       TEXT,          -- the mentor
  mentor_slug      TEXT,
  mentor_tz        TEXT,
  attendee_tz      TEXT,
  reschedule_count INTEGER,
  no_show_by       TEXT,
  deadline_state   TEXT,
  offer_id         UUID,
  offer_by         TEXT,
  offer_status     TEXT,
  offer_date       DATE,
  range_start      TIMESTAMPTZ,
  range_end        TIMESTAMPTZ,
  selected_time    TIMESTAMPTZ,
  requested_date   DATE,
  req_id           UUID,
  req_kind         TEXT,
  req_initiated_by TEXT,
  req_status       TEXT,
  req_respond_by   TIMESTAMPTZ
) LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT
    b.id, b.status::TEXT, b.slot_time, b.slot_end, b.meeting_url,
    s.title, s.duration,
    m.display_name, m.slug,
    COALESCE(m.app_timezone, 'UTC'),
    COALESCE(b.attendee_timezone, p.timezone, 'UTC'),
    b.reschedule_count, b.no_show_by, booking_deadline_state(b.slot_time),
    ro.id, ro.proposed_by, ro.status, ro.offer_date, ro.range_start, ro.range_end,
    ro.selected_time, ro.requested_date,
    rq.id, rq.kind, rq.initiated_by, rq.status, rq.respond_by
  FROM bookings b
  JOIN  mentors  m ON m.id = b.mentor_id
  LEFT JOIN services s ON s.id = b.service_id
  LEFT JOIN profiles p ON p.id = b.candidate_id
  LEFT JOIN LATERAL (
    SELECT * FROM reschedule_offers
    WHERE booking_id = b.id AND status IN ('pending','mentee_selected')
    ORDER BY created_at DESC LIMIT 1
  ) ro ON TRUE
  LEFT JOIN LATERAL (
    SELECT * FROM booking_requests
    WHERE booking_id = b.id AND status IN ('pending','approved','auto_approved')
    ORDER BY created_at DESC LIMIT 1
  ) rq ON TRUE
  WHERE b.candidate_id = p_candidate_id
  ORDER BY b.slot_time DESC NULLS LAST;
$$;
GRANT EXECUTE ON FUNCTION my_bookings(UUID) TO authenticated;

-- ── Mentor view ───────────────────────────────────────────────────────────────
DROP FUNCTION IF EXISTS mentor_sessions(UUID);
CREATE OR REPLACE FUNCTION mentor_sessions(p_mentor_id UUID)
RETURNS TABLE (
  id                  UUID,
  status              TEXT,
  slot_time           TIMESTAMPTZ,
  slot_end            TIMESTAMPTZ,
  meeting_url         TEXT,
  service_title       TEXT,
  service_duration    INTEGER,
  other_name          TEXT,        -- the mentee
  other_email         TEXT,
  mentor_tz           TEXT,
  attendee_tz         TEXT,
  mentor_confirmed_at TIMESTAMPTZ,
  reschedule_count    INTEGER,
  no_show_by          TEXT,
  deadline_state      TEXT,
  offer_id            UUID,
  offer_by            TEXT,
  offer_status        TEXT,
  offer_date          DATE,
  range_start         TIMESTAMPTZ,
  range_end           TIMESTAMPTZ,
  selected_time       TIMESTAMPTZ,
  requested_date      DATE,
  req_id              UUID,
  req_kind            TEXT,
  req_initiated_by    TEXT,
  req_status          TEXT,
  req_respond_by      TIMESTAMPTZ
) LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT
    b.id, b.status::TEXT, b.slot_time, b.slot_end, b.meeting_url,
    s.title, s.duration,
    COALESCE(p.display_name, p.full_name, b.candidate_name, b.candidate_email),
    COALESCE(b.candidate_email, p.email),
    COALESCE(m.app_timezone, 'UTC'),
    COALESCE(b.attendee_timezone, p.timezone, 'UTC'),
    b.mentor_confirmed_at, b.reschedule_count, b.no_show_by,
    booking_deadline_state(b.slot_time),
    ro.id, ro.proposed_by, ro.status, ro.offer_date, ro.range_start, ro.range_end,
    ro.selected_time, ro.requested_date,
    rq.id, rq.kind, rq.initiated_by, rq.status, rq.respond_by
  FROM bookings b
  JOIN  mentors  m ON m.id = b.mentor_id
  LEFT JOIN services s ON s.id = b.service_id
  LEFT JOIN profiles p ON p.id = b.candidate_id
  LEFT JOIN LATERAL (
    SELECT * FROM reschedule_offers
    WHERE booking_id = b.id AND status IN ('pending','mentee_selected')
    ORDER BY created_at DESC LIMIT 1
  ) ro ON TRUE
  LEFT JOIN LATERAL (
    SELECT * FROM booking_requests
    WHERE booking_id = b.id AND status IN ('pending','approved','auto_approved')
    ORDER BY created_at DESC LIMIT 1
  ) rq ON TRUE
  WHERE b.mentor_id = p_mentor_id
    AND b.slot_time IS NOT NULL
  ORDER BY b.slot_time DESC NULLS LAST;
$$;
GRANT EXECUTE ON FUNCTION mentor_sessions(UUID) TO authenticated;
