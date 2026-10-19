#!/usr/bin/env python3
"""Fake drones -> Kafka, for testing the pipeline before PX4 SITL is ready.

Each drone flies a lawnmower path over its own strip of a square area, sends
telemetry at 10 Hz and a synthetic JPEG at 2 fps, exactly in the A2 format.
When its battery drops below 20% it switches to AUTO_RTL and an
`rtl_triggered` event is sent to replanning-events.

Examples
    python3 tools/sim_producer.py --drones 3                 # run until Ctrl+C
    python3 tools/sim_producer.py --drones 3 --seconds 60 --loss 0.05
    python3 tools/sim_producer.py --dry-run --seconds 2      # print + validate, no Kafka

Needs: pip install confluent-kafka pillow   (jsonschema for --dry-run)
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import random
import sys
import time
import uuid

EARTH_R = 6_378_137.0
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def offset_latlon(lat0, lon0, north_m, east_m):
    dlat = math.degrees(north_m / EARTH_R)
    dlon = math.degrees(east_m / (EARTH_R * math.cos(math.radians(lat0))))
    return lat0 + dlat, lon0 + dlon


class FakeDrone:
    """Lawnmower over the strip [x0, x0+width] x [0, length] (east, north, metres)."""

    def __init__(self, n, total, area_m, speed, spacing, lat0, lon0, drain_per_min, battery0):
        self.id = f"uav{n}"
        width = area_m / total
        self.x0, self.width, self.length = (n - 1) * width, width, area_m
        self.speed, self.spacing = speed, spacing
        self.lat0, self.lon0 = lat0, lon0
        self.dist = 0.0
        self.battery = battery0
        self.drain = drain_per_min / 60.0
        self.rtl = False
        self.frame_id = 0

    def _waypoints(self):
        """Boustrophedon over the strip, then the same path backwards (a closed loop)."""
        lanes = max(1, int(self.width // self.spacing))
        xs = [self.x0 + self.spacing / 2 + i * self.spacing for i in range(lanes)]
        out = []
        for i, x in enumerate(xs):
            ys = (0.0, self.length) if i % 2 == 0 else (self.length, 0.0)
            out += [(x, ys[0]), (x, ys[1])]
        return out + out[-2:0:-1]

    def _pos_on_path(self, d):
        if not hasattr(self, "_wps"):
            self._wps = self._waypoints()
            segs = list(zip(self._wps, self._wps[1:] + self._wps[:1]))
            self._segs = [(a, b, math.dist(a, b)) for a, b in segs if math.dist(a, b) > 0]
            self._loop = sum(L for _, _, L in self._segs)
        d %= self._loop
        for (ax, ay), (bx, by), L in self._segs:
            if d <= L:
                f = d / L
                heading = math.atan2(bx - ax, by - ay)  # 0 = north, pi/2 = east
                return ax + f * (bx - ax), ay + f * (by - ay), heading
            d -= L
        ax, ay = self._segs[0][0]
        return ax, ay, 0.0

    def step(self, dt):
        self.dist += self.speed * dt
        self.battery = max(0.0, self.battery - self.drain * dt)

    def telemetry(self, mission_id, ts_us):
        x, y, yaw = self._pos_on_path(self.dist)
        lat, lon = offset_latlon(self.lat0, self.lon0, y, x)
        vn, ve = self.speed * math.cos(yaw), self.speed * math.sin(yaw)
        return {
            "schema_version": 1, "drone_id": self.id, "mission_id": mission_id, "ts_us": ts_us,
            "lat": round(lat, 8), "lon": round(lon, 8), "alt_amsl": 32.0, "alt_rel": 30.0,
            "q": [round(math.cos(yaw / 2), 6), 0.0, 0.0, round(math.sin(yaw / 2), 6)],
            "vel_ned": [round(vn, 3), round(ve, 3), 0.0],
            "battery_remaining": round(self.battery, 4),
            "battery_voltage": round(13.2 + 3.6 * self.battery, 3),
            "nav_state": "AUTO_RTL" if self.rtl else "OFFBOARD", "armed": True,
        }


def make_jpeg(drone_id, frame_id, w, h, quality):
    from PIL import Image, ImageDraw
    rnd = random.Random(hash((drone_id, frame_id)))
    # low-frequency noise: water/debris-like texture with a realistic JPEG size
    img = Image.effect_noise((max(1, w // 6), max(1, h // 6)), 60).convert("RGB").resize((w, h))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 40], fill=(20, 40, 70))
    d.text((10, 12), f"{drone_id} frame {frame_id}", fill=(255, 255, 255))
    for _ in range(6):  # some "debris" so the JPEG size is realistic
        x, y = rnd.randrange(w), rnd.randrange(h)
        d.ellipse([x, y, x + rnd.randrange(10, 80), y + rnd.randrange(10, 80)],
                  fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--drones", type=int, default=3)
    ap.add_argument("--seconds", type=float, default=0, help="0 = run until Ctrl+C")
    ap.add_argument("--hz", type=float, default=10.0, help="telemetry rate per drone")
    ap.add_argument("--fps", type=float, default=2.0, help="images per second per drone (0 = none)")
    ap.add_argument("--image-size", default="1280x720")
    ap.add_argument("--jpeg-quality", type=int, default=80)
    ap.add_argument("--center", default="13.7563,100.5018", help="lat,lon of the area's south-west corner")
    ap.add_argument("--area-m", type=float, default=300.0)
    ap.add_argument("--speed", type=float, default=5.0)
    ap.add_argument("--spacing", type=float, default=30.0, help="lane spacing (m)")
    ap.add_argument("--battery-start", type=float, default=1.0)
    ap.add_argument("--drain-per-min", type=float, default=0.05, help="battery fraction used per minute")
    ap.add_argument("--loss", type=float, default=0.0, help="drop this fraction of telemetry on purpose")
    ap.add_argument("--mission-id", default="")
    ap.add_argument("--dry-run", action="store_true", help="print and schema-check messages, no Kafka")
    a = ap.parse_args()

    lat0, lon0 = (float(v) for v in a.center.split(","))
    w, h = (int(v) for v in a.image_size.lower().split("x"))
    mission_id = a.mission_id or str(uuid.uuid4())
    drones = [FakeDrone(n, a.drones, a.area_m, a.speed, a.spacing, lat0, lon0, a.drain_per_min, a.battery_start)
              for n in range(1, a.drones + 1)]

    if a.dry_run:
        import jsonschema
        schemas = {}
        for t in ("telemetry", "replanning-events"):
            with open(os.path.join(REPO, "schemas", f"{t}.schema.json")) as f:
                schemas[t] = json.load(f)

        def send(topic, key, value, headers=None):
            if topic == "image-frames":
                print(f"{topic:18} {key} {len(value) // 1024} KB {dict((k, v.decode()) for k, v in headers)}")
                return
            msg = json.loads(value)
            jsonschema.validate(msg, schemas[topic])
            print(f"{topic:18} {key} {value.decode()[:150]}...")
        flush = poll = lambda *_: None  # noqa: E731
    else:
        from confluent_kafka import Producer
        fast = Producer({"bootstrap.servers": a.bootstrap, "acks": "1", "enable.idempotence": False,
                         "linger.ms": 5, "message.max.bytes": 2097152})
        safe = Producer({"bootstrap.servers": a.bootstrap, "acks": "all", "enable.idempotence": True})

        def send(topic, key, value, headers=None):
            p = safe if topic == "replanning-events" else fast
            p.produce(topic, key=key.encode(), value=value, headers=headers)

        def poll():
            fast.poll(0)
            safe.poll(0)

        def flush():
            fast.flush(5)
            safe.flush(5)

    print(f"mission_id={mission_id} drones={[d.id for d in drones]} -> "
          f"{'DRY RUN' if a.dry_run else a.bootstrap}", file=sys.stderr)
    dt = 1.0 / a.hz
    img_every = int(round(a.hz / a.fps)) if a.fps > 0 else 0
    start, tick, sent, dropped = time.time(), 0, 0, 0
    try:
        while a.seconds <= 0 or time.time() - start < a.seconds:
            ts_us = time.time_ns() // 1000
            for d in drones:
                d.step(dt)
                if not d.rtl and d.battery < 0.20:
                    d.rtl = True
                    ev = {"schema_version": 1, "event_id": str(uuid.uuid4()), "mission_id": mission_id,
                          "ts_us": ts_us, "event_type": "rtl_triggered", "drone_id": d.id,
                          "battery_remaining": round(d.battery, 4), "waypoints_reassigned": None,
                          "assigned_to": None}
                    send("replanning-events", mission_id, json.dumps(ev).encode())
                if random.random() < a.loss:
                    dropped += 1
                else:
                    send("telemetry", d.id, json.dumps(d.telemetry(mission_id, ts_us)).encode())
                    sent += 1
                if img_every and tick % img_every == 0:
                    jpeg = make_jpeg(d.id, d.frame_id, w, h, a.jpeg_quality)
                    headers = [("schema_version", b"1"), ("drone_id", d.id.encode()),
                               ("ts_us", str(ts_us).encode()), ("frame_id", str(d.frame_id).encode()),
                               ("width", str(w).encode()), ("height", str(h).encode())]
                    send("image-frames", d.id, jpeg, headers)
                    d.frame_id += 1
            poll()
            tick += 1
            time.sleep(max(0.0, start + tick * dt - time.time()))
    except KeyboardInterrupt:
        pass
    flush()
    print(f"done: telemetry sent={sent} dropped_on_purpose={dropped}", file=sys.stderr)


if __name__ == "__main__":
    main()
