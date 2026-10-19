"""ROS 2–Kafka Bridge (Phase B2).

Subscribes to the PX4 and camera topics of every drone, turns them into the
A2 messages and produces them to Kafka:
    telemetry     JSON, 10 Hz per drone, key = drone_id
    image-frames  JPEG bytes + headers,   key = drone_id

Run:
    ros2 launch aquarius_bridge bridge.launch.py
"""
from __future__ import annotations

import time
import uuid
from typing import Dict

import rclpy
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy)
from sensor_msgs.msg import CompressedImage, Image

from px4_msgs.msg import (BatteryStatus, VehicleAttitude, VehicleGlobalPosition,
                          VehicleLocalPosition, VehicleStatus)

from .core import (ClockOffsetEstimator, Counters, DroneState, EpochMapper,
                   RateLimiter, build_telemetry, encode_json, image_headers,
                   is_jpeg, jpeg_dimensions, parse_drone_specs, stamp_to_us)
from .kafka_out import KafkaOut

# PX4 publishes with BEST_EFFORT; a RELIABLE subscriber would receive nothing.
PX4_QOS = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                     durability=DurabilityPolicy.VOLATILE,
                     history=HistoryPolicy.KEEP_LAST, depth=5)
CAMERA_QOS = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                        durability=DurabilityPolicy.VOLATILE,
                        history=HistoryPolicy.KEEP_LAST, depth=2)


class BridgeNode(Node):
    def __init__(self):
        super().__init__("aquarius_bridge")
        p = self.declare_parameter
        p("kafka_bootstrap", "localhost:9092")
        p("mission_id", "")
        p("drones", ["uav1,/px4_1,/uav1/camera/image/compressed"])
        p("telemetry_hz", 10.0)
        p("max_image_fps", 2.0)
        p("image_input", "compressed")          # compressed | raw
        p("jpeg_quality", 80)                   # only for image_input=raw
        p("px4_time_mode", "estimate_offset")   # estimate_offset | already_ros_time
        p("topic_global_position", "fmu/out/vehicle_global_position")
        p("topic_local_position", "fmu/out/vehicle_local_position_v1")
        p("topic_attitude", "fmu/out/vehicle_attitude")
        p("topic_battery", "fmu/out/battery_status_v1")
        p("topic_status", "fmu/out/vehicle_status_v4")
        p("stats_period_s", 10.0)

        g = lambda n: self.get_parameter(n).value  # noqa: E731
        self.mission_id = g("mission_id") or str(uuid.uuid4())
        self.drones = parse_drone_specs(list(g("drones")))
        self.image_input = g("image_input")
        self.jpeg_quality = int(g("jpeg_quality"))
        self.px4_time_mode = g("px4_time_mode")
        use_sim_time = bool(self.get_parameter("use_sim_time").value)

        self.kafka = KafkaOut(g("kafka_bootstrap"), logger=self.get_logger())
        self.epoch = EpochMapper(use_sim_time)
        self.counters = Counters()
        self.img_limit = RateLimiter(float(g("max_image_fps")))
        self.states: Dict[str, DroneState] = {}
        self.offsets: Dict[str, ClockOffsetEstimator] = {}
        self.frame_ids: Dict[str, int] = {}

        for d in self.drones:
            self._subscribe_drone(d)

        self.create_timer(1.0 / float(g("telemetry_hz")), self._publish_telemetry)
        self.create_timer(float(g("stats_period_s")), self._log_stats)
        self.get_logger().info(
            f"bridge up: mission_id={self.mission_id} drones={[d.drone_id for d in self.drones]} "
            f"sim_time={use_sim_time} kafka={g('kafka_bootstrap')}")

    # -- subscriptions ------------------------------------------------------
    def _subscribe_drone(self, d):
        did, ns = d.drone_id, d.px4_ns
        self.states[did] = DroneState()
        self.offsets[did] = ClockOffsetEstimator()
        self.frame_ids[did] = 0
        t = lambda name: f"{ns}/{self.get_parameter(name).value}"  # noqa: E731

        self.create_subscription(VehicleGlobalPosition, t("topic_global_position"),
                                 lambda m, did=did: self._on_global(did, m), PX4_QOS)
        self.create_subscription(VehicleLocalPosition, t("topic_local_position"),
                                 lambda m, did=did: self._on_local(did, m), PX4_QOS)
        self.create_subscription(VehicleAttitude, t("topic_attitude"),
                                 lambda m, did=did: self._on_attitude(did, m), PX4_QOS)
        self.create_subscription(BatteryStatus, t("topic_battery"),
                                 lambda m, did=did: self._on_battery(did, m), PX4_QOS)
        self.create_subscription(VehicleStatus, t("topic_status"),
                                 lambda m, did=did: self._on_status(did, m), PX4_QOS)
        if self.image_input == "raw":
            self.create_subscription(Image, d.camera_topic,
                                     lambda m, did=did: self._on_raw_image(did, m), CAMERA_QOS)
        else:
            self.create_subscription(CompressedImage, d.camera_topic,
                                     lambda m, did=did: self._on_image(did, m), CAMERA_QOS)

    def _ros_now_us(self) -> int:
        return self.get_clock().now().nanoseconds // 1000

    def _observe_px4_time(self, did: str, px4_ts: int) -> None:
        now = self._ros_now_us()
        self.epoch.latch(time.time_ns() // 1000, now)
        if self.px4_time_mode == "estimate_offset" and px4_ts > 0 and now > 0:
            self.offsets[did].observe(px4_ts, now)

    def _on_global(self, did, m):
        self._observe_px4_time(did, m.timestamp)
        self.states[did].update_global(m.timestamp_sample or m.timestamp, m.lat, m.lon, m.alt)

    def _on_local(self, did, m):
        self._observe_px4_time(did, m.timestamp)
        self.states[did].update_local(m.z, m.vx, m.vy, m.vz)

    def _on_attitude(self, did, m):
        self._observe_px4_time(did, m.timestamp)
        self.states[did].update_attitude(m.q)

    def _on_battery(self, did, m):
        self.states[did].update_battery(m.remaining, m.voltage_v)

    def _on_status(self, did, m):
        self.states[did].update_status(m.nav_state, m.arming_state)

    # -- telemetry ----------------------------------------------------------
    def _px4_to_epoch_us(self, did: str, px4_ts: int):
        if not self.epoch.ready:
            return None
        if self.px4_time_mode == "already_ros_time":
            return self.epoch.to_epoch_us(px4_ts)
        est = self.offsets[did]
        return self.epoch.to_epoch_us(est.to_ros_us(px4_ts)) if est.ready else None

    def _publish_telemetry(self):
        for d in self.drones:
            st = self.states[d.drone_id]
            if not st.has_new_sample():
                continue
            ts_us = self._px4_to_epoch_us(d.drone_id, st.global_ts)
            if ts_us is None:
                continue
            msg = build_telemetry(d.drone_id, self.mission_id, st, ts_us)
            if self.kafka.send_telemetry(d.drone_id, encode_json(msg)):
                self.counters.inc(self.counters.sent, "telemetry")
            else:
                self.counters.inc(self.counters.dropped, "telemetry")
            st.last_sent_global_ts = st.global_ts
        self.kafka.poll()

    # -- images -------------------------------------------------------------
    def _forward_jpeg(self, did: str, jpeg: bytes, stamp) -> None:
        self.epoch.latch(time.time_ns() // 1000, self._ros_now_us())
        if not self.epoch.ready:
            return
        ros_us = stamp_to_us(stamp.sec, stamp.nanosec)
        if not self.img_limit.allow(did, ros_us):
            return
        try:
            w, h = jpeg_dimensions(jpeg)
        except ValueError as e:
            self.counters.inc(self.counters.dropped, "image-frames(bad-jpeg)")
            self.get_logger().warn(f"{did}: {e}", throttle_duration_sec=5.0)
            return
        headers = image_headers(did, self.epoch.to_epoch_us(ros_us), self.frame_ids[did], w, h)
        if self.kafka.send_image(did, jpeg, headers):
            self.frame_ids[did] += 1
            self.counters.inc(self.counters.sent, "image-frames")
        else:
            self.counters.inc(self.counters.dropped, "image-frames")

    def _on_image(self, did, m: CompressedImage):
        data = bytes(m.data)
        if not is_jpeg(data):
            self.counters.inc(self.counters.dropped, "image-frames(not-jpeg)")
            self.get_logger().warn(f"{did}: camera format '{m.format}' is not JPEG",
                                   throttle_duration_sec=5.0)
            return
        self._forward_jpeg(did, data, m.header.stamp)

    def _on_raw_image(self, did, m: Image):
        # Encoding only happens for frames that pass the rate limit
        ros_us = stamp_to_us(m.header.stamp.sec, m.header.stamp.nanosec)
        if not self.img_limit.would_allow(did, ros_us):
            return
        import cv2
        import numpy as np
        if m.encoding not in ("rgb8", "bgr8"):
            self.get_logger().warn(f"{did}: unsupported raw encoding {m.encoding}",
                                   throttle_duration_sec=5.0)
            return
        img = np.frombuffer(bytes(m.data), dtype=np.uint8).reshape(m.height, m.step // 3, 3)[:, :m.width]
        if m.encoding == "rgb8":
            img = img[:, :, ::-1]
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if ok:
            self._forward_jpeg(did, buf.tobytes(), m.header.stamp)

    # -- housekeeping -------------------------------------------------------
    def _log_stats(self):
        offs = " ".join(f"{k}:{v.offset_us / 1e6:.3f}s" for k, v in self.offsets.items() if v.ready)
        self.get_logger().info(f"[stats] {self.counters.summary_and_reset()} "
                               f"| kafka errors={self.kafka.take_error_count()} | px4 offset {offs or 'n/a'}")

    def destroy_node(self):
        self.kafka.flush(5.0)
        super().destroy_node()


def main():
    rclpy.init()
    node = BridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
