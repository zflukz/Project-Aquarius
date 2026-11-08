#!/usr/bin/env bash
# One measurement round of the DDS-over-Wi-Fi test (run on the ground station).
#
#   tools/dds/measure_round.sh <label> [seconds]
#   e.g. tools/dds/measure_round.sh 3uav_clean
#        (on the drone/SITL machine: sudo tools/dds/netem.sh on wlan0 5 50 20)
#        tools/dds/measure_round.sh 3uav_loss5
#
# Writes results/dds_<label>_<time>.csv and prints a one-line verdict per drone
# (telemetry loss must stay below 5 %).
set -euo pipefail
cd "$(dirname "$0")/../.."
label="${1:?usage: measure_round.sh <label> [seconds]}"
secs="${2:-120}"
mkdir -p results
out="results/dds_${label}_$(date +%Y%m%d_%H%M%S).csv"
echo "ROS_DISCOVERY_SERVER=${ROS_DISCOVERY_SERVER:-<not set>}  measuring ${secs}s -> $out"
python3 tools/pipeline_stats.py --seconds "$secs" --csv "$out"
python3 - "$out" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
bad = [r for r in rows if float(r["telemetry_loss_pct"]) >= 5.0]
for r in rows:
    flag = "FAIL" if r in bad else "ok"
    print(f"  {r['drone_id']:<6} loss {float(r['telemetry_loss_pct']):5.2f} %  p95 latency {r['latency_p95_ms']} ms  [{flag}]")
print(f"{len(rows)} drones, {len(bad)} above 5 % loss -> {'FAIL' if bad else 'PASS'}")
PY
