-- 0007 (Phase D) Helpers for the Grafana dashboard. Each panel is one short
-- query on these, so the SQL lives (and is tested) here, not inside Grafana JSON.

-- The search area the dashboard shows for a mission: the newest one.
CREATE OR REPLACE FUNCTION aq_current_area(p_mission UUID)
RETURNS UUID LANGUAGE sql STABLE AS $$
    SELECT area_id FROM search_area WHERE mission_id = p_mission ORDER BY created_at DESC LIMIT 1
$$;

-- D2: every grid cell with its centre and whether a flown path crossed it
-- (same rule as aq_coverage: line crosses the cell, or the widened swath does).
CREATE OR REPLACE FUNCTION aq_cell_coverage(
    p_area      UUID,
    p_swath_m   DOUBLE PRECISION DEFAULT 0,
    p_min_alt_m REAL DEFAULT 2)
RETURNS TABLE (cell_row INTEGER, cell_col INTEGER, lat DOUBLE PRECISION, lon DOUBLE PRECISION, covered BOOLEAN)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_srid    INTEGER;
    v_mission UUID;
    v_foot    geometry;
BEGIN
    SELECT aq_utm_srid(a.geom), a.mission_id INTO v_srid, v_mission
      FROM search_area a WHERE a.area_id = p_area;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    SELECT ST_Union(ST_Transform(f.geom, v_srid)) INTO v_foot
      FROM aq_flown_paths(v_mission, p_min_alt_m) f;
    IF v_foot IS NOT NULL AND p_swath_m > 0 THEN
        v_foot := ST_Buffer(v_foot, p_swath_m / 2);
    END IF;
    v_foot := ST_Transform(v_foot, 4326);
    RETURN QUERY
    SELECT c.cell_row, c.cell_col,
           ST_Y(ST_PointOnSurface(c.geom)), ST_X(ST_PointOnSurface(c.geom)),
           COALESCE(v_foot IS NOT NULL AND ST_Intersects(c.geom, v_foot), FALSE)
      FROM grid_cells c
     WHERE c.area_id = p_area;
END $$;

-- D1: newest sample of every drone in a mission (one row per drone).
CREATE OR REPLACE FUNCTION aq_drone_status(p_mission UUID, p_window INTERVAL DEFAULT INTERVAL '1 hour')
RETURNS TABLE (drone_id TEXT, ts TIMESTAMPTZ, lat DOUBLE PRECISION, lon DOUBLE PRECISION,
               alt_rel REAL, battery_pct REAL, battery_voltage REAL, nav_state TEXT, armed BOOLEAN,
               speed_ms REAL, age_s DOUBLE PRECISION)
LANGUAGE sql STABLE AS $$
    -- newest row per drone through the (drone_id, ts) primary key: fast however long the mission
    SELECT t.drone_id, t.ts, t.lat, t.lon, t.alt_rel,
           (t.battery_remaining * 100)::real,
           t.battery_voltage, t.nav_state, t.armed,
           sqrt(coalesce(t.vel_ned[1], 0) ^ 2 + coalesce(t.vel_ned[2], 0) ^ 2)::real,
           extract(epoch FROM now() - t.received_at)::double precision
      FROM drones d
      CROSS JOIN LATERAL (
            SELECT * FROM telemetry x
             WHERE x.drone_id = d.drone_id AND x.mission_id = p_mission
             ORDER BY x.ts DESC LIMIT 1) t
     WHERE t.received_at > now() - p_window
     ORDER BY length(t.drone_id), t.drone_id
$$;
COMMENT ON FUNCTION aq_drone_status(UUID, INTERVAL) IS
    'age_s = seconds since the newest sample reached the database (received_at, so it is right in Gazebo too)';
