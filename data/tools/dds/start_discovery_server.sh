#!/usr/bin/env bash
# Run on the ground station. Every DDS participant (drones + ground) registers here.
# Keep it running for the whole mission; if it stops, nodes already connected keep
# talking, but new or restarted nodes cannot find anyone until it is back.
set -euo pipefail
PORT="${1:-11811}"
exec fastdds discovery --server-id 0 --udp-address 0.0.0.0 --udp-port "$PORT"
