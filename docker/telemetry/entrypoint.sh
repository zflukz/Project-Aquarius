#!/usr/bin/env bash
# telemetry entrypoint — server-side telemetry topics + camera view for the px4-sim drones
#   1. MicroXRCEAgent udp4 :XRCE_PORT  PX4 uORB -> ROS 2   drone 0: /fmu/out/*, drone i: /uav_i/fmu/out/*
#   2. ros_gz_bridge                   Gazebo -> ROS 2     /uav_i/gz/{imu,navsat,air_pressure,magnetometer},
#                                                          /uav_i/camera/{image_raw,camera_info}, /clock
#      ground_truth.py                 true pose           /uav_i/gz/ground_truth (PoseStamped, GT_RATE Hz)
#   3. web_video_server :WEB_VIDEO_PORT camera in the browser  http://<host>:8083/
#   4. mavlink_mqtt.py  udp :14700     PX4 MAVLink (3rd link per drone) -> MQTT aquarius/<FLEET>/drone/<i>/*
#                                       -> Telegraf -> InfluxDB -> Grafana :3000 (docker/communication_plan.md)
#   5. RECORD=1                        ros2 bag (mcap) of RECORD_REGEX -> $DATA_DIR/bags/<date>
#
# Must share px4-sim's network: PX4's uXRCE client sends to 127.0.0.1:8888, and Gazebo transport
# discovery must reach the gz server -> network_mode host (VM) / service:px4-sim (local).
# Also used natively by scripts/run_telemetry_native.sh.
#
# Env (defaults in Dockerfile):
#   PX4_NUM_DRONES PX4_GZ_WORLD PX4_SIM_MODEL   same values as px4-sim (topic names)
#   GZ_PARTITION     same as px4-sim (Gazebo transport partition)
#   ROS_DOMAIN_ID    same as px4-sim (PX4 sets UXRCE_DDS_DOM_ID from it)
#   GZ_CAMERA        camera "<link>/<sensor>" in the model; empty = from PX4_SIM_MODEL, none = off
#   XRCE_PORT=8888  WEB_VIDEO_PORT=8083 (empty = off)
#   MQTT_URL=mqtt://127.0.0.1:1883 (empty = no MQTT bridge)  MAVLINK_IN=udpin:0.0.0.0:14700  FLEET=sim
#   RECORD (1 = record a bag)  RECORD_REGEX (topics)  DATA_DIR=/data
#   GT_RATE=30 (Hz, ground truth; empty = off)
#   ROS_SETUP PX4_MSGS_SETUP XRCE_LIB_DIR GT_SCRIPT MQTT_SCRIPT   paths (native installs)
set -e
# job control: non-interactive bash starts background jobs with SIGINT ignored, so cleanup's
# kill -INT never reached ros2 bag (no metadata.yaml, bag unreadable). With -m they keep SIGINT.
set -m

PX4_NUM_DRONES=${PX4_NUM_DRONES:-1}
PX4_GZ_WORLD=${PX4_GZ_WORLD:-baylands}
PX4_SIM_MODEL=${PX4_SIM_MODEL:-gz_x500}
export GZ_PARTITION=${GZ_PARTITION:-aquarius}
export ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0}
XRCE_PORT=${XRCE_PORT:-8888}
WEB_VIDEO_PORT=${WEB_VIDEO_PORT-8083}
export MQTT_URL=${MQTT_URL-mqtt://127.0.0.1:1883}
export MAVLINK_IN=${MAVLINK_IN:-udpin:0.0.0.0:14700}
export FLEET=${FLEET:-sim}
DATA_DIR=${DATA_DIR:-/data}
RECORD_REGEX=${RECORD_REGEX:-'.*/fmu/out/.*|/uav_[0-9]+/gz/.*'}
ROS_SETUP=${ROS_SETUP:-/opt/ros/jazzy/setup.bash}
PX4_MSGS_SETUP=${PX4_MSGS_SETUP:-/opt/px4_ws/install/setup.bash}
XRCE_LIB_DIR=${XRCE_LIB_DIR:-/usr/local/lib}
BRIDGE_CONFIG=${BRIDGE_CONFIG:-/tmp/gz_bridge.yaml}
GT_SCRIPT=${GT_SCRIPT:-/ground_truth.py}
MQTT_SCRIPT=${MQTT_SCRIPT:-/mavlink_mqtt.py}
export GT_RATE=${GT_RATE-30}
export PX4_NUM_DRONES PX4_GZ_WORLD PX4_SIM_MODEL   # for ground_truth.py

# camera sensor of the PX4 models (PX4-Autopilot Tools/simulation/gz/models, includes are merged
# into the drone model): mono_cam / mono_cam_down / gimbal -> camera_link/camera, depth -> camera_link/IMX214
case "$PX4_SIM_MODEL" in
    *depth*)        cam_default=camera_link/IMX214 ;;
    *cam*|*gimbal*) cam_default=camera_link/camera ;;
    *)              cam_default= ;;
esac
GZ_CAMERA=${GZ_CAMERA:-$cam_default}
[ "$GZ_CAMERA" = none ] && GZ_CAMERA=
MODEL=${PX4_SIM_MODEL#*gz_}   # PX4 spawns drone i as <model>_<i>, e.g. x500_mono_cam_down_0

# --- ros_gz_bridge config: one entry per topic, lazy = no Gazebo subscription until someone listens
bridge() {   # ros_topic gz_topic ros_type gz_type
    cat <<EOF
- ros_topic_name: "$1"
  gz_topic_name: "$2"
  ros_type_name: "$3"
  gz_type_name: "$4"
  direction: GZ_TO_ROS
  lazy: true
EOF
}
{
    bridge /clock "/world/$PX4_GZ_WORLD/clock" rosgraph_msgs/msg/Clock gz.msgs.Clock
    # ground truth: ground_truth.py (the bridge's Pose_V -> TFMessage drops the model names)
    for i in $(seq 0 $((PX4_NUM_DRONES - 1))); do
        m="/world/$PX4_GZ_WORLD/model/${MODEL}_$i"
        s="$m/link/base_link/sensor"
        bridge "/uav_$i/gz/imu"          "$s/imu_sensor/imu"                   sensor_msgs/msg/Imu           gz.msgs.IMU
        bridge "/uav_$i/gz/navsat"       "$s/navsat_sensor/navsat"             sensor_msgs/msg/NavSatFix     gz.msgs.NavSat
        bridge "/uav_$i/gz/air_pressure" "$s/air_pressure_sensor/air_pressure" sensor_msgs/msg/FluidPressure gz.msgs.FluidPressure
        bridge "/uav_$i/gz/magnetometer" "$s/magnetometer_sensor/magnetometer" sensor_msgs/msg/MagneticField gz.msgs.Magnetometer
        if [ -n "$GZ_CAMERA" ]; then
            c="$m/link/${GZ_CAMERA%%/*}/sensor/${GZ_CAMERA#*/}"
            bridge "/uav_$i/camera/image_raw"   "$c/image"       sensor_msgs/msg/Image      gz.msgs.Image
            bridge "/uav_$i/camera/camera_info" "$c/camera_info" sensor_msgs/msg/CameraInfo gz.msgs.CameraInfo
        fi
    done
} > "$BRIDGE_CONFIG"

PIDS=()
cleanup() {
    echo "Stopping telemetry..."
    kill -INT "${PIDS[@]}" 2>/dev/null || true   # SIGINT lets ros2 bag close the mcap file
    wait
    exit 0
}
trap cleanup SIGTERM SIGINT

# 1. uXRCE-DDS agent — own Fast-DDS in XRCE_LIB_DIR (same sonames as ROS's, so keep ROS's off the path)
LD_LIBRARY_PATH="$XRCE_LIB_DIR" MicroXRCEAgent udp4 -p "$XRCE_PORT" > >(sed -u 's/^/[xrce] /') 2>&1 &
PIDS+=($!)

source "$ROS_SETUP"
# px4_msgs: lets ros2 bag / ros2 topic echo decode /fmu/out/*
if [ -f "$PX4_MSGS_SETUP" ]; then source "$PX4_MSGS_SETUP"; else echo "WARN: no px4_msgs ($PX4_MSGS_SETUP)"; fi

# 2. Gazebo -> ROS 2
ros2 run ros_gz_bridge parameter_bridge --ros-args -p config_file:="$BRIDGE_CONFIG" \
    > >(sed -u 's/^/[gz_bridge] /') 2>&1 &
PIDS+=($!)

# 2b. true pose per drone (MCPP coverage / position error) — gz.transport13 Python from ROS's vendor packages
if [ -n "$GT_RATE" ]; then
    PYTHONPATH="/opt/ros/$ROS_DISTRO/opt/gz_transport_vendor/lib/python:/opt/ros/$ROS_DISTRO/opt/gz_msgs_vendor/lib/python:$PYTHONPATH" \
        python3 -u "$GT_SCRIPT" > >(sed -u 's/^/[ground_truth] /') 2>&1 &
    PIDS+=($!)
fi

# 3. camera view in the browser
if [ -n "$WEB_VIDEO_PORT" ]; then
    ros2 run web_video_server web_video_server --ros-args -p port:="$WEB_VIDEO_PORT" -p address:=0.0.0.0 \
        > >(sed -u 's/^/[web_video] /') 2>&1 &
    PIDS+=($!)
fi

# 4. MAVLink telemetry -> MQTT (Grafana dashboard via Telegraf / InfluxDB; no ROS needed)
if [ -n "$MQTT_URL" ]; then
    python3 -u "$MQTT_SCRIPT" > >(sed -u 's/^/[mqtt] /') 2>&1 &
    PIDS+=($!)
fi

# 5. record an experiment
if [ "$RECORD" = "1" ]; then
    bag="$DATA_DIR/bags/$(date +%Y-%m-%d_%H%M%S)"
    mkdir -p "$DATA_DIR/bags"
    ros2 bag record -s mcap -o "$bag" -e "$RECORD_REGEX" > >(sed -u 's/^/[bag] /') 2>&1 &
    PIDS+=($!)
    echo "recording '$RECORD_REGEX' -> $bag"
fi

echo "telemetry: $PX4_NUM_DRONES x ${MODEL} in '$PX4_GZ_WORLD' (GZ_PARTITION=$GZ_PARTITION, ROS_DOMAIN_ID=$ROS_DOMAIN_ID)"
echo "  PX4 uORB  : /fmu/out/* (drone 0), /uav_<i>/fmu/out/* (drone i)   agent udp $XRCE_PORT"
echo "  Gazebo    : /uav_<i>/gz/{imu,navsat,air_pressure,magnetometer,ground_truth}, /clock"
if [ -n "$GZ_CAMERA" ]; then
    echo "  camera    : /uav_<i>/camera/image_raw  ($GZ_CAMERA)"
    [ -n "$WEB_VIDEO_PORT" ] && echo "              http://<host>:$WEB_VIDEO_PORT/stream?topic=/uav_0/camera/image_raw"
else
    echo "  camera    : none — $PX4_SIM_MODEL has no camera (PX4_SIM_MODEL=gz_x500_mono_cam_down, PX4_SYS_AUTOSTART=4014)"
fi
[ -n "$MQTT_URL" ] && echo "  MQTT      : $MAVLINK_IN -> $MQTT_URL aquarius/$FLEET/drone/<i>/*  (Grafana http://<host>:3000)"

# exit (docker restarts us) as soon as one part dies
wait -n "${PIDS[@]}"
echo "A telemetry process exited, shutting down"
cleanup
