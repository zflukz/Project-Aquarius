-- 0001 Phase B tables: just enough for the ingestion consumer to store every topic.
-- Phase C adds the rest in 0002+ (missions, drones, search_area, grid_cells,
-- flight_paths, ground truth, persons, indexes, compression).
--
-- Duplicate protection: each table has a natural key, and the consumer inserts
-- with ON CONFLICT DO NOTHING. A message replayed after a crash is ignored.

-- telemetry ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry (
    ts                 TIMESTAMPTZ      NOT NULL,   -- from ts_us: when the sample was taken
    drone_id           TEXT             NOT NULL,
    mission_id         UUID,
    lat                DOUBLE PRECISION NOT NULL,
    lon                DOUBLE PRECISION NOT NULL,
    alt_amsl           REAL,
    alt_rel            REAL,
    q                  REAL[4],                     -- w, x, y, z (body FRD -> NED)
    vel_ned            REAL[3],
    battery_remaining  REAL,                        -- 0.0 – 1.0
    battery_voltage    REAL,
    nav_state          TEXT,
    armed              BOOLEAN,
    received_at        TIMESTAMPTZ      NOT NULL DEFAULT now(),  -- for latency (D3)
    geom               geometry(Point, 4326)
                       GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED,
    PRIMARY KEY (drone_id, ts)
);
SELECT create_hypertable('telemetry', 'ts', chunk_time_interval => INTERVAL '1 day',
                         if_not_exists => TRUE);

-- detections -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS detections (
    ts             TIMESTAMPTZ NOT NULL,
    drone_id       TEXT        NOT NULL,
    frame_id       BIGINT      NOT NULL,
    model_version  TEXT,
    image_w        INTEGER,
    image_h        INTEGER,
    objects        JSONB       NOT NULL,             -- [{class, conf, bbox_xyxy, center_uv}]
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (drone_id, frame_id, ts)
);

-- survivors (one row per geolocated detection; merging into people is C5) -----
CREATE TABLE IF NOT EXISTS survivors (
    detection_id   UUID PRIMARY KEY,
    mission_id     UUID,
    drone_id       TEXT             NOT NULL,
    frame_id       BIGINT,
    ts             TIMESTAMPTZ      NOT NULL,
    lat            DOUBLE PRECISION NOT NULL,
    lon            DOUBLE PRECISION NOT NULL,
    conf           REAL,
    pose_dt_ms     REAL,
    snapshot_path  TEXT,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'confirmed', 'rejected')),
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    geom           geometry(Point, 4326)
                   GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED
);

-- replanning_events ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS replanning_events (
    event_id              UUID PRIMARY KEY,
    mission_id            UUID,
    ts                    TIMESTAMPTZ NOT NULL,
    event_type            TEXT NOT NULL
                          CHECK (event_type IN ('rtl_triggered', 'reassigned', 'mission_complete')),
    drone_id              TEXT,
    battery_remaining     REAL,
    waypoints_reassigned  INTEGER,
    assigned_to           TEXT[],
    received_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Messages that failed validation, kept for debugging instead of being dropped silently
CREATE TABLE IF NOT EXISTS ingest_rejects (
    id           BIGSERIAL PRIMARY KEY,
    topic        TEXT        NOT NULL,
    kafka_part   INTEGER,
    kafka_offset BIGINT,
    reason       TEXT        NOT NULL,
    payload      TEXT,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
