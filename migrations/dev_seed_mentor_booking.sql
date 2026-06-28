-- =============================================================================
-- dev_seed_mentor_booking.sql
-- Run this DIRECTLY against your testing DB to make every seed mentor bookable
-- WITHOUT resetting anything. Adds one free 30-min video service + a varied
-- weekly schedule to each seed mentor (profile_id IS NULL) that doesn't have one.
-- Idempotent — safe to run repeatedly.
--
-- After running, refresh a mentor page (e.g. /mentors/maya-singh) and the
-- in-app Book widget appears with open slots.
-- =============================================================================

-- 1) CHECK first — how many services/availability rows each seed mentor has:
SELECT m.slug,
       COUNT(DISTINCT s.id) AS services,
       COUNT(DISTINCT w.id) AS weekly_slots
FROM mentors m
LEFT JOIN services s             ON s.mentor_id = m.id
LEFT JOIN weekly_availability w  ON w.mentor_id = m.id
WHERE m.profile_id IS NULL
GROUP BY m.slug
ORDER BY m.slug;

-- 2) SEED — add a service + varied weekly availability where missing:
DO $$
DECLARE
  m RECORD;
  i INT := 0;
  v_start TIME;
  v_end   TIME;
  v_days  TEXT[];
BEGIN
  FOR m IN SELECT id FROM mentors WHERE profile_id IS NULL ORDER BY slug LOOP
    i := i + 1;
    v_start := TIME '08:00' + ((i % 4) * INTERVAL '1 hour');   -- 08:00–11:00
    v_end   := TIME '15:00' + ((i % 4) * INTERVAL '1 hour');   -- 15:00–18:00
    v_days  := CASE (i % 3)
                 WHEN 0 THEN ARRAY['Monday','Tuesday','Wednesday','Thursday','Friday']
                 WHEN 1 THEN ARRAY['Monday','Wednesday','Friday']
                 ELSE        ARRAY['Tuesday','Thursday','Saturday']
               END;

    IF NOT EXISTS (SELECT 1 FROM services WHERE mentor_id = m.id) THEN
      INSERT INTO services (mentor_id, title, description, type, duration, category,
                            set_price, set_currency, is_active)
      VALUES (m.id, '1-on-1 Mentoring Session',
              'A 30-minute video call to talk through your visa and career questions.',
              'video', 30, 'job_career', 0, 'USD', TRUE);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM weekly_availability WHERE mentor_id = m.id) THEN
      INSERT INTO weekly_availability (mentor_id, weekday, start_time, end_time, timezone, is_active)
      SELECT m.id, d, v_start, v_end, 'UTC', TRUE FROM unnest(v_days) AS d;
    END IF;
  END LOOP;
END $$;

-- 3) VERIFY — re-run the check; every seed mentor should now show services >= 1:
SELECT m.slug,
       COUNT(DISTINCT s.id) AS services,
       COUNT(DISTINCT w.id) AS weekly_slots
FROM mentors m
LEFT JOIN services s             ON s.mentor_id = m.id
LEFT JOIN weekly_availability w  ON w.mentor_id = m.id
WHERE m.profile_id IS NULL
GROUP BY m.slug
ORDER BY m.slug;
