#!/usr/bin/env python3
"""Save the JPEG frames from Kafka (topic image-frames) as files you can open.

    python3 tools/save_images.py                       # newest 10 frames per drone -> results/images/
    python3 tools/save_images.py --per-drone 30 --drone uav2
    python3 tools/save_images.py --from-beginning      # oldest frames still kept (retention 1 day)

Files are named <drone>_<UTC time>_<frame_id>.jpg, e.g. uav1_20261019T051203.512Z_000123.jpg
Open the folder from WSL in Windows Explorer:  explorer.exe "$(wslpath -w results/images)"

Needs: pip install confluent-kafka
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
import uuid
from collections import defaultdict


def file_name(h: dict, offset: int) -> str:
    did = h.get("drone_id", "unknown")
    try:
        t = dt.datetime.fromtimestamp(int(h["ts_us"]) / 1e6, tz=dt.timezone.utc)
        stamp = t.strftime("%Y%m%dT%H%M%S.") + f"{t.microsecond // 1000:03d}Z"
    except (KeyError, ValueError):
        stamp = f"offset{offset}"
    fid = h.get("frame_id", str(offset))
    return f"{did}_{stamp}_{int(fid):06d}.jpg" if fid.isdigit() else f"{did}_{stamp}_{fid}.jpg"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--out", default="results/images")
    ap.add_argument("--per-drone", type=int, default=10, help="frames to save per drone")
    ap.add_argument("--drone", help="only this drone_id, e.g. uav1")
    ap.add_argument("--from-beginning", action="store_true", help="start at the oldest kept frame")
    ap.add_argument("--timeout", type=float, default=30, help="give up after this many seconds")
    a = ap.parse_args()

    from confluent_kafka import Consumer
    c = Consumer({"bootstrap.servers": a.bootstrap, "group.id": f"save-images-{uuid.uuid4()}",
                  "auto.offset.reset": "earliest" if a.from_beginning else "latest",
                  "enable.auto.commit": False, "max.partition.fetch.bytes": 2097152})
    c.subscribe(["image-frames"])
    os.makedirs(a.out, exist_ok=True)
    saved = defaultdict(int)
    t_start = time.time()
    t_end = t_start + a.timeout
    print(f"waiting for frames on image-frames ({'oldest' if a.from_beginning else 'new'}) ...", file=sys.stderr)
    try:
        while time.time() < t_end:
            m = c.poll(0.5)
            if m is None or m.error():
                continue
            h = {k: v.decode() for k, v in (m.headers() or [])}
            did = h.get("drone_id") or (m.key() or b"unknown").decode()
            h["drone_id"] = did
            if a.drone and did != a.drone:
                continue
            if saved[did] >= a.per_drone:
                # done when every drone seen so far has enough (wait >= 5 s so all drones show up)
                if a.drone or (all(n >= a.per_drone for n in saved.values()) and time.time() - t_start >= 5):
                    break
                continue
            path = os.path.join(a.out, file_name(h, m.offset()))
            with open(path, "wb") as f:
                f.write(m.value() or b"")
            saved[did] += 1
            print(f"{path}  {h.get('width', '?')}x{h.get('height', '?')}  {len(m.value() or b'') / 1024:.1f} KB")
    except KeyboardInterrupt:
        pass
    finally:
        c.close()
    if not saved:
        print("no frames received - is the bridge running with a camera model? (check Kafka UI, topic image-frames)")
        return 1
    print(f"\nsaved {sum(saved.values())} frames in {a.out}: " + ", ".join(f"{d}={n}" for d, n in sorted(saved.items())))
    print(f'open in Windows: explorer.exe "$(wslpath -w {a.out})"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
