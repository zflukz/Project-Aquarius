"""Tests for the measurement logic in pipeline_stats.py and the fake drone path."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline_stats import DroneStats, pct, summarize  # noqa: E402
from sim_producer import FakeDrone  # noqa: E402
from check_time_sync import analyze  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sitl"))
from make_cam_bridge import bridge_yaml, parse_gz_topics  # noqa: E402


class TestLoss(unittest.TestCase):
    def test_no_loss(self):
        s = DroneStats()
        s.tele_ts = [i * 100_000 for i in range(100)]
        self.assertEqual(s.loss(10.0), (100, 100, 0.0))

    def test_gaps_counted(self):
        s = DroneStats()
        ts = [i * 100_000 for i in range(100)]
        del ts[50:55]          # 5 missing in the middle
        del ts[10]             # 1 more
        s.tele_ts = ts
        got, expected, loss = s.loss(10.0)
        self.assertEqual((got, expected), (94, 100))
        self.assertAlmostEqual(loss, 6.0)

    def test_jitter_is_not_loss(self):
        s = DroneStats()
        s.tele_ts = [i * 100_000 + (12_000 if i % 3 else -9_000) for i in range(50)]
        self.assertEqual(s.loss(10.0)[2], 0.0)

    def test_duplicates_ignored(self):
        s = DroneStats()
        s.tele_ts = [0, 100_000, 100_000, 200_000]
        self.assertEqual(s.loss(10.0)[:2], (3, 3))

    def test_summary_orders_uav10_after_uav2(self):
        st = {d: DroneStats() for d in ("uav10", "uav2", "uav1")}
        for s in st.values():
            s.tele_ts = [0, 100_000]
            s.tele_lat_ms = [10.0, 20.0]
        rows = summarize(st, 10.0, 1.0)
        self.assertEqual([r["drone_id"] for r in rows], ["uav1", "uav2", "uav10"])

    def test_pct(self):
        self.assertEqual(pct(list(range(101)), 95), 95)
        self.assertTrue(math.isnan(pct([], 50)))


class TestFakeDrone(unittest.TestCase):
    def test_path_is_continuous_and_inside_strip(self):
        d = FakeDrone(2, 3, 300, 5, 30, 13.75, 100.5, 0.05, 1.0)
        prev = None
        for _ in range(5000):
            d.step(0.1)
            x, y, _ = d._pos_on_path(d.dist)
            self.assertTrue(100 <= x <= 200 and 0 <= y <= 300)
            if prev:
                self.assertLessEqual(math.dist(prev, (x, y)), 0.5 + 1e-9)
            prev = (x, y)


class TestCamBridge(unittest.TestCase):
    LIST = [
        "/clock", "/stats", "/world/default/clock",
        "/world/default/model/x500_mono_cam_down_1/link/camera_link/sensor/imager/image",
        "/world/default/model/x500_mono_cam_down_1/link/camera_link/sensor/imager/camera_info",
        "/world/default/model/x500_mono_cam_down_2/link/camera_link/sensor/imager/image",
        "/world/default/model/x500_mono_cam_down_10/link/camera_link/sensor/imager/image",
        "/world/default/model/x500_1/link/base_link/sensor/imu_sensor/imu",
    ]

    def test_maps_instance_to_drone(self):
        cams, clock = parse_gz_topics(self.LIST)
        self.assertEqual(clock, "/clock")
        self.assertEqual(sorted(cams), [1, 2, 10])
        y = bridge_yaml(cams, clock)
        self.assertIn('ros_topic_name: "/uav10/camera/image"', y)
        self.assertIn("x500_mono_cam_down_10/", y)
        import yaml
        self.assertEqual(len(yaml.safe_load(y)), 4)

    def test_world_clock_fallback(self):
        _, clock = parse_gz_topics(["/world/flood/clock"])
        self.assertEqual(clock, "/world/flood/clock")


class TestTimeSync(unittest.TestCase):
    def _data(self, img_clock_offset_us=0):
        tele = [(i * 100_000, i * 100_000 + 20_000) for i in range(100)]                    # 10 Hz, 20 ms delay
        imgs = [(i * 500_000 + 30_000 + img_clock_offset_us, i * 500_000 + 30_000 + 60_000)  # 2 fps
                for i in range(1, 19)]
        return tele, imgs

    def test_same_clock_passes(self):
        r = analyze(*self._data())
        self.assertEqual(r["verdict"], "PASS")
        self.assertLessEqual(r["pair_p95_ms"], 50)
        self.assertEqual(r["bracketed_pct"], 100.0)

    def test_shifted_clock_fails(self):
        r = analyze(*self._data(img_clock_offset_us=-3_000_000))   # images 3 s "early"
        self.assertEqual(r["verdict"], "FAIL")
        self.assertGreater(abs(r["clock_gap_ms"]), 1000)

    def test_no_images(self):
        self.assertEqual(analyze([(0, 1)], [])["verdict"], "NO DATA")


if __name__ == "__main__":
    unittest.main()
