-- 0010 Last MQTT-only data on the Kafka path (2026-11-16), needs the px4-sim dds_topics patch:
-- telemetry: PX4 CPU load + mission progress; new table events: PX4 text messages (Kafka topic "events").

ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS cpu_load         REAL;      -- %
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS mission_seq      INTEGER;   -- current item, 0-based
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS mission_total    INTEGER;   -- 0 = no mission uploaded
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS mission_finished BOOLEAN;

CREATE TABLE IF NOT EXISTS events (
    ts           TIMESTAMPTZ NOT NULL,           -- from ts_us
    drone_id     TEXT        NOT NULL,
    mission_id   UUID,
    severity     SMALLINT    NOT NULL,           -- syslog 0 emergency ... 7 debug
    level        TEXT        NOT NULL,
    text         TEXT        NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (drone_id, ts, text)             -- a replayed Kafka message is ignored
);
SELECT create_hypertable('events', 'ts', chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS events_mission_idx ON events (mission_id, ts DESC);

-- D1 drone status + CPU / mission (return type changes -> drop + create; Grafana selects by name)
DROP FUNCTION IF EXISTS aq_drone_status(UUID, INTERVAL);
CREATE FUNCTION aq_drone_status(p_mission UUID, p_window INTERVAL DEFAULT INTERVAL '1 hour')
RETURNS TABLE (drone_id TEXT, ts TIMESTAMPTZ, lat DOUBLE PRECISION, lon DOUBLE PRECISION,
               alt_rel REAL, battery_pct REAL, battery_voltage REAL, nav_state TEXT, armed BOOLEAN,
               speed_ms REAL, age_s DOUBLE PRECISION,
               landed BOOLEAN, gps_fix_type SMALLINT, gps_satellites SMALLINT, health_ok BOOLEAN,
               failsafe_flags TEXT[],
               cpu_load REAL, mission_seq INTEGER, mission_total INTEGER, mission_finished BOOLEAN)
LANGUAGE sql STABLE AS $$
    -- newest row per drone through the (drone_id, ts) primary key: fast however long the mission
    SELECT t.drone_id, t.ts, t.lat, t.lon, t.alt_rel,
           (t.battery_remaining * 100)::real,
           t.battery_voltage, t.nav_state, t.armed,
           sqrt(coalesce(t.vel_ned[1], 0) ^ 2 + coalesce(t.vel_ned[2], 0) ^ 2)::real,
           extract(epoch FROM now() - t.received_at)::double precision,
           t.landed, t.gps_fix_type, t.gps_satellites, t.health_ok, t.failsafe_flags,
           t.cpu_load, t.mission_seq, t.mission_total, t.mission_finished
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
