#!/usr/bin/env python3
"""How well did the detector find the people? Per camera frame, against ground_truth_persons (F4).

For every camera frame of a time window it projects each ground-truth person into the image (drone pose from
`telemetry`, camera model below) and compares with the boxes in `detections`:

    TP  a detection near a person who is in the image          FN  a person in the image, no detection near it
    FP  a detection near no person

It also prints how big a person is in the image (ground sample distance), which decides whether a detector can
see them at all. Needs no Georef: positions are projected from the known truth, not estimated from the boxes.

    python3 tools/eval_detections.py --mission <uuid>                 # whole mission
    python3 tools/eval_detections.py --since "2026-11-26 05:45" --until "2026-11-26 05:56" --conf 0.4
    python3 tools/eval_detections.py --mission <uuid> --csv /tmp/eval.csv

Camera model (gz_x500_mono_cam_down): pinhole, hfov 1.74 rad, image size from the frames (640×480), looking
straight down from the body; image right = body right (+y FRD), image down = body backward (−x FRD). Checked on
a 2 m red box in the KMUTT sim: median 6.6 px between the projected and the seen box centre (44 frames, 10 m).
Frames come from Kafka `image-frames` (headers only are used; kept 1 day), the rest from TimescaleDB through
`docker exec timescaledb psql`. Needs confluent-kafka (e.g. Data-Pipeline/ai/.venv/bin/python).
"""
from __future__ import annotations

import argparse
import bisect
import csv
import math
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass

WGS84_A, WGS84_E2 = 6_378_137.0, 6.69437999014e-3
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9:.]+([+-][0-9:]+|Z)?$")


# ---------- geometry (unit-tested) ----------
def quat_to_matrix(w, x, y, z):
    """Rotation body (FRD) -> NED for PX4's q = (w, x, y, z)."""
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]]


def ned_offset(lat, lon, alt, lat_t, lon_t, alt_t):
    """Metres north / east / down from (lat, lon, alt) to the target (local tangent plane)."""
    p = math.radians(lat)
    s = math.sin(p) ** 2
    m = WGS84_A * (1 - WGS84_E2) / (1 - WGS84_E2 * s) ** 1.5
    n = WGS84_A / math.sqrt(1 - WGS84_E2 * s)
    return (math.radians(lat_t - lat) * m, math.radians(lon_t - lon) * n * math.cos(p), -(alt_t - alt))


@dataclass
class Camera:
    width: int = 640
    height: int = 480
    hfov: float = 1.74

    @property
    def f(self) -> float:
        return (self.width / 2) / math.tan(self.hfov / 2)

    def project(self, pose, target):
        """pose = (lat, lon, alt_amsl, w, x, y, z), target = (lat, lon, alt_amsl) ->
        (u, v, depth_m) or None when the target is behind / above the camera."""
        n, e, d = ned_offset(pose[0], pose[1], pose[2], *target)
        r = quat_to_matrix(*pose[3:7])
        bx, by, bz = (r[0][i] * n + r[1][i] * e + r[2][i] * d for i in range(3))   # NED -> body = R^T
        if bz <= 0.1:
            return None
        return self.width / 2 + self.f * by / bz, self.height / 2 - self.f * bx / bz, bz

    def in_image(self, uv, margin=0.0) -> bool:
        return -margin <= uv[0] < self.width + margin and -margin <= uv[1] < self.height + margin

    def gsd_cm(self, depth_m) -> float:
        """Ground sample distance at that depth, cm per pixel."""
        return 100.0 * depth_m / self.f


def match_frame(persons_uv, boxes, radius_px):
    """persons_uv = {label: (u, v)} (in the image), boxes = [(u, v, conf)] ->
    (tp_labels, fn_labels, fp_boxes). Greedy: closest pair first, one box per person."""
    pairs = sorted((math.hypot(pu - bu, pv - bv), lab, i)
                   for lab, (pu, pv) in persons_uv.items() for i, (bu, bv, _) in enumerate(boxes))
    used_l, used_b, tp = set(), set(), []
    for dist, lab, i in pairs:
        if dist > radius_px or lab in used_l or i in used_b:
            continue
        used_l.add(lab)
        used_b.add(i)
        tp.append(lab)
    fn = [lab for lab in persons_uv if lab not in used_l]
    fp = [b for i, b in enumerate(boxes) if i not in used_b]
    return tp, fn, fp


def band(alt_rel, width=10.0):
    """Altitude bin label, e.g. 23.4 m with width 10 -> '20-30 m'."""
    lo = math.floor(alt_rel / width) * width
    return f"{lo:g}-{lo + width:g} m"


# ---------- data ----------
def psql(sql: str, container="timescaledb") -> list[list[str]]:
    r = subprocess.run(["docker", "exec", "-i", container, "psql", "-U", "aquarius", "-d", "aquarius", "-At",
                        "-F", "\t", "-v", "ON_ERROR_STOP=1"], input=sql, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return [line.split("\t") for line in r.stdout.splitlines() if line]


def time_window(args):
    """-> (sim ts from, sim ts to, wall received_at from) as SQL timestamps / epoch seconds."""
    if args.mission:
        rows = psql(f"SELECT extract(epoch from min(ts)), extract(epoch from max(ts)), "
                    f"extract(epoch from min(received_at)) FROM telemetry WHERE mission_id = '{args.mission}'")
    else:
        rows = psql(f"SELECT extract(epoch from min(ts)), extract(epoch from max(ts)), "
                    f"extract(epoch from min(received_at)) FROM telemetry "
                    f"WHERE received_at BETWEEN '{args.since}'::timestamptz AND '{args.until}'::timestamptz")
    if not rows or not rows[0][0]:
        raise RuntimeError("no telemetry in that window / mission")
    return float(rows[0][0]), float(rows[0][1]), float(rows[0][2])


def load(args):
    t0, t1, wall0 = time_window(args)
    win = f"ts BETWEEN to_timestamp({t0}) AND to_timestamp({t1})"
    tel = defaultdict(list)
    for d, ts, la, lo, am, ar, w, x, y, z in psql(
            f"SELECT drone_id, (extract(epoch from ts)*1e6)::bigint, lat, lon, alt_amsl, alt_rel, "
            f"q[1], q[2], q[3], q[4] FROM telemetry WHERE {win} AND q IS NOT NULL "
            + (f"AND mission_id = '{args.mission}' " if args.mission else "") + "ORDER BY ts"):
        tel[d].append((int(ts), (float(la), float(lo), float(am), float(w), float(x), float(y), float(z)),
                       float(ar or 0)))
    where_gt = f"world = '{args.world}'" + (f" AND mission_id = '{args.mission}'" if args.mission and
                                            not args.all_ground_truth else "")
    gt = {lab: (float(la), float(lo), float(alt or 0)) for lab, la, lo, alt in
          psql(f"SELECT label, lat, lon, alt_amsl FROM ground_truth_persons WHERE {where_gt} ORDER BY label")}
    dets = defaultdict(list)
    for d, f, w, h, u, v, conf in psql(
            f"SELECT d.drone_id, d.frame_id, d.image_w, d.image_h, (o->'center_uv'->>0)::float, "
            f"(o->'center_uv'->>1)::float, (o->>'conf')::float FROM detections d, jsonb_array_elements(d.objects) o "
            f"WHERE d.{win}"):
        if float(conf) >= args.conf:
            dets[(d, int(f))].append((float(u), float(v), float(conf)))
    return t0, t1, wall0, tel, gt, dets


def frames_from_kafka(bootstrap, wall0, t0_us, t1_us, drones):
    """(drone_id, frame_id, ts_us, w, h) of every image-frames message in the sim window."""
    from confluent_kafka import Consumer, TopicPartition
    c = Consumer({"bootstrap.servers": bootstrap, "group.id": "eval-detections", "enable.auto.commit": False})
    parts = c.list_topics("image-frames", timeout=10).topics["image-frames"].partitions
    tps = [tp for tp in c.offsets_for_times([TopicPartition("image-frames", p, int((wall0 - 30) * 1000))
                                             for p in parts], timeout=10) if tp.offset >= 0]
    c.assign(tps)
    live, idle, out = {tp.partition for tp in tps}, 0, []
    while live and idle < 5:
        m = c.poll(1.0)
        if m is None:
            idle += 1
            continue
        idle = 0
        if m.error():
            continue
        h = {k: v.decode() for k, v in (m.headers() or [])}
        ts = int(h.get("ts_us", 0))
        if ts > t1_us:
            c.pause([TopicPartition("image-frames", m.partition())])
            live.discard(m.partition())
            continue
        if ts >= t0_us and h.get("drone_id") in drones:
            out.append((h["drone_id"], int(h["frame_id"]), ts, int(h.get("width", 640)), int(h.get("height", 480))))
    c.close()
    return out


def evaluate(frames, tel, gt, dets, cam_hfov, radius_m, min_radius_px, max_dt_us=100_000, min_alt_m=2.0):
    """-> list of per (frame, person / box) rows and the number of frames skipped (no pose within max_dt_us, or
    below min_alt_m: on the ground the camera sits under the terrain surface and sees nothing)."""
    rows, skipped = [], 0
    for d, fid, ts, w, h in frames:
        series = tel.get(d, [])
        i = bisect.bisect_left(series, (ts,))
        cand = [series[j] for j in (i - 1, i) if 0 <= j < len(series)]
        if not cand:
            skipped += 1
            continue
        tts, pose, alt_rel = min(cand, key=lambda c: abs(c[0] - ts))
        if abs(tts - ts) > max_dt_us or alt_rel < min_alt_m:
            skipped += 1
            continue
        cam = Camera(w, h, cam_hfov)
        in_view, depth = {}, {}
        for lab, target in gt.items():
            p = cam.project(pose, target)
            if p and cam.in_image(p):
                in_view[lab], depth[lab] = (p[0], p[1]), p[2]
        boxes = dets.get((d, fid), [])
        radius = max(min_radius_px, (radius_m * 100 / cam.gsd_cm(max(alt_rel, 1.0))) if alt_rel else min_radius_px)
        tp, fn, fp = match_frame(in_view, boxes, radius)
        for lab in tp + fn:
            rows.append(dict(drone=d, frame=fid, ts_us=ts, alt_rel=round(alt_rel, 1), result="TP" if lab in tp else "FN",
                             person=lab, u=round(in_view[lab][0], 1), v=round(in_view[lab][1], 1),
                             gsd_cm=round(cam.gsd_cm(depth[lab]), 2), person_px=round(50 / cam.gsd_cm(depth[lab]), 1)))
        for bu, bv, conf in fp:
            rows.append(dict(drone=d, frame=fid, ts_us=ts, alt_rel=round(alt_rel, 1), result="FP", person="",
                             u=round(bu, 1), v=round(bv, 1), conf=round(conf, 3)))
        if not in_view and not boxes:
            rows.append(dict(drone=d, frame=fid, ts_us=ts, alt_rel=round(alt_rel, 1), result="EMPTY"))
    return rows, skipped


def summarize(rows, bin_m=10.0):
    def stats(rs):
        tp = sum(r["result"] == "TP" for r in rs)
        fn = sum(r["result"] == "FN" for r in rs)
        fp = sum(r["result"] == "FP" for r in rs)
        rec = tp / (tp + fn) if tp + fn else float("nan")
        prec = tp / (tp + fp) if tp + fp else float("nan")
        px = sorted(r["person_px"] for r in rs if "person_px" in r)
        return tp, fn, fp, rec, prec, (px[len(px) // 2] if px else float("nan"))
    out = []
    bins = sorted({math.floor(r["alt_rel"] / bin_m) for r in rows})
    groups = [("all", rows)] + [(f"alt {band(b * bin_m, bin_m)}",
                                 [r for r in rows if math.floor(r["alt_rel"] / bin_m) == b]) for b in bins]
    groups += [(f"person {p}", [r for r in rows if r.get("person") == p])
               for p in sorted({r["person"] for r in rows if r.get("person")})]
    for name, rs in groups:
        if any(r["result"] != "EMPTY" for r in rs):
            out.append((name, *stats(rs)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--mission", help="mission_id (telemetry + ground truth of that mission)")
    g.add_argument("--since", help="wall-clock start (received_at), e.g. '2026-11-26 05:45:00+00'")
    ap.add_argument("--until", help="wall-clock end, with --since")
    ap.add_argument("--world", default="kmutt")
    ap.add_argument("--all-ground-truth", action="store_true", help="with --mission: use every ground truth row of the world")
    ap.add_argument("--conf", type=float, default=0.0, help="ignore detections below this confidence")
    ap.add_argument("--hfov", type=float, default=1.74, help="camera horizontal FOV, rad (mono_cam: 1.74)")
    ap.add_argument("--radius-m", type=float, default=1.0, help="a box counts for a person within this distance")
    ap.add_argument("--min-radius-px", type=float, default=15.0)
    ap.add_argument("--min-alt", type=float, default=2.0, help="skip frames below this alt_rel, m (on the ground)")
    ap.add_argument("--bin-m", type=float, default=10.0, help="altitude bin width for the summary, m")
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--csv", help="write every TP / FN / FP row here")
    a = ap.parse_args(argv)
    if a.mission and not UUID_RE.match(a.mission):
        ap.error("bad --mission")
    if a.since and (not a.until or not TIME_RE.match(a.since) or not TIME_RE.match(a.until)):
        ap.error("--since and --until must both be timestamps like '2026-11-26 05:45:00+00'")
    if not re.match(r"^[A-Za-z0-9_\-]+$", a.world):
        ap.error("bad --world")
    try:
        t0, t1, wall0, tel, gt, dets = load(a)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not gt:
        print("no ground_truth_persons rows for that world / mission (tools/sim_persons.py spawn --mission ...)",
              file=sys.stderr)
        return 1
    frames = frames_from_kafka(a.bootstrap, wall0, int(t0 * 1e6), int(t1 * 1e6), set(tel))
    rows, skipped = evaluate(frames, tel, gt, dets, a.hfov, a.radius_m, a.min_radius_px, min_alt_m=a.min_alt)
    print(f"window {t1 - t0:.0f} s sim time, drones {sorted(tel)}, people {sorted(gt)}, frames {len(frames)} "
          f"(skipped: no pose within 100 ms or below {a.min_alt:g} m: {skipped}), "
          f"detections conf >= {a.conf}: {sum(len(v) for v in dets.values())}")
    print(f"{'group':<20}{'TP':>6}{'FN':>6}{'FP':>6}{'recall':>9}{'precision':>11}{'person px':>11}")
    for name, tp, fn, fp, rec, prec, px in summarize(rows, a.bin_m):
        print(f"{name:<20}{tp:>6}{fn:>6}{fp:>6}{rec:>9.2f}{prec:>11.2f}{px:>11.1f}")
    print("person px = median size of a 0.5 m wide person in the image (top view).")
    if a.csv:
        keys = ["drone", "frame", "ts_us", "alt_rel", "result", "person", "u", "v", "gsd_cm", "person_px", "conf"]
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print(f"rows -> {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
