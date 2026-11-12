# AI Detection template

Ready-made Kafka service for the AI team: it reads the drone camera frames, runs a
person detector and writes the results where the rest of the pipeline expects them.
**Only the detector is yours to write** (`detectors.py`); Kafka, schema, timestamps,
evidence images and lag handling are done.

```
PX4/Gazebo camera ─► aquarius_bridge ─► Kafka image-frames ─┬─► imagestream :8090 / Grafana (view)
                     (2 fps, JPEG, GPS-synced ts_us)        └─► ai_detection.py  (this folder)
                                                                  │  detector.detect(image, meta)
                                                                  ▼
                                    Kafka detections ─► ingestion ─► TimescaleDB `detections`
                                    evidence JPEG ─► results/evidence/<drone>/<ts_us>_<frame>.jpg
```

Both readers use their own consumer group, so viewing in Grafana and AI detection do not
take frames from each other.

## Files

| File | What |
| :--- | :--- |
| `ai_detection.py` | the service: consumer `image-frames` → detector → producer `detections` |
| `detectors.py` | detectors: `FakeDetector` (no model), `UltralyticsDetector` (YOLO); add yours here |
| `test_ai_detection.py` | 16 unit tests, no Kafka needed (one runs `main()` against a stand-in Kafka) |
| `requirements.txt` | `confluent-kafka`, `numpy`, `opencv-python-headless`, `jsonschema` (+ optional `ultralytics`) |

## Run

The pipeline must be up (Kafka + bridge): `aquarius pipeline up` (see `docker/README.md`).

```bash
cd Data-Pipeline/ai
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

python3 test_ai_detection.py                 # unit tests
python3 ai_detection.py --detector fake      # fake person in every 10th frame -> real Kafka
python3 ai_detection.py --detector fake --dry-run   # print messages instead of sending

pip install ultralytics
python3 ai_detection.py --detector yolov8n.pt --conf 0.4     # COCO person (class 0)
python3 ai_detection.py --detector best.pt --drone uav1      # your trained model, one drone
```

| Option | Default | |
| :--- | :--- | :--- |
| `--bootstrap` | `$KAFKA_BOOTSTRAP` or `localhost:9092` | Kafka |
| `--detector` | `$AI_DETECTOR` or `fake` | `fake`, `fake:N`, or a model file |
| `--conf` | 0.4 | min confidence (YOLO) |
| `--drone` | all | only these drones, repeatable |
| `--group` | `ai-detection` | run 2+ copies with the same group to share the drones |
| `--from-beginning` | off | start at the oldest kept frame (default: newest) |
| `--max-lag-s` | 5 | skip frames older than this (catch up after a pause), 0 = never |
| `--evidence-dir` | `$EVIDENCE_DIR` or `Data-Pipeline/results/evidence` | where evidence JPEGs go |
| `--no-evidence`, `--dry-run` | | |

Every 10 s it logs `frames, sent (objects), skipped old, bad, avg infer ms`.

Check the output:

```bash
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 \
  --topic detections --max-messages 3
psql -h localhost -U aquarius -d aquarius -c \
  "SELECT ts, drone_id, frame_id, model_version, jsonb_array_length(objects) FROM detections ORDER BY ts DESC LIMIT 5"
```

## Input: `image-frames`

| | |
| :--- | :--- |
| key | `drone_id` (`uav0` … `uavN-1`) |
| value | JPEG bytes (640×480 in the sim) |
| headers | `schema_version`, `drone_id`, `ts_us` (UTC µs, GPS-synced, ±100 ms), `frame_id`, `width`, `height` |

## Output: `detections` (schema `Data-Pipeline/schemas/detections.schema.json`)

One message per frame **with at least one object**; frames with nothing found send nothing.
Key = `drone_id`. `ts_us` and `frame_id` are copied from the frame, so Georef can match the
telemetry sample (position, attitude) of the same moment.

```json
{"schema_version": 1, "drone_id": "uav1", "frame_id": 42, "ts_us": 1790000000000000,
 "model_version": "yolo:yolov8n.pt", "image_w": 640, "image_h": 480,
 "objects": [{"class": "person", "conf": 0.87,
              "bbox_xyxy": [100.0, 120.0, 140.0, 200.0], "center_uv": [120.0, 160.0]}]}
```

Pixels from the top-left corner; boxes are clipped to the image, `conf` to 0–1,
`center_uv` = box center. Every message is checked against the schema before sending
(same check the ingestion service does).

## Your own model

```python
# detectors.py
class MyDetector:
    version = "aquarius-person-v3"          # stored in detections.model_version

    def __init__(self, path, conf):
        self.net = load(path); self.conf = conf

    def detect(self, image, meta=None):      # numpy BGR (H, W, 3); meta = drone_id, ts_us, frame_id, ...
        return [Detection("person", score, x1, y1, x2, y2) for ... in self.net(image) if score >= self.conf]
```

then return it from `make_detector()` (e.g. for paths ending in `.engine`). Keep `detect()`
under ~500 ms per frame per drone (2 fps); if it is slower, frames older than `--max-lag-s`
are skipped rather than piling up.

## Not done yet

- No compose service / Docker image (GPU on the VM is still open); runs as a script next to the stack.
- Georef (detection + telemetry → `survivors` with lat/lon) is the next consumer of `detections`.
