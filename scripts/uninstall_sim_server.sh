#!/usr/bin/env bash
# uninstall_sim_server.sh — remove what install_sim_server.sh (native PX4 + Gazebo) and
# install_telemetry_server.sh (Micro XRCE-DDS Agent, px4_msgs, ROS bridge packages) installed
#
# Usage: bash scripts/uninstall_sim_server.sh [--purge-build-deps]
#   --purge-build-deps  also remove the -dev packages that OLD versions of the install script
#                       used to build Gazebo from source (~/gz_ws). Careful: other software may need them.
#
# Docker (px4-sim container, images, gz-fuel-cache volume) is not touched.
# Env: PX4_DIR (default ~/PX4-Autopilot), PX4_ROS_WS (default ~/px4_ros_ws)
set -e

PX4_DIR=${PX4_DIR:-$HOME/PX4-Autopilot}
PX4_ROS_WS=${PX4_ROS_WS:-$HOME/px4_ros_ws}

echo ">>> [1/6] Stop native PX4 / Gazebo"
# only this install's PX4 binary — a plain "pkill -f px4" would also hit the px4-sim container
pkill -f "$PX4_DIR/build/px4_sitl_default/bin/px4" 2>/dev/null || true
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx px4-sim; then
    pkill -f "gz sim" 2>/dev/null || true
else
    echo "px4-sim container is running — leaving its gz sim alone"
fi
rm -f /tmp/px4_lock-* /tmp/px4-sock-*

echo ">>> [2/6] Remove PX4 Autopilot ($PX4_DIR)"
rm -rf "$PX4_DIR"

echo ">>> [3/6] Remove Gazebo Harmonic (apt) + old source build (~/gz_ws)"
sudo apt-get remove --purge -y gz-harmonic gz-garden 2>/dev/null || true
rm -rf ~/gz_ws
sed -i '/gz_ws\/install\/setup.bash/d' ~/.bashrc
sudo rm -f /etc/apt/sources.list.d/gazebo-stable.list
sudo rm -f /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg

echo ">>> [4/6] Remove cached Baylands world"
rm -rf ~/.gz/fuel/fuel.gazebosim.org/openrobotics/models/baylands

echo ">>> [5/6] Telemetry: Micro XRCE-DDS Agent, px4_msgs ($PX4_ROS_WS), ROS bridge packages"
pkill -f MicroXRCEAgent 2>/dev/null || true
if [ -f "$PX4_ROS_WS/xrce_install_manifest.txt" ]; then
    xargs -a "$PX4_ROS_WS/xrce_install_manifest.txt" sudo rm -f
    sudo ldconfig
fi
rm -rf "$PX4_ROS_WS"
# ROS 2 base stays (uninstall_control_computer.sh removes it)
# (foxglove-bridge: older installs only)
sudo apt-get remove --purge -y ros-jazzy-ros-gz-bridge ros-jazzy-web-video-server     ros-jazzy-foxglove-bridge ros-jazzy-rosbag2-storage-mcap 2>/dev/null || true
sudo pip3 uninstall -y --break-system-packages pymavlink paho-mqtt 2>/dev/null || true   # mavlink_mqtt.py

echo ">>> [6/6] Old Gazebo source-build dependencies"
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
