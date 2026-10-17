FROM ubuntu:24.04
# telemetry — server-side telemetry + camera for the px4-sim drones (see docker/telemetry_plan.md)
#   MicroXRCEAgent   PX4 uORB -> ROS 2 /fmu/out/*            (T2)
#   ros_gz_bridge    Gazebo sensors / camera -> ROS 2   (C3)
#   ground_truth.py  true pose per drone -> /uav_i/gz/ground_truth   (T5)
#   web_video_server camera in the browser (MJPEG)
#   foxglove_bridge  every topic in Foxglove (plots, map, images)
#   rosbag2 (mcap)   record an experiment (T4)
# Runs in px4-sim's network namespace — it needs no change to the px4-sim image.

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=en_US.UTF-8
ENV ROS_DISTRO=jazzy

RUN apt-get update && apt-get install -y \
    curl git gnupg2 lsb-release locales \
    cmake build-essential \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

# ROS 2 Jazzy (pairs with Gazebo Harmonic = px4-sim's gz-transport13)
RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
        | tee /usr/share/keyrings/ros-archive-keyring.gpg > /dev/null && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
        http://packages.ros.org/ros2/ubuntu noble main" \
        > /etc/apt/sources.list.d/ros2.list && \
    apt-get update && apt-get install -y \
        ros-jazzy-ros-base \
        ros-jazzy-ros-gz-bridge \
        ros-jazzy-web-video-server \
        ros-jazzy-foxglove-bridge \
        ros-jazzy-rosbag2-storage-mcap \
        python3-colcon-common-extensions \
    && rm -rf /var/lib/apt/lists/*

# Micro XRCE-DDS Agent (no apt package for Jazzy). v2.4.3 = version PX4 docs pair with Jazzy.
# Superbuild downloads Fast-DDS 2.14 / Fast-CDR into /usr/local — same sonames as /opt/ros/jazzy/lib,
# so entrypoint.sh starts the agent with LD_LIBRARY_PATH=/usr/local/lib.
ARG XRCE_AGENT_REF=v2.4.3
RUN git clone --depth 1 -b ${XRCE_AGENT_REF} \
        https://github.com/eProsima/Micro-XRCE-DDS-Agent.git /tmp/xrce && \
    cmake -S /tmp/xrce -B /tmp/xrce/build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build /tmp/xrce/build -j"$(nproc)" && \
    cmake --install /tmp/xrce/build && ldconfig /usr/local/lib && \
    rm -rf /tmp/xrce

# px4_msgs — message definitions must match the PX4 firmware (versioned topics like
# /fmu/out/vehicle_status_v4). Package skeleton from PX4/px4_msgs, .msg/.srv from PX4-Autopilot at
# PX4_REF. Pin it to px4-sim's commit: docker exec px4-sim git -C /px4 rev-parse HEAD
ARG PX4_REF=main
RUN git clone --depth 1 https://github.com/PX4/px4_msgs.git /opt/px4_ws/src/px4_msgs && \
    git init -q /tmp/px4 && cd /tmp/px4 && \
    git remote add origin https://github.com/PX4/PX4-Autopilot.git && \
    git fetch -q --depth 1 origin "${PX4_REF}" && git checkout -q FETCH_HEAD && \
    rm -f /opt/px4_ws/src/px4_msgs/msg/*.msg /opt/px4_ws/src/px4_msgs/srv/*.srv && \
    cp msg/*.msg /opt/px4_ws/src/px4_msgs/msg/ && \
    if [ -d msg/versioned ]; then cp msg/versioned/*.msg /opt/px4_ws/src/px4_msgs/msg/; fi && \
    if [ -d srv ]; then cp srv/*.srv /opt/px4_ws/src/px4_msgs/srv/; fi && \
    git rev-parse HEAD > /opt/px4_ws/PX4_COMMIT && \
    cd / && rm -rf /tmp/px4 && \
    . /opt/ros/jazzy/setup.sh && cd /opt/px4_ws && \
    colcon build --cmake-args -DCMAKE_BUILD_TYPE=Release && \
    rm -rf build log src

# `docker exec telemetry with-ros ros2 topic list` — ROS 2 + px4_msgs sourced for one command
RUN printf '#!/usr/bin/env bash\nsource /opt/ros/jazzy/setup.bash\nsource /opt/px4_ws/install/setup.bash\nexec "$@"\n' \
        > /usr/local/bin/with-ros && chmod +x /usr/local/bin/with-ros && \
    echo 'source /opt/ros/jazzy/setup.bash && source /opt/px4_ws/install/setup.bash' >> /root/.bashrc

# defaults — override in docker/compose.*.yml / docker/.env (same names as px4-sim)
ENV PX4_NUM_DRONES=1 \
    PX4_GZ_WORLD=baylands \
    PX4_SIM_MODEL=gz_x500 \
    GZ_PARTITION=aquarius \
    ROS_DOMAIN_ID=0 \
    XRCE_PORT=8888 \
    WEB_VIDEO_PORT=8083 \
    FOXGLOVE_PORT=8765 \
    RECORD= \
    DATA_DIR=/data

EXPOSE 8083 8765
VOLUME /data

COPY ground_truth.py /ground_truth.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
