#!/usr/bin/env bash
# run_sim_native.sh — start N PX4 SITL drones in one Gazebo world WITHOUT Docker
#
# Native twin of docker/px4-sim/entrypoint.sh (same env names, same MAVLink ports), minus the
# virtual screens / VNC / QGC: with a desktop the Gazebo GUI opens on $DISPLAY, on a server use
# HEADLESS=1. Ctrl+C stops everything.
#
#   CONTROL_HOST=192.168.1.50 PX4_NUM_DRONES=2 HEADLESS=1 bash scripts/run_sim_native.sh
#
# MAVLink per drone i (= PX4 instance i, sysid i+1):
#   MAVSDK / offboard : PX4 listens 14640+i, sends to CONTROL_HOST:14540+i
#   QGroundControl    : PX4 listens 18640+i, sends to CONTROL_HOST:14550
#   telemetry         : PX4 listens 14740+i, sends to TELEM_HOST:14700 (mavlink_mqtt.py -> MQTT -> Grafana)
#
# Env (defaults): CONTROL_HOST (empty = PX4 defaults, localhost only)  PX4_NUM_DRONES=1
#   PX4_GZ_WORLD=baylands  PX4_SIM_MODEL=gz_x500  PX4_SYS_AUTOSTART=4001  DRONE_SPACING=3
#   HEADLESS (1 = no GUI)  PX4_DIR=~/PX4-Autopilot  LOG_DIR=/tmp/px4-native
#   GZ_PARTITION=aquarius  ROS_DOMAIN_ID=0  PX4_VIDEO_HOST_IP=127.0.0.1   (as in the compose files)
#   TELEM (0 = no telemetry link)  TELEM_HOST=127.0.0.1
#   CAMERA_WIDTH=640 CAMERA_HEIGHT=480 CAMERA_RATE=10  camera models (edits $PX4_DIR models, original kept as
#                                                     model.sdf.orig); empty = model default (needs a GPU)
#
# Telemetry topics + camera view: start scripts/run_telemetry_native.sh in a second terminal
# (same PX4_NUM_DRONES / PX4_GZ_WORLD / PX4_SIM_MODEL / GZ_PARTITION / ROS_DOMAIN_ID).
set -e

PX4_DIR=${PX4_DIR:-$HOME/PX4-Autopilot}
BIN="$PX4_DIR/build/px4_sitl_default/bin"
LOG_DIR=${LOG_DIR:-/tmp/px4-native}
FUEL_DIR="$HOME/.gz/fuel/fuel.gazebosim.org/openrobotics/models"
export PX4_NUM_DRONES=${PX4_NUM_DRONES:-1}
export PX4_GZ_WORLD=${PX4_GZ_WORLD:-baylands}
export PX4_SIM_MODEL=${PX4_SIM_MODEL:-gz_x500}
export PX4_SYS_AUTOSTART=${PX4_SYS_AUTOSTART:-4001}
export HEADLESS=${HEADLESS:-}
# shared with the telemetry bridge (Gazebo transport partition, uXRCE-DDS domain), camera RTP target
export GZ_PARTITION=${GZ_PARTITION:-aquarius}
export ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0}
export PX4_VIDEO_HOST_IP=${PX4_VIDEO_HOST_IP:-127.0.0.1}
DRONE_SPACING=${DRONE_SPACING:-3}
API_LOCAL_BASE=14640; API_REMOTE_BASE=14540
GCS_LOCAL_BASE=18640; GCS_REMOTE_PORT=14550
TELEM_LOCAL_BASE=14740; TELEM_PORT=14700; TELEM_HOST=${TELEM_HOST:-127.0.0.1}

[ -x "$BIN/px4" ] || { echo "PX4 not built: $BIN/px4 — run scripts/install_sim_server.sh"; exit 1; }
if [ -z "$HEADLESS" ] && [ -z "$DISPLAY" ]; then
    echo "no DISPLAY -> HEADLESS=1 (no Gazebo GUI)"
    export HEADLESS=1
fi
mkdir -p "$LOG_DIR"

# stale processes / locks from a previous run
pkill -9 -f "$BIN/px4" 2>/dev/null || true
pkill -9 -f "gz sim" 2>/dev/null || true
rm -f /tmp/px4_lock-* /tmp/px4-sock-*

# drone camera size / rate — same as docker/px4-sim/entrypoint.sh (1280x960 @ 30 Hz without GPU -> RTF ~0.03)
CAMERA_WIDTH=${CAMERA_WIDTH-640}; CAMERA_HEIGHT=${CAMERA_HEIGHT-480}; CAMERA_RATE=${CAMERA_RATE-10}
for sdf in mono_cam gimbal OakD-Lite; do
    f="$PX4_DIR/Tools/simulation/gz/models/$sdf/model.sdf"
    [ -f "$f" ] || continue
    [ -f "$f.orig" ] || cp "$f" "$f.orig"
    W=$CAMERA_WIDTH H=$CAMERA_HEIGHT R=$CAMERA_RATE python3 - "$f.orig" "$f" <<'PY'
import os, re, sys
src = open(sys.argv[1]).read()
def fix(m):   # only <sensor type="camera"> blocks (not imu / depth_camera)
    b = m.group(0)
    for tag, env in (('width', 'W'), ('height', 'H'), ('update_rate', 'R')):
        if os.environ[env]:
            b = re.sub(rf'<{tag}>[^<]*</{tag}>', f'<{tag}>{os.environ[env]}</{tag}>', b, count=1)
    return b
open(sys.argv[2], 'w').write(re.sub(r'<sensor name="[^"]*" type="camera">.*?</sensor>', fix, src, flags=re.S))
PY
done

if [ "$PX4_GZ_WORLD" = baylands ] && ! compgen -G "$FUEL_DIR/baylands/*/model.sdf" > /dev/null; then
    echo "Baylands not cached, downloading from Gazebo Fuel ..."
    timeout 900 gz fuel download -v 3 \
        -u "https://fuel.gazebosim.org/1.0/openrobotics/models/Baylands" \
        || echo "WARN: Baylands download failed — Gazebo may hang waiting for the world"
fi

PX4_PIDS=()
cleanup() {
    echo "Stopping PX4 and Gazebo..."
    kill "${PX4_PIDS[@]}" 2>/dev/null || true
    pkill -f "gz sim" 2>/dev/null || true
    wait
    exit 0
}
trap cleanup SIGTERM SIGINT

# instance i must finish rcS (= found the Gazebo world) before i+1 starts, else i+1 launches a
# second gz server. The log is the only reliable signal (see CLAUDE.md, wait_ready).
wait_ready() {
    for _ in $(seq 1 180); do
        grep -q "Startup script returned successfully" "$LOG_DIR/px4-$1.log" 2>/dev/null && return 0
        sleep 1
    done
    echo "WARN: PX4 instance $1 not ready after 180 s"
    return 1
}

CONTROL_IP=""
if [ -n "$CONTROL_HOST" ]; then
    CONTROL_IP=$(getent ahostsv4 "$CONTROL_HOST" | awk 'NR==1 {print $1}')
    [ -n "$CONTROL_IP" ] || echo "WARN: cannot resolve CONTROL_HOST=$CONTROL_HOST, using PX4 default links only"
fi

TELEM_IP=$(getent ahostsv4 "$TELEM_HOST" | awk 'NR==1 {print $1}'); TELEM_IP=${TELEM_IP:-$TELEM_HOST}

echo "Starting $PX4_NUM_DRONES x $PX4_SIM_MODEL in world '$PX4_GZ_WORLD' (control: ${CONTROL_IP:-localhost}), logs: $LOG_DIR"

cd "$PX4_DIR"
for i in $(seq 0 $((PX4_NUM_DRONES - 1))); do
    PX4_GZ_MODEL_POSE="0,$((i * DRONE_SPACING))" \
        "$BIN/px4" -i "$i" -d \
        > >(tee "$LOG_DIR/px4-$i.log" | sed -u "s/^/[drone$i] /") 2>&1 &
    PX4_PIDS+=($!)

    wait_ready "$i" || continue

    if [ -n "$CONTROL_IP" ]; then
        "$BIN/px4-mavlink" --instance "$i" start -x -f -m onboard -r 4000000 \
            -u $((API_LOCAL_BASE + i)) -t "$CONTROL_IP" -o $((API_REMOTE_BASE + i))
        "$BIN/px4-mavlink" --instance "$i" start -x -f -r 4000000 \
            -u $((GCS_LOCAL_BASE + i)) -t "$CONTROL_IP" -o $GCS_REMOTE_PORT
        echo "drone$i (sysid $((i + 1))) -> $CONTROL_IP:$((API_REMOTE_BASE + i)) [MAVSDK], :$GCS_REMOTE_PORT [QGC]"
    fi
    if [ "$TELEM" != "0" ]; then   # dashboard link, all drones -> one port
        # max 6 MAVLink instances -> free PX4's unused onboard-payload link (14280+i), as in entrypoint.sh
        "$BIN/px4-mavlink" --instance "$i" stop -u $((14280 + i)) > /dev/null 2>&1 || true
        sleep 1
        if "$BIN/px4-mavlink" --instance "$i" start -x -f -r 200000 \
                -u $((TELEM_LOCAL_BASE + i)) -t "$TELEM_IP" -o $TELEM_PORT; then
            echo "drone$i (sysid $((i + 1))) -> $TELEM_IP:$TELEM_PORT [telemetry -> MQTT / Grafana]"
        else
            echo "WARN: drone$i telemetry link not started (no dashboard data for it)"
        fi
    fi
done

echo "Running (GZ_PARTITION=$GZ_PARTITION, ROS_DOMAIN_ID=$ROS_DOMAIN_ID). Ctrl+C to stop."
echo "Telemetry / camera / MQTT: bash scripts/run_telemetry_native.sh (second terminal)"
wait -n "${PX4_PIDS[@]}"
echo "A PX4 instance exited, shutting down"
cleanup
