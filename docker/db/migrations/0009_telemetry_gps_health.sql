-- 0009 Optional telemetry fields so the Kafka path carries what the MQTT path has (2026-11-16):
-- landed (vehicle_land_detected), GPS (vehicle_gnss), health (failsafe_flags). All nullable:
-- rows from older bridges / before the first PX4 sample simply stay NULL.
-- ADD COLUMN without a default also works on compressed telemetry chunks.
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS landed         BOOLEAN;
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS gps_fix_type   SMALLINT;   -- 1 none, 2 2D, 3 3D, 4 DGPS, 5/6 RTK, 8 extrapolated
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS gps_satellites SMALLINT;
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS gps_eph        REAL;       -- m
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS gps_epv        REAL;       -- m
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS health_ok      BOOLEAN;    -- false = critical PX4 failsafe flag set
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS failsafe_flags TEXT[];     -- names of the active PX4 failsafe flags

-- D1 drone status gets the new fields (return type changes -> drop + create; Grafana selects by name)
DROP FUNCTION IF EXISTS aq_drone_status(UUID, INTERVAL);
CREATE FUNCTION aq_drone_status(p_mission UUID, p_window INTERVAL DEFAULT INTERVAL '1 hour')
RETURNS TABLE (drone_id TEXT, ts TIMESTAMPTZ, lat DOUBLE PRECISION, lon DOUBLE PRECISION,
               alt_rel REAL, battery_pct REAL, battery_voltage REAL, nav_state TEXT, armed BOOLEAN,
               speed_ms REAL, age_s DOUBLE PRECISION,
               landed BOOLEAN, gps_fix_type SMALLINT, gps_satellites SMALLINT, health_ok BOOLEAN,
               failsafe_flags TEXT[])
LANGUAGE sql STABLE AS $$
    -- newest row per drone through the (drone_id, ts) primary key: fast however long the mission
    SELECT t.drone_id, t.ts, t.lat, t.lon, t.alt_rel,
           (t.battery_remaining * 100)::real,
           t.battery_voltage, t.nav_state, t.armed,
           sqrt(coalesce(t.vel_ned[1], 0) ^ 2 + coalesce(t.vel_ned[2], 0) ^ 2)::real,
           extract(epoch FROM now() - t.received_at)::double precision,
           t.landed, t.gps_fix_type, t.gps_satellites, t.health_ok, t.failsafe_flags
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
