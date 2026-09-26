#!/usr/bin/env bash
set -e

# build PX4 SITL first if not already built
cd /px4
if [ ! -f "build/px4_sitl_default/bin/px4" ]; then
    make px4_sitl_default
fi

# start virtual display
Xvfb :99 -screen 0 1280x720x24 &
XVFB_PID=$!
sleep 2

# start window manager
fluxbox &
FLUX_PID=$!

# start VNC server
x11vnc -display :99 -nopw -listen 0.0.0.0 -xkb -forever &
VNC_PID=$!
sleep 2

# trap SIGTERM/SIGINT — kill all child processes cleanly
cleanup() {
    echo "Stopping PX4 and Gazebo..."
    kill $PX4_PID 2>/dev/null || true
    pkill -f "gz sim" 2>/dev/null || true
    kill $VNC_PID $FLUX_PID $XVFB_PID 2>/dev/null || true
    wait
    exit 0
}
trap cleanup SIGTERM SIGINT

# start PX4 SITL with Gazebo
make px4_sitl gz_x500 &
PX4_PID=$!

# wait on PX4 process
wait $PX4_PID
