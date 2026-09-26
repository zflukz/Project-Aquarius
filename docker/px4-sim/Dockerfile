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

EXPOSE 14540/udp
EXPOSE 5900

COPY docker/px4-sim/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

STOPSIGNAL SIGTERM
ENTRYPOINT ["/entrypoint.sh"]
