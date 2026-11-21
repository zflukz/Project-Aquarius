FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=en_US.UTF-8
ENV GZ_VERSION=harmonic
ENV DISPLAY=:99

RUN apt-get update && apt-get install -y \
    curl git cmake ninja-build ccache build-essential \
    python3-pip python3-jinja2 python3-jsonschema \
    python3-toml python3-empy python3-packaging \
    locales wget gnupg2 lsb-release \
    xvfb x11vnc fluxbox \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

# Gazebo Harmonic
RUN curl -sSL https://packages.osrfoundation.org/gazebo.gpg \
        | tee /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg > /dev/null && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] \
        http://packages.osrfoundation.org/gazebo/ubuntu-stable noble main" \
        > /etc/apt/sources.list.d/gazebo-stable.list && \
    apt-get update && apt-get install -y gz-harmonic && \
    rm -rf /var/lib/apt/lists/*

# PX4 — sudo needed by ubuntu.sh, remove apt numpy first to avoid pip conflict
RUN apt-get update && apt-get install -y sudo && \
    apt-get remove -y python3-numpy && \
    git clone --depth 1 --recursive \
        https://github.com/PX4/PX4-Autopilot.git /px4 && \
    bash /px4/Tools/setup/ubuntu.sh --no-nuttx

WORKDIR /px4

# build SITL once in the image instead of on every new container
RUN make px4_sitl_default

# extra uXRCE-DDS topics (cpuload, mission_result, mavlink_log) for the Kafka path. Own layer AFTER the full
# build: the cached clone / build above stay valid (PX4 commit must keep matching telemetry's PX4_REF) and
# make only regenerates the uxrce_dds_client and relinks.
COPY dds_topics_extra.yaml patch_dds_topics.sh /tmp/
RUN bash /tmp/patch_dds_topics.sh && make px4_sitl_default

# QGroundControl on its own virtual display :100 -> VNC 5901 -> second noVNC (QGC=1).
# AppImage is extracted (no FUSE in Docker); QGC refuses to run as root -> user "qgc".
# It auto-connects via PX4's default GCS link (18570+i -> localhost:14550).
ARG QGC_VERSION=v5.1.5
RUN wget -q "https://github.com/mavlink/qgroundcontrol/releases/download/${QGC_VERSION}/QGroundControl-x86_64.AppImage" \
        -O /tmp/qgc.AppImage && \
    chmod +x /tmp/qgc.AppImage && \
    cd /opt && /tmp/qgc.AppImage --appimage-extract > /dev/null && \
    mv /opt/squashfs-root /opt/qgc && rm /tmp/qgc.AppImage && \
    useradd -m -G dialout qgc && chown -R qgc:qgc /opt/qgc

# KMUTT world (repo folder Simulation/, compose additional context "simulation") -> PX4's world / model
# folders: px4-rc.gzsim starts $PX4_GZ_WORLDS/$PX4_GZ_WORLD.sdf and both folders are on GZ_SIM_RESOURCE_PATH
# (gz_env.sh), so model://kmutt_* resolves. Last layers before ENV: a map change does not rebuild PX4 / QGC.
# Baylands is no longer pre-cached; PX4_GZ_WORLD=baylands still works (entrypoint downloads it once into the
# gz-fuel-cache volume).
COPY --from=simulation models/ /px4/Tools/simulation/gz/models/
COPY --from=simulation worlds/ /px4/Tools/simulation/gz/worlds/

# simulation defaults — override in docker/compose.*.yml / docker/.env
ENV PX4_SIM_MODEL=gz_x500 \
    PX4_SYS_AUTOSTART=4001 \
    PX4_GZ_WORLD=kmutt \
    PX4_NUM_DRONES=1 \
    DRONE_SPACING=3 \
    DRONE_SPAWN_X= \
    DRONE_SPAWN_Y= \
    DRONE_SPAWN_Z= \
    KMUTT_TREES= \
    CONTROL_HOST= \
    HEADLESS= \
    QGC=1 \
    SCREEN_SIZE=1920x1080x24 \
    VNC_LISTEN=0.0.0.0

# per drone i: PX4 listens on 14640+i and sends to CONTROL_HOST:14540+i (MAVSDK)
#              PX4 listens on 18640+i and sends to CONTROL_HOST:14550   (QGroundControl)
EXPOSE 14640-14649/udp
EXPOSE 18640-18649/udp
EXPOSE 5900 5901

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

STOPSIGNAL SIGTERM
ENTRYPOINT ["/entrypoint.sh"]
