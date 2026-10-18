#!/usr/bin/env bash
# run_telemetry_native.sh — telemetry topics + camera view for scripts/run_sim_native.sh, WITHOUT Docker
#
# Runs docker/telemetry/entrypoint.sh (same script as the telemetry container) with the native paths
# from scripts/install_telemetry_server.sh. Start it after (or next to) run_sim_native.sh with the same
# env. Ctrl+C stops it.
#
#   PX4_NUM_DRONES=2 PX4_SIM_MODEL=gz_x500_mono_cam_down bash scripts/run_telemetry_native.sh
#
# Gives: ROS 2 /fmu/out/* + /uav_i/fmu/out/* (PX4), /uav_i/gz/* incl. ground_truth (Gazebo),
#        /uav_i/camera/image_raw (camera models), camera http://<host>:8083/, RECORD=1 -> ros2 bag in
#        $DATA_DIR/bags, mavlink_mqtt.py: MAVLink udp 14700 -> MQTT_URL (default mqtt://127.0.0.1:1883; broker +
#        Grafana: docker compose -f docker/compose.server.yml up -d mosquitto influxdb telegraf grafana).
# Env: as docker/telemetry/entrypoint.sh, plus PX4_ROS_WS (~/px4_ros_ws), DATA_DIR (~/aquarius-data)
set -e

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
PX4_ROS_WS=${PX4_ROS_WS:-$HOME/px4_ros_ws}

command -v MicroXRCEAgent > /dev/null && [ -f /opt/ros/jazzy/setup.bash ] \
    || { echo "telemetry not installed — run scripts/install_telemetry_server.sh"; exit 1; }

export PX4_MSGS_SETUP=${PX4_MSGS_SETUP:-$PX4_ROS_WS/install/setup.bash}
export DATA_DIR=${DATA_DIR:-$HOME/aquarius-data}
export BRIDGE_CONFIG=${BRIDGE_CONFIG:-/tmp/aquarius_gz_bridge.yaml}
export GT_SCRIPT=${GT_SCRIPT:-$REPO_DIR/docker/telemetry/ground_truth.py}
export MQTT_SCRIPT=${MQTT_SCRIPT:-$REPO_DIR/docker/telemetry/mavlink_mqtt.py}

exec bash "$REPO_DIR/docker/telemetry/entrypoint.sh"
