#!/usr/bin/env python3
"""AI Detection template: Kafka image-frames -> person detector -> Kafka detections.

    python3 ai_detection.py --detector fake            # no model: fake person every 10th frame
    python3 ai_detection.py --detector fake:4          # every 4th frame
    python3 ai_detection.py --detector yolov8n.pt      # real YOLO (pip install ultralytics)
    python3 ai_detection.py --detector best.pt --conf 0.5 --drone uav1

What comes in (topic `image-frames`, written by aquarius_bridge, 2 fps per drone):
    key   = drone_id ("uav0")
    value = JPEG bytes (no re-encoding, the same bytes imagestream / Grafana show)
    headers: schema_version, drone_id, ts_us (UTC microseconds, GPS-synced), frame_id, width, height

What goes out (topic `detections`, schema Data-Pipeline/schemas/detections.schema.json):
    one JSON message per frame that has >= 1 object, key = drone_id. Frames with nothing
    found send nothing. The ingestion service stores it in the `detections` table.

Evidence image: every frame with a detection is also saved as
    <evidence-dir>/<drone_id>/<ts_us>_<frame_id>.jpg
so Georef can put that path into survivors.snapshot_path (dashboard pin photo).

Only the detector is yours to change: see detectors.py. The rest (Kafka, schema,
timestamps, evidence, lag) is already done here.

Needs: pip install -r requirements.txt   (confluent-kafka, numpy, opencv-python-headless, jsonschema)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from detectors import Detection, make_detector  # noqa: E402
import inspect  # noqa: E402

log = logging.getLogger("ai_detection")
DRONE_RE = re.compile(r"^uav[0-9]+$")
SCHEMA_PATH = HERE.parent / "schemas" / "detections.schema.json"
SCHEMA_VERSION = 1


# ---------- pure helpers (unit-tested, no Kafka) ----------

def parse_headers(raw_headers, key: bytes | None) -> dict | None:
    """Kafka headers -> dict with typed values, or None if the frame is unusable."""
    h = {k: (v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v))
         for k, v in (raw_headers or [])}
    did = h.get("drone_id") or (key or b"").decode("utf-8", "replace")
    if not DRONE_RE.match(did):
        return None
    try:
        return {"drone_id": did, "ts_us": int(h["ts_us"]), "frame_id": int(h.get("frame_id", 0)),
                "width": int(h.get("width", 0)), "height": int(h.get("height", 0))}
    except (KeyError, ValueError):
        return None


def decode_jpeg(data: bytes):
    """JPEG bytes -> numpy BGR image (H, W, 3), or None."""
    import numpy as np
    try:
        import cv2
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        return img
    except ImportError:
        from io import BytesIO
        from PIL import Image
        try:
            return np.asarray(Image.open(BytesIO(data)).convert("RGB"))[:, :, ::-1].copy()
        except Exception:
            return None


def _clip(v: float, hi: int) -> float:
    return round(min(max(float(v), 0.0), float(hi)), 1)


def build_message(meta: dict, dets: list[Detection], model_version: str, img_w: int, img_h: int) -> dict | None:
    """One detections message (schema v1) or None when nothing was found."""
    objects = []
    for d in dets:
        x1, y1, x2, y2 = _clip(d.x1, img_w), _clip(d.y1, img_h), _clip(d.x2, img_w), _clip(d.y2, img_h)
        if x2 <= x1 or y2 <= y1:
            continue
        objects.append({"class": str(d.cls), "conf": round(min(max(float(d.conf), 0.0), 1.0), 3),
                        "bbox_xyxy": [x1, y1, x2, y2],
                        "center_uv": [round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1)]})
    if not objects:
        return None
    return {"schema_version": SCHEMA_VERSION, "drone_id": meta["drone_id"], "frame_id": meta["frame_id"],
            "ts_us": meta["ts_us"], "model_version": model_version,
            "image_w": int(img_w), "image_h": int(img_h), "objects": objects}


def evidence_path(root: Path, meta: dict) -> Path:
    return Path(root) / meta["drone_id"] / f"{meta['ts_us']}_{meta['frame_id']:06d}.jpg"


def too_old(ts_us: int, max_lag_s: float, now_s: float | None = None) -> bool:
    """True if the frame is older than max_lag_s (0 = never skip). Old frames waste GPU time."""
    if max_lag_s <= 0:
        return False
    now_s = time.time() if now_s is None else now_s
    return (now_s * 1e6 - ts_us) > max_lag_s * 1e6


def load_validator():
    try:
        import jsonschema
    except ImportError:
        log.warning("jsonschema not installed: messages are not checked against the schema")
        return None
    schema = json.loads(SCHEMA_PATH.read_text())
    return jsonschema.Draft202012Validator(schema) if "2020-12" in schema.get("$schema", "") \
        else jsonschema.Draft7Validator(schema)


def detect_fn(detector):
    """detector.detect as f(image, meta): passes meta only if detect() takes a 2nd argument."""
    params = list(inspect.signature(detector.detect).parameters.values())
    if len(params) >= 2 or any(p.kind == p.VAR_POSITIONAL for p in params):
        return detector.detect
    return lambda image, meta: detector.detect(image)


# ---------- Kafka loop ----------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bootstrap", default=os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"))
    ap.add_argument("--detector", default=os.environ.get("AI_DETECTOR", "fake"),
                    help="'fake', 'fake:N' or a model file (yolov8n.pt, best.pt, model.onnx)")
    ap.add_argument("--conf", type=float, default=0.4, help="min confidence (YOLO)")
    ap.add_argument("--drone", action="append", help="only these drone_ids (repeat), default all")
    ap.add_argument("--group", default="ai-detection", help="Kafka consumer group (share it to split the load)")
    ap.add_argument("--from-beginning", action="store_true", help="start at the oldest frame kept (default newest)")
    ap.add_argument("--max-lag-s", type=float, default=5.0, help="skip frames older than this, 0 = never")
    ap.add_argument("--evidence-dir", default=os.environ.get("EVIDENCE_DIR", str(HERE.parent / "results" / "evidence")))
    ap.add_argument("--no-evidence", action="store_true", help="do not save evidence JPEGs")
    ap.add_argument("--dry-run", action="store_true", help="print messages, do not send to Kafka")
    args = ap.parse_args(argv)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from confluent_kafka import Consumer, KafkaException, Producer

    detector = make_detector(args.detector, conf=args.conf)
    detect = detect_fn(detector)
    validator = load_validator()
    consumer = Consumer({"bootstrap.servers": args.bootstrap, "group.id": args.group,
                         "auto.offset.reset": "earliest" if args.from_beginning else "latest",
                         "enable.auto.commit": False, "max.partition.fetch.bytes": 2097152})
    producer = None if args.dry_run else Producer({"bootstrap.servers": args.bootstrap, "linger.ms": 5,
                                                     "enable.idempotence": True})
    consumer.subscribe(["image-frames"])
    log.info("detector %s, reading image-frames from %s (group %s)", detector.version, args.bootstrap, args.group)

    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    stats = dict(frames=0, sent=0, objects=0, skipped_old=0, bad=0, infer_ms=0.0)
    last_log = time.time()
    try:
        while not stop["now"]:
            m = consumer.poll(0.5)
            if m is not None and not m.error():
                meta = parse_headers(m.headers(), m.key())
                if meta is None or not m.value():
                    stats["bad"] += 1
                elif args.drone and meta["drone_id"] not in args.drone:
                    pass
                elif too_old(meta["ts_us"], args.max_lag_s):
                    stats["skipped_old"] += 1
                else:
                    img = decode_jpeg(m.value())
                    if img is None:
                        stats["bad"] += 1
                    else:
                        stats["frames"] += 1
                        t0 = time.perf_counter()
                        dets = detect(img, meta)
                        stats["infer_ms"] += (time.perf_counter() - t0) * 1000
                        h, w = img.shape[:2]
                        msg = build_message(meta, dets, detector.version, w, h)
                        if msg is not None:
                            if validator is not None:
                                validator.validate(msg)
                            if not args.no_evidence:
                                p = evidence_path(Path(args.evidence_dir), meta)
                                p.parent.mkdir(parents=True, exist_ok=True)
                                p.write_bytes(m.value())
                            body = json.dumps(msg, separators=(",", ":"))
                            if producer is None:
                                print(body, flush=True)
                            else:
                                producer.produce("detections", key=meta["drone_id"], value=body)
                                producer.poll(0)
                            stats["sent"] += 1
                            stats["objects"] += len(msg["objects"])
                consumer.commit(message=m, asynchronous=True)
            if time.time() - last_log >= 10:
                n = max(stats["frames"], 1)
                log.info("frames %d, sent %d (%d objects), skipped old %d, bad %d, avg infer %.0f ms",
                         stats["frames"], stats["sent"], stats["objects"], stats["skipped_old"], stats["bad"],
                         stats["infer_ms"] / n)
                last_log = time.time()
    except KafkaException as e:
        log.error("kafka: %s", e)
        return 1
    finally:
        if producer is not None:
            producer.flush(5)
        consumer.close()
        log.info("stopped: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
