#!/usr/bin/env python3
"""Print every SQL query of the provisioned Grafana dashboards, with Grafana's macros
replaced, so they can be run with psql (used by tools/phase_c_check.sh).

    python3 tools/dashboard_sql.py <mission_id> | docker exec -i timescaledb psql -U aquarius -d aquarius
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def queries(mission, swath="0", minutes=60):
    for path in sorted(glob.glob(os.path.join(HERE, "..", "..", "docker", "grafana", "dashboards", "mission.json"))):
        d = json.load(open(path, encoding="utf-8"))
        for v in d.get("templating", {}).get("list", []):
            if v.get("type") == "query":
                yield f"{os.path.basename(path)} variable {v['name']}", v["query"]
        for p in d.get("panels", []):
            for t in p.get("targets", []):
                s = t.get("rawSql", "")
                s = re.sub(r"\$__timeGroupAlias\((\w+),\s*'([^']+)'\)",
                           r"time_bucket(interval '\2', \1) AS time", s)
                s = re.sub(r"\$__timeFilter\((\w+)\)", rf"\1 > now() - interval '{minutes} minutes'", s)
                s = s.replace("$swath", swath).replace("$mission", mission)
                if "$" in s.replace("$$", ""):
                    raise SystemExit(f"unreplaced Grafana variable in {p['title']}: {s}")
                yield f"{os.path.basename(path)} / {p['title']} / {t['refId']}", s.strip()


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print("\\set ON_ERROR_STOP on")
    n = 0
    for label, sql in queries(sys.argv[1]):
        n += 1
        print(f"\\echo -- {label}\n{sql};")
    print(f"\\echo DASHBOARD QUERIES OK ({n})")


if __name__ == "__main__":
    main()
