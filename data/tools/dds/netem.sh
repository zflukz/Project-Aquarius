#!/usr/bin/env bash
# Make a network link behave like bad Wi-Fi, to compare the two discovery options fairly.
#   sudo tools/dds/netem.sh on  <iface> [loss%] [delay_ms] [jitter_ms]   e.g.  on wlan0 5 50 20
#   sudo tools/dds/netem.sh off <iface>
#   tools/dds/netem.sh show <iface>
# Apply it on the interface that carries drone <-> ground-station traffic. Do not use
# `lo` when everything runs on one machine: it would slow Kafka and the DB as well.
set -euo pipefail
cmd="${1:-}"; dev="${2:-}"
[[ -z "$cmd" || -z "$dev" ]] && { sed -n '2,7p' "$0"; exit 1; }
case "$cmd" in
  on)
    loss="${3:-5}"; delay="${4:-50}"; jitter="${5:-20}"
    tc qdisc replace dev "$dev" root netem loss "${loss}%" delay "${delay}ms" "${jitter}ms"
    echo "netem on $dev: loss ${loss}% delay ${delay}±${jitter} ms" ;;
  off)  tc qdisc del dev "$dev" root 2>/dev/null || true; echo "netem off $dev" ;;
  show) tc qdisc show dev "$dev" ;;
  *)    echo "unknown command $cmd"; exit 1 ;;
esac
