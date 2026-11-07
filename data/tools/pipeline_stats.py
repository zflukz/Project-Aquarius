#!/usr/bin/env python3
"""Measure what actually arrives in Kafka: loss, latency and image size per drone.

Used for
  * B2 done-check   : data from 3 simulated drones reaches every topic
  * DDS test        : loss with Fast DDS Discovery Server over Wi-Fi, clean vs. degraded link
  * D3 / A3 checks  : latency drone -> Kafka, real JPEG size from the Gazebo camera

    python3 tools/pipeline_stats.py --seconds 60
    python3 tools/pipeline_stats.py --seconds 60 --csv results/dds_loss5.csv

Loss and rates are computed from ts_us (the data's own clock), so they do not
depend on when the window starts, and stay correct when Gazebo runs slower than
real time. telemetry_rate_wall_hz = messages per real second (shows the slowdown). Latency = arrival wall time - ts_us,
so it is only meaningful when the clocks agree (same machine, or chrony synced),
and in Gazebo only when the real-time factor is ~1.0.

Needs: pip install confluent-kafka
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
import uuid
from collections import defaultdict


def pct(values, p):
    if not values:
        return float("nan")
    s = sorted(values)
    k = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[k]


class DroneStats:
    def __init__(self):
        self.tele_ts = []
        self.tele_lat_ms = []
        self.img_count = 0
        self.img_bytes = []
        self.img_lat_ms = []
        self.img_ts = []

    def loss(self, hz):
        ts = sorted(set(self.tele_ts))
        if len(ts) < 2:
            return 0, 0, float("nan")
        period = 1e6 / hz
        missing = sum(max(0, round((b - a) / period) - 1) for a, b in zip(ts, ts[1:]))
        expected = len(ts) + missing
        return len(ts), expected, 100.0 * missing / expected


def data_rate(ts_list):
    ts = sorted(set(ts_list))
    if len(ts) < 2 or ts[-1] == ts[0]:
        return 0.0
    return round((len(ts) - 1) / ((ts[-1] - ts[0]) / 1e6), 2)


def summarize(stats, hz, seconds):
    rows = []
    for did in sorted(stats, key=lambda s: (len(s), s)):
        s = stats[did]
        got, expected, loss = s.loss(hz)
        rows.append({
            "drone_id": did,
            "telemetry_msgs": got,
            "telemetry_expected": expected,
            "telemetry_loss_pct": round(loss, 2),
            # rates measured on the data's own clock (ts_us), so a Gazebo running
            # slower than real time does not look like data loss
            "telemetry_rate_hz": data_rate(s.tele_ts),
            "telemetry_rate_wall_hz": round(len(s.tele_ts) / seconds, 2),
            "latency_p50_ms": round(pct(s.tele_lat_ms, 50), 1),
            "latency_p95_ms": round(pct(s.tele_lat_ms, 95), 1),
            "latency_max_ms": round(max(s.tele_lat_ms), 1) if s.tele_lat_ms else float("nan"),
            "images": s.img_count,
            "image_fps": data_rate(s.img_ts),
            "image_avg_kb": round(statistics.mean(s.img_bytes) / 1024, 1) if s.img_bytes else 0,
            "image_max_kb": round(max(s.img_bytes) / 1024, 1) if s.img_bytes else 0,
            "image_latency_p50_ms": round(pct(s.img_lat_ms, 50), 1),
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--hz", type=float, default=10.0, help="expected telemetry rate per drone")
    ap.add_argument("--csv", help="also write the table to this CSV file")
    a = ap.parse_args()

    from confluent_kafka import Consumer
    c = Consumer({
        "bootstrap.servers": a.bootstrap,
        "group.id": f"pipeline-stats-{uuid.uuid4()}",   # private group: never steals messages
        "auto.offset.reset": "latest",
        "enable.auto.commit": False,
        "max.partition.fetch.bytes": 2097152,
    })
    c.subscribe(["telemetry", "image-frames"])
    stats = defaultdict(DroneStats)
    bad = 0

    print(f"measuring for {a.seconds:.0f} s ...", file=sys.stderr)
    t_end = None
    while True:
        msg = c.poll(0.5)
        now = time.time()
        if t_end is None and c.assignment():
            t_end = now + a.seconds
        if t_end is not None and now >= t_end:
            break
        if msg is None or msg.error():
            continue
        now_us = time.time_ns() // 1000
        if msg.topic() == "telemetry":
            try:
                m = json.loads(msg.value())
                s = stats[m["drone_id"]]
                s.tele_ts.append(int(m["ts_us"]))
                s.tele_lat_ms.append((now_us - int(m["ts_us"])) / 1000)
            except (ValueError, KeyError, TypeError):
                bad += 1
        else:
            h = {k: v.decode() for k, v in (msg.headers() or [])}
            did = h.get("drone_id") or (msg.key() or b"?").decode()
            s = stats[did]
            s.img_count += 1
            s.img_bytes.append(len(msg.value() or b""))
            if "ts_us" in h:
                s.img_lat_ms.append((now_us - int(h["ts_us"])) / 1000)
                s.img_ts.append(int(h["ts_us"]))
    c.close()

    rows = summarize(stats, a.hz, a.seconds)
    if not rows:
        print("no data received - is anything producing? (check `docker compose ps` and the bridge log)")
        return 1
    cols = list(rows[0])
    print("  ".join(f"{k:>{max(len(k), 6)}}" for k in cols))
    for r in rows:
        print("  ".join(f"{str(r[k]):>{max(len(k), 6)}}" for k in cols))
    if bad:
        print(f"({bad} telemetry messages could not be parsed)")
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        print(f"saved {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
