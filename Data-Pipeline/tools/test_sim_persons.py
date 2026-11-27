"""Tests for tools/sim_persons.py (no Docker / Gazebo needed)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sim_persons as sp  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

KMUTT = {"lat": 13.6513, "lon": 100.4944, "elevation": 0.0, "heading": 0.0}

POSE_INFO = """pose {
  name: "kmutt_terrain"
  id: 8
  position {
    z: -1.883
  }
}
pose {
  name: "person_01"
  id: 204
  position {
    x: -75
    y: -60
    z: 7
  }
  orientation {
    z: 0.2474
    w: 0.9689
  }
}
pose {
  name: "person_02"
  id: 210
  position {
    x: -54.999933510790207
    y: -61.999998015098612
    z: 6.7152485997611731
  }
}
"""


class TestScenario(unittest.TestCase):
    def test_default_file_loads(self):
        world, persons = sp.load_scenario(read(sp.DEFAULT_FILE))
        self.assertEqual(world, "kmutt")
        self.assertEqual([p.label for p in persons], ["person_01", "person_02", "person_03", "person_04"])
        self.assertEqual(persons[0].z, 7.0)                       # spawn_z
        self.assertAlmostEqual(persons[2].roll, 1.5708, 3)        # lying
        self.assertEqual(persons[2].z, 7.2)                       # own z wins

    def test_rejects_bad_input(self):
        ok = {"model": "OpenRobotics/Casual female", "x": 0, "y": 0}
        for bad, why in [
            ({"persons": [dict(ok, label="a b")]}, "label"),               # space -> not a Gazebo name
            ({"persons": [dict(ok, label="p'; DROP")]}, "label"),          # no SQL / shell characters
            ({"persons": [dict(ok, label="p1"), dict(ok, label="p1")]}, "twice"),
            ({"persons": [dict(ok, label="p1", model="Casual female")]}, "model"),
            ({"persons": [dict(ok, label="p1", pose="sitting")]}, "pose"),
            ({"persons": [dict(ok, label="p1", x=float("nan"))]}, "finite"),
            ({"world": "km utt", "persons": [dict(ok, label="p1")]}, "world"),
            ({"persons": []}, "no persons"),
        ]:
            with self.assertRaises(ValueError, msg=why) as cm:
                sp.load_scenario(json.dumps(bad))
            self.assertIn(why, str(cm.exception))


class TestGeo(unittest.TestCase):
    def test_spherical_from_world_sdf(self):
        sdf = read(os.path.join(sp.HERE, "..", "..", "Simulation", "worlds", "kmutt.sdf"))
        o = sp.parse_spherical(sdf)
        self.assertEqual((o["lat"], o["lon"], o["elevation"], o["heading"]), (13.6513, 100.4944, 0.0, 0.0))

    def test_enu_to_latlon(self):
        lat, lon, alt = sp.enu_to_latlon(KMUTT, 0, 0, 7)
        self.assertEqual((lat, lon, alt), (13.6513, 100.4944, 7))
        # drone 0 spawn (-65, -53) = home 13.65082, 100.49380 as PX4 reports it
        lat, lon, _ = sp.enu_to_latlon(KMUTT, -65, -53, 0)
        self.assertAlmostEqual(lat, 13.65082, 5)
        self.assertAlmostEqual(lon, 100.49380, 5)
        # 100 m north / east is ~100 m on the ground
        lat, lon, _ = sp.enu_to_latlon(KMUTT, 100, 100, 0)
        self.assertAlmostEqual((lat - 13.6513) * 110_600, 100, delta=0.5)
        self.assertAlmostEqual((lon - 100.4944) * 108_200, 100, delta=0.5)


class TestGazeboText(unittest.TestCase):
    def test_parse_pose_info(self):
        p = sp.parse_pose_info(POSE_INFO)
        self.assertEqual(p["person_01"], (-75.0, -60.0, 7.0))
        self.assertAlmostEqual(p["person_02"][2], 6.7152, 3)
        self.assertEqual(p["kmutt_terrain"], (0.0, 0.0, -1.883))   # missing axes = 0

    def test_spawn_request(self):
        _, persons = sp.load_scenario(read(sp.DEFAULT_FILE))
        sdf = sp.spawn_sdf(persons[0])
        self.assertIn("<uri>https://fuel.gazebosim.org/1.0/OpenRobotics/models/Casual female</uri>", sdf)
        self.assertIn("<name>person_01</name><pose>-75 -60 7 0.0000 0 0.5000</pose>", sdf)
        req = sp.gz_req_string(sdf)
        self.assertTrue(req.startswith('sdf: "<sdf version=\\"1.9\\">'))
        self.assertNotIn("'", req)                                  # safe inside the single-quoted bash arg


class TestSql(unittest.TestCase):
    def test_upsert(self):
        sql = sp.upsert_sql("kmutt", [("person_01", 13.6507, 100.4937, 7.0)], None)
        self.assertIn("('kmutt', 'person_01', NULL, 13.650700000, 100.493700000, 7.000)", sql)
        self.assertIn("ON CONFLICT (world, label) DO UPDATE", sql)
        self.assertNotIn("INSERT INTO missions", sql)

    def test_upsert_with_mission(self):
        m = "bff5392c-b9c5-4726-88f0-f57873402862"
        sql = sp.upsert_sql("kmutt", [("p1", 1.0, 2.0, 3.0)], m)
        self.assertIn(f"INSERT INTO missions (mission_id) VALUES ('{m}'::uuid) ON CONFLICT", sql)
        self.assertIn(f"('kmutt', 'p1', '{m}'::uuid,", sql)
        with self.assertRaises(ValueError):
            sp.upsert_sql("kmutt", [("p1", 1.0, 2.0, 3.0)], "x'; DROP TABLE missions; --")


class TestDryRun(unittest.TestCase):
    def test_spawn_dry_run_prints_commands(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = sp.main(["spawn", "--dry-run"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("/world/kmutt/create"), 4)
        self.assertIn("INSERT INTO ground_truth_persons", out)

    def test_bad_mission_arg(self):
        self.assertEqual(sp.main(["list", "--mission", "nope", "--dry-run"]), 2)


if __name__ == "__main__":
    unittest.main()
