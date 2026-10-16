#!/usr/bin/env bash
# install_control_computer.sh — NATIVE (no Docker) control station: ROS 2 + MAVSDK + Drone-Control
#
# Same software as docker/drone-control/Dockerfile. Docker (docker/compose.control.yml) is the
# maintained path; use this to run the scripts directly on Ubuntu / WSL2.
#   Ubuntu 24.04 -> ROS 2 Jazzy (= Docker image), Ubuntu 22.04 -> ROS 2 Humble
#
# Usage: bash scripts/install_control_computer.sh
#
# No VM IP needed: PX4 sends MAVLink to this computer (CONTROL_HOST on the VM) and the scripts
# only listen — drone i on udp 14540+i (DRONE_ID / NUM_DRONES), QGroundControl on udp 14550.
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
. /etc/os-release
case "$VERSION_CODENAME" in
    noble) ROS_DISTRO=jazzy ;;
    jammy) ROS_DISTRO=humble ;;
    *) echo "Unsupported Ubuntu '$VERSION_CODENAME' (need 24.04 noble or 22.04 jammy)"; exit 1 ;;
esac

echo ">>> [1/3] ROS 2 $ROS_DISTRO"
sudo apt-get update && sudo apt-get install -y curl gnupg2 lsb-release locales
sudo locale-gen en_US.UTF-8

curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    | sudo tee /usr/share/keyrings/ros-archive-keyring.gpg > /dev/null
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
http://packages.ros.org/ros2/ubuntu $VERSION_CODENAME main" \
    | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null
sudo apt-get update && sudo apt-get install -y \
    "ros-$ROS_DISTRO-ros-base" \
    "ros-$ROS_DISTRO-ros-gz-bridge" \
    "ros-$ROS_DISTRO-cv-bridge" \
    python3-colcon-common-extensions \
    python3-opencv python3-venv

echo ">>> [2/3] Python environment (Drone-Control/.venv)"
cd "$PROJECT_DIR/Drone-Control"
python3 -m venv --system-site-packages .venv
# shellcheck disable=SC1091
source .venv/bin/activate
# scripts import mavsdk_grpc — the plain "mavsdk" package is now a different (native) API
pip install mavsdk-grpc

echo ">>> [3/3] Clean up the old IP patch (older versions of this script)"
# they rewrote udp://:14540 in the scripts and wrote VM_IP to Drone-Control/.env — both unused now
rm -f "$PROJECT_DIR/Drone-Control/.env"
if grep -lq "udp://[0-9.]*:14540" "$PROJECT_DIR"/Drone-Control/*.py 2>/dev/null; then
    echo "WARN: a Drone-Control script still contains udp://<ip>:14540 — restore it with git checkout"
fi

echo ""
echo "=== Control computer setup complete ==="
echo ""
echo "Every new shell:"
echo "  source /opt/ros/$ROS_DISTRO/setup.bash"
echo "  source $PROJECT_DIR/Drone-Control/.venv/bin/activate"
echo "  cd $PROJECT_DIR/Drone-Control"
echo ""
echo "Run (VM started with CONTROL_HOST=<this computer's IP>):"
echo "  python3 flight_test.py                      # automated flight, drone 0"
echo "  DRONE_ID=1 python3 teleop_drone.py          # keyboard control of drone 1"
echo "  NUM_DRONES=2 python3 mcpp_mission.py        # MCPP coverage flight"
echo "  python3 view_cam.py                         # camera (needs a camera model + bridge)"
echo ""
echo "Firewall (only if ufw is enabled):"
echo "  sudo ufw allow from <VM_IP> to any port 14540:14549 proto udp   # MAVSDK"
echo "  sudo ufw allow from <VM_IP> to any port 14550 proto udp         # QGroundControl"
