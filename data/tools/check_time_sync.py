#!/usr/bin/env python3
"""A4 check: are image timestamps and telemetry timestamps on the same clock?

    python3 tools/check_time_sync.py --seconds 30

Reads telemetry and image-frames from Kafka (private consumer group, newest data
only) and reports per drone:

  clock_gap_ms  median(arrival - ts) of images minus that of telemetry.
                Both travel the same path, so on one clock this is small
                (tens of ms: JPEG encoding). Hundreds of ms or seconds = the
                image clock and the PX4 clock disagree  -> FAIL.
  pair_p95_ms   for each image, distance to the nearest telemetry sample;
                95th percentile. Must be <= --threshold-ms (A4: 50 ms) for
                Georef to interpolate the pose at the moment of the photo.
  bracketed_%   images that have telemetry both before AND after them
                (needed for interpolation; low = clocks shifted).

Needs: pip install confluent-kafka
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
import time
import uuid
from collections import defaultdict


def analyze(tele, imgs, threshold_ms=50.0):
    """tele / imgs: lists of (ts_us, arrival_us) for ONE drone. Returns a dict of metrics."""
    out = {"telemetry": len(tele), "images": len(imgs)}
    if not tele or not imgs:
        out["verdict"] = "NO DATA"
        return out
    t_delay = statistics.median(a - t for t, a in tele) / 1000
    i_delay = statistics.median(a - t for t, a in imgs) / 1000
    ts = sorted(t for t, _ in tele)
    gaps, bracketed = [], 0
    for t, _ in imgs:
        k = bisect.bisect_left(ts, t)
        cands = [abs(t - ts[j]) for j in (k - 1, k) if 0 <= j < len(ts)]
        gaps.append(min(cands) / 1000)
        if 0 < k < len(ts):
            bracketed += 1
    gaps.sort()
    p95 = gaps[min(len(gaps) - 1, int(round(0.95 * (len(gaps) - 1))))]
    out.update({
        "tele_delay_ms": round(t_delay, 1),
        "img_delay_ms": round(i_delay, 1),
        "clock_gap_ms": round(i_delay - t_delay, 1),
        "pair_median_ms": round(statistics.median(gaps), 1),
        "pair_p95_ms": round(p95, 1),
        "bracketed_pct": round(100.0 * bracketed / len(imgs), 1),
    })
    ok = abs(out["clock_gap_ms"]) < 250 and p95 <= threshold_ms and out["bracketed_pct"] >= 90
    out["verdict"] = "PASS" if ok else "FAIL"
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--threshold-ms", type=float, default=50.0)
    a = ap.parse_args()

    from confluent_kafka import Consumer
    c = Consumer({"bootstrap.servers": a.bootstrap, "group.id": f"time-sync-{uuid.uuid4()}",
                  "auto.offset.reset": "latest", "enable.auto.commit": False,
                  "max.partition.fetch.bytes": 2097152})
    c.subscribe(["telemetry", "image-frames"])
    tele, imgs = defaultdict(list), defaultdict(list)
    t_end = None
    print(f"collecting for {a.seconds:.0f} s ...", file=sys.stderr)
    while True:
        m = c.poll(0.5)
        if t_end is None and c.assignment():
            t_end = time.time() + a.seconds
        if t_end is not None and time.time() >= t_end:
            break
        if m is None or m.error():
            continue
        now = time.time_ns() // 1000
        if m.topic() == "telemetry":
            try:
                d = json.loads(m.value())
                tele[d["drone_id"]].append((int(d["ts_us"]), now))
            except (ValueError, KeyError):
                pass
        else:
            h = {k: v.decode() for k, v in (m.headers() or [])}
            if "ts_us" in h and "drone_id" in h:
                imgs[h["drone_id"]].append((int(h["ts_us"]), now))
    c.close()

    drones = sorted(set(tele) | set(imgs), key=lambda s: (len(s), s))
    if not drones:
        print("no data received - is the bridge running?")
        return 1
    cols = ["telemetry", "images", "tele_delay_ms", "img_delay_ms", "clock_gap_ms",
            "pair_median_ms", "pair_p95_ms", "bracketed_pct", "verdict"]
    print("drone   " + "  ".join(f"{k:>14}" for k in cols))
    failed = False
    for d in drones:
        r = analyze(tele[d], imgs[d], a.threshold_ms)
        failed |= r["verdict"] != "PASS"
        print(f"{d:<7} " + "  ".join(f"{str(r.get(k, '-')):>14}" for k in cols))
    print("\nclock_gap_ms large -> image and PX4 clocks differ (check use_sim_time:=true and /clock bridge)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
