#!/usr/bin/env bash
# Insert docker/px4-sim/dds_topics_extra.yaml at the end of the "publications:" list of PX4's
# uXRCE-DDS topic file (idempotent). Used by the px4-sim Dockerfile before the incremental make.
set -euo pipefail
f=${1:-src/modules/uxrce_dds_client/dds_topics.yaml}
extra=${2:-/tmp/dds_topics_extra.yaml}

if grep -q '/fmu/out/mavlink_log' "$f"; then
    echo "dds_topics: extra topics already present"
    exit 0
fi
grep -q '^subscriptions:' "$f" || { echo "dds_topics: no 'subscriptions:' line in $f" >&2; exit 1; }

tmp=$(mktemp)
awk -v extra="$extra" '
    /^subscriptions:/ && !done {
        while ((getline line < extra) > 0) if (line !~ /^#/) print line
        print ""
        done = 1
    }
    { print }' "$f" > "$tmp"
mv "$tmp" "$f"

n=$(grep -cE '/fmu/out/(cpuload|mission_result|mavlink_log)$' "$f")
[ "$n" = 3 ] || { echo "dds_topics: expected 3 extra topics, found $n" >&2; exit 1; }
echo "dds_topics: added cpuload, mission_result, mavlink_log"
