#!/usr/bin/env bash
# px4-sim entrypoint
#   - virtual display + VNC (watch Gazebo through noVNC)
#   - pre-caches the Gazebo world assets (Baylands) so PX4 does not hang
#   - starts PX4_NUM_DRONES PX4 SITL instances in one Gazebo world
#   - opens a MAVLink link per drone towards CONTROL_HOST (the control computer)
#
# Env (defaults in Dockerfile):
#   PX4_NUM_DRONES   number of PX4 instances (drone i = instance i, MAV_SYS_ID i+1)
#   PX4_GZ_WORLD     Gazebo world, e.g. baylands / default
#   PX4_SIM_MODEL    vehicle model, e.g. gz_x500 / gz_x500_depth
#   PX4_SYS_AUTOSTART airframe id matching the model (gz_x500=4001, gz_x500_depth=4002)
#   CONTROL_HOST     IP / hostname of the control computer; empty = PX4 defaults (localhost)
#   HEADLESS         1 = no Gazebo GUI (saves CPU/GPU on the VM)
#   QGC              1 = also run QGroundControl on the virtual display (seen in noVNC)
#   SCREEN_SIZE      Xvfb screen, e.g. 1920x1080x24
#   VNC_LISTEN       x11vnc bind address (127.0.0.1 on the VM server)
set -e

BIN=/px4/build/px4_sitl_default/bin
LOG_DIR=/var/log/px4
FUEL_DIR="$HOME/.gz/fuel/fuel.gazebosim.org/openrobotics/models"
API_LOCAL_BASE=14640   # PX4 listens here (drone i -> +i)
API_REMOTE_BASE=14540  # control computer MAVSDK listens here (drone i -> +i)
GCS_LOCAL_BASE=18640
GCS_REMOTE_PORT=14550  # QGroundControl, shared by all drones

mkdir -p "$LOG_DIR"
cd /px4

# build PX4 SITL if the image was built without it
if [ ! -f "$BIN/px4" ]; then
    make px4_sitl_default
fi

# clear processes / locks left by a previous run
# ("PX4 server already running for instance 0", baylands_setup_guide.md issue A)
pkill -9 -f "$BIN/px4" 2>/dev/null || true
pkill -9 -f "gz sim" 2>/dev/null || true
pkill -9 -f /opt/qgc 2>/dev/null || true
rm -f /tmp/px4_lock-* /tmp/px4-sock-*

# pre-cache world assets from Gazebo Fuel (baylands_setup_guide.md section 3)
if [ "$PX4_GZ_WORLD" = "baylands" ] && ! compgen -G "$FUEL_DIR/baylands/*/model.sdf" > /dev/null; then
    echo "Baylands not cached, downloading from Gazebo Fuel ..."
    timeout 900 gz fuel download -v 3 \
        -u "https://fuel.gazebosim.org/1.0/openrobotics/models/Baylands" \
        || echo "WARN: Baylands download failed — Gazebo may hang waiting for the world"
fi

# start virtual display
Xvfb :99 -screen 0 "${SCREEN_SIZE:-1920x1080x24}" &
XVFB_PID=$!
sleep 2

# start window manager
fluxbox &
FLUX_PID=$!

# start VNC server
x11vnc -display :99 -nopw -listen "$VNC_LISTEN" -xkb -forever -shared &
VNC_PID=$!
sleep 2

PX4_PIDS=()

# trap SIGTERM/SIGINT — kill all child processes cleanly
cleanup() {
    echo "Stopping PX4 and Gazebo..."
    kill "${PX4_PIDS[@]}" 2>/dev/null || true
    pkill -f "gz sim" 2>/dev/null || true
    pkill -f /opt/qgc 2>/dev/null || true
    kill $VNC_PID $FLUX_PID $XVFB_PID 2>/dev/null || true
    wait
    exit 0
}
trap cleanup SIGTERM SIGINT

# wait until instance $1 has finished booting (rcS done => px4-rc.gzsim found the
# Gazebo world, so the next instance joins this world instead of launching a
# second gz server). Checks the log: px4-<module> client output (e.g. `mavlink
# status`) is printed by the daemon, not returned to the caller.
wait_ready() {
    for _ in $(seq 1 180); do
        grep -q "Startup script returned successfully" "$LOG_DIR/px4-$1.log" 2>/dev/null && return 0
        sleep 1
    done
    echo "WARN: PX4 instance $1 not ready after 180 s"
    return 1
}

# resolve CONTROL_HOST to an IPv4 (mavlink -t needs an IP); retries because
# the control container may start after this one
resolve_control_host() {
    for _ in $(seq 1 60); do
        ip=$(getent ahostsv4 "$CONTROL_HOST" | awk 'NR==1 {print $1}')
        [ -n "$ip" ] && { echo "$ip"; return 0; }
        sleep 1
    done
    return 1
}

CONTROL_IP=""
if [ -n "$CONTROL_HOST" ]; then
    CONTROL_IP=$(resolve_control_host) \
        || echo "WARN: cannot resolve CONTROL_HOST=$CONTROL_HOST, using PX4 default links only"
fi

echo "Starting $PX4_NUM_DRONES x $PX4_SIM_MODEL in world '$PX4_GZ_WORLD' (control: ${CONTROL_IP:-localhost})"

for i in $(seq 0 $((PX4_NUM_DRONES - 1))); do
    # instance 0 starts the Gazebo server (+ GUI unless HEADLESS), others join it
    PX4_GZ_MODEL_POSE="0,$((i * DRONE_SPACING))" \
        "$BIN/px4" -i "$i" -d \
        > >(tee "$LOG_DIR/px4-$i.log" | sed -u "s/^/[drone$i] /") 2>&1 &
    PX4_PIDS+=($!)

    wait_ready "$i" || continue

    if [ -n "$CONTROL_IP" ]; then
        # offboard / MAVSDK link: VM:14640+i <-> control:14540+i
        "$BIN/px4-mavlink" --instance "$i" start -x -f -m onboard -r 4000000 \
            -u $((API_LOCAL_BASE + i)) -t "$CONTROL_IP" -o $((API_REMOTE_BASE + i))
        # QGroundControl link: VM:18640+i <-> control:14550
        "$BIN/px4-mavlink" --instance "$i" start -x -f -r 4000000 \
            -u $((GCS_LOCAL_BASE + i)) -t "$CONTROL_IP" -o $GCS_REMOTE_PORT
        echo "drone$i (sysid $((i + 1))) -> $CONTROL_IP:$((API_REMOTE_BASE + i)) [MAVSDK], :$GCS_REMOTE_PORT [QGC]"
    fi
done

# QGroundControl in noVNC — not root (QGC refuses), auto-connects on udp 14550.
# Not in PX4_PIDS: closing QGC does not restart the simulation.
if [ "$QGC" = "1" ] && [ -x /opt/qgc/AppRun ]; then
    su qgc -c "DISPLAY=$DISPLAY /opt/qgc/AppRun" > "$LOG_DIR/qgc.log" 2>&1 &
    echo "QGroundControl started (log: $LOG_DIR/qgc.log)"
fi

# exit (and let docker restart us) as soon as any PX4 instance dies
wait -n "${PX4_PIDS[@]}"
echo "A PX4 instance exited, shutting down"
cleanup
