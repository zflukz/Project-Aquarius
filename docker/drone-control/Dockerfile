FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=en_US.UTF-8
ENV ROS_DISTRO=jazzy

RUN apt-get update && apt-get install -y \
    curl gnupg2 lsb-release locales \
    python3-pip python3-opencv \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

# ROS 2 Jazzy
RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
        | tee /usr/share/keyrings/ros-archive-keyring.gpg > /dev/null && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
        http://packages.ros.org/ros2/ubuntu noble main" \
        > /etc/apt/sources.list.d/ros2.list && \
    apt-get update && apt-get install -y \
        ros-jazzy-ros-base \
        ros-jazzy-ros-gz-bridge \
        ros-jazzy-cv-bridge \
    && rm -rf /var/lib/apt/lists/*

COPY Drone-Control/ /drone-control/
RUN pip3 install mavsdk-grpc --break-system-packages

WORKDIR /drone-control

# which drone to control: MAVSDK listens on udp 14540 + DRONE_ID
# (MAVLINK_URL overrides the whole address, e.g. serial:///dev/ttyUSB0:57600)
ENV DRONE_ID=0
EXPOSE 14540-14549/udp

CMD ["bash", "-c", "source /opt/ros/jazzy/setup.bash && python3 flight_test.py"]
