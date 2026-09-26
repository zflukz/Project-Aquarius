#!/usr/bin/env bash
# install_sim_server.sh
# Run this on the VM server (any Ubuntu version)
# Usage: bash install_sim_server.sh <COMPUTER_IP>
set -e

COMPUTER_IP="${1:?Usage: bash install_sim_server.sh <COMPUTER_IP>}"
GZ_WS="$HOME/gz_ws"

echo ">>> [1/5] Base dependencies"
sudo apt-get update && sudo apt-get install -y \
    curl git cmake ninja-build ccache build-essential \
    python3-pip python3-jinja2 python3-jsonschema \
    python3-toml python3-numpy python3-empy python3-packaging \
    locales wget gnupg2 lsb-release \
    python3-vcstool python3-colcon-common-extensions \
    ruby ruby-dev
sudo locale-gen en_US.UTF-8

echo ">>> [2/5] Gazebo Harmonic build dependencies"
sudo apt-get install -y \
    libfreeimage-dev libgts-dev libavdevice-dev \
    libzmq3-dev libsqlite3-dev libwebsockets-dev \
    libtinyxml2-dev liburdfdom-dev libogre-1.9-dev \
    libbullet-dev libdart6-dev libprotobuf-dev \
    libprotoc-dev protobuf-compiler \
    libeigen3-dev libccd-dev libfcl-dev \
    libassimp-dev libboost-all-dev \
    libgflags-dev libgoogle-glog-dev \
    libyaml-cpp-dev libzip-dev \
    libgz-utils2-dev 2>/dev/null || true

echo ">>> [3/5] Gazebo Harmonic from source"
mkdir -p "$GZ_WS/src"
cd "$GZ_WS"

if [ ! -f "$GZ_WS/harmonic.repos" ]; then
    curl -sSL \
        https://raw.githubusercontent.com/gazebo-tooling/gazebodistro/master/collection-harmonic.yaml \
        -o harmonic.repos
    vcs import src < harmonic.repos
fi

# install remaining rosdep-style deps
sudo apt-get install -y python3-rosdep 2>/dev/null || pip3 install rosdep
sudo rosdep init 2>/dev/null || true
rosdep update
rosdep install --from-paths src --ignore-src -r -y 2>/dev/null || true

colcon build \
    --symlink-install \
    --cmake-args -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    --parallel-workers "$(nproc)"

# source in bashrc
GZ_SETUP="source $GZ_WS/install/setup.bash"
grep -qxF "$GZ_SETUP" ~/.bashrc || echo "$GZ_SETUP" >> ~/.bashrc
source "$GZ_WS/install/setup.bash"

echo ">>> [4/5] PX4 Autopilot"
if [ ! -d "$HOME/PX4-Autopilot" ]; then
    git clone --depth 1 --recursive \
        https://github.com/PX4/PX4-Autopilot.git ~/PX4-Autopilot
fi
bash ~/PX4-Autopilot/Tools/setup/ubuntu.sh --no-nuttx

echo ">>> [5/5] MAVLink broadcast → $COMPUTER_IP"
RC_FILE=~/PX4-Autopilot/ROMFS/px4fmu_common/init.d-posix/px4-rc.mavlink
if ! grep -q "$COMPUTER_IP" "$RC_FILE"; then
    echo "mavlink start -x -u 14540 -t ${COMPUTER_IP} -r 4000000" >> "$RC_FILE"
fi

echo ""
echo "=== VM server setup complete ==="
echo "Reload shell then start the sim:"
echo "  source ~/.bashrc"
echo "  cd ~/PX4-Autopilot && make px4_sitl gz_x500"
echo ""
echo "Open firewall port 14540/udp:"
echo "  sudo ufw allow 14540/udp"
