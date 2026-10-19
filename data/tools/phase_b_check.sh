#!/usr/bin/env bash
# Phase B check-up in one command (run while the bridge is sending data).
#
#   tools/phase_b_check.sh            # 30 s measurement
#   tools/phase_b_check.sh 60
#
# Prints PASS/FAIL for: services, topics, data per drone in Kafka, images,
# time sync (A4), rows in the database, duplicates and rejected messages.
set -uo pipefail
cd "$(dirname "$0")/.."
SECS="${1:-30}"
pass=0; fail=0
ok()  { echo "  PASS  $*"; pass=$((pass+1)); }
bad() { echo "  FAIL  $*"; fail=$((fail+1)); }
psql_q() { docker exec aquarius-db psql -U aquarius -d aquarius -tAc "$1" 2>/dev/null; }

echo "== 1. Docker services (B1)"
for s in kafka db grafana ingestion kafka-ui; do
  st=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$s" '$1==s {print $2}')
  [[ "$st" == "running" ]] && ok "$s running" || bad "$s is '${st:-missing}'"
done

echo "== 2. Kafka topics (A3)"
desc=$(docker exec aquarius-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:19092 --describe 2>/dev/null | grep -E '^Topic:')
for t in telemetry image-frames detections survivors replanning-events; do
  n=$(echo "$desc" | awk -v t="$t" '$2==t {for(i=1;i<=NF;i++) if($i=="PartitionCount:") print $(i+1)}')
  want=10; [[ "$t" == "replanning-events" ]] && want=1
  [[ "$n" == "$want" ]] && ok "$t ($n partitions)" || bad "$t partitions=${n:-missing}, expected $want"
done

echo "== 3. Data in Kafka for ${SECS}s (B2)"
tmp=$(mktemp --suffix=.csv)
if python3 tools/pipeline_stats.py --seconds "$SECS" --csv "$tmp" >/dev/null 2>&1; then
  python3 - "$tmp" <<'PY'
import csv, sys
for r in csv.DictReader(open(sys.argv[1])):
    rate, loss, imgs = float(r["telemetry_rate_hz"]), float(r["telemetry_loss_pct"]), int(r["images"])
    tag = "PASS" if rate >= 8 and loss < 5 else "FAIL"
    print(f"  {tag}  {r['drone_id']}: telemetry {rate} Hz (sim clock; {r['telemetry_rate_wall_hz']} Hz real time), loss {loss} %, latency p50 {r['latency_p50_ms']} ms; "
          f"images {imgs} ({r['image_fps']} fps, max {r['image_max_kb']} KB)")
PY
  drones=$(($(wc -l < "$tmp") - 1))
  (( drones >= 3 )) && ok "$drones drones sending (B2 needs 3)" || bad "only $drones drone(s) sending (B2 needs 3)"
  maxkb=$(python3 -c "import csv,sys;print(max([float(r['image_max_kb']) for r in csv.DictReader(open('$tmp'))]+[0]))")
  python3 -c "import sys;sys.exit(0 if 0<$maxkb<2048 else 1)" && ok "largest JPEG ${maxkb} KB (< 2048 KB limit, A3)" \
    || bad "no images or JPEG too large (max ${maxkb} KB)"
else
  bad "no data from Kafka (is the bridge running?)"
fi
rm -f "$tmp"

echo "== 4. Image / telemetry time sync (A4)"
python3 tools/check_time_sync.py --seconds "$SECS" > /tmp/aq_sync.txt 2>/dev/null
if grep -qE ' (PASS|FAIL)$' /tmp/aq_sync.txt; then
  grep -E ' (PASS|FAIL|NO DATA)$' /tmp/aq_sync.txt | sed 's/^/        /'
  if grep -qE ' (FAIL|NO DATA)$' /tmp/aq_sync.txt; then bad "some drones out of sync or without images"; else ok "all drones within 50 ms"; fi
else
  bad "time sync not verified (no images yet?) - run tools/check_time_sync.py for details"
fi

echo "== 5. Database (B3)"
rows=$(psql_q "SELECT string_agg(drone_id || '=' || n, ' ' ORDER BY length(drone_id), drone_id) FROM (SELECT drone_id, count(*) n FROM telemetry WHERE received_at > now() - interval '5 minutes' GROUP BY 1) x;")
[[ -n "$rows" ]] && ok "telemetry rows in last 5 min: $rows" || bad "no telemetry rows in the last 5 minutes"
d=$(psql_q "SELECT count(*) FROM (SELECT drone_id, ts FROM telemetry GROUP BY 1,2 HAVING count(*) > 1) d;")
[[ "$d" == "0" ]] && ok "no duplicate rows" || bad "$d duplicate rows"
r=$(psql_q "SELECT count(*) FROM ingest_rejects WHERE received_at > now() - interval '1 hour';")
[[ "$r" == "0" ]] && ok "no rejected messages in the last hour" || bad "$r rejected messages (SELECT topic, reason FROM ingest_rejects)"

echo
echo "Result: $pass passed, $fail failed"
(( fail == 0 ))
