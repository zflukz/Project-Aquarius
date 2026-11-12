"""Unit tests for the AI detection template (no Kafka needed).

    python3 -m pytest Data-Pipeline/ai -q        or        python3 Data-Pipeline/ai/test_ai_detection.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

import ai_detection as ai  # noqa: E402
from detectors import Detection, FakeDetector, make_detector  # noqa: E402

META = {"drone_id": "uav1", "ts_us": 1_790_000_000_000_000, "frame_id": 42, "width": 640, "height": 480}


def jpeg(w=640, h=480):
    img = np.zeros((h, w, 3), np.uint8)
    img[:, : w // 2] = (0, 128, 255)
    try:
        import cv2
        return cv2.imencode(".jpg", img)[1].tobytes()
    except ImportError:
        from io import BytesIO
        from PIL import Image
        b = BytesIO()
        Image.fromarray(img).save(b, "JPEG")
        return b.getvalue()


class TestHeaders(unittest.TestCase):
    def test_bridge_headers(self):
        raw = [("schema_version", b"1"), ("drone_id", b"uav1"), ("ts_us", b"1790000000000000"),
               ("frame_id", b"42"), ("width", b"640"), ("height", b"480")]
        self.assertEqual(ai.parse_headers(raw, b"uav1"), META)

    def test_key_fallback_and_bad(self):
        self.assertEqual(ai.parse_headers([("ts_us", b"5")], b"uav3")["drone_id"], "uav3")
        self.assertIsNone(ai.parse_headers([("drone_id", b"drone1"), ("ts_us", b"5")], None))
        self.assertIsNone(ai.parse_headers([("drone_id", b"uav1")], None))          # no ts_us
        self.assertIsNone(ai.parse_headers([("drone_id", b"uav1"), ("ts_us", b"x")], None))


class TestMessage(unittest.TestCase):
    def setUp(self):
        self.validator = ai.load_validator()

    def test_valid_against_schema(self):
        msg = ai.build_message(META, [Detection("person", 0.87, 100, 120, 140, 200)], "yolo:test", 640, 480)
        self.assertEqual(msg["objects"][0]["center_uv"], [120.0, 160.0])
        self.assertEqual((msg["image_w"], msg["image_h"], msg["frame_id"]), (640, 480, 42))
        if self.validator:
            self.validator.validate(msg)

    def test_nothing_found_sends_nothing(self):
        self.assertIsNone(ai.build_message(META, [], "m", 640, 480))
        # box fully outside / zero size after clipping is dropped too
        self.assertIsNone(ai.build_message(META, [Detection("person", 0.9, 700, 10, 800, 50)], "m", 640, 480))

    def test_clipping_and_conf(self):
        msg = ai.build_message(META, [Detection("person", 1.7, -5, -3, 900, 500)], "m", 640, 480)
        o = msg["objects"][0]
        self.assertEqual(o["bbox_xyxy"], [0.0, 0.0, 640.0, 480.0])
        self.assertEqual(o["conf"], 1.0)
        if self.validator:
            self.validator.validate(msg)

    def test_schema_rejects_wrong(self):
        if not self.validator:
            self.skipTest("jsonschema not installed")
        msg = ai.build_message(META, [Detection("person", 0.5, 1, 1, 5, 5)], "m", 640, 480)
        msg["extra"] = 1
        self.assertFalse(self.validator.is_valid(msg))

    def test_ingestion_accepts_it(self):
        """The ingestion service's own row builder takes the message (same as in the DB)."""
        sys.path.insert(0, str(HERE.parents[1] / "docker" / "ingestion"))
        try:
            from aquarius_ingest import rows
        except ImportError as e:
            self.skipTest(f"ingestion not importable: {e}")
        msg = ai.build_message(META, [Detection("person", 0.8, 10, 10, 30, 40)], "m", 640, 480)
        body = json.dumps(msg).encode()
        decoded = rows.decode("detections", body, rows.load_validators(str(HERE.parent / "schemas")))
        row = rows.to_row("detections", decoded)
        self.assertEqual(row[1:6], ("uav1", 42, "m", 640, 480))


class TestDetectorsAndHelpers(unittest.TestCase):
    def test_fake_every_n(self):
        d = FakeDetector(every=3)
        img = np.zeros((480, 640, 3), np.uint8)
        hits = [bool(d.detect(img)) for _ in range(9)]
        self.assertEqual(hits, [False, False, True] * 3)
        box = FakeDetector(every=1).detect(img)[0]
        self.assertEqual(box.center, (320, 240))

    def test_fake_counts_per_drone(self):
        """Interleaved uav1, uav2, ... with every=4: both drones get a hit every 4th of their own frames."""
        d = FakeDetector(every=4)
        img = np.zeros((480, 640, 3), np.uint8)
        hits = {"uav1": 0, "uav2": 0}
        for i in range(16):
            did = "uav1" if i % 2 == 0 else "uav2"
            hits[did] += bool(d.detect(img, {"drone_id": did}))
        self.assertEqual(hits, {"uav1": 2, "uav2": 2})

    def test_detect_fn_old_signature(self):
        class Old:
            version = "old"
            def detect(self, image): return [Detection("person", 0.5, 1, 1, 5, 5)]
        self.assertEqual(len(ai.detect_fn(Old())(None, META)), 1)

    def test_make_detector(self):
        self.assertEqual(make_detector("fake").every, 10)
        self.assertEqual(make_detector("fake:4").every, 4)

    def test_decode_jpeg(self):
        img = ai.decode_jpeg(jpeg())
        self.assertEqual(img.shape, (480, 640, 3))
        self.assertIsNone(ai.decode_jpeg(b"not a jpeg"))

    def test_evidence_path(self):
        p = ai.evidence_path(Path("/x/evidence"), META)
        self.assertEqual(str(p), "/x/evidence/uav1/1790000000000000_000042.jpg")

    def test_too_old(self):
        now = META["ts_us"] / 1e6
        self.assertFalse(ai.too_old(META["ts_us"], 5, now + 4.9))
        self.assertTrue(ai.too_old(META["ts_us"], 5, now + 5.1))
        self.assertFalse(ai.too_old(META["ts_us"], 0, now + 1e6))

    def test_end_to_end_without_kafka(self):
        """JPEG bytes -> decode -> fake detector -> message -> evidence file."""
        data = jpeg()
        img = ai.decode_jpeg(data)
        msg = ai.build_message(META, FakeDetector(every=1).detect(img), "fake-v1", img.shape[1], img.shape[0])
        with tempfile.TemporaryDirectory() as t:
            p = ai.evidence_path(Path(t), META)
            p.parent.mkdir(parents=True)
            p.write_bytes(data)
            self.assertTrue(p.exists())
        json.dumps(msg)
        self.assertEqual(msg["model_version"], "fake-v1")


class TestLoopWithFakeKafka(unittest.TestCase):
    """main() with a stand-in confluent_kafka: 2 drones x 4 frames, fake:2 -> 2 detections per drone, 1 stale frame skipped."""

    def test_main_loop(self):
        import os
        import signal
        import time
        import types

        now_us = int(time.time() * 1e6)
        data = jpeg()

        class Msg:
            def __init__(self, i, ts, drone="uav0"):
                self.i, self.ts, self.drone = i, ts, drone.encode()

            def error(self): return None
            def key(self): return self.drone
            def value(self): return data

            def headers(self):
                return [("drone_id", self.drone), ("ts_us", str(self.ts).encode()), ("frame_id", str(self.i).encode()),
                        ("width", b"640"), ("height", b"480")]

        # uav0 and uav1 interleaved (like real drones), 4 frames each, then one stale uav0 frame
        queue = [Msg(i, now_us, d) for i in range(4) for d in ("uav0", "uav1")] + [Msg(99, now_us - 60_000_000)]
        sent, commits = [], []

        class Consumer:
            def __init__(self, conf): self.conf = conf
            def subscribe(self, topics): assert topics == ["image-frames"]
            def commit(self, message, asynchronous): commits.append(message)
            def close(self): pass

            def poll(self, timeout):
                if queue:
                    return queue.pop(0)
                os.kill(os.getpid(), signal.SIGINT)
                return None

        class Producer:
            def __init__(self, conf): pass
            def produce(self, topic, key, value): sent.append((topic, key, json.loads(value)))
            def poll(self, t): pass
            def flush(self, t): pass

        fake = types.ModuleType("confluent_kafka")
        fake.Consumer, fake.Producer, fake.KafkaException = Consumer, Producer, RuntimeError
        old = sys.modules.get("confluent_kafka")
        old_int = signal.getsignal(signal.SIGINT)
        sys.modules["confluent_kafka"] = fake
        try:
            with tempfile.TemporaryDirectory() as t:
                rc = ai.main(["--detector", "fake:2", "--evidence-dir", t])
                files = {d: sorted(p.name for p in Path(t, d).glob("*.jpg")) for d in ("uav0", "uav1")}
        finally:
            signal.signal(signal.SIGINT, old_int)
            if old is None:
                sys.modules.pop("confluent_kafka")
            else:
                sys.modules["confluent_kafka"] = old
        self.assertEqual(rc, 0)
        self.assertEqual([(t, k, m["frame_id"]) for t, k, m in sent],
                         [("detections", "uav0", 1), ("detections", "uav1", 1),
                          ("detections", "uav0", 3), ("detections", "uav1", 3)])
        self.assertEqual(len(commits), 9)                     # stale frame committed too, never re-read
        want = [f"{now_us}_000001.jpg", f"{now_us}_000003.jpg"]
        self.assertEqual(files, {"uav0": want, "uav1": want})


if __name__ == "__main__":
    unittest.main()
