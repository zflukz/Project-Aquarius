#!/usr/bin/env bash
# uninstall_control_computer.sh
# Removes everything installed by install_control_computer.sh
# Usage: bash uninstall_control_computer.sh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo ">>> [1/4] Stop any running drone scripts"
pkill -f "teleop_drone.py" 2>/dev/null || true
pkill -f "flight_test.py" 2>/dev/null || true
pkill -f "view_cam.py" 2>/dev/null || true

echo ">>> [2/4] Remove Python venv"
rm -rf "$PROJECT_DIR/Drone-Control/.venv"
rm -f  "$PROJECT_DIR/Drone-Control/.env"

echo ">>> [3/4] Revert VM IP patch in scripts"
# Restore udp://<anything>:14540 back to udp://:14540
sed -i 's|udp://[^:]*:14540|udp://:14540|g' \
    "$PROJECT_DIR/Drone-Control/teleop_drone.py" \
    "$PROJECT_DIR/Drone-Control/flight_test.py"

echo ">>> [4/4] Remove ROS 2 packages and apt repo"
sudo apt-get remove --purge -y \
    ros-humble-ros-base \
    ros-humble-ros-gz-bridge \
    ros-humble-cv-bridge \
    python3-colcon-common-extensions \
    python3-opencv 2>/dev/null || true
sudo apt-get autoremove -y

sudo rm -f /etc/apt/sources.list.d/ros2.list
sudo rm -f /usr/share/keyrings/ros-archive-keyring.gpg
sudo apt-get update

echo ""
echo "=== Computer uninstall complete ==="
echo "ROS 2, venv, and IP patches have been removed."
