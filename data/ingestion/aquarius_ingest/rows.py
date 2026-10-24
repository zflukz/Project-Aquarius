"""Kafka message -> database row (pure logic, unit-tested without Kafka or Postgres)."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from jsonschema import Draft202012Validator

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
SUPPORTED_SCHEMA_VERSION = 1
JSON_TOPICS = ("telemetry", "detections", "survivors", "replanning-events")

SQL = {
    "telemetry": """
        INSERT INTO telemetry (ts, drone_id, mission_id, lat, lon, alt_amsl, alt_rel, q, vel_ned,
                               battery_remaining, battery_voltage, nav_state, armed)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING""",
    "detections": """
        INSERT INTO detections (ts, drone_id, frame_id, model_version, image_w, image_h, objects)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT DO NOTHING""",
    "survivors": """
        INSERT INTO survivors (detection_id, mission_id, drone_id, frame_id, ts, lat, lon,
                               conf, pose_dt_ms, snapshot_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING""",
    "replanning-events": """
        INSERT INTO replanning_events (event_id, mission_id, ts, event_type, drone_id,
                                       battery_remaining, waypoints_reassigned, assigned_to)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING""",
}

# Phase C: every mission / drone seen in a batch gets its row in missions / drones
# (same transaction as the data), keeping the earliest and latest data time.
MISSION_UPSERT_SQL = """
    INSERT INTO missions (mission_id, first_ts, last_ts) VALUES (%s, %s, %s)
    ON CONFLICT (mission_id) DO UPDATE
       SET first_ts = LEAST(missions.first_ts, EXCLUDED.first_ts),
           last_ts  = GREATEST(missions.last_ts, EXCLUDED.last_ts)"""
DRONE_UPSERT_SQL = """
    INSERT INTO drones (drone_id, first_ts, last_ts) VALUES (%s, %s, %s)
    ON CONFLICT (drone_id) DO UPDATE
       SET first_ts = LEAST(drones.first_ts, EXCLUDED.first_ts),
           last_ts  = GREATEST(drones.last_ts, EXCLUDED.last_ts)"""
# C5: merge new survivors of a mission into persons (pins)
MERGE_SQL = "SELECT aq_merge_survivors(%s, %s)"

# fields that must be real UUIDs (the JSON schema only checks the shape); a bad
# one would make the whole database batch fail, so it is rejected up front
UUID_FIELDS = ("mission_id", "detection_id", "event_id")

REJECT_SQL = """
    INSERT INTO ingest_rejects (topic, kafka_part, kafka_offset, reason, payload)
    VALUES (%s, %s, %s, %s, %s)"""


def ts_from_us(ts_us: int) -> datetime:
    """Exact µs conversion (float division would lose precision at epoch scale)."""
    return EPOCH + timedelta(microseconds=int(ts_us))


def default_schema_dir() -> str:
    env = os.environ.get("SCHEMA_DIR")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "..", "schemas"), os.path.join(here, "..", "schemas"), "/app/schemas"):
        if os.path.isdir(cand):
            return os.path.abspath(cand)
    raise FileNotFoundError("schemas/ directory not found; set SCHEMA_DIR")


def load_validators(schema_dir: Optional[str] = None) -> Dict[str, Draft202012Validator]:
    schema_dir = schema_dir or default_schema_dir()
    out = {}
    for topic in JSON_TOPICS:
        with open(os.path.join(schema_dir, f"{topic}.schema.json"), encoding="utf-8") as f:
            out[topic] = Draft202012Validator(json.load(f))
    return out


class Rejected(Exception):
    """Message is not stored as data; it goes to ingest_rejects with this reason."""


def decode(topic: str, value: Optional[bytes], validators) -> Dict[str, Any]:
    if value is None:
        raise Rejected("empty value")
    try:
        msg = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise Rejected(f"invalid JSON: {e}") from None
    if not isinstance(msg, dict):
        raise Rejected("value is not a JSON object")
    ver = msg.get("schema_version")
    if ver != SUPPORTED_SCHEMA_VERSION:
        raise Rejected(f"unsupported schema_version {ver!r}")
    err = next(iter(validators[topic].iter_errors(msg)), None)
    if err is not None:
        path = "/".join(str(p) for p in err.absolute_path) or "(root)"
        raise Rejected(f"schema: {path}: {err.message}"[:500])
    for f in UUID_FIELDS:
        if msg.get(f) is not None:
            try:
                uuid.UUID(msg[f])
            except ValueError:
                raise Rejected(f"{f}: not a valid UUID") from None
    return msg


def to_row(topic: str, m: Dict[str, Any]) -> Tuple:
    ts = ts_from_us(m["ts_us"])
    if topic == "telemetry":
        return (ts, m["drone_id"], m["mission_id"], m["lat"], m["lon"], m["alt_amsl"], m["alt_rel"],
                m["q"], m["vel_ned"], m["battery_remaining"], m["battery_voltage"],
                m["nav_state"], m["armed"])
    if topic == "detections":
        return (ts, m["drone_id"], m["frame_id"], m["model_version"], m["image_w"], m["image_h"],
                json.dumps(m["objects"], separators=(",", ":")))
    if topic == "survivors":
        return (m["detection_id"], m["mission_id"], m["drone_id"], m["frame_id"], ts, m["lat"], m["lon"],
                m["conf"], m["pose_dt_ms"], m["snapshot_path"])
    if topic == "replanning-events":
        return (m["event_id"], m["mission_id"], ts, m["event_type"], m.get("drone_id"),
                m.get("battery_remaining"), m.get("waypoints_reassigned"), m.get("assigned_to"))
    raise ValueError(f"unknown topic {topic}")


class Seen:
    """Missions and drones in one batch, with their earliest/latest data time (Phase C)."""

    def __init__(self):
        self.missions: Dict[str, list] = {}
        self.drones: Dict[str, list] = {}
        self.survivor_missions: set = set()

    @staticmethod
    def _add(d, key, ts):
        if key is None:
            return
        if key in d:
            d[key][0] = min(d[key][0], ts)
            d[key][1] = max(d[key][1], ts)
        else:
            d[key] = [ts, ts]

    def add(self, topic: str, m: Dict[str, Any]) -> None:
        ts = ts_from_us(m["ts_us"])
        self._add(self.missions, m.get("mission_id"), ts)
        self._add(self.drones, m.get("drone_id"), ts)
        if topic == "survivors":
            self.survivor_missions.add(m["mission_id"])

    def mission_rows(self):
        return [(k, v[0], v[1]) for k, v in sorted(self.missions.items())]

    def drone_rows(self):
        return [(k, v[0], v[1]) for k, v in sorted(self.drones.items())]
