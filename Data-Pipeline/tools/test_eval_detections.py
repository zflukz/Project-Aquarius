"""Tests for tools/eval_detections.py (no Docker / Kafka needed)."""
import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eval_detections as ev  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAT, LON = 13.6508, 100.4938          # near the KMUTT drones' home
M_PER_DEG_LAT, M_PER_DEG_LON = 110_600.0, 108_150.0   # roughly, at 13.65 deg N
LEVEL_NORTH = (1.0, 0.0, 0.0, 0.0)    # q: level, nose north
LEVEL_EAST = (math.cos(math.pi / 4), 0.0, 0.0, math.sin(math.pi / 4))   # yaw +90 deg = nose east


def pose(alt, q=LEVEL_NORTH, lat=LAT, lon=LON):
    return (lat, lon, alt, *q)


def target(north_m=0.0, east_m=0.0, alt=0.0):
    return (LAT + north_m / M_PER_DEG_LAT, LON + east_m / M_PER_DEG_LON, alt)


class TestProjection(unittest.TestCase):
    cam = ev.Camera(640, 480, 1.74)

    def test_focal_length_and_gsd(self):
        self.assertAlmostEqual(self.cam.f, 269.97, 1)
        self.assertAlmostEqual(self.cam.gsd_cm(10), 3.70, 2)         # 10 m: 3.7 cm per pixel
        self.assertAlmostEqual(self.cam.gsd_cm(40), 14.82, 2)

    def test_straight_below_is_the_centre(self):
        u, v, depth = self.cam.project(pose(20), target())
        self.assertAlmostEqual(u, 320, 1)
        self.assertAlmostEqual(v, 240, 1)
        self.assertAlmostEqual(depth, 20, 2)

    def test_axes_nose_north(self):
        f = self.cam.f
        u, v, _ = self.cam.project(pose(20), target(north_m=5))      # ahead -> up in the image
        self.assertAlmostEqual(v, 240 - f * 5 / 20, delta=0.5)
        self.assertAlmostEqual(u, 320, delta=0.5)
        u, v, _ = self.cam.project(pose(20), target(east_m=5))       # right of the nose -> right in the image
        self.assertAlmostEqual(u, 320 + f * 5 / 20, delta=0.5)

    def test_axes_follow_the_yaw(self):
        u, v, _ = self.cam.project(pose(20, LEVEL_EAST), target(east_m=5))   # nose east: east is ahead
        self.assertLess(v, 240 - 50)
        self.assertAlmostEqual(u, 320, delta=0.5)

    def test_behind_or_above_is_none(self):
        self.assertIsNone(self.cam.project(pose(20), target(alt=25)))
        self.assertFalse(self.cam.in_image(self.cam.project(pose(5), target(north_m=40))[:2]))

    def test_red_marker_in_the_kmutt_sim(self):
        """Real frames: a 2 m red box seen by drone 0 at ~10 m, pose from telemetry (2026-11-26)."""
        with open(os.path.join(HERE, "testdata_marker_kmutt.json"), encoding="utf-8") as f:
            data = json.load(f)
        errs = []
        for fr in data["frames"]:
            u, v, _ = self.cam.project(fr["pose"], data["target"])
            errs.append(math.hypot(u - fr["seen_uv"][0], v - fr["seen_uv"][1]))
        errs.sort()
        self.assertLess(errs[len(errs) // 2], 15, errs)


class TestBins(unittest.TestCase):
    def test_band(self):
        self.assertEqual(ev.band(23.4), "20-30 m")
        self.assertEqual(ev.band(4.9, 5), "0-5 m")
        self.assertEqual(ev.band(40.0, 5), "40-45 m")


class TestMatching(unittest.TestCase):
    def test_tp_fn_fp(self):
        persons = {"p1": (100, 100), "p2": (400, 300)}
        boxes = [(104, 98, 0.8), (600, 50, 0.5)]
        tp, fn, fp = ev.match_frame(persons, boxes, radius_px=15)
        self.assertEqual((tp, fn), (["p1"], ["p2"]))
        self.assertEqual(fp, [(600, 50, 0.5)])

    def test_one_box_per_person(self):
        tp, fn, fp = ev.match_frame({"p1": (100, 100), "p2": (110, 100)}, [(105, 100, 0.9)], radius_px=20)
        self.assertEqual(len(tp), 1)
        self.assertEqual(len(fn), 1)
        self.assertEqual(fp, [])

    def test_evaluate_counts_and_skips_without_pose(self):
        tel = {"uav0": [(1_000_000, pose(20), 20.0)]}
        gt = {"p1": target(), "p2": target(north_m=200)}                # p2 far outside the image
        dets = {("uav0", 1): [(321, 239, 0.9), (50, 50, 0.4)]}
        frames = [("uav0", 1, 1_000_000, 640, 480), ("uav0", 2, 5_000_000, 640, 480)]   # frame 2: no pose
        rows, skipped = ev.evaluate(frames, tel, gt, dets, 1.74, 1.0, 15)
        self.assertEqual(skipped, 1)
        self.assertEqual(sorted(r["result"] for r in rows), ["FP", "TP"])
        tp = next(r for r in rows if r["result"] == "TP")
        self.assertAlmostEqual(tp["person_px"], 50 / (100 * 20 / ev.Camera().f), 1)   # ~6.7 px at 20 m
        s = dict((x[0], x[1:]) for x in ev.summarize(rows))
        self.assertEqual(s["all"][:3], (1, 0, 1))


if __name__ == "__main__":
    unittest.main()
