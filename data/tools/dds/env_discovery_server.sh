# ROS 2 over DDS with Fast DDS Discovery Server (decided in Phase A).
#   Usage:  source tools/dds/env_discovery_server.sh <ground-station-ip>
#
# Why: plain DDS finds peers with multicast, which is slow and lossy on Wi-Fi and
# grows ~N^2 with 10 drones. With a Discovery Server every node registers with ONE
# server on the ground station over unicast. Data itself still flows as normal DDS.
#
# 1) On the ground station, start the server once:   tools/dds/start_discovery_server.sh
# 2) Source this file in EVERY terminal that runs a ROS 2 node or a DDS participant,
#    on every machine: PX4 MicroXRCEAgent, camera nodes, ros_gz_bridge, aquarius_bridge,
#    Multi-UAV Control, and the terminal you use for `ros2 topic ...`.
SERVER_IP="${1:-127.0.0.1}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_DISCOVERY_SERVER="${SERVER_IP}:11811"
# `ros2 topic list` etc. need "super client" mode to see the whole graph
export FASTRTPS_DEFAULT_PROFILES_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/super_client.xml"
sed -i "s|<address>.*</address>|<address>${SERVER_IP}</address>|" "$FASTRTPS_DEFAULT_PROFILES_FILE"
ros2 daemon stop >/dev/null 2>&1 || true
echo "DDS Discovery Server mode: ROS_DISCOVERY_SERVER=$ROS_DISCOVERY_SERVER"
