-- =============================================================================
-- 017 — Booking lifecycle v2 (ported from immigroov-main 0040–0048)
--
-- Ports the other dev's booking state machine, adapted to our schema:
--   - bigint IDs            -> UUID
--   - users / user_id       -> profiles / candidate_id
--   - guest_email           -> candidate_email
--   - customer_timezone     -> attendee_timezone
--   - 'customer'            -> 'user'  (our existing convention)
--
-- LOGIC ONLY — the booking_ledger / customer_payments / mentor_payouts money
-- layer is intentionally NOT ported (Phase 2). No-show strikes still COUNT, but
-- carry no payout penalty. Their DB sends email via a trigger + notify_booking_event;
-- our emails come from FastAPI, so notify_booking_event() records to a
-- booking_events outbox table instead (FastAPI sends the actual mail).
--
-- Rule direction: >=24h before = free · 2–24h = late (approval) · <2h = buffer (blocked).
-- Run after 015 + 016. Safe to re-run (idempotent).
-- =============================================================================

-- ── 1. New columns ───────────────────────────────────────────────────────────
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reschedule_count INT NOT NULL DEFAULT 0;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS no_show_by TEXT;          -- 'mentor' | 'user'
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS no_show_strikes INT NOT NULL DEFAULT 0;
ALTER TABLE mentors  ADD COLUMN IF NOT EXISTS last_no_show_at TIMESTAMPTZ;
ALTER TABLE reschedule_offers ADD COLUMN IF NOT EXISTS was_late BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE reschedule_offers ADD COLUMN IF NOT EXISTS respond_by TIMESTAMPTZ;

-- widen the reschedule_offers status set for the v2 flow
ALTER TABLE reschedule_offers DROP CONSTRAINT IF EXISTS reschedule_offers_status_check;
ALTER TABLE reschedule_offers ADD CONSTRAINT reschedule_offers_status_check
  CHECK (status IN ('pending','mentee_selected','accepted','declined','rejected','superseded','expired'));

-- ── 2. booking_requests (cancel / reschedule approval workflow) ───────────────
CREATE TABLE IF NOT EXISTS booking_requests (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id   UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('cancel','reschedule')),
  initiated_by TEXT NOT NULL CHECK (initiated_by IN ('user','mentor')),
  status       TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','approved','rejected','auto_approved','expired','withdrawn','completed')),
  respond_by   TIMESTAMPTZ,
  note         TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_booking_requests_booking ON booking_requests(booking_id);
CREATE INDEX IF NOT EXISTS idx_booking_requests_open ON booking_requests(status) WHERE status = 'pending';
ALTER TABLE booking_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS booking_requests_read ON booking_requests;
CREATE POLICY booking_requests_read ON booking_requests FOR SELECT USING (TRUE);

-- ── 3. booking_events outbox (FastAPI reads this to send mail for cron events) ─
CREATE TABLE IF NOT EXISTS booking_events (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id  UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  event       TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sent_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_booking_events_unsent ON booking_events(created_at) WHERE sent_at IS NULL;

CREATE OR REPLACE FUNCTION notify_booking_event(p_booking_id UUID, p_event TEXT)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  INSERT INTO booking_events(booking_id, event) VALUES (p_booking_id, p_event);
$$;

-- ── 4. Deadline helpers ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION booking_deadline_state(p_slot TIMESTAMPTZ)
RETURNS TEXT LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN p_slot IS NULL                        THEN 'free'
    WHEN p_slot - NOW() < INTERVAL '2 hours'   THEN 'buffer'
    WHEN p_slot - NOW() < INTERVAL '24 hours'  THEN 'late'
    ELSE 'free'
  END;
$$;

CREATE OR REPLACE FUNCTION response_window(p_slot TIMESTAMPTZ)
RETURNS TIMESTAMPTZ LANGUAGE sql STABLE AS $$
  SELECT LEAST(NOW() + INTERVAL '48 hours', p_slot - INTERVAL '2 hours');
$$;
GRANT EXECUTE ON FUNCTION booking_deadline_state(TIMESTAMPTZ) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION response_window(TIMESTAMPTZ) TO anon, authenticated;

-- ── 5. Cancel flow (REPLACES the old block-on-late cancel_booking) ────────────
-- >=24h: cancelled immediately · 2–24h (user): opens a cancel request for mentor
-- approval · <2h: blocked. Mentor cancel is always allowed (>=2h) and is free
-- >=24h, bumps the cancellation counter when late. Auth is enforced in FastAPI.
CREATE OR REPLACE FUNCTION cancel_booking(p_booking_id UUID, p_cancelled_by TEXT DEFAULT 'user')
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_state TEXT;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking % not found', p_booking_id; END IF;
  IF b.status IN ('cancelled','completed','no_show') THEN
    RAISE EXCEPTION 'Booking % is already %', p_booking_id, b.status;
  END IF;

  v_state := booking_deadline_state(b.slot_time);
  IF v_state = 'buffer' THEN
    RAISE EXCEPTION 'Within 2 hours of the session — it can no longer be cancelled here. Please contact the other party.';
  END IF;

  IF p_cancelled_by = 'mentor' THEN
    UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id RETURNING * INTO b;
    UPDATE reschedule_offers SET status = 'superseded'
      WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
    IF v_state = 'late' THEN PERFORM bump_mentor_cancellation(b.mentor_id); END IF;
    PERFORM notify_booking_event(p_booking_id, 'cancelled');
    RETURN b;
  END IF;

  IF v_state = 'free' THEN
    UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id RETURNING * INTO b;
    UPDATE reschedule_offers SET status = 'superseded'
      WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
    PERFORM notify_booking_event(p_booking_id, 'cancelled');
    RETURN b;
  ELSE
    -- late: booking stays confirmed; open a cancel request for the mentor
    UPDATE booking_requests SET status = 'withdrawn', resolved_at = NOW()
      WHERE booking_id = p_booking_id AND status = 'pending';
    INSERT INTO booking_requests(booking_id, kind, initiated_by, status, respond_by, note)
      VALUES (p_booking_id, 'cancel', 'user', 'pending', response_window(b.slot_time),
              'User requested late cancellation');
    PERFORM notify_booking_event(p_booking_id, 'cancel_requested');
    RETURN b;
  END IF;
END;
$$;
GRANT EXECUTE ON FUNCTION cancel_booking(UUID, TEXT) TO authenticated;

-- ── 6. Respond to a cancel / reschedule request (mentor side) ─────────────────
CREATE OR REPLACE FUNCTION respond_booking_request(p_request_id UUID, p_accept BOOLEAN)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r booking_requests;
BEGIN
  SELECT * INTO r FROM booking_requests WHERE id = p_request_id;
  IF NOT FOUND OR r.status <> 'pending' THEN RAISE EXCEPTION 'This request is no longer open'; END IF;

  IF r.kind = 'cancel' THEN
    -- A late cancel resolves to cancelled either way (accept = goodwill, reject =
    -- 50% fee would apply once payments exist). State is identical without money.
    UPDATE bookings SET status = 'cancelled' WHERE id = r.booking_id;
    UPDATE booking_requests
       SET status = CASE WHEN p_accept THEN 'approved' ELSE 'rejected' END, resolved_at = NOW()
     WHERE id = p_request_id;
    UPDATE reschedule_offers SET status = 'superseded'
      WHERE booking_id = r.booking_id AND status IN ('pending','mentee_selected');
    PERFORM notify_booking_event(r.booking_id, 'cancelled');
  ELSIF r.kind = 'reschedule' THEN
    IF p_accept THEN
      UPDATE booking_requests SET status = 'approved', resolved_at = NOW() WHERE id = p_request_id;
      PERFORM notify_booking_event(r.booking_id, 'reschedule_approved');
    ELSE
      UPDATE booking_requests SET status = 'rejected', resolved_at = NOW() WHERE id = p_request_id;
      PERFORM notify_booking_event(r.booking_id, 'reschedule_rejected');
    END IF;
  END IF;
END;
$$;
GRANT EXECUTE ON FUNCTION respond_booking_request(UUID, BOOLEAN) TO authenticated;

-- ── 7. force_autocancel (3rd reschedule attempt) — state only ─────────────────
CREATE OR REPLACE FUNCTION force_autocancel(p_booking_id UUID, p_initiator TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE bookings SET status = 'cancelled' WHERE id = p_booking_id;
  UPDATE reschedule_offers SET status = 'superseded'
    WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
  PERFORM notify_booking_event(p_booking_id, 'cancelled');
END;
$$;
GRANT EXECUTE ON FUNCTION force_autocancel(UUID, TEXT) TO authenticated;

-- ── 8. Reschedule — mentor proposes a date + range ────────────────────────────
CREATE OR REPLACE FUNCTION mentor_propose_reschedule(
  p_booking_id UUID, p_date DATE, p_start TIMESTAMPTZ, p_end TIMESTAMPTZ
) RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_id UUID; b bookings;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking not found'; END IF;
  IF b.status IN ('cancelled','completed','no_show') THEN
    RAISE EXCEPTION 'Cannot reschedule booking with status %', b.status;
  END IF;
  IF b.reschedule_count >= 2 THEN
    PERFORM force_autocancel(p_booking_id, 'mentor');
    RETURN NULL;
  END IF;
  IF p_end <= p_start THEN RAISE EXCEPTION 'Range end must be after start'; END IF;
  UPDATE reschedule_offers SET status = 'superseded'
    WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
  INSERT INTO reschedule_offers(booking_id, proposed_by, offer_date, range_start, range_end,
                                status, was_late, respond_by)
    VALUES (p_booking_id, 'mentor', p_date, p_start, p_end, 'pending',
            booking_deadline_state(b.slot_time) = 'late', response_window(b.slot_time))
    RETURNING id INTO v_id;
  PERFORM notify_booking_event(p_booking_id, 'proposed');
  RETURN v_id;
END;
$$;

-- ── 9. Mentee accepts a slot in the proposed range — finalises directly ───────
CREATE OR REPLACE FUNCTION mentee_accept_reschedule(p_offer_id UUID, p_slot_time TIMESTAMPTZ)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE o reschedule_offers; b bookings;
BEGIN
  SELECT * INTO o FROM reschedule_offers WHERE id = p_offer_id;
  IF NOT FOUND OR o.status <> 'pending' OR o.proposed_by <> 'mentor' THEN
    RAISE EXCEPTION 'This proposal is no longer open';
  END IF;
  IF p_slot_time < o.range_start OR p_slot_time >= o.range_end THEN
    RAISE EXCEPTION 'Please pick a time inside the proposed range';
  END IF;
  IF p_slot_time <= NOW() THEN RAISE EXCEPTION 'Please pick a future time'; END IF;
  SELECT * INTO b FROM bookings WHERE id = o.booking_id;
  IF NOT is_slot_available(b.mentor_id, b.service_id, p_slot_time) THEN
    RAISE EXCEPTION 'That time is no longer available — pick another slot inside the range.';
  END IF;
  UPDATE reschedule_offers SET status = 'accepted', selected_time = p_slot_time WHERE id = p_offer_id;
  UPDATE bookings SET slot_time = p_slot_time, slot_end = NULL, status = 'rescheduled',
                      reschedule_count = reschedule_count + 1
    WHERE id = o.booking_id RETURNING * INTO b;
  DELETE FROM booking_reminders WHERE booking_id = o.booking_id;
  PERFORM notify_booking_event(o.booking_id, 'rescheduled');
  RETURN b;
END;
$$;

-- ── 10. Mentee rejects the mentor's proposal — booking cancelled ──────────────
CREATE OR REPLACE FUNCTION mentee_reject_reschedule(p_offer_id UUID)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE o reschedule_offers; b bookings;
BEGIN
  SELECT * INTO o FROM reschedule_offers WHERE id = p_offer_id;
  IF NOT FOUND OR o.status NOT IN ('pending','mentee_selected') OR o.proposed_by <> 'mentor' THEN
    RAISE EXCEPTION 'This proposal is no longer open';
  END IF;
  UPDATE reschedule_offers SET status = 'rejected' WHERE id = p_offer_id;
  UPDATE bookings SET status = 'cancelled' WHERE id = o.booking_id RETURNING * INTO b;
  PERFORM notify_booking_event(o.booking_id, 'cancelled');
  RETURN b;
END;
$$;

-- ── 11. Customer-initiated reschedule (free path picks a slot directly) ───────
CREATE OR REPLACE FUNCTION customer_reschedule(p_booking_id UUID, p_slot_time TIMESTAMPTZ)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_state TEXT; v_approved BOOLEAN;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking not found'; END IF;
  IF b.status IN ('cancelled','completed','no_show') THEN
    RAISE EXCEPTION 'Cannot reschedule booking with status %', b.status;
  END IF;
  IF b.reschedule_count >= 2 THEN
    PERFORM force_autocancel(p_booking_id, 'user');
    RETURN 'autocancelled';
  END IF;
  v_state := booking_deadline_state(b.slot_time);
  IF v_state = 'buffer' THEN RAISE EXCEPTION 'Within 2 hours of the session — cannot reschedule.'; END IF;
  v_approved := EXISTS (SELECT 1 FROM booking_requests
                        WHERE booking_id = p_booking_id AND kind = 'reschedule'
                          AND status IN ('approved','auto_approved'));
  IF v_state <> 'free' AND NOT v_approved THEN
    RAISE EXCEPTION 'A late reschedule needs mentor approval first — send a request.';
  END IF;
  IF NOT is_slot_available(b.mentor_id, b.service_id, p_slot_time) THEN
    RAISE EXCEPTION 'That time is not available — pick another slot.';
  END IF;
  UPDATE bookings SET slot_time = p_slot_time, slot_end = NULL, status = 'rescheduled',
                      reschedule_count = reschedule_count + 1
    WHERE id = p_booking_id;
  DELETE FROM booking_reminders WHERE booking_id = p_booking_id;
  UPDATE booking_requests SET status = 'completed', resolved_at = NOW()
    WHERE booking_id = p_booking_id AND kind = 'reschedule' AND status IN ('approved','auto_approved');
  PERFORM notify_booking_event(p_booking_id, 'rescheduled');
  RETURN 'rescheduled';
END;
$$;

-- ── 12. Customer requests a late reschedule (needs mentor approval) ───────────
CREATE OR REPLACE FUNCTION request_reschedule(p_booking_id UUID)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings; v_id UUID;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking not found'; END IF;
  IF b.status IN ('cancelled','completed','no_show') THEN
    RAISE EXCEPTION 'Cannot reschedule booking with status %', b.status;
  END IF;
  IF b.reschedule_count >= 2 THEN
    PERFORM force_autocancel(p_booking_id, 'user');
    RETURN NULL;
  END IF;
  IF booking_deadline_state(b.slot_time) = 'buffer' THEN
    RAISE EXCEPTION 'Within 2 hours of the session — cannot reschedule.';
  END IF;
  UPDATE booking_requests SET status = 'withdrawn', resolved_at = NOW()
    WHERE booking_id = p_booking_id AND status = 'pending';
  INSERT INTO booking_requests(booking_id, kind, initiated_by, status, respond_by, note)
    VALUES (p_booking_id, 'reschedule', 'user', 'pending', response_window(b.slot_time),
            'User requested reschedule')
    RETURNING id INTO v_id;
  PERFORM notify_booking_event(p_booking_id, 'reschedule_requested');
  RETURN v_id;
END;
$$;

GRANT EXECUTE ON FUNCTION mentor_propose_reschedule(UUID, DATE, TIMESTAMPTZ, TIMESTAMPTZ) TO authenticated;
GRANT EXECUTE ON FUNCTION mentee_accept_reschedule(UUID, TIMESTAMPTZ) TO authenticated;
GRANT EXECUTE ON FUNCTION mentee_reject_reschedule(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION customer_reschedule(UUID, TIMESTAMPTZ) TO authenticated;
GRANT EXECUTE ON FUNCTION request_reschedule(UUID) TO authenticated;

-- ── 13. No-show: strike ladder (counting only — no payout penalty) ────────────
CREATE OR REPLACE FUNCTION apply_mentor_strike(p_mentor_id UUID)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_str INT; v_last TIMESTAMPTZ;
BEGIN
  SELECT no_show_strikes, last_no_show_at INTO v_str, v_last FROM mentors WHERE id = p_mentor_id;
  IF v_last IS NULL OR v_last < NOW() - INTERVAL '90 days' THEN v_str := 0; END IF;
  v_str := COALESCE(v_str, 0) + 1;
  UPDATE mentors SET no_show_strikes = v_str, last_no_show_at = NOW() WHERE id = p_mentor_id;
  RETURN v_str;
END;
$$;

-- Report a no-show (allowed only 10 min after the start). p_no_show_party: 'mentor'|'user'.
CREATE OR REPLACE FUNCTION flag_no_show(p_booking_id UUID, p_no_show_party TEXT)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings;
BEGIN
  IF p_no_show_party NOT IN ('mentor','user') THEN
    RAISE EXCEPTION 'no_show_party must be mentor or user';
  END IF;
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Booking not found'; END IF;
  IF b.status NOT IN ('confirmed','rescheduled') THEN
    RAISE EXCEPTION 'Only an active session can be reported as a no-show';
  END IF;
  IF b.slot_time IS NULL OR NOW() < b.slot_time + INTERVAL '10 minutes' THEN
    RAISE EXCEPTION 'No-shows can only be reported 10 minutes after the start time';
  END IF;
  UPDATE bookings SET status = 'no_show', no_show_by = p_no_show_party
    WHERE id = p_booking_id RETURNING * INTO b;
  UPDATE reschedule_offers SET status = 'superseded'
    WHERE booking_id = p_booking_id AND status IN ('pending','mentee_selected');
  UPDATE booking_requests SET status = 'withdrawn', resolved_at = NOW()
    WHERE booking_id = p_booking_id AND status = 'pending';
  PERFORM notify_booking_event(p_booking_id, 'no_show');
  RETURN b;
END;
$$;

-- Mentor no-showed → the user picks one of three outcomes.
CREATE OR REPLACE FUNCTION resolve_mentor_no_show(p_booking_id UUID, p_choice TEXT)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF b.no_show_by IS DISTINCT FROM 'mentor' OR b.status <> 'no_show' THEN
    RAISE EXCEPTION 'Not a mentor no-show awaiting resolution';
  END IF;
  IF p_choice = 'rebook_same' THEN
    UPDATE bookings SET status = 'confirmed', no_show_by = NULL WHERE id = p_booking_id RETURNING * INTO b;
  ELSIF p_choice IN ('rebook_different','refund') THEN
    PERFORM apply_mentor_strike(b.mentor_id);
    -- status stays 'no_show'; (credit/refund would post to the ledger in Phase 2)
  ELSE
    RAISE EXCEPTION 'Unknown choice %', p_choice;
  END IF;
  RETURN b;
END;
$$;

-- User no-showed → the mentor picks one of two outcomes.
CREATE OR REPLACE FUNCTION resolve_customer_no_show(p_booking_id UUID, p_choice TEXT)
RETURNS bookings LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE b bookings;
BEGIN
  SELECT * INTO b FROM bookings WHERE id = p_booking_id;
  IF b.no_show_by IS DISTINCT FROM 'user' OR b.status <> 'no_show' THEN
    RAISE EXCEPTION 'Not a user no-show awaiting resolution';
  END IF;
  IF p_choice = 'accept_rebook' THEN
    UPDATE bookings SET status = 'confirmed', no_show_by = NULL WHERE id = p_booking_id RETURNING * INTO b;
  ELSIF p_choice = 'reject' THEN
    UPDATE bookings SET status = 'completed' WHERE id = p_booking_id RETURNING * INTO b;
  ELSE
    RAISE EXCEPTION 'Unknown choice %', p_choice;
  END IF;
  RETURN b;
END;
$$;

GRANT EXECUTE ON FUNCTION flag_no_show(UUID, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION resolve_mentor_no_show(UUID, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION resolve_customer_no_show(UUID, TEXT) TO authenticated;

-- ── 14. Cron-driven transitions ──────────────────────────────────────────────
CREATE OR REPLACE FUNCTION resolve_expired_requests()
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r RECORD; n INT := 0;
BEGIN
  FOR r IN SELECT * FROM booking_requests WHERE status = 'pending' AND respond_by < NOW() LOOP
    PERFORM respond_booking_request(r.id, TRUE);     -- no response = auto-approve
    UPDATE booking_requests SET status = 'auto_approved' WHERE id = r.id;
    n := n + 1;
  END LOOP;
  -- stale mentor proposals with no customer response expire silently
  UPDATE reschedule_offers SET status = 'expired'
    WHERE status IN ('pending','mentee_selected') AND proposed_by = 'mentor'
      AND respond_by IS NOT NULL AND respond_by < NOW();
  RETURN n;
END;
$$;

CREATE OR REPLACE FUNCTION mark_past_bookings_completed()
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE n INT;
BEGIN
  UPDATE bookings SET status = 'completed'
   WHERE status IN ('confirmed','rescheduled')
     AND slot_end IS NOT NULL AND slot_end < NOW();
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END;
$$;

-- pg_cron schedules — guarded so the migration still succeeds where pg_cron is
-- not enabled. Enable it in Supabase (Database → Extensions → pg_cron), then
-- re-run just this block to activate the jobs.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'resolve-requests') THEN
      PERFORM cron.unschedule('resolve-requests');
    END IF;
    PERFORM cron.schedule('resolve-requests', '*/10 * * * *', 'SELECT resolve_expired_requests()');

    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'auto-complete') THEN
      PERFORM cron.unschedule('auto-complete');
    END IF;
    PERFORM cron.schedule('auto-complete', '*/15 * * * *', 'SELECT mark_past_bookings_completed()');
  ELSE
    RAISE NOTICE 'pg_cron not enabled — skipping booking cron schedules. Enable it and re-run the cron block.';
  END IF;
END $$;
