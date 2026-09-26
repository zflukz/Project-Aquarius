# Docker Compose Flow

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  docker-compose.yml                                             │
│                                                                 │
│  ┌──────────────────┐     VNC      ┌──────────────────┐        │
│  │   px4-sim        │   :5900      │   novnc          │        │
│  │  ──────────────  │ ◄──────────► │  ──────────────  │        │
│  │  Xvfb :99        │              │  VNC→WebSocket   │        │
│  │  fluxbox         │              │  bridge          │        │
│  │  x11vnc          │              │  port 8080       │        │
│  │  Gazebo Harmonic │              └──────────────────┘        │
│  │  PX4 SITL        │                      ▲                   │
│  └──────────────────┘                      │ http              │
│           │ UDP :14540                     │ localhost:8080    │
│           ▼                           Your Browser             │
│  ┌──────────────────┐                                          │
│  │  drone-control   │                                          │
│  │  ──────────────  │                                          │
│  │  ROS 2 Jazzy     │                                          │
│  │  MAVSDK          │                                          │
│  │  flight_test.py  │                                          │
│  │  teleop_drone.py │                                          │
│  │  view_cam.py     │                                          │
│  └──────────────────┘                                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Containers

### px4-sim
- Base: `ubuntu:24.04`
- Installs: Gazebo Harmonic, PX4 Autopilot
- Starts in order:
  1. `Xvfb :99` — creates a virtual display, Gazebo renders into it as if it were a real monitor
  2. `fluxbox` — minimal window manager so Gazebo window has somewhere to live
  3. `x11vnc` — reads the virtual display and streams it as VNC on port `5900`
  4. `make px4_sitl gz_x500` — starts PX4 SITL and Gazebo with the x500 quadrotor
- Exposes `14540/udp` for MAVLink to `drone-control`
- Exposes `5900` for VNC to `novnc`

### novnc
- Image: `theasp/novnc` (prebuilt, no custom Dockerfile needed)
- Connects to `px4-sim:5900` (VNC)
- Serves it as a webpage on port `8080` via WebSocket
- Open `http://localhost:8080/vnc.html` to see the live Gazebo window

### drone-control
- Base: `ubuntu:24.04`
- Installs: ROS 2 Jazzy, MAVSDK
- Connects to PX4 via `udp://px4-sim:14540` (Docker DNS resolves `px4-sim` automatically)
- Runs `flight_test.py` by default

---

## Why noVNC instead of a Gazebo web plugin

Gazebo Harmonic has no built-in HTTP/web server plugin. The approach here is:

```
Gazebo → renders to Xvfb (virtual display)
Xvfb   → x11vnc reads the framebuffer → VNC stream on :5900
noVNC  → bridges VNC:5900 to WebSocket → serves on HTTP:8080
Browser → opens http://localhost:8080/vnc.html
```

Gazebo does not need to know anything about the web viewer. It just renders normally into a virtual display. This works for any GUI application in Docker, not just Gazebo.

---

## Startup Order

```
1. px4-sim starts
      └─ Xvfb :99 up
      └─ fluxbox up
      └─ x11vnc up (VNC ready on :5900)
      └─ PX4 + Gazebo up (renders into :99)

2. novnc starts
      └─ connects to px4-sim:5900
      └─ serves http://localhost:8080/vnc.html

3. drone-control starts
      └─ waits for PX4 MAVLink on udp://px4-sim:14540
      └─ runs flight_test.py
```

---

## How to Run

```bash
# build and start all containers
docker compose up --build

# watch Gazebo in browser
open http://localhost:8080/vnc.html

# run keyboard teleop instead of flight_test
docker compose run --rm drone-control \
  bash -c "source /opt/ros/jazzy/setup.bash && python3 teleop_drone.py"

# stop everything
docker compose down
```

---

## File Reference

```
Project-Aquarius/
├── docker-compose.yml
├── .env                              PX4_HOST=px4-sim
└── docker/
    ├── px4-sim/
    │   └── Dockerfile                Ubuntu 24.04 + Xvfb + x11vnc + Gazebo Harmonic + PX4
    └── drone-control/
        └── Dockerfile                Ubuntu 24.04 + ROS 2 Jazzy + MAVSDK
```
