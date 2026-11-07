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
                95th percentile (information only: at 10 Hz it sits near 50 ms
                by construction, half the telemetry period).
  bracketed_%   images that have a telemetry sample before AND after them, each
                within --bracket-ms (A4 decision: 100 ms), so Georef can
                interpolate the pose at the moment of the photo.

PASS = |clock_gap_ms| < 250 and bracketed_% >= 90.

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


def analyze(tele, imgs, bracket_ms=100.0):
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
        if 0 < k < len(ts) and t - ts[k - 1] <= bracket_ms * 1000 and ts[k] - t <= bracket_ms * 1000:
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
    why = []
    if abs(out["clock_gap_ms"]) >= 250:
        why.append(f"clock_gap {out['clock_gap_ms']} ms (image and PX4 clocks differ: check use_sim_time:=true, /clock bridge, PX4_TIMESYNC=0)")
    if out["bracketed_pct"] < 90:
        why.append(f"only {out['bracketed_pct']}% of images have telemetry within {bracket_ms:.0f} ms on both sides")
    out["verdict"] = "FAIL" if why else "PASS"
    out["why"] = "; ".join(why)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--bracket-ms", type=float, default=100.0,
                    help="max distance to the telemetry sample before and after each image (A4: 100)")
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
    failed, reasons = False, []
    for d in drones:
        r = analyze(tele[d], imgs[d], a.bracket_ms)
        failed |= r["verdict"] != "PASS"
        print(f"{d:<7} " + "  ".join(f"{str(r.get(k, '-')):>14}" for k in cols))
        if r.get("why"):
            reasons.append(f"{d}: {r['why']}")
    print(f"\ncriteria: |clock_gap| < 250 ms and >= 90% of images bracketed by telemetry within {a.bracket_ms:.0f} ms")
    for line in reasons:
        print("FAIL " + line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
