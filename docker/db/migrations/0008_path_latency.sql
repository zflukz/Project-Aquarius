-- 0008 Path comparison (MQTT stack vs Kafka stack, Grafana "Path compare").
-- Both stacks get the same two wall-clock times per row, taken on the same machine:
--   sent        when the bridge sent the sample   MQTT: payload "t" (= column time, mavlink_mqtt.py)
--                                                 Kafka: the message CreateTime (aquarius_bridge produce)
--   received_at when the row was written          clock_timestamp() — now() would be the start of the
--                                                 ingestion / Telegraf batch transaction for every row
-- latency = received_at - sent  (bridge -> broker -> writer -> database, PX4 -> bridge not included).
-- telemetry.ts is sim time and drifts from the wall clock when RTF < 1, so it is not used for latency.
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;   -- NULL for rows from before 0008
ALTER TABLE telemetry ALTER COLUMN received_at SET DEFAULT clock_timestamp();
CREATE INDEX IF NOT EXISTS telemetry_received_idx ON telemetry (received_at DESC);
