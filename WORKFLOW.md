# Project Aquarius — Workflow

## Overview

```
Environment Setup (Docker: VM server + control computer)
      │
      ▼
PX4 SITL + Gazebo on the VM server  (Baylands world, 1..N drones)
      │  MAVLink UDP  (drone i → control computer :14540+i)
      ▼
Control computer
      ├──► Keyboard Teleop        (teleop_drone.py, DRONE_ID=i)
      ├──► Automated Flight Test  (flight_test.py,  DRONE_ID=i)
      ├──► QGroundControl         (all drones on :14550)
      └──► Camera Stream          (view_cam.py)
            │
            ▼
      Multi-drone / Swarm  →  Real Drone Deployment
```

Everything Docker / simulator related lives in **`docker/`**:
**`docker/README.md`** covers every container, port, env var and the multi-drone plan, and
**`docker/baylands_setup_guide.md`** covers Baylands world caching and troubleshooting.

---

## 1. Environment Setup

```
 1a LOCAL TEST (your computer)          1b publish             1c SPLIT
 docker/compose.local.yml               compose push           VM server                     Control computer
 px4-sim ─UDP─► drone-control           ──► ghcr.io ──►        docker/compose.server.yml     docker/compose.control.yml
 novnc :8080                                                   (pull image only, no git)     drone-control + QGC
```

Develop and test locally (1a). When it works, push the images (1b). The VM only pulls and runs them (1c).

### 1a. Local test — all containers on your computer

```bash
cd docker
cp .env.example .env                       # first time only
docker compose -f compose.local.yml up -d --build
docker compose -f compose.local.yml logs -f px4-sim     # wait for "Ready for takeoff!"
# Gazebo view: http://localhost:8080/vnc.html
```

`px4-sim`, `novnc` and `drone-control` all run on your computer. MAVLink takes the same path as in
the split setup, so a passing local test is a good check before you deploy.

### 1b. Publish images (from your computer)

```bash
echo <TOKEN> | docker login ghcr.io -u zflukz --password-stdin   # once, token with write:packages
cd docker
docker compose -f compose.local.yml build
docker compose -f compose.local.yml push px4-sim drone-control
```

Images: `ghcr.io/zflukz/aquarius-px4-sim` and `ghcr.io/zflukz/aquarius-drone-control`
(`REGISTRY` / `TAG` in `docker/.env`). Make the packages public on GitHub, or `docker login` on the VM.

### 1c. Split — VM server (pull only) + control computer

**On the VM server** — no git clone. It needs Docker plus `compose.server.yml` and `.env` in one folder:

```bash
mkdir -p ~/aquarius && cd ~/aquarius
curl -fsSLO https://raw.githubusercontent.com/zflukz/Project-Aquarius/main/docker/compose.server.yml
curl -fsSL  https://raw.githubusercontent.com/zflukz/Project-Aquarius/main/docker/.env.example -o .env
#   (or: scp docker/compose.server.yml docker/.env.example user@<VM_IP>:~/aquarius/)
nano .env        # CONTROL_HOST=<control computer IP>, PX4_NUM_DRONES, PX4_GZ_WORLD, HEADLESS

docker compose -f compose.server.yml pull
docker compose -f compose.server.yml up -d
docker compose -f compose.server.yml logs -f px4-sim
```

The sim is ready when every drone logs:

```text
[drone0] INFO  [commander] Ready for takeoff!
drone0 (sysid 1) -> <CONTROL_IP>:14540 [MAVSDK], :14550 [QGC]
```

Update later with `docker compose -f compose.server.yml pull && docker compose -f compose.server.yml up -d`.

**On the control computer:**

```bash
cd docker
docker compose -f compose.control.yml up -d          # pulls drone-control (or add --build)
```

Firewall: the VM allows `14640-14649/udp`, `18640-18649/udp` and `8081/tcp` from the control computer.
The control computer allows `14540-14550/udp` from the VM. Gazebo view: `http://<VM_IP>:8081/vnc.html`.

### 1d. Native install — no Docker

```bash
bash scripts/install_sim_server.sh <CONTROL_COMPUTER_IP>   # VM server (PX4 + Gazebo)
bash scripts/install_control_computer.sh <VM_SERVER_IP>    # control computer (ROS 2 + MAVSDK)
```

> These scripts still use the older single-port MAVLink setup. Docker is the maintained path.

---

## 2. Start the Simulated Drones

With Docker the sim starts automatically with `up -d`. Configure it in `.env`
(`docker/.env` locally, `~/aquarius/.env` on the VM):

| `.env` | Example | Effect |
| :--- | :--- | :--- |
| `PX4_NUM_DRONES` | `3` | 3 drones, 3 m apart, MAV_SYS_ID 1..3 |
| `PX4_GZ_WORLD` | `baylands` / `default` | 3D terrain / light flat world |
| `PX4_SIM_MODEL` + `PX4_SYS_AUTOSTART` | `gz_x500_depth` + `4002` | drone with depth camera |
| `HEADLESS` | `1` | no Gazebo GUI (saves CPU on the VM) |

Apply changes (no rebuild needed):

```bash
docker compose -f compose.server.yml up -d --force-recreate px4-sim    # VM
docker compose -f compose.local.yml  up -d --force-recreate px4-sim    # local test (in docker/)
```

Drone `i` is reachable on the control computer at `udpin://0.0.0.0:14540+i`.

---

## 3. Flight Control (on your computer)

Shortcut used below (run from the repo root). Pick the file that matches your setup:

```bash
DC="docker compose -f docker/compose.local.yml exec"      # 1a local test
DC="docker compose -f docker/compose.control.yml exec"    # 1c split, VM runs the sim
ROS="source /opt/ros/jazzy/setup.bash"
```

### 3a. Automated Flight Test

Verifies every movement axis without touching a keyboard.

```bash
$DC drone-control bash -c "$ROS && python3 flight_test.py"                 # drone 0
$DC -e DRONE_ID=2 drone-control bash -c "$ROS && python3 flight_test.py"   # drone 2
```

Sequence executed automatically:

```
Connect → GPS lock → Arm → Takeoff
  → Forward → Backward → Left → Right
  → Up → Down → Yaw-Left → Yaw-Right
  → Hover → Land
```

Each step prints live telemetry (altitude, lat, lon). Test passes when the drone lands and `in_air == False`.

### 3b. Keyboard Teleop (manual)

```bash
$DC -e DRONE_ID=0 drone-control bash -c "$ROS && python3 teleop_drone.py"
```

| Key | Action |
|-----|--------|
| `W` / `S` | Forward / Backward |
| `Q` / `E` | Strafe Left / Right |
| `R` / `F` | Up / Down |
| `A` / `D` | Yaw Left / Right |
| `Space` | Hover (stop all movement) |
| `X` | Land and exit |

> `teleop_drone.py` uses `termios` / `tty`, so it needs a real terminal (`docker compose exec` is fine).
> It does not run in PowerShell outside Docker.

### 3c. Several drones at once

Open one terminal per drone, each with its own `DRONE_ID`:

```bash
# terminal 1                                   # terminal 2
$DC -e DRONE_ID=0 drone-control \              $DC -e DRONE_ID=1 drone-control \
  bash -c "$ROS && python3 teleop_drone.py"      bash -c "$ROS && python3 flight_test.py"
```

### 3d. QGroundControl

Run QGroundControl on the control computer. It listens on `udp 14550` by default and shows every
drone (one vehicle per MAV_SYS_ID). No extra configuration is needed.

### Script connection settings

| Env | Default | Meaning |
| :--- | :--- | :--- |
| `DRONE_ID` | `0` | connect to drone `i` → `udpin://0.0.0.0:14540+i` |
| `MAVLINK_URL` | – | override the full address (e.g. a real drone, see section 7) |

Running the scripts natively instead of in Docker works the same way:
`DRONE_ID=1 python3 Drone-Control/teleop_drone.py`.

---

## 4. Camera Stream

Requires `PX4_SIM_MODEL=gz_x500_depth`, `PX4_SYS_AUTOSTART=4002` (section 2).

The Gazebo image topic lives where Gazebo runs, so the bridge runs **on the sim machine**:

```bash
gz topic -l                         # find the camera topic, e.g. /camera

source /opt/ros/jazzy/setup.bash
ros2 run ros_gz_bridge parameter_bridge \
  '/camera@sensor_msgs/msg/Image@gz.msgs.Image' \
  --ros-args -r '/camera:=/camera'
```

Then view it on a machine on the same ROS 2 network (same `ROS_DOMAIN_ID`):

```bash
source /opt/ros/jazzy/setup.bash
python3 Drone-Control/view_cam.py
```

> If `view_cam.py` waits forever, change QoS in the file from `ReliabilityPolicy.RELIABLE` → `ReliabilityPolicy.BEST_EFFORT`.
>
> The `px4-sim` image does not include `ros_gz_bridge` yet. A camera bridge on the VM server is
> planned (`docker/README.md`, Future plan Phase 2). Until then, use the native setup (1d) for camera work.

---

## 5. File Reference

```
Project-Aquarius/
├── docker/                         everything Docker / simulator
│   ├── README.md                   containers, port map, publish images, multi-drone roadmap
│   ├── baylands_setup_guide.md     Baylands world: pre-cache + troubleshooting
│   ├── .env.example                REGISTRY, CONTROL_HOST, PX4_NUM_DRONES, PX4_GZ_WORLD, HEADLESS, ...
│   ├── compose.local.yml           local test: all containers on your computer (+ build & push)
│   ├── compose.server.yml          VM server: pull image only
│   ├── compose.control.yml         control computer: drone-control
│   ├── px4-sim/
│   │   ├── Dockerfile              Gazebo Harmonic + PX4 (pre-built) + Baylands cache + VNC
│   │   └── entrypoint.sh           starts N PX4 instances, MAVLink links → CONTROL_HOST
│   └── drone-control/
│       ├── Dockerfile              ROS 2 Jazzy + MAVSDK + Drone-Control/
│       └── Dockerfile.dockerignore
├── scripts/
│   ├── install_sim_server.sh       native VM server install
│   └── install_control_computer.sh native control computer install
└── Drone-Control/
    ├── teleop_drone.py             keyboard controller → PX4 via MAVSDK (DRONE_ID)
    ├── flight_test.py              automated flight test (arm/takeoff/move/land, DRONE_ID)
    └── view_cam.py                 ROS 2 subscriber → OpenCV camera window
```

---

## 6. Component Dependency Map

```
Control computer                                    VM server
teleop_drone.py ─┐                                  ┌─► PX4 instance 0 ─┐
flight_test.py  ─┼─► MAVSDK ◄── udp 14540+i ────────┼─► PX4 instance 1 ─┼─► Gazebo (baylands)
QGroundControl  ◄────────────── udp 14550 ──────────┘   ...             ─┘
view_cam.py ──► rclpy ◄── ROS 2 DDS ◄── ros_gz_bridge ◄── Gazebo /camera (gz_x500_depth)
```

---

## 7. Roadmap

### Multi-drone on the VM server
See `docker/README.md` (Future plan):
1. **Now:** `PX4_NUM_DRONES=N` in one container (up to 10 with the default port ranges).
2. **Next:** a swarm script (one MAVSDK `System()` per drone with `asyncio.gather`),
   per-drone ROS 2 topics via a single MicroXRCE-DDS agent, and the camera bridge on the VM.
3. **Later:** one container per drone plus a separate Gazebo server container, spread over several VMs.

### Real drone deployment
1. Point the scripts at the real drone with `MAVLINK_URL`, no code change needed:
   `MAVLINK_URL=serial:///dev/ttyUSB0:57600 python3 teleop_drone.py`
   (pass the device into the container with `devices: ["/dev/ttyUSB0"]` if you use Docker)
2. Remove `await drone.action.arm()` auto-arm and arm manually, for safety
3. Test in an open area with a spotter
