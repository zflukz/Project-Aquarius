#!/usr/bin/env bash
# uninstall_sim_server.sh
# Removes everything installed by install_sim_server.sh
# Usage: bash uninstall_sim_server.sh
set -e

echo ">>> [1/4] Stop any running PX4 / Gazebo processes"
pkill -f "px4" 2>/dev/null || true
pkill -f "gz sim" 2>/dev/null || true
pkill -f "ruby" 2>/dev/null || true

echo ">>> [2/4] Remove PX4 Autopilot"
rm -rf ~/PX4-Autopilot

echo ">>> [3/4] Remove Gazebo source build"
rm -rf ~/gz_ws

# Remove apt repo and keyring
sudo rm -f /etc/apt/sources.list.d/gazebo-stable.list
sudo rm -f /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
sudo apt-get update

# Remove Gazebo apt packages if they were installed
sudo apt-get remove --purge -y \
    gz-harmonic gz-garden \
    libgz-cmake3-dev libgz-common5-dev \
    libgz-fuel-tools9-dev libgz-sim8-dev \
    libgz-gui8-dev libgz-launch7-dev \
    libgz-math7-dev libgz-msgs10-dev \
    libgz-physics7-dev libgz-plugin2-dev \
    libgz-rendering8-dev libgz-sensors8-dev \
    libgz-tools2-dev libgz-transport13-dev \
    libgz-utils2-dev libsdformat14-dev \
    libfreeimage-dev libgts-dev \
    libzmq3-dev libwebsockets-dev \
    libtinyxml2-dev liburdfdom-dev \
    libogre-1.9-dev libbullet-dev libdart6-dev \
    libprotobuf-dev libprotoc-dev protobuf-compiler \
    libccd-dev libfcl-dev libassimp-dev \
    libgflags-dev libgoogle-glog-dev \
    libyaml-cpp-dev libzip-dev \
    python3-vcstool python3-rosdep \
    ccache ninja-build 2>/dev/null || true
sudo apt-get autoremove -y

echo ">>> [4/4] Clean up bashrc entries"
sed -i '/gz_ws\/install\/setup.bash/d' ~/.bashrc

echo ""
echo "=== VM server uninstall complete ==="
echo "PX4, Gazebo, and all related packages have been removed."
