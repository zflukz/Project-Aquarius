-- Phase D checks: the dashboard helpers of migration 0007 (run by tools/phase_c_check.sh
-- on the same throwaway database, after phase_c_test.sql).
\set ON_ERROR_STOP on
SET client_min_messages = notice;
\pset tuples_only on
\pset format unaligned

CREATE OR REPLACE FUNCTION pg_temp.check(ok BOOLEAN, what TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    IF ok IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', what; END IF;
    RAISE NOTICE 'PASS %', what;
END $$;
CREATE OR REPLACE FUNCTION pg_temp.ll(e DOUBLE PRECISION, n DOUBLE PRECISION) RETURNS geometry
LANGUAGE sql IMMUTABLE AS $$ SELECT ST_Transform(ST_SetSRID(ST_MakePoint(e, n), 32647), 4326) $$;

INSERT INTO missions (mission_id, name) VALUES ('d0000000-0000-4000-8000-000000000001', 'phase D test');
SELECT aq_create_search_area('d0000000-0000-4000-8000-000000000001',
    ST_AsGeoJSON(ST_Transform(ST_MakeEnvelope(680000, 1587000, 680200, 1587200, 32647), 4326)), 'D square', 20);
INSERT INTO drones (drone_id) VALUES ('uav911'), ('uav912') ON CONFLICT DO NOTHING;

-- same two crossing lines as the C4 check (19 cells), samples arriving "now"
INSERT INTO telemetry (ts, drone_id, mission_id, lat, lon, alt_rel, battery_remaining, nav_state, armed, vel_ned)
SELECT now() - make_interval(secs => 43 - i), 'uav911', 'd0000000-0000-4000-8000-000000000001',
       ST_Y(p), ST_X(p), 30, 0.80 - i * 0.001, 'OFFBOARD', TRUE, ARRAY[0, 5, 0]::real[]
FROM generate_series(0, 42) i, LATERAL (SELECT pg_temp.ll(679995 + 5 * i, 1587010) p) q;
INSERT INTO telemetry (ts, drone_id, mission_id, lat, lon, alt_rel, battery_remaining, nav_state, armed, vel_ned)
SELECT now() - make_interval(secs => 43 - i), 'uav912', 'd0000000-0000-4000-8000-000000000001',
       ST_Y(p), ST_X(p), 30, 0.50, 'AUTO_RTL', TRUE, ARRAY[3, 4, 0]::real[]
FROM generate_series(0, 42) i, LATERAL (SELECT pg_temp.ll(680110, 1586995 + 5 * i) p) q;

SELECT pg_temp.check(aq_current_area('d0000000-0000-4000-8000-000000000001') IS NOT NULL,
                     'D2 aq_current_area finds the search area');
SELECT pg_temp.check(count(*) = 100 AND count(*) FILTER (WHERE covered) = 19,
                     format('D2 aq_cell_coverage: 100 cells, 19 covered (got %s, %s)',
                            count(*), count(*) FILTER (WHERE covered)))
  FROM aq_cell_coverage(aq_current_area('d0000000-0000-4000-8000-000000000001'));
SELECT pg_temp.check(count(*) = 2, format('D1 aq_drone_status: one row per drone (got %s)', count(*)))
  FROM aq_drone_status('d0000000-0000-4000-8000-000000000001');
SELECT pg_temp.check(drone_id = 'uav912' AND nav_state = 'AUTO_RTL' AND abs(battery_pct - 50) < 0.01
                     AND abs(speed_ms - 5) < 0.01 AND age_s < 60,
                     'D1 newest sample: mode, battery %, speed, age')
  FROM aq_drone_status('d0000000-0000-4000-8000-000000000001') WHERE drone_id = 'uav912';

\echo ALL PHASE D CHECKS PASSED
