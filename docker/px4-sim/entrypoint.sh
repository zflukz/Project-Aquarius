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
#   QGC              1 = also run QGroundControl on its own virtual display (second noVNC)
#   SCREEN_SIZE      Xvfb screen, e.g. 1920x1080x24 (Gazebo :99 and QGC :100)
#   VNC_LISTEN       x11vnc bind address (127.0.0.1 on the VM server)
#   GZ_FOLLOW        drone index the Gazebo camera follows (default 0, empty = free camera)
#   PX4_GZ_FOLLOW_OFFSET_X/Y/Z  camera offset from that drone, m (ENU world frame)
#   TELEM            0 = no telemetry link; else PX4 sends 14740+i -> TELEM_HOST:14700 (mavlink_mqtt.py)
#   TELEM_HOST       where mavlink_mqtt.py runs (default 127.0.0.1 = telemetry container next to us)
#   CAMERA_WIDTH/HEIGHT/RATE    drone camera models (mono_cam, gimbal, OakD-Lite RGB), default 640x480 @ 10 Hz;
#                               empty = model default (1280x960 @ 30 -> RTF ~0.03 with software rendering)
set -e

BIN=/px4/build/px4_sitl_default/bin
LOG_DIR=/var/log/px4
FUEL_DIR="$HOME/.gz/fuel/fuel.gazebosim.org/openrobotics/models"
API_LOCAL_BASE=14640   # PX4 listens here (drone i -> +i)
API_REMOTE_BASE=14540  # control computer MAVSDK listens here (drone i -> +i)
GCS_LOCAL_BASE=18640
GCS_REMOTE_PORT=14550  # QGroundControl, shared by all drones
TELEM_LOCAL_BASE=14740 # telemetry link (dashboard), PX4 listens here (drone i -> +i)
TELEM_PORT=14700       # mavlink_mqtt.py listens here, shared by all drones
TELEM_HOST=${TELEM_HOST:-127.0.0.1}
GZ_FOLLOW=${GZ_FOLLOW-0}   # unset -> follow drone 0, set empty -> free camera

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
pkill -9 -x Xvfb 2>/dev/null || true
pkill -9 -x x11vnc 2>/dev/null || true
pkill -9 -x fluxbox 2>/dev/null || true
rm -f /tmp/px4_lock-* /tmp/px4-sock-*
# X locks survive a container restart -> Xvfb "Server is already active" -> no GUI / VNC
rm -f /tmp/.X99-lock /tmp/.X100-lock /tmp/.X11-unix/X99 /tmp/.X11-unix/X100
# same for QGC's single-instance lock -> "A second instance of QGroundControl is already running"
rm -f /tmp/qgc-*.lock

# pre-cache world assets from Gazebo Fuel (baylands_setup_guide.md section 3)
if [ "$PX4_GZ_WORLD" = "baylands" ] && ! compgen -G "$FUEL_DIR/baylands/*/model.sdf" > /dev/null; then
    echo "Baylands not cached, downloading from Gazebo Fuel ..."
    timeout 900 gz fuel download -v 3 \
        -u "https://fuel.gazebosim.org/1.0/openrobotics/models/Baylands" \
        || echo "WARN: Baylands download failed — Gazebo may hang waiting for the world"
fi

# drone camera size / rate. Gazebo renders sensors on the CPU here (llvmpipe, no GPU) and lockstep
# waits for every frame: mono_cam's 1280x960 @ 30 Hz in Baylands dropped RTF to ~0.03, 640x480 @ 10 Hz -> ~1.0.
# Always edits from the original SDF (*.orig), so changing the env + restart works.
CAMERA_WIDTH=${CAMERA_WIDTH-640}; CAMERA_HEIGHT=${CAMERA_HEIGHT-480}; CAMERA_RATE=${CAMERA_RATE-10}
for sdf in mono_cam gimbal OakD-Lite; do
    f=/px4/Tools/simulation/gz/models/$sdf/model.sdf
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
case "$PX4_SIM_MODEL" in *cam*|*gimbal*|*depth*)
    echo "drone camera: ${CAMERA_WIDTH:-default}x${CAMERA_HEIGHT:-default} @ ${CAMERA_RATE:-default} Hz" ;; esac

# one virtual screen + window manager + VNC server per viewer:
#   :99  -> VNC 5900 -> noVNC "novnc"      Gazebo
#   :100 -> VNC 5901 -> noVNC "novnc-qgc"  QGroundControl (only with QGC=1)
DISPLAY_PIDS=()
# fluxbox: open QGroundControl / Gazebo maximized on their screens
mkdir -p ~/.fluxbox
cat > ~/.fluxbox/apps <<'EOF'
[app] (name=QGroundControl)
  [Maximized] {yes}
[end]
[app] (name=gz-sim-gui)
  [Maximized] {yes}
[end]
EOF
start_screen() {   # $1 display, $2 VNC port
    Xvfb "$1" -screen 0 "${SCREEN_SIZE:-1920x1080x24}" &
    DISPLAY_PIDS+=($!)
    sleep 2
    DISPLAY="$1" fluxbox &
    DISPLAY_PIDS+=($!)
    x11vnc -display "$1" -rfbport "$2" -nopw -listen "$VNC_LISTEN" -xkb -forever -shared &
    DISPLAY_PIDS+=($!)
}
start_screen :99 5900
if [ "$QGC" = "1" ]; then start_screen :100 5901; fi
sleep 2

PX4_PIDS=()

# trap SIGTERM/SIGINT — kill all child processes cleanly
cleanup() {
    echo "Stopping PX4 and Gazebo..."
    kill "${PX4_PIDS[@]}" 2>/dev/null || true
    pkill -f "gz sim" 2>/dev/null || true
    pkill -f /opt/qgc 2>/dev/null || true
    kill "${DISPLAY_PIDS[@]}" 2>/dev/null || true
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

TELEM_IP=$(getent ahostsv4 "$TELEM_HOST" | awk 'NR==1 {print $1}')
TELEM_IP=${TELEM_IP:-$TELEM_HOST}

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
    if [ "$TELEM" != "0" ]; then
        # telemetry link for the dashboard: VM:14740+i -> TELEM_HOST:14700, all drones to one port
        # (telemetry container's mavlink_mqtt.py tells them apart by sysid). PX4 allows 6 MAVLink
        # instances: 4 from px4-rc.mavlink + our 2 above -> free the onboard-payload link
        # (14280+i -> 14030+i, for a MAVLink camera companion; nothing listens there in the sim).
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

# QGroundControl on its own screen :100 (noVNC "novnc-qgc") — not root (QGC refuses), auto-connects on udp 14550.
# Not in PX4_PIDS: closing QGC does not restart the simulation.
if [ "$QGC" = "1" ] && [ -x /opt/qgc/AppRun ]; then
    su qgc -c "DISPLAY=:100 /opt/qgc/AppRun" > "$LOG_DIR/qgc.log" 2>&1 &
    echo "QGroundControl started (log: $LOG_DIR/qgc.log)"
fi

# Gazebo camera follows drone $GZ_FOLLOW, so the Gazebo view moves with the drone like QGC's map.
# PX4's rcS publishes /gui/track once, often before the GUI is up, and gz-gui 8.4 ends up with an
# empty follow_target -> camera stays at the spawn point. The GUI's /gui/follow services work, but
# a call made before the GUI has loaded the model is accepted (also shown in /gui/currently_tracked)
# and silently does nothing -> repeat until the GUI camera really leaves its start pose.
follow_camera() {
    target="${PX4_SIM_MODEL#*gz_}_$GZ_FOLLOW"
    cam_pose() { timeout 5 gz topic -e -t /gui/camera/pose -n 1 2>/dev/null; }
    start_pose=""
    for _ in $(seq 1 120); do
        sleep 5
        [ -n "$start_pose" ] || start_pose=$(cam_pose)
        [ -n "$start_pose" ] || continue   # GUI not up yet
        gz service -s /gui/follow --reqtype gz.msgs.StringMsg --reptype gz.msgs.Boolean \
            --timeout 3000 --req "data: \"$target\"" > /dev/null 2>&1 || continue
        gz service -s /gui/follow/offset --reqtype gz.msgs.Vector3d --reptype gz.msgs.Boolean \
            --timeout 3000 --req "x: ${PX4_GZ_FOLLOW_OFFSET_X:--8}, y: ${PX4_GZ_FOLLOW_OFFSET_Y:--8}, \
z: ${PX4_GZ_FOLLOW_OFFSET_Z:-5}" > /dev/null 2>&1
        sleep 3
        pose=$(cam_pose)
        if [ -n "$pose" ] && [ "$pose" != "$start_pose" ]; then
            echo "Gazebo camera follows $target"
            return 0
        fi
    done
    echo "WARN: Gazebo camera does not follow $target (move it by hand in the GUI)"
}
if [ -z "$HEADLESS" ] && [ -n "$GZ_FOLLOW" ]; then follow_camera & fi

# exit (and let docker restart us) as soon as any PX4 instance dies
wait -n "${PX4_PIDS[@]}"
echo "A PX4 instance exited, shutting down"
cleanup
