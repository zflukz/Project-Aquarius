"""Person detectors for ai_detection.py. Put the team's model in a class like these.

A detector gets one image (numpy array, H x W x 3, BGR like OpenCV) and returns
a list of Detection. Coordinates are pixels from the top-left corner.

    FakeDetector         no model: "finds" one person in every Nth frame (pipeline tests)
    UltralyticsDetector  any YOLO .pt / .onnx through the `ultralytics` package (class 0 = person)

Add your own:

    class MyDetector:
        version = "my-model-v1"          # goes into detections.model_version
        def detect(self, image):          # -> list[Detection]
            ...
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Detection:
    cls: str
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self):
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2


class FakeDetector:
    """One fake person in the middle of every `every`-th frame. Tests the whole
    path (Kafka -> AI -> detections -> evidence image) without a model or real people."""

    version = "fake-v1"

    def __init__(self, every: int = 10, size_px: int = 40):
        self.every = max(1, every)
        self.size = size_px
        self._n = 0

    def detect(self, image) -> List[Detection]:
        self._n += 1
        if self._n % self.every:
            return []
        h, w = image.shape[:2]
        s = min(self.size, w // 2, h // 2)
        return [Detection("person", 0.9, w / 2 - s / 2, h / 2 - s / 2, w / 2 + s / 2, h / 2 + s / 2)]


class UltralyticsDetector:
    """YOLO through `pip install ultralytics`. Keeps only `classes` (COCO 0 = person)."""

    def __init__(self, model_path: str, conf: float = 0.4, classes: Optional[List[int]] = None,
                 device: Optional[str] = None):
        from ultralytics import YOLO      # imported here so the template runs without it
        self.model = YOLO(model_path)
        self.conf = conf
        self.classes = classes if classes is not None else [0]
        self.device = device
        self.version = f"yolo:{model_path.rsplit('/', 1)[-1]}"

    def detect(self, image) -> List[Detection]:
        res = self.model.predict(image, conf=self.conf, classes=self.classes, device=self.device, verbose=False)[0]
        names = res.names
        out = []
        for box in res.boxes:
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            out.append(Detection(names[int(box.cls[0])], float(box.conf[0]), x1, y1, x2, y2))
        return out


def make_detector(spec: str, conf: float = 0.4):
    """--detector value -> detector: 'fake', 'fake:5' (every 5th frame) or a model path ('yolov8n.pt')."""
    if spec == "fake" or spec.startswith("fake:"):
        every = int(spec.split(":", 1)[1]) if ":" in spec else 10
        return FakeDetector(every=every)
    return UltralyticsDetector(spec, conf=conf)
