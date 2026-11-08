"""Pure logic of the ROS 2–Kafka Bridge (no rclpy, no Kafka).

Kept separate from the ROS node so it can be unit-tested on any machine:
    python3 -m pytest ros2_ws/src/aquarius_bridge/test
"""
from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1

# PX4 VehicleStatus.nav_state values -> readable names (px4_msgs/msg/VehicleStatus.msg)
NAV_STATE_NAMES = {
    0: "MANUAL", 1: "ALTCTL", 2: "POSCTL", 3: "AUTO_MISSION", 4: "AUTO_LOITER",
    5: "AUTO_RTL", 10: "ACRO", 12: "DESCEND", 13: "TERMINATION", 14: "OFFBOARD",
    15: "STAB", 17: "AUTO_TAKEOFF", 18: "AUTO_LAND", 19: "AUTO_FOLLOW_TARGET",
    20: "AUTO_PRECLAND", 21: "ORBIT", 22: "AUTO_VTOL_TAKEOFF",
}
ARMING_STATE_ARMED = 2


def nav_state_name(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return NAV_STATE_NAMES.get(int(value), f"UNKNOWN_{int(value)}")


# ---------------------------------------------------------------------------
# Drone list
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DroneSpec:
    drone_id: str       # "uav1" — Kafka key and value in every message
    px4_ns: str         # "/px4_1" — namespace of the PX4 uXRCE-DDS topics ("" for none)
    camera_topic: str   # "/uav1/camera/image/compressed"


def parse_drone_specs(entries: List[str]) -> List[DroneSpec]:
    """Parse ["uav1,/px4_1,/uav1/camera/image/compressed", ...]."""
    specs, seen = [], set()
    for raw in entries:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 3 or not parts[0]:
            raise ValueError(f"drone entry must be 'drone_id,px4_namespace,camera_topic': {raw!r}")
        drone_id, ns, cam = parts
        if not drone_id.startswith("uav") or not drone_id[3:].isdigit():
            raise ValueError(f"drone_id must look like uav1, uav2, ...: {drone_id!r}")
        if drone_id in seen:
            raise ValueError(f"duplicate drone_id {drone_id!r}")
        seen.add(drone_id)
        ns = "" if ns in ("", "/") else "/" + ns.strip("/")
        specs.append(DroneSpec(drone_id, ns, cam))
    if not specs:
        raise ValueError("no drones configured")
    return specs


# ---------------------------------------------------------------------------
# Time (A4)
# ---------------------------------------------------------------------------
class ClockOffsetEstimator:
    """Maps PX4 timestamps (µs since PX4 boot) onto the ROS clock.

    Every received message gives one sample  d = ros_receive_time - px4_timestamp.
    Transport delay only ever makes d larger, so the smallest d in a recent
    window is the best estimate of the true clock offset. This is the same idea
    as NTP / Cristian's algorithm, and it means the sample time (not the receive
    time) ends up in ts_us, as A4 requires.
    """

    def __init__(self, window: int = 500):
        self._samples: Deque[int] = deque(maxlen=window)
        self._min: Optional[int] = None

    def observe(self, px4_ts_us: int, ros_now_us: int) -> None:
        d = int(ros_now_us) - int(px4_ts_us)
        if len(self._samples) == self._samples.maxlen and self._samples[0] == self._min:
            self._samples.append(d)
            self._min = min(self._samples)
        else:
            self._samples.append(d)
            self._min = d if self._min is None else min(self._min, d)

    @property
    def ready(self) -> bool:
        return self._min is not None

    @property
    def offset_us(self) -> int:
        if self._min is None:
            raise RuntimeError("no samples yet")
        return self._min

    def to_ros_us(self, px4_ts_us: int) -> int:
        return int(px4_ts_us) + self.offset_us


class EpochMapper:
    """Turns ROS time into UTC epoch µs.

    Real hardware: the ROS clock already is UTC (synced by chrony), offset = 0.
    Simulation (use_sim_time): the ROS clock starts near 0 when Gazebo starts, so
    a constant offset (wall clock - sim clock, taken once) is added. Every message
    goes through the same bridge, so images and telemetry stay on one clock.
    """

    def __init__(self, use_sim_time: bool):
        self.use_sim_time = use_sim_time
        self._offset: Optional[int] = None if use_sim_time else 0

    def latch(self, wall_now_us: int, ros_now_us: int) -> None:
        if self._offset is None and ros_now_us > 0:
            self._offset = int(wall_now_us) - int(ros_now_us)

    @property
    def ready(self) -> bool:
        return self._offset is not None

    def to_epoch_us(self, ros_us: int) -> int:
        if self._offset is None:
            raise RuntimeError("sim clock not received yet")
        return int(ros_us) + self._offset


def stamp_to_us(sec: int, nanosec: int) -> int:
    return int(sec) * 1_000_000 + int(nanosec) // 1000


# ---------------------------------------------------------------------------
# Telemetry (A2)
# ---------------------------------------------------------------------------
@dataclass
class DroneState:
    """Latest value of every PX4 topic for one drone."""
    global_ts: Optional[int] = None          # PX4 µs-since-boot of the global position
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt_amsl: Optional[float] = None
    local_z: Optional[float] = None          # NED, positive down
    vel_ned: Optional[Tuple[float, float, float]] = None
    q: Optional[Tuple[float, float, float, float]] = None
    battery_remaining: Optional[float] = None
    battery_voltage: Optional[float] = None
    nav_state: Optional[int] = None
    arming_state: Optional[int] = None
    last_sent_global_ts: Optional[int] = None

    def update_global(self, ts: int, lat: float, lon: float, alt: float) -> None:
        self.global_ts, self.lat, self.lon, self.alt_amsl = int(ts), float(lat), float(lon), float(alt)

    def update_local(self, z: float, vx: float, vy: float, vz: float) -> None:
        self.local_z = float(z)
        self.vel_ned = (float(vx), float(vy), float(vz))

    def update_attitude(self, q) -> None:
        self.q = tuple(float(v) for v in q)  # type: ignore[assignment]

    def update_battery(self, remaining: float, voltage: float) -> None:
        # PX4 sends remaining = -1 when unknown
        self.battery_remaining = None if remaining is None or remaining < 0 else min(float(remaining), 1.0)
        self.battery_voltage = None if voltage is None or voltage <= 0 else float(voltage)

    def update_status(self, nav_state: int, arming_state: int) -> None:
        self.nav_state, self.arming_state = int(nav_state), int(arming_state)

    def has_new_sample(self) -> bool:
        return (self.global_ts is not None and self.q is not None
                and self.global_ts != self.last_sent_global_ts)


def _finite(x: Optional[float]) -> Optional[float]:
    return None if x is None or not math.isfinite(x) else x


def build_telemetry(drone_id: str, mission_id: str, state: DroneState, ts_us: int) -> dict:
    """One telemetry message, exactly the fields of the A2 table."""
    vel = state.vel_ned or (0.0, 0.0, 0.0)
    return {
        "schema_version": SCHEMA_VERSION,
        "drone_id": drone_id,
        "mission_id": mission_id,
        "ts_us": int(ts_us),
        "lat": state.lat,
        "lon": state.lon,
        "alt_amsl": _finite(state.alt_amsl),
        "alt_rel": _finite(-state.local_z) if state.local_z is not None else None,
        "q": [round(v, 6) for v in state.q],  # type: ignore[union-attr]
        "vel_ned": [round(_finite(v) or 0.0, 4) for v in vel],
        "battery_remaining": state.battery_remaining,
        "battery_voltage": state.battery_voltage,
        "nav_state": nav_state_name(state.nav_state),
        "armed": None if state.arming_state is None else state.arming_state == ARMING_STATE_ARMED,
    }


def encode_json(msg: dict) -> bytes:
    return json.dumps(msg, separators=(",", ":"), allow_nan=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Images (A2: value = JPEG bytes, metadata in Kafka headers)
# ---------------------------------------------------------------------------
_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def is_jpeg(data: bytes) -> bool:
    return len(data) > 4 and data[0] == 0xFF and data[1] == 0xD8


def jpeg_dimensions(data: bytes) -> Tuple[int, int]:
    """(width, height) read from the JPEG SOF header, without decoding the image."""
    if not is_jpeg(data):
        raise ValueError("not a JPEG")
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            raise ValueError("corrupt JPEG marker stream")
        marker = data[i + 1]
        if marker == 0xFF:            # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length field
            i += 2
            continue
        seg_len = (data[i + 2] << 8) | data[i + 3]
        if marker in _SOF_MARKERS:
            if i + 9 > n:
                break
            h = (data[i + 5] << 8) | data[i + 6]
            w = (data[i + 7] << 8) | data[i + 8]
            return w, h
        i += 2 + seg_len
    raise ValueError("no SOF segment found")


def image_headers(drone_id: str, ts_us: int, frame_id: int, width: int, height: int) -> List[Tuple[str, bytes]]:
    return [
        ("schema_version", str(SCHEMA_VERSION).encode()),
        ("drone_id", drone_id.encode()),
        ("ts_us", str(int(ts_us)).encode()),
        ("frame_id", str(int(frame_id)).encode()),
        ("width", str(int(width)).encode()),
        ("height", str(int(height)).encode()),
    ]


class RateLimiter:
    """Lets at most `hz` events per second through (by message time), per key."""

    def __init__(self, hz: float):
        self.min_dt_us = 0 if hz <= 0 else int(1_000_000 / hz)
        self._last: Dict[str, int] = {}

    def would_allow(self, key: str, t_us: int) -> bool:
        last = self._last.get(key)
        # allow 10% jitter so a 2 fps camera is not cut to 1 fps by timing noise
        return last is None or t_us - last >= self.min_dt_us * 0.9

    def allow(self, key: str, t_us: int) -> bool:
        if not self.would_allow(key, t_us):
            return False
        self._last[key] = t_us
        return True


@dataclass
class Counters:
    sent: Dict[str, int] = field(default_factory=dict)
    dropped: Dict[str, int] = field(default_factory=dict)

    def inc(self, table: Dict[str, int], key: str, n: int = 1) -> None:
        table[key] = table.get(key, 0) + n

    def summary_and_reset(self) -> str:
        s = (" ".join(f"{k}={v}" for k, v in sorted(self.sent.items())) or "nothing sent")
        if self.dropped:
            s += " | dropped " + " ".join(f"{k}={v}" for k, v in sorted(self.dropped.items()))
        self.sent.clear()
        self.dropped.clear()
        return s
