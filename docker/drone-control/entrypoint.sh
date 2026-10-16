#!/usr/bin/env bash
# drone-control entrypoint
#   AUTO_RUN   command run once at container start, e.g. "python3 mcpp_mission.py"
#              (empty = nothing; the container just idles as a control station)
# The script waits for PX4 itself (connect timeout), so no start delay is needed.
source /opt/ros/jazzy/setup.bash

if [ -n "$AUTO_RUN" ]; then
    echo "AUTO_RUN: $AUTO_RUN"
    bash -c "$AUTO_RUN" || echo "AUTO_RUN failed (exit $?)"
fi

exec "$@"
