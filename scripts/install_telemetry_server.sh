#!/usr/bin/env bash
# install_telemetry_server.sh — NATIVE (no Docker) telemetry + camera bridge on the VM server
#
# Same software as docker/telemetry/Dockerfile: ROS 2 Jazzy + ros_gz_bridge + web_video_server +
# rosbag2 mcap, pymavlink + paho-mqtt (mavlink_mqtt.py), Micro XRCE-DDS Agent (source, /usr/local), px4_msgs built from the
# native PX4's own msg/ (so the messages match the firmware exactly).
# Run after scripts/install_sim_server.sh (needs $PX4_DIR). Ubuntu 24.04 only (Jazzy).
#
# Usage:  bash scripts/install_telemetry_server.sh
# Then:   bash scripts/run_sim_native.sh            (terminal 1)
#         bash scripts/run_telemetry_native.sh      (terminal 2)
#
# Env: PX4_DIR (~/PX4-Autopilot), PX4_ROS_WS (~/px4_ros_ws), XRCE_AGENT_REF (v2.4.3)
set -e

PX4_DIR=${PX4_DIR:-$HOME/PX4-Autopilot}
PX4_ROS_WS=${PX4_ROS_WS:-$HOME/px4_ros_ws}
XRCE_AGENT_REF=${XRCE_AGENT_REF:-v2.4.3}

. /etc/os-release
[ "$VERSION_CODENAME" = noble ] || { echo "Ubuntu 24.04 (noble) needed for ROS 2 Jazzy, this is $PRETTY_NAME"; exit 1; }
[ -d "$PX4_DIR/msg" ] || { echo "PX4 not found in $PX4_DIR — run scripts/install_sim_server.sh first"; exit 1; }

echo ">>> [1/3] ROS 2 Jazzy + bridge / viewer packages"
sudo apt-get update
sudo apt-get install -y curl git gnupg2 lsb-release cmake build-essential
if [ ! -f /etc/apt/sources.list.d/ros2.list ]; then
    curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
        | sudo tee /usr/share/keyrings/ros-archive-keyring.gpg > /dev/null
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
http://packages.ros.org/ros2/ubuntu noble main" | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null
    sudo apt-get update
fi
sudo apt-get install -y \
    ros-jazzy-ros-base ros-jazzy-ros-gz-bridge ros-jazzy-web-video-server \
    ros-jazzy-rosbag2-storage-mcap python3-colcon-common-extensions python3-pip
# mavlink_mqtt.py (MAVLink -> MQTT -> Grafana); noble's python3-paho-mqtt is 1.6 (no v2 API)
sudo pip3 install --break-system-packages "pymavlink>=2.4.40" "paho-mqtt>=2,<3"

echo ">>> [2/3] Micro XRCE-DDS Agent $XRCE_AGENT_REF -> /usr/local"
mkdir -p "$PX4_ROS_WS"
if ! command -v MicroXRCEAgent > /dev/null; then
    rm -rf /tmp/xrce
    git clone --depth 1 -b "$XRCE_AGENT_REF" https://github.com/eProsima/Micro-XRCE-DDS-Agent.git /tmp/xrce
    cmake -S /tmp/xrce -B /tmp/xrce/build -DCMAKE_BUILD_TYPE=Release
    cmake --build /tmp/xrce/build -j"$(nproc)"
    sudo cmake --install /tmp/xrce/build
    sudo ldconfig /usr/local/lib
    # file list for scripts/uninstall_sim_server.sh
    cp /tmp/xrce/build/install_manifest.txt "$PX4_ROS_WS/xrce_install_manifest.txt"
    rm -rf /tmp/xrce
else
    echo "MicroXRCEAgent already installed"
fi

echo ">>> [3/3] px4_msgs from $PX4_DIR/msg -> $PX4_ROS_WS"
rm -rf "$PX4_ROS_WS/src/px4_msgs"
git clone --depth 1 https://github.com/PX4/px4_msgs.git "$PX4_ROS_WS/src/px4_msgs"
rm -f "$PX4_ROS_WS"/src/px4_msgs/msg/*.msg "$PX4_ROS_WS"/src/px4_msgs/srv/*.srv
cp "$PX4_DIR"/msg/*.msg "$PX4_ROS_WS/src/px4_msgs/msg/"
if [ -d "$PX4_DIR/msg/versioned" ]; then cp "$PX4_DIR"/msg/versioned/*.msg "$PX4_ROS_WS/src/px4_msgs/msg/"; fi
if [ -d "$PX4_DIR/srv" ]; then cp "$PX4_DIR"/srv/*.srv "$PX4_ROS_WS/src/px4_msgs/srv/"; fi
(
    set +u; source /opt/ros/jazzy/setup.bash
    cd "$PX4_ROS_WS" && colcon build --cmake-args -DCMAKE_BUILD_TYPE=Release
)

echo ""
echo "=== telemetry (native) setup complete ==="
echo "  terminal 1: bash scripts/run_sim_native.sh"
echo "  terminal 2: bash scripts/run_telemetry_native.sh"
echo "  camera:     http://<VM_IP>:8083/"
echo "  dashboard:  MQTT broker + InfluxDB + Telegraf + Grafana are containers only:"
echo "              docker compose -f docker/compose.server.yml up -d mosquitto influxdb telegraf grafana"
echo "              -> Grafana http://<VM_IP>:3000"
echo "Firewall (only if ufw is enabled):"
echo "  sudo ufw allow from <CONTROL_IP> to any port 8083 proto tcp   # camera (web_video_server)"
echo "  sudo ufw allow from <CONTROL_IP> to any port 3000 proto tcp   # Grafana"
echo "  sudo ufw allow from <CONTROL_IP> to any port 1883 proto tcp   # MQTT (optional)"
