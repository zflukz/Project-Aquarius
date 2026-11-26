#!/usr/bin/env python3
"""People for the Gazebo world: spawn them, read where they really are, store that as ground truth.

The KMUTT world has no people, so AI detection (P6) and geo-localization (P7) have nothing to find. This tool
puts person models from Gazebo Fuel into the RUNNING sim (nothing in the world file changes; they are gone after
px4-sim restarts) and writes their real positions into `ground_truth_persons` (F3), so detections / survivor pins
can later be compared with the truth in metres.

    python3 tools/sim_persons.py spawn                         # tools/persons_kmutt.json -> Gazebo + DB
    python3 tools/sim_persons.py spawn --file my.json --mission <uuid>
    python3 tools/sim_persons.py list                          # where they are now (world m + lat/lon)
    python3 tools/sim_persons.py ground-truth                  # (re)write the current positions to the DB
    python3 tools/sim_persons.py remove                        # take them out of Gazebo (+ DB rows with --db)
    python3 tools/sim_persons.py spawn --dry-run               # print the gz / SQL commands only

Scenario file (JSON):
    {"world": "kmutt", "spawn_z": 7.0,
     "persons": [{"label": "person_01", "model": "OpenRobotics/Standing person", "x": -75, "y": -60,
                  "pose": "standing", "yaw": 0.5}, ...]}
The Mission monitor map shows ground truth only for the selected mission → pass --mission (the bridge prints the
current one: `docker logs telemetry | grep "bridge up"`); without it the rows have mission_id NULL.

x / y = world metres (x east, y north, ENU, origin = the world's <spherical_coordinates>); "pose" standing | lying
(lying = rolled 90°); "z" per person overrides "spawn_z" and must be the ground height there (people are
static by default: "static": false per person / file gives them physics again). Fuel models are downloaded once inside px4-sim.
Use a model that renders in the drone camera: "OpenRobotics/Standing person" does; "Casual female" and
"Rescue Randy" are in Gazebo but invisible to the camera (checked 2026-11-26 at 10-40 m).
Lying (rolled) people did not show up in the drone camera at all (even 0.6 m above the ground, 2026-11-26),
so the default scenario has standing people only; a lying / sitting / half-submerged model is still needed.
Keep people on open ground: one standing next to a building was hidden by it.
Static on purpose: "Standing person" collides with its full mesh; against the KMUTT terrain mesh 4 of them
dropped the sim from RTF 1.0 to 0.1 (and missions timed out). Static models skip that physics.

Runs on the host next to the Docker stack: Gazebo through `docker exec px4-sim gz ...`, the DB through
`docker exec timescaledb psql` (no password / port needed, no .env read). Containers: --sim / --db-container.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(HERE, "persons_kmutt.json")
FUEL = "https://fuel.gazebosim.org/1.0"
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")          # Gazebo model name = ground truth label
MODEL_RE = re.compile(r"^[A-Za-z0-9_\-]+/[A-Za-z0-9 _\-]+$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
WGS84_A, WGS84_E2 = 6_378_137.0, 6.69437999014e-3


# ---------- pure helpers (unit-tested) ----------
@dataclass
class Person:
    label: str
    model: str          # Fuel "Owner/Model name"
    x: float
    y: float
    z: float
    roll: float
    yaw: float
    static: bool = True


def load_scenario(text: str) -> tuple[str, list[Person]]:
    """Scenario JSON -> (world, persons). Raises ValueError with the reason on bad input."""
    d = json.loads(text)
    world = d.get("world", "kmutt")
    if not NAME_RE.match(world):
        raise ValueError(f"bad world name {world!r}")
    spawn_z = float(d.get("spawn_z", 7.0))
    static_default = bool(d.get("static", True))
    out, seen = [], set()
    for i, p in enumerate(d.get("persons", [])):
        label, model = str(p.get("label", "")), str(p.get("model", ""))
        if not NAME_RE.match(label):
            raise ValueError(f"person {i}: label {label!r} must be letters / digits / _ / - (it is the Gazebo name)")
        if label in seen:
            raise ValueError(f"person {i}: label {label!r} used twice")
        if not MODEL_RE.match(model):
            raise ValueError(f"person {i}: model {model!r} must look like 'OpenRobotics/Standing person'")
        pose = p.get("pose", "standing")
        if pose not in ("standing", "lying"):
            raise ValueError(f"person {i}: pose must be standing or lying, not {pose!r}")
        vals = [float(p["x"]), float(p["y"]), float(p.get("z", spawn_z)), float(p.get("yaw", 0.0))]
        if not all(math.isfinite(v) for v in vals):
            raise ValueError(f"person {i}: x / y / z / yaw must be finite numbers")
        seen.add(label)
        out.append(Person(label, model, vals[0], vals[1], vals[2], math.pi / 2 if pose == "lying" else 0.0, vals[3],
                          bool(p.get("static", static_default))))
    if not out:
        raise ValueError("no persons in the file")
    return world, out


def fuel_uri(model: str) -> str:
    owner, name = model.split("/", 1)
    return f"{FUEL}/{owner}/models/{name}"


def spawn_sdf(p: Person) -> str:
    return (f'<sdf version="1.9"><include><uri>{fuel_uri(p.model)}</uri><name>{p.label}</name>'
            f"<pose>{p.x:g} {p.y:g} {p.z:g} {p.roll:.4f} 0 {p.yaw:.4f}</pose>"
            + ("<static>true</static>" if p.static else "") + "</include></sdf>")


def gz_req_string(sdf: str) -> str:
    """EntityFactory request in protobuf text format (quotes escaped)."""
    return 'sdf: "' + sdf.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_spherical(sdf_text: str) -> dict:
    """<spherical_coordinates> of a world SDF -> lat, lon, elevation, heading (deg)."""
    def tag(name, default=None):
        m = re.search(rf"<{name}>\s*([-+0-9.eE]+)\s*</{name}>", sdf_text)
        if m:
            return float(m.group(1))
        if default is None:
            raise ValueError(f"world SDF has no <{name}>")
        return default
    return dict(lat=tag("latitude_deg"), lon=tag("longitude_deg"), elevation=tag("elevation", 0.0),
                heading=tag("heading_deg", 0.0))


def enu_to_latlon(origin: dict, x: float, y: float, z: float) -> tuple[float, float, float]:
    """World ENU metres -> WGS84 lat, lon, alt (local tangent plane; cm-accurate over a few km)."""
    h = math.radians(origin.get("heading", 0.0))
    east, north = x * math.cos(h) - y * math.sin(h), x * math.sin(h) + y * math.cos(h)
    lat0 = math.radians(origin["lat"])
    s = math.sin(lat0) ** 2
    m = WGS84_A * (1 - WGS84_E2) / (1 - WGS84_E2 * s) ** 1.5      # meridian radius
    n = WGS84_A / math.sqrt(1 - WGS84_E2 * s)                     # prime vertical radius
    return (origin["lat"] + math.degrees(north / m), origin["lon"] + math.degrees(east / (n * math.cos(lat0))),
            origin.get("elevation", 0.0) + z)


def parse_pose_info(text: str) -> dict[str, tuple[float, float, float]]:
    """`gz topic -e -t /world/<w>/pose/info -n 1` -> {model name: (x, y, z)} (missing axes are 0)."""
    poses, name, block, pos = {}, None, None, {}
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r'^name: "(.*)"$', line)
        if m and raw.startswith("  ") and not raw.startswith("    "):
            if name is not None:
                poses.setdefault(name, (pos.get("x", 0.0), pos.get("y", 0.0), pos.get("z", 0.0)))
            name, pos, block = m.group(1), {}, None
        elif line.endswith("{"):
            block = line[:-1].strip()
        elif line == "}":
            block = None
        elif block == "position" and name is not None:
            m = re.match(r"^([xyz]): ([-+0-9.eE]+)$", line)
            if m:
                pos[m.group(1)] = float(m.group(2))
    if name is not None:
        poses.setdefault(name, (pos.get("x", 0.0), pos.get("y", 0.0), pos.get("z", 0.0)))
    return poses


def upsert_sql(world: str, rows: list[tuple[str, float, float, float]], mission: str | None) -> str:
    """rows = (label, lat, lon, alt_amsl). Labels / world / mission are validated before, numbers are floats."""
    if mission is not None and not UUID_RE.match(mission):
        raise ValueError(f"bad mission id {mission!r}")
    mid = f"'{mission}'::uuid" if mission else "NULL"
    if mission:   # ground_truth_persons.mission_id has an FK -> make sure the mission row exists
        head = f"INSERT INTO missions (mission_id) VALUES ({mid}) ON CONFLICT (mission_id) DO NOTHING;\n"
    else:
        head = ""
    vals = ",\n  ".join(f"('{world}', '{lab}', {mid}, {lat:.9f}, {lon:.9f}, {alt:.3f})" for lab, lat, lon, alt in rows)
    return (head + "INSERT INTO ground_truth_persons (world, label, mission_id, lat, lon, alt_amsl) VALUES\n  "
            + vals + "\nON CONFLICT (world, label) DO UPDATE SET mission_id = EXCLUDED.mission_id, "
            "lat = EXCLUDED.lat, lon = EXCLUDED.lon, alt_amsl = EXCLUDED.alt_amsl;")


# ---------- docker / gz / psql ----------
class Runner:
    def __init__(self, sim: str, db: str, dry: bool):
        self.sim, self.db, self.dry = sim, db, dry

    def run(self, cmd: list[str], stdin: str | None = None, check=True) -> str:
        if self.dry:
            print("$ " + " ".join(cmd) + (f"  <<< {stdin}" if stdin else ""))
            return ""
        r = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd[:4])} ... failed: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout

    def sim_sh(self, script: str, check=True) -> str:
        return self.run(["docker", "exec", self.sim, "bash", "-c", script], check=check)

    def psql(self, sql: str) -> str:
        return self.run(["docker", "exec", "-i", self.db, "psql", "-v", "ON_ERROR_STOP=1", "-U", "aquarius",
                         "-d", "aquarius", "-q", "-At"], stdin=sql)


def world_origin(r: Runner, world: str) -> dict:
    text = r.sim_sh(f"cat /px4/Tools/simulation/gz/worlds/{world}.sdf")
    if r.dry:
        return dict(lat=0.0, lon=0.0, elevation=0.0, heading=0.0)
    return parse_spherical(text)


def live_poses(r: Runner, world: str) -> dict:
    out = r.sim_sh(f"timeout 10 gz topic -e -t /world/{world}/pose/info -n 1")
    return {} if r.dry else parse_pose_info(out)


def ensure_fuel(r: Runner, models: set[str]) -> None:
    for model in sorted(models):
        owner, name = model.split("/", 1)
        cache = f"$HOME/.gz/fuel/fuel.gazebosim.org/{owner.lower()}/models/{name.lower()}"
        print(f"fuel: {model}")
        r.sim_sh(f'[ -d "{cache}" ] || timeout 600 gz fuel download -u "{fuel_uri(model)}"')


def write_ground_truth(r: Runner, world: str, labels: list[str], mission: str | None) -> int:
    origin, poses = world_origin(r, world), live_poses(r, world)
    rows, missing = [], []
    for lab in labels:
        if r.dry:
            rows.append((lab, 0.0, 0.0, 0.0))
        elif lab in poses:
            rows.append((lab, *enu_to_latlon(origin, *poses[lab])))
        else:
            missing.append(lab)
    if missing:
        print(f"not in Gazebo (skipped): {', '.join(missing)}")
    if rows:
        r.psql(upsert_sql(world, rows, mission))
        for lab, lat, lon, alt in rows:
            print(f"ground truth {lab}: {lat:.7f}, {lon:.7f}, alt {alt:.2f} m")
    return len(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["spawn", "list", "ground-truth", "remove"])
    ap.add_argument("--file", default=DEFAULT_FILE, help="scenario JSON (default tools/persons_kmutt.json)")
    ap.add_argument("--mission", help="mission_id to attach the ground truth to (optional)")
    ap.add_argument("--no-db", action="store_true", help="spawn only, do not write ground_truth_persons")
    ap.add_argument("--db", action="store_true", help="with remove: also delete their ground_truth_persons rows")
    ap.add_argument("--sim", default="px4-sim", help="Gazebo container (default px4-sim)")
    ap.add_argument("--db-container", default="timescaledb")
    ap.add_argument("--dry-run", action="store_true", help="print the commands, change nothing")
    a = ap.parse_args(argv)
    try:
        with open(a.file, encoding="utf-8") as f:
            world, persons = load_scenario(f.read())
        if a.mission and not UUID_RE.match(a.mission):
            raise ValueError(f"bad --mission {a.mission!r}")
    except (OSError, ValueError, KeyError) as e:
        print(f"scenario: {e}", file=sys.stderr)
        return 2
    r = Runner(a.sim, a.db_container, a.dry_run)
    labels = [p.label for p in persons]
    try:
        if a.action == "spawn":
            ensure_fuel(r, {p.model for p in persons})
            present = set(live_poses(r, world))
            for p in persons:
                if p.label in present:
                    print(f"{p.label}: already in Gazebo, left as it is")
                    continue
                out = r.sim_sh(f"gz service -s /world/{world}/create --reqtype gz.msgs.EntityFactory "
                               f"--reptype gz.msgs.Boolean --timeout 10000 --req '{gz_req_string(spawn_sdf(p))}'")
                if not r.dry and "data: true" not in out:
                    raise RuntimeError(f"Gazebo refused {p.label}: {out.strip()[:200]}")
                print(f"spawned {p.label} ({p.model}, {'lying' if p.roll else 'standing'}) at {p.x:g}, {p.y:g}")
            if not a.no_db:
                r.sim_sh("sleep 3")          # let physics put them on the ground before reading positions
                write_ground_truth(r, world, labels, a.mission)
        elif a.action == "ground-truth":
            write_ground_truth(r, world, labels, a.mission)
        elif a.action == "list":
            origin, poses = world_origin(r, world), live_poses(r, world)
            for lab in labels:
                if lab in poses:
                    lat, lon, alt = enu_to_latlon(origin, *poses[lab])
                    x, y, z = poses[lab]
                    print(f"{lab}: world {x:.2f}, {y:.2f}, {z:.2f} m -> {lat:.7f}, {lon:.7f}, alt {alt:.2f} m")
                else:
                    print(f"{lab}: not in Gazebo")
        elif a.action == "remove":
            for lab in labels:
                out = r.sim_sh(f"gz service -s /world/{world}/remove --reqtype gz.msgs.Entity --reptype "
                               f"gz.msgs.Boolean --timeout 5000 --req 'name: \"{lab}\" type: MODEL'", check=False)
                print(f"remove {lab}: {'ok' if r.dry or 'data: true' in out else 'not found'}")
            if a.db:
                names = ", ".join(f"'{lab}'" for lab in labels)
                r.psql(f"DELETE FROM ground_truth_persons WHERE world = '{world}' AND label IN ({names});")
                print("ground truth rows deleted")
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
