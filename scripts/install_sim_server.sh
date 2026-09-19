#!/usr/bin/env bash
# install_sim_server.sh
# Run this on the VM server (Ubuntu 22.04)
# Usage: bash install_sim_server.sh <COMPUTER_IP>
# Example: bash install_sim_server.sh 192.168.1.50
set -e

COMPUTER_IP="${1:?Usage: bash install_sim_server.sh <COMPUTER_IP>}"

echo ">>> [1/4] Base dependencies"
sudo apt-get update && sudo apt-get install -y \
    curl git cmake ninja-build ccache \
    python3-pip python3-jinja2 python3-jsonschema \
    python3-toml python3-numpy python3-empy python3-packaging \
    locales wget gnupg2 lsb-release
sudo locale-gen en_US.UTF-8

echo ">>> [2/4] Gazebo Garden"
curl -sSL https://packages.osrfoundation.org/gazebo.gpg \
    -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] \
    http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
    | sudo tee /etc/apt/sources.list.d/gazebo-stable.list
sudo apt-get update && sudo apt-get install -y gz-garden

echo ">>> [3/4] PX4 Autopilot"
if [ ! -d "$HOME/PX4-Autopilot" ]; then
    git clone --depth 1 --recursive https://github.com/PX4/PX4-Autopilot.git ~/PX4-Autopilot
fi
bash ~/PX4-Autopilot/Tools/setup/ubuntu.sh --no-nuttx

echo ">>> [4/4] MAVLink broadcast → $COMPUTER_IP"
# Patch px4-rc.mavlink to unicast MAVLink to the computer
RC_FILE=~/PX4-Autopilot/ROMFS/px4fmu_common/init.d-posix/px4-rc.mavlink
if ! grep -q "$COMPUTER_IP" "$RC_FILE"; then
    echo "mavlink start -x -u 14540 -t ${COMPUTER_IP} -r 4000000" >> "$RC_FILE"
fi

echo ""
echo "=== VM server setup complete ==="
echo "Start the sim with:"
echo "  cd ~/PX4-Autopilot && make px4_sitl gz_x500"
echo ""
echo "Open firewall port 14540/udp so your computer can reach it:"
echo "  sudo ufw allow 14540/udp"
