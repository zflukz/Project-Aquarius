-- Phase C done-checks, run on a THROWAWAY database by tools/phase_c_check.sh
-- (it creates the database, applies every migration, runs this file, drops it).
-- Each check prints "PASS ..." or stops with "FAIL ...".
--
-- Test area: a 200 m x 200 m square in UTM 47N near Ayutthaya, corner
-- E 680000 / N 1587000, 20 m cells -> 10 x 10 = 100 cells of 400 m2.
\set ON_ERROR_STOP on
SET client_min_messages = notice;
\pset tuples_only on
\pset format unaligned

CREATE OR REPLACE FUNCTION pg_temp.check(ok BOOLEAN, what TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    IF ok IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', what; END IF;
    RAISE NOTICE 'PASS %', what;
END $$;

-- helper: a UTM 47N point as lon/lat
CREATE OR REPLACE FUNCTION pg_temp.ll(e DOUBLE PRECISION, n DOUBLE PRECISION) RETURNS geometry
LANGUAGE sql IMMUTABLE AS $$ SELECT ST_Transform(ST_SetSRID(ST_MakePoint(e, n), 32647), 4326) $$;

-- C1 ----------------------------------------------------------------------------
SELECT pg_temp.check(
    (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN
       ('missions', 'drones', 'telemetry', 'detections', 'survivors', 'search_area', 'grid_cells',
        'flight_paths', 'replanning_events', 'ground_truth_persons', 'persons')) = 11,
    'C1 all 11 tables exist');

INSERT INTO missions (mission_id, name) VALUES
    ('c0000000-0000-4000-8000-000000000001', 'phase C test: coverage'),
    ('c0000000-0000-4000-8000-000000000002', 'phase C test: index'),
    ('c0000000-0000-4000-8000-000000000003', 'phase C test: merge');

DO $$
DECLARE v_area UUID; v_cells INT;
BEGIN
    -- GeoJSON as a Feature, the way a planner would send it
    v_area := aq_create_search_area(
        'c0000000-0000-4000-8000-000000000001',
        json_build_object('type', 'Feature', 'properties', json_build_object(),
            'geometry', ST_AsGeoJSON(ST_Transform(ST_MakeEnvelope(680000, 1587000, 680200, 1587200, 32647), 4326))::json)::text,
        'test square', 20);
    SELECT count(*) INTO v_cells FROM grid_cells WHERE area_id = v_area;
    PERFORM pg_temp.check(v_cells = 100, format('C1 grid of 200x200 m at 20 m has 100 cells (got %s)', v_cells));
    BEGIN
        PERFORM aq_create_search_area('c0000000-0000-4000-8000-000000000001',
            '{"type":"Point","coordinates":[100.5,14.3]}');
        PERFORM pg_temp.check(FALSE, 'C1 a Point is refused as search area');
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'FAIL%' THEN RAISE; END IF;
        PERFORM pg_temp.check(TRUE, 'C1 a Point is refused as search area');
    END;
END $$;

-- C3 ----------------------------------------------------------------------------
SELECT pg_temp.check(aq_utm_srid(geom) = 32647, 'C3 search area in Thailand -> EPSG:32647')
  FROM search_area WHERE name = 'test square';
SELECT pg_temp.check(aq_utm_srid(ST_SetSRID(ST_MakePoint(8.5456, 47.3977), 4326)) = 32632,
                     'C3 PX4 default world (Zurich) -> EPSG:32632, not 32647');
SELECT pg_temp.check(abs(ST_Area(ST_Transform(geom, aq_utm_srid(geom))) - 40000) < 0.5,
                     format('C3 area of the square = 40000 m2 (got %s)',
                            round(ST_Area(ST_Transform(geom, aq_utm_srid(geom)))::numeric, 3)))
  FROM search_area WHERE name = 'test square';
SELECT pg_temp.check(abs(sum(ST_Area(ST_Transform(geom, 32647))) - 40000) < 0.5,
                     'C3 cell areas add up to the area')
  FROM grid_cells;

-- C4 ----------------------------------------------------------------------------
-- uav901 flies west -> east through the middle of row 0 (N +10 m)    -> 10 cells
-- uav902 flies south -> north through the middle of column 5 (E +110 m) -> 10 cells
-- they share cell (row 0, col 5) -> 19 cells = 7600 m2 = 19.00 % (by hand)
-- a sample on the ground (alt 0) far away must be ignored
INSERT INTO telemetry (ts, drone_id, mission_id, lat, lon, alt_rel, armed)
SELECT '2001-01-01 00:00:00+00'::timestamptz + make_interval(secs => i), 'uav901',
       'c0000000-0000-4000-8000-000000000001', ST_Y(p), ST_X(p), 30, TRUE
FROM generate_series(0, 42) i, LATERAL (SELECT pg_temp.ll(679995 + 5 * i, 1587010) p) q;
INSERT INTO telemetry (ts, drone_id, mission_id, lat, lon, alt_rel, armed)
SELECT '2001-01-01 00:00:00+00'::timestamptz + make_interval(secs => i), 'uav902',
       'c0000000-0000-4000-8000-000000000001', ST_Y(p), ST_X(p), 30, TRUE
FROM generate_series(0, 42) i, LATERAL (SELECT pg_temp.ll(680110, 1586995 + 5 * i) p) q;
INSERT INTO telemetry (ts, drone_id, mission_id, lat, lon, alt_rel, armed)
SELECT '2000-12-31 23:59:00+00', 'uav901', 'c0000000-0000-4000-8000-000000000001', ST_Y(p), ST_X(p), 0, FALSE
FROM (SELECT pg_temp.ll(680190, 1587190) p) q;

SELECT pg_temp.check(cells_covered = 19 AND abs(coverage_pct - 19.00) < 0.01 AND cells_total = 100,
                     format('C4 flown coverage = 19.00 %% / 19 cells (got %s %% / %s cells)', coverage_pct, cells_covered))
  FROM search_area a, aq_coverage(a.area_id) c WHERE a.name = 'test square';

-- camera footprint 30 m wide: rows 0-1 (20 cells) + columns 4-6 (30) - shared 6 = 44 cells
SELECT pg_temp.check(cells_covered = 44,
                     format('C4 coverage with a 30 m swath = 44 cells (got %s)', cells_covered))
  FROM search_area a, aq_coverage(a.area_id, 'flown', 30) c WHERE a.name = 'test square';

-- planned path (mCPP) through row 1 only -> 10 %
INSERT INTO drones (drone_id) VALUES ('uav901'), ('uav902') ON CONFLICT DO NOTHING;
INSERT INTO flight_paths (mission_id, drone_id, kind, geom)
VALUES ('c0000000-0000-4000-8000-000000000001', 'uav901', 'planned',
        ST_MakeLine(pg_temp.ll(679995, 1587030), pg_temp.ll(680205, 1587030)));
SELECT pg_temp.check(abs(coverage_pct - 10.00) < 0.01,
                     format('C4 planned coverage = 10.00 %% (got %s %%)', coverage_pct))
  FROM search_area a, aq_coverage(a.area_id, 'planned') c WHERE a.name = 'test square';

SELECT pg_temp.check(aq_save_flown_paths('c0000000-0000-4000-8000-000000000001') = 2,
                     'C4 flown paths of 2 drones saved to flight_paths');
SELECT pg_temp.check(abs(coverage_pct - 19.00) < 0.01, 'C4 saved coverage = live coverage')
  FROM search_area a, aq_coverage(a.area_id, 'saved') c WHERE a.name = 'test square';

-- C2 ----------------------------------------------------------------------------
-- 50 000 samples spread over ~20 km, then ask for a 200 m box and a 1 minute window
INSERT INTO telemetry (ts, drone_id, mission_id, lat, lon, alt_rel, armed)
SELECT '2001-02-01 00:00:00+00'::timestamptz + make_interval(secs => i * 0.1::float8), 'uav903',
       'c0000000-0000-4000-8000-000000000002',
       14.20 + (i / 223) * 0.0008, 100.40 + (i % 223) * 0.0008, 30, TRUE
FROM generate_series(0, 49999) i;
ANALYZE telemetry;

DO $$
DECLARE r RECORD; v_plan TEXT := '';
BEGIN
    FOR r IN EXECUTE $q$ EXPLAIN SELECT count(*) FROM telemetry
             WHERE ST_Intersects(geom, ST_MakeEnvelope(100.4500, 14.2500, 100.4520, 14.2520, 4326)) $q$ LOOP
        v_plan := v_plan || r."QUERY PLAN" || E'\n';
    END LOOP;
    IF v_plan NOT LIKE '%telemetry_geom_idx%' THEN RAISE NOTICE '%', v_plan; END IF;
    PERFORM pg_temp.check(v_plan LIKE '%telemetry_geom_idx%', 'C2 area query on telemetry uses the GiST index');

    v_plan := '';
    FOR r IN EXECUTE $q$ EXPLAIN SELECT * FROM telemetry
             WHERE mission_id = 'c0000000-0000-4000-8000-000000000002'
               AND ts BETWEEN '2001-02-01 00:30:00+00' AND '2001-02-01 00:31:00+00' $q$ LOOP
        v_plan := v_plan || r."QUERY PLAN" || E'\n';
    END LOOP;
    IF v_plan NOT LIKE '%Index%' THEN RAISE NOTICE '%', v_plan; END IF;
    PERFORM pg_temp.check(v_plan LIKE '%Index%',
                          'C2 time-window query on telemetry uses an index');
END $$;

SELECT pg_temp.check(TRUE, 'C2 telemetry compression: ' ||
       CASE WHEN EXISTS (SELECT 1 FROM timescaledb_information.jobs
                          WHERE hypertable_name = 'telemetry' AND proc_name = 'policy_compression')
            THEN 'policy after 7 days is active' ELSE 'NOT available in this database (data kept uncompressed)' END);

-- C5 ----------------------------------------------------------------------------
-- person A: 3 detections within ~1.5 m, person B: 2 detections 10 m from A,
-- person C: 1 detection far away -> 3 persons with 3, 2 and 1 detections
INSERT INTO survivors (detection_id, mission_id, drone_id, frame_id, ts, lat, lon, conf)
SELECT gen_random_uuid(), 'c0000000-0000-4000-8000-000000000003', d, f,
       '2001-03-01 00:00:00+00'::timestamptz + make_interval(secs => f), ST_Y(p), ST_X(p), c
FROM (VALUES
    ('uav1', 1, 680050.0, 1587050.0, 0.9), ('uav1', 2, 680051.0, 1587050.5, 0.6), ('uav2', 3, 680049.2, 1587051.2, 0.8),
    ('uav2', 4, 680060.0, 1587050.0, 0.7), ('uav3', 5, 680060.5, 1587049.5, 0.7),
    ('uav3', 6, 680150.0, 1587150.0, 0.5)
) v(d, f, e, n, c), LATERAL (SELECT pg_temp.ll(e, n) p) q;

SELECT pg_temp.check(aq_merge_survivors('c0000000-0000-4000-8000-000000000003') = 6, 'C5 6 detections merged');
SELECT pg_temp.check(string_agg(n_detections::text, ',' ORDER BY n_detections DESC) = '3,2,1',
                     'C5 3 persons with 3, 2 and 1 detections (got ' ||
                     string_agg(n_detections::text, ',' ORDER BY n_detections DESC) || ')')
  FROM persons WHERE mission_id = 'c0000000-0000-4000-8000-000000000003';
SELECT pg_temp.check(ST_Distance(ST_Transform(geom, 32647), ST_SetSRID(ST_MakePoint(680050, 1587050), 32647)) < 1.0,
                     'C5 pin of person A is within 1 m of the detections')
  FROM persons WHERE mission_id = 'c0000000-0000-4000-8000-000000000003' AND n_detections = 3;
SELECT pg_temp.check(aq_merge_survivors('c0000000-0000-4000-8000-000000000003') = 0,
                     'C5 running the merge again changes nothing');

INSERT INTO survivors (detection_id, mission_id, drone_id, frame_id, ts, lat, lon, conf, snapshot_path)
SELECT gen_random_uuid(), 'c0000000-0000-4000-8000-000000000003', 'uav4', 7, '2001-03-01 00:10:00+00',
       ST_Y(p), ST_X(p), 0.95, '/data/snap/a.jpg'
FROM (SELECT pg_temp.ll(680050.5, 1587049.0) p) q;
-- (two statements: a subquery in the same statement would still see the rows
--  as they were before the merge ran)
SELECT pg_temp.check(aq_merge_survivors('c0000000-0000-4000-8000-000000000003') = 1,
                     'C5 the later detection is merged');
SELECT pg_temp.check(string_agg(n_detections::text, ',' ORDER BY n_detections DESC) = '4,2,1',
                     'C5 it joins person A, no new person (got ' ||
                     string_agg(n_detections::text, ',' ORDER BY n_detections DESC) || ')')
  FROM persons WHERE mission_id = 'c0000000-0000-4000-8000-000000000003';
SELECT pg_temp.check(best_snapshot = '/data/snap/a.jpg', 'C5 survivor_pins shows the best evidence image')
  FROM survivor_pins WHERE mission_id = 'c0000000-0000-4000-8000-000000000003' AND n_detections = 4;

\echo ALL PHASE C CHECKS PASSED
