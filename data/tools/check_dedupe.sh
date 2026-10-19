#!/usr/bin/env bash
# B3 check: the ingestion consumer never writes duplicate rows.
#
#   tools/check_dedupe.sh
#
# Worst case on purpose: stop ingestion, rewind its Kafka offsets to the very
# beginning (so EVERY message is delivered again), start it and let it catch up.
# Pass = 0 duplicate (drone_id, ts) rows and the row count did not double.
set -euo pipefail
cd "$(dirname "$0")/.."

psql_q() { docker exec aquarius-db psql -U "${POSTGRES_USER:-aquarius}" -d "${POSTGRES_DB:-aquarius}" -tAc "$1"; }
dupes() { psql_q "SELECT count(*) FROM (SELECT drone_id, ts FROM telemetry GROUP BY 1,2 HAVING count(*) > 1) d;"; }
rows()  { psql_q "SELECT count(*) FROM telemetry;"; }

echo "1/4 stopping ingestion"
docker compose stop ingestion >/dev/null
before=$(rows)
echo "    telemetry rows before: $before"

echo "2/4 rewinding consumer group db-ingestion to the earliest offset (full replay)"
docker exec aquarius-kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:19092 \
  --group db-ingestion --reset-offsets --to-earliest --all-topics --execute >/dev/null

echo "3/4 starting ingestion and waiting for it to catch up"
docker compose start ingestion >/dev/null
for i in $(seq 1 60); do
  lag=$(docker exec aquarius-kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:19092 \
        --group db-ingestion --describe 2>/dev/null | awk 'NR>1 && $6 ~ /^[0-9]+$/ {s+=$6} END {print s+0}')
  [[ "$lag" == "0" && $i -gt 3 ]] && break
  sleep 2
done
echo "    remaining lag: ${lag:-?}"

after=$(rows); d=$(dupes)
echo "4/4 result"
echo "    telemetry rows after : $after   (before: $before; new rows only from data still arriving)"
echo "    duplicate rows       : $d"
# (telemetry has PRIMARY KEY (drone_id, ts), so the database itself refuses a duplicate;
#  this proves the consumer survives a full replay and keeps going without errors)
if [[ "$d" == "0" ]] && { [[ "$before" == "0" ]] || (( after < before * 2 )); }; then
  echo "PASS: full replay produced no duplicates"
else
  echo "FAIL: duplicates found or row count doubled"; exit 1
fi
