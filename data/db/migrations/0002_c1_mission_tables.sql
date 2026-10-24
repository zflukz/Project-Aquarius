-- 0002 (C1) Mission tables: missions, drones, search_area, grid_cells,
-- flight_paths and ground truth, plus foreign keys from the Phase B tables.
--
-- Coordinates are stored as EPSG:4326 (lon/lat, C3). Areas and distances are
-- computed in UTM by the functions in 0004.

-- missions ---------------------------------------------------------------------
-- A row appears automatically the first time ingestion sees a mission_id
-- (bridge `mission_id` parameter). name / status are set by the operator.
CREATE TABLE IF NOT EXISTS missions (
    mission_id  UUID PRIMARY KEY,
    name        TEXT,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('planned', 'active', 'completed', 'aborted')),
    first_ts    TIMESTAMPTZ,                 -- earliest data time seen (ingestion)
    last_ts     TIMESTAMPTZ,                 -- latest data time seen (ingestion)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes       TEXT
);

-- drones -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS drones (
    drone_id    TEXT PRIMARY KEY CHECK (drone_id ~ '^uav[0-9]+$'),
    model       TEXT,
    first_ts    TIMESTAMPTZ,
    last_ts     TIMESTAMPTZ,                 -- "last seen", for the dashboard
    notes       TEXT
);

-- Fill both from data that is already in the database (Phase B test runs)
INSERT INTO missions (mission_id, first_ts, last_ts)
SELECT mission_id, min(ts), max(ts) FROM (
    SELECT mission_id, ts FROM telemetry         WHERE mission_id IS NOT NULL
    UNION ALL SELECT mission_id, ts FROM survivors        WHERE mission_id IS NOT NULL
    UNION ALL SELECT mission_id, ts FROM replanning_events WHERE mission_id IS NOT NULL
) x GROUP BY mission_id
ON CONFLICT (mission_id) DO NOTHING;

INSERT INTO drones (drone_id, first_ts, last_ts)
SELECT drone_id, min(ts), max(ts) FROM (
    SELECT drone_id, ts FROM telemetry
    UNION ALL SELECT drone_id, ts FROM detections
    UNION ALL SELECT drone_id, ts FROM survivors
) x WHERE drone_id ~ '^uav[0-9]+$' GROUP BY drone_id
ON CONFLICT (drone_id) DO NOTHING;

-- Foreign keys on the low-volume tables. telemetry (10 rows/s per drone,
-- hypertable) has none on purpose: ingestion creates the mission row in the
-- same transaction, and a per-row FK check is not worth it there.
ALTER TABLE survivors
    ADD CONSTRAINT survivors_mission_fk FOREIGN KEY (mission_id) REFERENCES missions (mission_id);
ALTER TABLE replanning_events
    ADD CONSTRAINT replanning_events_mission_fk FOREIGN KEY (mission_id) REFERENCES missions (mission_id);

-- search_area: the polygon to search, one or more per mission ----------------
CREATE TABLE IF NOT EXISTS search_area (
    area_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id  UUID NOT NULL REFERENCES missions (mission_id) ON DELETE CASCADE,
    name        TEXT,
    cell_size_m REAL NOT NULL DEFAULT 20 CHECK (cell_size_m > 0),
    geom        geometry(Polygon, 4326) NOT NULL CHECK (ST_IsValid(geom)),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- grid_cells: square cells over the search area (made by aq_make_grid, 0004).
-- Cells on the edge are clipped to the area, so the cell areas add up to the
-- area of the polygon exactly.
CREATE TABLE IF NOT EXISTS grid_cells (
    area_id     UUID NOT NULL REFERENCES search_area (area_id) ON DELETE CASCADE,
    cell_row    INTEGER NOT NULL,            -- 0 = southern-most row
    cell_col    INTEGER NOT NULL,            -- 0 = western-most column
    geom        geometry(MultiPolygon, 4326) NOT NULL,
    PRIMARY KEY (area_id, cell_row, cell_col)
);

-- flight_paths: planned paths from mCPP (Nitis) and flown paths saved after a
-- mission. Replanning stores a new planned version instead of overwriting.
CREATE TABLE IF NOT EXISTS flight_paths (
    path_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id  UUID NOT NULL REFERENCES missions (mission_id) ON DELETE CASCADE,
    drone_id    TEXT NOT NULL REFERENCES drones (drone_id),
    kind        TEXT NOT NULL CHECK (kind IN ('planned', 'flown')),
    version     INTEGER NOT NULL DEFAULT 1,
    geom        geometry(LineString, 4326) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mission_id, drone_id, kind, version)
);

-- ground_truth_persons: where the people really are in the Gazebo world (F3),
-- used to measure the georeferencing error in metres.
CREATE TABLE IF NOT EXISTS ground_truth_persons (
    gt_id       BIGSERIAL PRIMARY KEY,
    world       TEXT NOT NULL,               -- Gazebo world name, e.g. 'flood'
    label       TEXT NOT NULL,               -- model name in the world
    mission_id  UUID REFERENCES missions (mission_id) ON DELETE SET NULL,
    lat         DOUBLE PRECISION NOT NULL,
    lon         DOUBLE PRECISION NOT NULL,
    alt_amsl    REAL,
    geom        geometry(Point, 4326)
                GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED,
    UNIQUE (world, label)
);
