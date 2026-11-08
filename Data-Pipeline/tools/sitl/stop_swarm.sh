#!/usr/bin/env bash
# Stop every PX4 SITL instance and Gazebo left from a previous run.
# Leftovers are the usual cause of "vehicle_imu ... timestamp error".
pkill -f "bin/px4 -i" 2>/dev/null
pkill -f "gz sim" 2>/dev/null
pkill -f "parameter_bridge" 2>/dev/null
sleep 2
pkill -9 -f "bin/px4" 2>/dev/null
pkill -9 -f "gz sim" 2>/dev/null
pkill -9 -f "ruby.*gz" 2>/dev/null
left=$(pgrep -fa "bin/px4|gz sim" || true)
if [[ -n "$left" ]]; then echo "Still running:"; echo "$left"; else echo "PX4 and Gazebo stopped."; fi
echo "(MicroXRCEAgent is left running; stop it with Ctrl+C in its tab if needed.)"
