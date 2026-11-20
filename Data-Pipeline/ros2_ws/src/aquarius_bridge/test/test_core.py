"""Unit tests for the bridge logic. Run without ROS:
    python3 -m pytest ros2_ws/src/aquarius_bridge/test      (or python3 -m unittest)
"""
import io
import json
import os
import sys
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))

from aquarius_bridge.core import (ClockOffsetEstimator, DroneState, EpochMapper, plausible_time, px4_topic,  # noqa: E402
                                  RateLimiter, build_telemetry, encode_json,
                                  image_headers, jpeg_dimensions, nav_state_name,
                                  parse_drone_specs, stamp_to_us)


def _schema(name):
    with open(os.path.join(REPO, "schemas", f"{name}.schema.json")) as f:
        return json.load(f)


def _make_jpeg(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 160)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


class TestDroneSpecs(unittest.TestCase):
    def test_parse(self):
        s = parse_drone_specs(["uav1,/px4_1,/uav1/cam", "uav2, px4_2/ ,/uav2/cam", "uav3,/,/c"])
        self.assertEqual([d.drone_id for d in s], ["uav1", "uav2", "uav3"])
        self.assertEqual(s[1].px4_ns, "/px4_2")
        self.assertEqual(s[2].px4_ns, "")

    def test_rejects_bad_entries(self):
        for bad in (["drone1,/px4_1,/c"], ["uav1,/px4_1"], ["uav1,/a,/c", "uav1,/b,/d"], []):
            with self.assertRaises(ValueError):
                parse_drone_specs(bad)


class TestTime(unittest.TestCase):
    def test_offset_takes_smallest_delay(self):
        est = ClockOffsetEstimator(window=10)
        true_offset = 5_000_000
        for i, delay in enumerate([900, 300, 1200, 250, 700]):
            px4 = 1_000_000 + i * 100_000
            est.observe(px4, px4 + true_offset + delay)
        self.assertEqual(est.offset_us, true_offset + 250)
        self.assertEqual(est.to_ros_us(2_000_000), 2_000_000 + true_offset + 250)

    def test_offset_window_forgets_old_minimum(self):
        est = ClockOffsetEstimator(window=3)
        est.observe(0, 100)        # d=100, will fall out of the window
        for t in (1, 2, 3):
            est.observe(t, t + 500)
        self.assertEqual(est.offset_us, 500)

    def test_epoch_real_time_has_no_offset(self):
        m = EpochMapper(use_sim_time=False)
        self.assertTrue(m.ready)
        self.assertEqual(m.to_epoch_us(1_791_000_000_000_000), 1_791_000_000_000_000)

    def test_epoch_sim_time_latches_once(self):
        m = EpochMapper(use_sim_time=True)
        m.latch(wall_now_us=1_791_000_000_000_000, ros_now_us=0)   # /clock not yet received
        self.assertFalse(m.ready)
        m.latch(wall_now_us=1_791_000_000_000_000, ros_now_us=12_000_000)
        m.latch(wall_now_us=1_791_000_999_000_000, ros_now_us=13_000_000)  # ignored
        self.assertEqual(m.to_epoch_us(12_000_000), 1_791_000_000_000_000)
        self.assertEqual(m.to_epoch_us(12_100_000), 1_791_000_000_100_000)

    def test_not_ready_before_min_samples(self):
        est = ClockOffsetEstimator(min_samples=20)
        for i in range(19):
            est.observe(i * 10_000, i * 10_000 + 500)
        self.assertFalse(est.ready)
        est.observe(19 * 10_000, 19 * 10_000 + 500)
        self.assertTrue(est.ready)

    def test_clock_step_down_is_taken_at_once(self):
        # PX4 time sync converges: timestamps jump from boot time to the agent's (wall) clock
        est = ClockOffsetEstimator(min_samples=1)
        for i in range(30):
            est.observe(5_000_000 + i * 20_000, 5_000_000 + i * 20_000 + 300)          # boot clock
        wall = 1_791_000_000_000_000
        est.observe(wall, 5_700_000)                                                    # synced clock
        self.assertEqual(est.to_ros_us(wall + 20_000), 5_720_000)
        self.assertEqual(est.resets, 0)

    def test_clock_step_up_resets_after_confirmation(self):
        # PX4 rebooted: its timestamps start again near 0, d jumps UP and the old minimum would hide it
        est = ClockOffsetEstimator(min_samples=1, jump_confirm=20)
        for i in range(50):
            est.observe(100_000_000 + i * 20_000, 100_000_000 + i * 20_000 + 300)    # d = 300
        for i in range(19):
            est.observe(i * 20_000, 101_000_000 + i * 20_000 + 400)                  # d ~ 101 s
        self.assertEqual(est.offset_us, 300)                                         # not yet confirmed
        est.observe(19 * 20_000, 101_000_000 + 19 * 20_000 + 400)
        self.assertEqual(est.resets, 1)
        self.assertEqual(est.offset_us, 101_000_400)

    def test_stale_sample_after_step_is_not_plausible(self):
        # the bug seen on the server: a sample still in the boot clock mapped with the new offset -> near 0
        est = ClockOffsetEstimator(min_samples=1)
        wall = 1_791_000_000_000_000
        sim_now = 60_000_000
        est.observe(wall, sim_now)                       # synced sample, offset = sim - wall
        stale = est.to_ros_us(53_000_000)                # boot-clock timestamp from before the step
        self.assertFalse(plausible_time(stale, sim_now))
        self.assertTrue(plausible_time(est.to_ros_us(wall - 50_000), sim_now))

    def test_plausible_window(self):
        now = 100_000_000
        self.assertTrue(plausible_time(now - 9_000_000, now))
        self.assertFalse(plausible_time(now - 11_000_000, now))
        self.assertTrue(plausible_time(now + 500_000, now))
        self.assertFalse(plausible_time(now + 2_000_000, now))

    def test_epoch_relatches_when_sim_restarts(self):
        m = EpochMapper(use_sim_time=True)
        m.latch(wall_now_us=1_791_000_000_000_000, ros_now_us=500_000_000)
        m.latch(wall_now_us=1_791_000_100_000_000, ros_now_us=600_000_000)
        m.latch(wall_now_us=1_791_000_200_000_000, ros_now_us=2_000_000)            # Gazebo restarted
        self.assertEqual(m.relatches, 1)
        self.assertEqual(m.to_epoch_us(2_000_000), 1_791_000_200_000_000)

    def test_stamp(self):
        self.assertEqual(stamp_to_us(12, 345_678_999), 12_345_678)


class TestPx4Topic(unittest.TestCase):
    def test_versioned_names(self):
        # names as PX4 main 0bd95058 publishes them (ros2 topic list, 2026-11-15)
        self.assertEqual(px4_topic("vehicle_status", 5), "fmu/out/vehicle_status_v5")
        self.assertEqual(px4_topic("battery_status", 1), "fmu/out/battery_status_v1")
        self.assertEqual(px4_topic("vehicle_attitude", 0), "fmu/out/vehicle_attitude")
        self.assertEqual(px4_topic("vehicle_global_position", None), "fmu/out/vehicle_global_position")


class TestTelemetry(unittest.TestCase):
    def _state(self):
        st = DroneState()
        st.update_global(ts=10_000, lat=13.7563, lon=100.5018, alt=42.5)
        st.update_local(z=-30.0, vx=4.0, vy=-1.0, vz=0.2)
        st.update_attitude([0.7071068, 0.0, 0.0, 0.7071068])
        st.update_battery(0.53, 15.7)
        st.update_status(14, 2)
        return st

    def test_message_matches_schema(self):
        import jsonschema
        schema = _schema("telemetry")
        msg = build_telemetry("uav1", str(uuid.uuid4()), self._state(), 1_791_000_000_123_456)
        jsonschema.validate(msg, schema)
        self.assertEqual(msg["alt_rel"], 30.0)          # NED z is down -> height is -z
        self.assertEqual(msg["nav_state"], "OFFBOARD")
        self.assertTrue(msg["armed"])
        self.assertEqual(len(encode_json(msg)) < 600, True)  # ~300 B expected

    def test_unknown_battery_becomes_null(self):
        import jsonschema
        st = self._state()
        st.update_battery(-1.0, 0.0)
        msg = build_telemetry("uav2", str(uuid.uuid4()), st, 1)
        self.assertIsNone(msg["battery_remaining"])
        self.assertIsNone(msg["battery_voltage"])
        jsonschema.validate(msg, _schema("telemetry"))

    def test_only_new_samples_are_sent(self):
        st = self._state()
        self.assertTrue(st.has_new_sample())
        st.last_sent_global_ts = st.global_ts
        self.assertFalse(st.has_new_sample())
        st.update_global(ts=20_000, lat=13.7564, lon=100.5019, alt=42.6)
        self.assertTrue(st.has_new_sample())

    def test_no_message_before_attitude(self):
        st = DroneState()
        st.update_global(ts=1, lat=0.0, lon=0.0, alt=0.0)
        self.assertFalse(st.has_new_sample())

    def test_nav_state_names(self):
        self.assertEqual(nav_state_name(5), "AUTO_RTL")
        self.assertEqual(nav_state_name(99), "UNKNOWN_99")
        self.assertIsNone(nav_state_name(None))

    def test_nan_is_never_emitted(self):
        st = self._state()
        st.update_local(z=float("nan"), vx=float("nan"), vy=0.0, vz=0.0)
        data = encode_json(build_telemetry("uav1", str(uuid.uuid4()), st, 1))
        self.assertNotIn(b"NaN", data)


class TestOptionalFields(unittest.TestCase):
    """landed / GPS / health (2026-11-16): null until received, schema-valid when set."""

    class _Flags:   # stands in for px4_msgs FailsafeFlags (ROS classmethod get_fields_and_field_types)
        @classmethod
        def get_fields_and_field_types(cls):
            return {"timestamp": "uint64", "mode_req_mission": "uint32", "attitude_invalid": "boolean",
                    "home_position_invalid": "boolean", "gcs_connection_lost": "boolean",
                    "battery_warning": "uint8"}

        def __init__(self, **true_flags):
            self.timestamp, self.mode_req_mission, self.battery_warning = 1, 7, 0
            for n in ("attitude_invalid", "home_position_invalid", "gcs_connection_lost"):
                setattr(self, n, true_flags.get(n, False))

    def _state(self):
        return TestTelemetry._state(self)

    def test_null_until_received(self):
        import jsonschema
        msg = build_telemetry("uav0", str(uuid.uuid4()), self._state(), 1)
        for k in ("landed", "gps_fix_type", "gps_satellites", "gps_eph", "gps_epv", "health_ok", "failsafe_flags"):
            self.assertIn(k, msg)
            self.assertIsNone(msg[k])
        jsonschema.validate(msg, _schema("telemetry"))

    def test_values_and_schema(self):
        import jsonschema
        st = self._state()
        st.update_land(True)
        st.update_gnss(3, 10, 0.7004, float("nan"))
        st.update_failsafe(self._Flags(home_position_invalid=True, gcs_connection_lost=True))
        msg = build_telemetry("uav0", str(uuid.uuid4()), st, 1)
        jsonschema.validate(msg, _schema("telemetry"))
        self.assertIs(msg["landed"], True)
        self.assertEqual((msg["gps_fix_type"], msg["gps_satellites"], msg["gps_eph"], msg["gps_epv"]),
                         (3, 10, 0.7, None))                       # NaN -> null
        self.assertEqual(msg["failsafe_flags"], ["gcs_connection_lost", "home_position_invalid"])
        self.assertIs(msg["health_ok"], True)                      # situations, not faults
        st.update_failsafe(self._Flags(attitude_invalid=True))
        self.assertIs(build_telemetry("uav0", str(uuid.uuid4()), st, 1)["health_ok"], False)

    def test_old_message_without_new_fields_is_still_valid(self):
        import jsonschema
        msg = build_telemetry("uav0", str(uuid.uuid4()), self._state(), 1)
        for k in ("landed", "gps_fix_type", "gps_satellites", "gps_eph", "gps_epv", "health_ok", "failsafe_flags"):
            del msg[k]
        jsonschema.validate(msg, _schema("telemetry"))


class TestCpuMissionEvents(unittest.TestCase):
    """cpuload / mission_result / mavlink_log (px4-sim dds_topics patch, 2026-11-16)."""

    def test_cpu_and_mission_in_telemetry(self):
        import jsonschema
        st = TestTelemetry._state(self)
        msg = build_telemetry("uav0", str(uuid.uuid4()), st, 1)
        self.assertEqual([msg[k] for k in ("cpu_load", "mission_seq", "mission_total", "mission_finished")],
                         [None] * 4)
        st.update_cpuload(0.4567)
        st.update_mission(3, 14, False)
        msg = build_telemetry("uav0", str(uuid.uuid4()), st, 1)
        jsonschema.validate(msg, _schema("telemetry"))
        self.assertEqual((msg["cpu_load"], msg["mission_seq"], msg["mission_total"], msg["mission_finished"]),
                         (45.7, 3, 14, False))
        st.update_cpuload(float("nan"))
        self.assertIsNone(build_telemetry("uav0", str(uuid.uuid4()), st, 1)["cpu_load"])

    def test_decode_px4_text(self):
        from aquarius_bridge.core import decode_px4_text
        raw = list(b"Landing detected") + [0] * 5 + list(b"junk")
        self.assertEqual(decode_px4_text(raw), "Landing detected")              # ints, NUL-terminated
        self.assertEqual(decode_px4_text(bytes(raw)), "Landing detected")       # bytes
        self.assertEqual(decode_px4_text([chr(c) for c in raw]), "Landing detected")  # 1-char strings
        self.assertEqual(decode_px4_text([0] * 10), "")

    def test_event_message(self):
        import jsonschema
        from aquarius_bridge.core import build_event
        m = build_event("uav1", str(uuid.uuid4()), 1_791_000_000_000_000, 6, "Takeoff detected")
        jsonschema.validate(m, _schema("events"))
        self.assertEqual((m["severity"], m["level"], m["text"]), (6, "info", "Takeoff detected"))
        self.assertEqual(build_event("uav1", str(uuid.uuid4()), 1, 12, "x")["level"], "debug")  # clamped
        self.assertIsNone(build_event("uav1", str(uuid.uuid4()), 1, 6, ""))


class TestImages(unittest.TestCase):
    def test_jpeg_dimensions(self):
        for w, h in ((1280, 720), (640, 480), (33, 17)):
            self.assertEqual(jpeg_dimensions(_make_jpeg(w, h)), (w, h))

    def test_not_jpeg(self):
        with self.assertRaises(ValueError):
            jpeg_dimensions(b"\x89PNG\r\n\x1a\n....")

    def test_headers(self):
        h = dict(image_headers("uav3", 1_791_000_000_000_001, 7, 1280, 720))
        self.assertEqual(h["drone_id"], b"uav3")
        self.assertEqual(h["ts_us"], b"1791000000000001")
        self.assertEqual(h["frame_id"], b"7")
        self.assertEqual(h["schema_version"], b"1")

    def test_rate_limiter_2fps(self):
        rl = RateLimiter(2.0)
        sent = sum(rl.allow("uav1", t * 33_333) for t in range(90))   # 30 fps for 3 s
        self.assertIn(sent, (6, 7))
        self.assertTrue(rl.allow("uav2", 0))   # independent per drone

    def test_rate_limiter_tolerates_jitter(self):
        rl = RateLimiter(2.0)
        times = [0, 480_000, 1_010_000, 1_490_000, 2_000_000]  # ~2 fps with jitter
        self.assertEqual(sum(rl.allow("uav1", t) for t in times), 5)


if __name__ == "__main__":
    unittest.main()
