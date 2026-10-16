#!/usr/bin/env bash
# uninstall_control_computer.sh — remove what install_control_computer.sh installed
# Usage: bash scripts/uninstall_control_computer.sh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo ">>> [1/3] Stop running drone scripts"
for s in teleop_drone.py flight_test.py mcpp_mission.py view_cam.py; do
    pkill -f "$s" 2>/dev/null || true
done
# leftover MAVSDK servers keep udp 14540+ busy (see CLAUDE.md gotchas)
pkill -f "mavsdk_server" 2>/dev/null || true

echo ">>> [2/3] Remove Python venv"
rm -rf "$PROJECT_DIR/Drone-Control/.venv"
rm -f  "$PROJECT_DIR/Drone-Control/.env"   # written by old versions of the install script

echo ">>> [3/3] Remove ROS 2 packages and apt repo (Jazzy and/or Humble)"
for distro in jazzy humble; do
    sudo apt-get remove --purge -y \
        "ros-$distro-ros-base" "ros-$distro-ros-gz-bridge" "ros-$distro-cv-bridge" 2>/dev/null || true
done
sudo apt-get remove --purge -y python3-colcon-common-extensions 2>/dev/null || true
sudo apt-get autoremove -y

sudo rm -f /etc/apt/sources.list.d/ros2.list
sudo rm -f /usr/share/keyrings/ros-archive-keyring.gpg
sudo apt-get update

echo ""
echo "=== Control computer uninstall complete ==="
echo "ROS 2 and the venv have been removed (python3-opencv kept — other software may use it)."
