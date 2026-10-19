#!/usr/bin/env bash
# Start N PX4 SITL drones in ONE Gazebo world (B2: multi-drone test).
#
#   tools/sitl/start_swarm.sh 3                    # 3 drones with a downward camera, Gazebo GUI
#   HEADLESS=1 tools/sitl/start_swarm.sh 10        # 10 drones, no GUI (much lighter)
#   MODEL=x500 tools/sitl/start_swarm.sh 3         # no camera (telemetry only)
#
# Instance i (1..N) gets ROS 2 namespace /uav_i (PX4 main; older PX4 used /px4_i),
# which matches config/bridge.yaml and bridge_sitl.yaml.
# Start `MicroXRCEAgent udp4 -p 8888` first: ONE agent serves every drone.
# Stop everything with tools/sitl/stop_swarm.sh
set -euo pipefail

N="${1:-3}"
MODEL="${MODEL:-x500_mono_cam_down}"
PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"
SPACING="${SPACING:-3}"                       # metres between drones on the ground
LOG_DIR="${LOG_DIR:-$HOME/aquarius-data/results/sitl_logs}"
# PX4_TIMESYNC=0 (default here): PX4 keeps its own (= Gazebo sim) clock in its timestamps
# instead of mapping them onto the computer's wall clock. With the bridge on
# use_sim_time:=true, images and telemetry then share ONE clock even when Gazebo runs
# slower than real time (A4). Real drones keep time sync ON (PX4 default).

(( N >= 1 && N <= 10 )) || { echo "N must be 1..10"; exit 1; }
BIN="$PX4_DIR/build/px4_sitl_default/bin/px4"
if [[ ! -x "$BIN" ]]; then
  echo "PX4 SITL not built yet. Run once:  cd $PX4_DIR && make px4_sitl"; exit 1
fi

# Airframe id for this model, e.g. 4001_gz_x500 -> 4001
AIRFRAME_DIR="$PX4_DIR/ROMFS/px4fmu_common/init.d-posix/airframes"
AF_FILE=$(ls "$AIRFRAME_DIR" | grep -E "^[0-9]+_gz_${MODEL}$" | head -1 || true)
if [[ -z "$AF_FILE" ]]; then
  echo "No airframe for model '$MODEL'. Available gz models:"
  ls "$AIRFRAME_DIR" | grep -E "_gz_" | sed 's/^[0-9]*_gz_/  /'
  exit 1
fi
AUTOSTART="${AF_FILE%%_*}"

if pgrep -f "bin/px4 -i" >/dev/null || pgrep -f "gz sim" >/dev/null; then
  echo "PX4 or Gazebo is already running. Run tools/sitl/stop_swarm.sh first."; exit 1
fi
pgrep -f MicroXRCEAgent >/dev/null || echo "WARNING: MicroXRCEAgent is not running (start: MicroXRCEAgent udp4 -p 8888)"

mkdir -p "$LOG_DIR"
cd "$PX4_DIR"
echo "Starting $N x $MODEL (airframe $AUTOSTART), logs in $LOG_DIR"
for i in $(seq 1 "$N"); do
  y=$(( (i - 1) * SPACING ))
  PX4_SYS_AUTOSTART="$AUTOSTART" \
  PX4_SIM_MODEL="gz_${MODEL}" \
  PX4_GZ_MODEL_POSE="0,${y}" \
  PX4_PARAM_UXRCE_DDS_SYNCT="${PX4_TIMESYNC:-0}" \
  HEADLESS="${HEADLESS:-}" \
    nohup "$BIN" -i "$i" -d > "$LOG_DIR/px4_$i.log" 2>&1 &
  echo "  uav$i -> /uav_$i  (pid $!, pose 0,$y)"
  # the first instance also starts the Gazebo server: give it time
  if (( i == 1 )); then sleep 15; else sleep 4; fi
done

echo
echo "Waiting for PX4 topics..."
for t in $(seq 1 30); do
  found=$(ros2 topic list 2>/dev/null | grep -cE "^/(uav|px4)_[0-9]+/fmu/out/vehicle_attitude$" || true)
  (( found >= N )) && break
  sleep 2
done
echo "vehicle_attitude topics seen: ${found:-0}/$N"
ros2 topic list | grep -E "^/(uav|px4)_[0-9]+/fmu/out/vehicle_attitude$" || true
echo "(topics appear even if no data flows: check with  ros2 topic hz /uav_1/fmu/out/vehicle_attitude)"
echo
echo "Next: python3 tools/sitl/make_cam_bridge.py   (camera images -> ROS 2)"
