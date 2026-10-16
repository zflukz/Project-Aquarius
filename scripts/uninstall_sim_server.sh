#!/usr/bin/env bash
# uninstall_sim_server.sh — remove what install_sim_server.sh installed (native PX4 + Gazebo)
#
# Usage: bash scripts/uninstall_sim_server.sh [--purge-build-deps]
#   --purge-build-deps  also remove the -dev packages that OLD versions of the install script
#                       used to build Gazebo from source (~/gz_ws). Careful: other software may need them.
#
# Docker (px4-sim container, images, gz-fuel-cache volume) is not touched.
# Env: PX4_DIR (default ~/PX4-Autopilot)
set -e

PX4_DIR=${PX4_DIR:-$HOME/PX4-Autopilot}

echo ">>> [1/5] Stop native PX4 / Gazebo"
# only this install's PX4 binary — a plain "pkill -f px4" would also hit the px4-sim container
pkill -f "$PX4_DIR/build/px4_sitl_default/bin/px4" 2>/dev/null || true
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx px4-sim; then
    pkill -f "gz sim" 2>/dev/null || true
else
    echo "px4-sim container is running — leaving its gz sim alone"
fi
rm -f /tmp/px4_lock-* /tmp/px4-sock-*

echo ">>> [2/5] Remove PX4 Autopilot ($PX4_DIR)"
rm -rf "$PX4_DIR"

echo ">>> [3/5] Remove Gazebo Harmonic (apt) + old source build (~/gz_ws)"
sudo apt-get remove --purge -y gz-harmonic gz-garden 2>/dev/null || true
rm -rf ~/gz_ws
sed -i '/gz_ws\/install\/setup.bash/d' ~/.bashrc
sudo rm -f /etc/apt/sources.list.d/gazebo-stable.list
sudo rm -f /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg

echo ">>> [4/5] Remove cached Baylands world"
rm -rf ~/.gz/fuel/fuel.gazebosim.org/openrobotics/models/baylands

echo ">>> [5/5] Old Gazebo source-build dependencies"
if [ "${1:-}" = "--purge-build-deps" ]; then
    sudo apt-get remove --purge -y \
        libfreeimage-dev libgts-dev libavdevice-dev \
        libzmq3-dev libsqlite3-dev libwebsockets-dev \
        libtinyxml2-dev liburdfdom-dev libogre-1.9-dev \
        libbullet-dev libdart6-dev libprotobuf-dev \
        libprotoc-dev protobuf-compiler \
        libccd-dev libfcl-dev libassimp-dev \
        libgflags-dev libgoogle-glog-dev \
        libyaml-cpp-dev libzip-dev \
        python3-vcstool python3-rosdep 2>/dev/null || true
else
    echo "skipped (add --purge-build-deps to remove them)"
fi
sudo apt-get autoremove -y
sudo apt-get update

echo ""
echo "=== VM server (native) uninstall complete ==="
