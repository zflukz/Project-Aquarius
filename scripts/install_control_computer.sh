#!/usr/bin/env bash
# install_control_computer.sh
# Run this on your computer (Ubuntu 22.04 / WSL2)
# Usage: bash install_control_computer.sh <VM_SERVER_IP>
# Example: bash install_control_computer.sh 192.168.1.100
set -e

VM_IP="${1:?Usage: bash install_control_computer.sh <VM_SERVER_IP>}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo ">>> [1/3] ROS 2 Humble"
sudo apt-get update && sudo apt-get install -y curl gnupg2 lsb-release locales
sudo locale-gen en_US.UTF-8

curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
    http://packages.ros.org/ros2/ubuntu $(lsb_release -cs) main" \
    | sudo tee /etc/apt/sources.list.d/ros2.list
sudo apt-get update && sudo apt-get install -y \
    ros-humble-ros-base \
    ros-humble-ros-gz-bridge \
    ros-humble-cv-bridge \
    python3-colcon-common-extensions \
    python3-opencv

echo ">>> [2/3] Python environment"
cd "$PROJECT_DIR/Drone-Control"
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install mavsdk

echo ">>> [3/3] Patching VM IP → $VM_IP"
# Replace localhost UDP address with the VM server IP in both scripts
sed -i "s|udp://:14540|udp://${VM_IP}:14540|g" \
    "$PROJECT_DIR/Drone-Control/teleop_drone.py" \
    "$PROJECT_DIR/Drone-Control/flight_test.py"

# Persist VM_IP for the run scripts
echo "VM_IP=${VM_IP}" > "$PROJECT_DIR/Drone-Control/.env"

echo ""
echo "=== Computer setup complete ==="
echo ""
echo "Activate the venv before running:"
echo "  source $PROJECT_DIR/Drone-Control/.venv/bin/activate"
echo "  source /opt/ros/humble/setup.bash"
echo ""
echo "Then run:"
echo "  python3 $PROJECT_DIR/Drone-Control/flight_test.py   # automated flight"
echo "  python3 $PROJECT_DIR/Drone-Control/teleop_drone.py  # keyboard control"
echo "  python3 $PROJECT_DIR/Drone-Control/view_cam.py      # camera stream"
