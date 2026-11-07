#!/usr/bin/env bash
# Creates every Kafka topic with the settings agreed in Phase A (A3).
# Safe to run again: existing topics are left as they are.
set -euo pipefail

BOOTSTRAP="${BOOTSTRAP:-localhost:9092}"
PARTITIONS="${PARTITIONS:-10}"   # = max drones (10)
KT=/opt/kafka/bin/kafka-topics.sh

DAY_MS=86400000

create() {
  local topic="$1" parts="$2"; shift 2
  local configs=()
  for c in "$@"; do configs+=(--config "$c"); done
  "$KT" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
        --topic "$topic" --partitions "$parts" --replication-factor 1 "${configs[@]}"
}

create telemetry         "$PARTITIONS" "retention.ms=$((7 * DAY_MS))"
# 10 drones x 2 fps x ~165 KB = ~3.3 MB/s. retention.bytes caps each partition at
# 2 GB (20 GB for the topic) so a long run can never fill the ground-station disk.
create image-frames      "$PARTITIONS" "retention.ms=$DAY_MS" "retention.bytes=2147483648" "max.message.bytes=2097152"
create detections        "$PARTITIONS" "retention.ms=$((7 * DAY_MS))"
create survivors         "$PARTITIONS" "retention.ms=$((30 * DAY_MS))"
# One partition: keeps the order of events across all drones in a mission (A3)
create replanning-events 1             "retention.ms=$((30 * DAY_MS))"

echo "Topics now on the broker:"
"$KT" --bootstrap-server "$BOOTSTRAP" --describe \
  --topic 'telemetry|image-frames|detections|survivors|replanning-events' \
  | grep -E '^Topic:' || true
