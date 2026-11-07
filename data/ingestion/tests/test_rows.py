"""Unit tests for message validation and row mapping (no Kafka / Postgres needed).
    python3 -m pytest ingestion/tests      (or python3 -m unittest discover -s ingestion/tests)
"""
import json
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from aquarius_ingest.rows import (DRONE_UPSERT_SQL, MISSION_UPSERT_SQL, SQL, Rejected, Seen,  # noqa: E402
                                  decode, load_validators, to_row, ts_from_us)

V = load_validators(os.path.join(HERE, "..", "..", "schemas"))
MISSION = str(uuid.uuid4())


def telemetry(**over):
    m = {"schema_version": 1, "drone_id": "uav1", "mission_id": MISSION, "ts_us": 1_791_000_000_123_456,
         "lat": 13.7563, "lon": 100.5018, "alt_amsl": 42.5, "alt_rel": 30.0,
         "q": [1.0, 0.0, 0.0, 0.0], "vel_ned": [4.0, -1.0, 0.2], "battery_remaining": 0.53,
         "battery_voltage": 15.7, "nav_state": "OFFBOARD", "armed": True}
    m.update(over)
    return m


def enc(m):
    return json.dumps(m).encode()


SAMPLES = {
    "telemetry": telemetry(),
    "detections": {"schema_version": 1, "drone_id": "uav1", "frame_id": 1842, "ts_us": 1_791_000_000_123_456,
                   "model_version": "yolo-sar-v1", "image_w": 1280, "image_h": 720,
                   "objects": [{"class": "person", "conf": 0.81, "bbox_xyxy": [790, 410, 834, 452],
                                "center_uv": [812, 431]}]},
    "survivors": {"schema_version": 1, "detection_id": str(uuid.uuid4()), "mission_id": MISSION,
                  "drone_id": "uav2", "frame_id": 77, "ts_us": 1_791_000_000_500_000, "lat": 13.75,
                  "lon": 100.5, "conf": 0.9, "pose_dt_ms": 12.5, "snapshot_path": "/data/snap/abc.jpg"},
    "replanning-events": {"schema_version": 1, "event_id": str(uuid.uuid4()), "mission_id": MISSION,
                          "ts_us": 1_791_000_001_000_000, "event_type": "reassigned", "drone_id": "uav1",
                          "battery_remaining": 0.19, "waypoints_reassigned": 14,
                          "assigned_to": ["uav2", "uav3"]},
}


class TestDecode(unittest.TestCase):
    def test_all_samples_valid_and_row_width_matches_sql(self):
        for topic, msg in SAMPLES.items():
            row = to_row(topic, decode(topic, enc(msg), V))
            self.assertEqual(len(row), SQL[topic].count("%s"), topic)

    def test_rejects(self):
        cases = [
            ("telemetry", b"not json", "invalid JSON"),
            ("telemetry", b"[1,2]", "not a JSON object"),
            ("telemetry", enc(telemetry(schema_version=2)), "unsupported schema_version"),
            ("telemetry", enc(telemetry(lat=123.0)), "schema: lat"),
            ("telemetry", enc(telemetry(drone_id="drone-1")), "schema: drone_id"),
            ("telemetry", enc(telemetry(q=[1, 0, 0])), "schema: q"),
            ("telemetry", enc(telemetry(extra_field=1)), "schema"),
            ("telemetry", None, "empty value"),
        ]
        for topic, value, expect in cases:
            with self.assertRaises(Rejected, msg=expect) as cm:
                decode(topic, value, V)
            self.assertIn(expect, str(cm.exception))

    def test_detection_without_objects_is_rejected(self):
        m = dict(SAMPLES["detections"], objects=[])
        with self.assertRaises(Rejected):
            decode("detections", enc(m), V)

    def test_replanning_minimal_event(self):
        m = {"schema_version": 1, "event_id": str(uuid.uuid4()), "mission_id": MISSION,
             "ts_us": 1, "event_type": "mission_complete", "drone_id": None}
        row = to_row("replanning-events", decode("replanning-events", enc(m), V))
        self.assertEqual(row[3], "mission_complete")
        self.assertIsNone(row[6])


class TestPhaseC(unittest.TestCase):
    def test_bad_uuid_is_rejected_not_crashing_the_batch(self):
        for topic, field in (("telemetry", "mission_id"), ("survivors", "detection_id"),
                             ("replanning-events", "event_id")):
            m = dict(SAMPLES[topic], **{field: "--------" + "-0000-0000-0000-" + "0" * 12})
            with self.assertRaises(Rejected, msg=topic) as cm:
                decode(topic, enc(m), V)
            self.assertIn(field, str(cm.exception))

    def test_seen_keeps_min_and_max_time_per_mission_and_drone(self):
        seen = Seen()
        for ts, did in ((3_000_000, "uav1"), (1_000_000, "uav2"), (2_000_000, "uav1")):
            seen.add("telemetry", decode("telemetry", enc(telemetry(ts_us=ts, drone_id=did)), V))
        seen.add("detections", decode("detections", enc(SAMPLES["detections"]), V))   # no mission_id
        seen.add("survivors", decode("survivors", enc(SAMPLES["survivors"]), V))
        (mid, first, last), = seen.mission_rows()
        self.assertEqual(mid, MISSION)
        self.assertEqual(first, ts_from_us(1_000_000))
        self.assertEqual(last, ts_from_us(SAMPLES["survivors"]["ts_us"]))
        drones = {d: (a, b) for d, a, b in seen.drone_rows()}
        # uav1: telemetry at 2 s and 3 s, plus the detection sample (later)
        self.assertEqual(drones["uav1"], (ts_from_us(2_000_000), ts_from_us(SAMPLES["detections"]["ts_us"])))
        self.assertEqual(set(drones), {"uav1", "uav2"})
        self.assertEqual(seen.survivor_missions, {MISSION})
        for sql, rows in ((MISSION_UPSERT_SQL, seen.mission_rows()), (DRONE_UPSERT_SQL, seen.drone_rows())):
            self.assertEqual(sql.count("%s"), len(rows[0]))


class TestTime(unittest.TestCase):
    def test_exact_microseconds(self):
        t = ts_from_us(1_791_000_000_123_457)
        self.assertEqual(t.microsecond, 123_457)
        self.assertEqual(t.tzinfo, timezone.utc)
        self.assertEqual(t, datetime.fromtimestamp(1_791_000_000, tz=timezone.utc).replace(microsecond=123_457))


if __name__ == "__main__":
    unittest.main()
