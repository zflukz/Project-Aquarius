#!/usr/bin/env python3
"""Demo mission for the dashboard (Phase D): everything a real mission has, with fake drones.

Step 1 - put the mission, its search area + grid, the planned paths (mCPP) and
the ground truth people into the database:

    python3 tools/demo_mission.py | docker exec -i timescaledb psql -U aquarius -d aquarius -q

Step 2 - fly it: fake drones send telemetry, images, detections and survivors
through Kafka -> ingestion -> database, exactly like the real pipeline:

    python3 tools/demo_mission.py --print-fly-command      # prints the sim_producer command

Running step 1 again resets the demo mission (its old data is deleted first).
The same --drones / --area-m / --spacing / --center / --persons / --seed must be
used in both steps; --print-fly-command takes care of that.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim_producer import FakeDrone, offset_latlon, planted_persons  # noqa: E402

DEMO_MISSION = "de300000-0000-4000-8000-000000000001"


def q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def build_sql(a) -> str:
    lat0, lon0 = (float(v) for v in a.center.split(","))
    corners = [offset_latlon(lat0, lon0, n, e) for e, n in
               ((0, 0), (a.area_m, 0), (a.area_m, a.area_m), (0, a.area_m), (0, 0))]
    area = {"type": "Polygon", "coordinates": [[[round(lon, 8), round(lat, 8)] for lat, lon in corners]]}
    drones = [FakeDrone(n, a.drones, a.area_m, 5.0, a.spacing, lat0, lon0, 0.05, 1.0)
              for n in range(1, a.drones + 1)]
    m = q(DEMO_MISSION)
    out = [
        "\\set ON_ERROR_STOP on",
        "BEGIN;",
        "-- reset the demo mission",
        f"DELETE FROM survivors         WHERE mission_id = {m};",
        f"DELETE FROM replanning_events WHERE mission_id = {m};",
        f"DELETE FROM telemetry         WHERE mission_id = {m};",
        "DELETE FROM ground_truth_persons WHERE world = 'demo';",
        f"DELETE FROM missions          WHERE mission_id = {m};   -- cascades to area, grid, paths, persons",
        f"INSERT INTO missions (mission_id, name, status) VALUES ({m}, {q(a.name)}, 'active');",
        f"SELECT aq_create_search_area({m}, {q(json.dumps(area))}, 'demo area', {a.cell_m});",
    ]
    for d in drones:
        pts = [offset_latlon(lat0, lon0, y, x) for x, y in d.planned_lanes()]
        line = {"type": "LineString", "coordinates": [[round(lon, 8), round(lat, 8)] for lat, lon in pts]}
        out.append(f"INSERT INTO drones (drone_id) VALUES ({q(d.id)}) ON CONFLICT DO NOTHING;")
        out.append("INSERT INTO flight_paths (mission_id, drone_id, kind, version, geom) VALUES "
                   f"({m}, {q(d.id)}, 'planned', 1, ST_SetSRID(ST_GeomFromGeoJSON({q(json.dumps(line))}), 4326));")
    for label, _, _, lat, lon in planted_persons(a.persons, a.area_m, lat0, lon0, a.seed):
        out.append("INSERT INTO ground_truth_persons (world, label, mission_id, lat, lon) VALUES "
                   f"('demo', {q(label)}, {m}, {lat:.8f}, {lon:.8f});")
    out += ["COMMIT;",
            f"\\echo demo mission {DEMO_MISSION} ready: {a.drones} drones, {a.persons} people, "
            f"{a.area_m:.0f} x {a.area_m:.0f} m at {a.center}"]
    return "\n".join(out) + "\n"


def fly_command(a) -> str:
    return (f"python3 tools/sim_producer.py --mission-id {DEMO_MISSION} --drones {a.drones} "
            f"--area-m {a.area_m:g} --spacing {a.spacing:g} --center {a.center} "
            f"--persons {a.persons} --seed {a.seed} --image-size 1280x960")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drones", type=int, default=3)
    ap.add_argument("--area-m", type=float, default=300.0, help="side of the square search area")
    ap.add_argument("--spacing", type=float, default=30.0, help="lane spacing of the lawnmower (m)")
    ap.add_argument("--cell-m", type=float, default=20.0, help="grid cell size (m)")
    ap.add_argument("--center", default="14.3540,100.5600", help="south-west corner lat,lon (Ayutthaya)")
    ap.add_argument("--persons", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--name", default="Demo — Ayutthaya")
    ap.add_argument("--print-fly-command", action="store_true")
    a = ap.parse_args()
    print(fly_command(a) if a.print_fly_command else build_sql(a), end="" if not a.print_fly_command else "\n")


if __name__ == "__main__":
    main()
