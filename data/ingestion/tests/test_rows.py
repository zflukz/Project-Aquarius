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

from aquarius_ingest.rows import SQL, Rejected, decode, load_validators, to_row, ts_from_us  # noqa: E402

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


class TestTime(unittest.TestCase):
    def test_exact_microseconds(self):
        t = ts_from_us(1_791_000_000_123_457)
        self.assertEqual(t.microsecond, 123_457)
        self.assertEqual(t.tzinfo, timezone.utc)
        self.assertEqual(t, datetime.fromtimestamp(1_791_000_000, tz=timezone.utc).replace(microsecond=123_457))


if __name__ == "__main__":
    unittest.main()
