-- 0003 (C2) Indexes and data lifetime.
--
-- Spatial: GiST on every geometry column, so "what lies inside / near this
-- shape" uses the index (check with EXPLAIN, tools/phase_c_check.sh does).
-- Time: telemetry already has the hypertable index on ts and the primary key
-- (drone_id, ts); add per-mission and plain time indexes where queries need them.

CREATE INDEX IF NOT EXISTS telemetry_geom_idx      ON telemetry USING GIST (geom);
CREATE INDEX IF NOT EXISTS telemetry_mission_ts_idx ON telemetry (mission_id, ts DESC);

CREATE INDEX IF NOT EXISTS detections_ts_idx       ON detections (ts DESC);

CREATE INDEX IF NOT EXISTS survivors_geom_idx      ON survivors USING GIST (geom);
CREATE INDEX IF NOT EXISTS survivors_mission_ts_idx ON survivors (mission_id, ts DESC);

CREATE INDEX IF NOT EXISTS replanning_mission_ts_idx ON replanning_events (mission_id, ts);

CREATE INDEX IF NOT EXISTS search_area_geom_idx    ON search_area USING GIST (geom);
CREATE INDEX IF NOT EXISTS search_area_mission_idx ON search_area (mission_id);
CREATE INDEX IF NOT EXISTS grid_cells_geom_idx     ON grid_cells USING GIST (geom);
CREATE INDEX IF NOT EXISTS flight_paths_geom_idx   ON flight_paths USING GIST (geom);
CREATE INDEX IF NOT EXISTS flight_paths_mission_idx ON flight_paths (mission_id, kind);
CREATE INDEX IF NOT EXISTS ground_truth_geom_idx   ON ground_truth_persons USING GIST (geom);

CREATE INDEX IF NOT EXISTS ingest_rejects_time_idx ON ingest_rejects (received_at DESC);

-- Data lifetime ---------------------------------------------------------------
-- Telemetry is NOT deleted automatically: the post-mission summary (E4) and the
-- benchmarks (F4) need whole missions. Instead, chunks older than 7 days are
-- compressed (typically ~90% smaller; still queryable, inserts still allowed).
-- Images are not in the database: Kafka keeps them 1 day (A3).
--
-- Compression needs the TimescaleDB community license (timescaledb-ha has it).
-- If it is not available the migration still succeeds and says so; the
-- database then simply keeps telemetry uncompressed.
DO $$
BEGIN
    ALTER TABLE telemetry SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'drone_id',
        timescaledb.compress_orderby   = 'ts DESC'
    );
    PERFORM add_compression_policy('telemetry', INTERVAL '7 days', if_not_exists => TRUE);
    RAISE NOTICE 'telemetry: compression after 7 days enabled';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'telemetry compression not enabled (%): telemetry stays uncompressed', SQLERRM;
END $$;

-- Rejected messages are debugging aids only: keep 30 days.
-- (Plain table, so this is a function to call from a cron / Grafana alert, or by hand.)
CREATE OR REPLACE FUNCTION aq_prune_ingest_rejects(p_keep INTERVAL DEFAULT INTERVAL '30 days')
RETURNS BIGINT LANGUAGE sql AS $$
    WITH d AS (DELETE FROM ingest_rejects WHERE received_at < now() - p_keep RETURNING 1)
    SELECT count(*) FROM d;
$$;
