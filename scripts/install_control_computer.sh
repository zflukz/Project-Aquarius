#!/usr/bin/env bash
# install_control_computer.sh — NATIVE (no Docker) control station: MAVSDK + Drone-Control scripts + aquarius CLI
#
# Installs a standalone copy, so the control computer / WSL does not need the whole repo:
#   <AQUARIUS_HOME>/aquarius                    CLI (linked to ~/.local/bin/aquarius)
#   <AQUARIUS_HOME>/Drone-Control/*.py + .venv  mission scripts + Python env (mavsdk-grpc)
# AQUARIUS_HOME defaults to ~/aquarius-control. Same scripts as docker/drone-control/Dockerfile.
#
# Usage (from any copy of the repo, e.g. the Windows checkout inside WSL):
#   bash /mnt/c/Users/<you>/.../Xenoir/scripts/install_control_computer.sh            # first install
#   bash /mnt/c/Users/<you>/.../Xenoir/scripts/install_control_computer.sh --update   # copy new scripts only
# Options:
#   --update     only copy the scripts again (after git pull); no apt, keeps the .venv
#   --no-ros     skip ROS 2 (not needed for aquarius missions / status / rtl; only for view_cam.py)
#   --home DIR   install into DIR instead of ~/aquarius-control
#
# Ubuntu 24.04 -> ROS 2 Jazzy (= Docker image), Ubuntu 22.04 -> ROS 2 Humble.
# No server IP needed here: PX4 on the server sends MAVLink to this computer (server .env CONTROL_HOST =
# this computer's IP, e.g. its Tailscale IP) and the scripts only listen on udp 14540+i (drone i).
set -e

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"          # the repo copy this script was started from
AQUARIUS_HOME=${AQUARIUS_HOME:-$HOME/aquarius-control}
UPDATE=; NO_ROS=
while [ $# -gt 0 ]; do
    case "$1" in
        --update) UPDATE=1 ;;
        --no-ros) NO_ROS=1 ;;
        --home)   AQUARIUS_HOME=$2; shift ;;
        -h|--help) sed -n '2,/^set -e/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option $1 (--update | --no-ros | --home DIR)"; exit 1 ;;
    esac
    shift
done
CTRL_DIR=$AQUARIUS_HOME/Drone-Control

for f in Drone-Control/mcpp_mission.py Drone-Control/mission_cli.py scripts/aquarius; do
    [ -f "$SRC_DIR/$f" ] || { echo "ERROR: $SRC_DIR/$f not found — run this script from the repo's scripts/ folder"; exit 1; }
done
case "$AQUARIUS_HOME" in /mnt/[a-z]/*)
    echo "WARN: $AQUARIUS_HOME is on the Windows drive (slow, no Linux permissions) — better a WSL path (default ~/aquarius-control)" ;;
esac

if [ -z "$UPDATE" ]; then
    . /etc/os-release
    echo ">>> [1/4] system packages"
    sudo apt-get update && sudo apt-get install -y curl python3-venv python3-pip
    if [ -z "$NO_ROS" ]; then
        case "$VERSION_CODENAME" in
            noble) ROS_DISTRO=jazzy ;;
            jammy) ROS_DISTRO=humble ;;
            *) echo "Unsupported Ubuntu '$VERSION_CODENAME' for ROS 2 (24.04 / 22.04) — use --no-ros"; exit 1 ;;
        esac
        echo ">>> [1/4] ROS 2 $ROS_DISTRO (only for view_cam.py; --no-ros skips this)"
        sudo apt-get install -y gnupg2 lsb-release locales
        sudo locale-gen en_US.UTF-8
        curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
            | sudo tee /usr/share/keyrings/ros-archive-keyring.gpg > /dev/null
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
http://packages.ros.org/ros2/ubuntu $VERSION_CODENAME main" \
            | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null
        sudo apt-get update && sudo apt-get install -y \
            "ros-$ROS_DISTRO-ros-base" "ros-$ROS_DISTRO-cv-bridge" python3-opencv
    fi
fi

echo ">>> [2/4] copy scripts $SRC_DIR -> $AQUARIUS_HOME"
mkdir -p "$CTRL_DIR"
cp "$SRC_DIR"/Drone-Control/*.py "$CTRL_DIR/"
cp "$SRC_DIR/scripts/aquarius" "$AQUARIUS_HOME/aquarius"
# a Windows checkout has CRLF line endings (core.autocrlf) -> "bad interpreter" / syntax errors in Linux
sed -i 's/\r$//' "$AQUARIUS_HOME/aquarius" "$CTRL_DIR"/*.py
chmod +x "$AQUARIUS_HOME/aquarius" "$CTRL_DIR"/*.py
echo "    $(ls "$CTRL_DIR"/*.py | wc -l) scripts + aquarius"

echo ">>> [3/4] Python environment ($CTRL_DIR/.venv)"
if [ -x "$CTRL_DIR/.venv/bin/python" ] && [ -n "$UPDATE" ]; then
    echo "    kept (exists)"
else
    python3 -m venv --system-site-packages "$CTRL_DIR/.venv"
    # scripts import mavsdk_grpc — the plain "mavsdk" package is now a different (native) API
    "$CTRL_DIR/.venv/bin/pip" install -q mavsdk-grpc
fi
"$CTRL_DIR/.venv/bin/python" -c "import mavsdk_grpc" || { echo "ERROR: mavsdk_grpc not importable"; exit 1; }

echo ">>> [4/4] aquarius command"
mkdir -p "$HOME/.local/bin"
ln -sf "$AQUARIUS_HOME/aquarius" "$HOME/.local/bin/aquarius"
echo "    ~/.local/bin/aquarius -> $AQUARIUS_HOME/aquarius"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *)
    echo "    add to ~/.bashrc, then open a new shell:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;; esac

IP=$(tailscale ip -4 2>/dev/null | head -1 || true)
echo ""
echo "=== Control computer ready ($AQUARIUS_HOME) ==="
echo ""
[ -n "$IP" ] || IP="<IP of this computer, e.g. tailscale ip -4>"
echo "Server .env:  CONTROL_HOST=$IP   then restart the server stack"
echo "Then here:"
echo "  aquarius status --drone 0                  # HOLD, FIX_3D, armable yes"
echo "  aquarius start coverage 13.6509,100.4939 --size 40x30 --drones 2 --alt 40"
echo "  aquarius rtl --drone 0                     # abort: come home"
echo "Update the scripts later (after git pull on Windows):"
echo "  bash $SRC_DIR/scripts/install_control_computer.sh --update"
echo "Firewall (only if ufw is enabled):  sudo ufw allow in on tailscale0"
echo "                               or:  sudo ufw allow from <SERVER_IP> to any port 14540:14549 proto udp"
