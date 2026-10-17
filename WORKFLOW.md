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
      ├──► MCPP Coverage Flight   (mcpp_mission.py, NUM_DRONES=N — auto path over the map)
      ├──► QGroundControl         (all drones on :14550)
      ├──► Telemetry + camera     (VM telemetry container: browser :8083, Foxglove :8765, ROS 2 topics)
      └──► Camera window          (view_cam.py)
            │
            ▼
      Multi-drone / Swarm  →  Real Drone Deployment
```

Everything Docker / simulator related lives in **`docker/`**:
**`docker/README.md`** covers every container, port, env var and the multi-drone plan, and
**`docker/baylands_setup_guide.md`** covers Baylands world caching and troubleshooting.

## 0. Quick start — `aquarius` command

`scripts/aquarius` wraps the steps below into one command (Docker or native, detected automatically).
Full guide with every option and troubleshooting: **[`AQUARIUS_CLI.md`](AQUARIUS_CLI.md)**.

```bash
bash scripts/aquarius install                 # once: ~/.local/bin/aquarius (then just "aquarius ...")

aquarius init                                 # local test: start sim + views + telemetry, wait for the drones
aquarius init --drones 2 --camera             # options are saved in docker/.env
aquarius init server --control-host <IP>      # VM (pull + up)      aquarius init control   # control computer

aquarius start mission 37.4125,-121.9985 37.4128,-121.9992        # fly waypoints (LAT,LON) -> RTL
aquarius start mission "{37.4125,-121.9985}" --alt 30 --drone 1   # options: --alt --speed --drone --detach
aquarius start coverage 37.4126,-121.9990 --size 40x30 --drones 2 # MCPP coverage centred on that point

aquarius status          # containers + armed / mode / battery / position / GPS
aquarius rtl | land      # return to launch / land now (stops a running mission script first)
aquarius logs mission    # follow again (also: coverage, px4, telemetry)
aquarius urls | down
```

- The mission is uploaded to PX4 (QGC shows it). Then: takeoff to `--alt` (default 20 m), waypoints in order,
  RTL, land. Points further than 1000 m from home are refused (`MAX_DIST`).
- It runs in the background inside drone-control, so **Ctrl+C only stops following the log**. The drone keeps
  flying until `aquarius rtl` / `land`.
- Baylands home is about `37.4122,-121.9989`; 0.0001° lat ≈ 11 m.

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
# Gazebo view: http://localhost:8080/vnc.html   QGroundControl view: http://localhost:8082/vnc.html
# Drone camera: http://localhost:8083/           all telemetry: Foxglove ws://localhost:8765   (section 4)
```

`px4-sim`, `novnc`, `telemetry` and `drone-control` all run on your computer. MAVLink takes the same path as in
the split setup, so a passing local test is a good check before you deploy.

### 1b. Publish images (from your computer)

```bash
echo <TOKEN> | docker login ghcr.io -u zflukz --password-stdin   # once, token with write:packages
cd docker
docker compose -f compose.local.yml build
docker compose -f compose.local.yml push px4-sim telemetry drone-control
```

Images: `ghcr.io/zflukz/aquarius-px4-sim`, `ghcr.io/zflukz/aquarius-telemetry` and `ghcr.io/zflukz/aquarius-drone-control`
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

Firewall: the VM allows `14640-14649/udp`, `18640-18649/udp`, `8081-8083/tcp` and `8765/tcp` from the control computer.
The control computer allows `14540-14550/udp` from the VM. Gazebo view: `http://<VM_IP>:8081/vnc.html`, QGroundControl view: `http://<VM_IP>:8082/vnc.html`,
drone camera: `http://<VM_IP>:8083/`, Foxglove: `ws://<VM_IP>:8765` (section 4).

### 1d. Native install — no Docker

Same software and the same MAVLink ports as the Docker images (Ubuntu 24.04, Gazebo Harmonic, PX4 `main`,
ROS 2 Jazzy). Docker stays the main path; native is for running without Docker and for comparing speed.

```bash
# VM server: Gazebo Harmonic (apt) + PX4 built + Baylands cache (~30-60 min)
bash scripts/install_sim_server.sh
CONTROL_HOST=<CONTROL_COMPUTER_IP> PX4_NUM_DRONES=1 HEADLESS=1 bash scripts/run_sim_native.sh   # Ctrl+C stops

# optional: telemetry topics + camera view (ROS 2 Jazzy, XRCE agent, px4_msgs) — second terminal
bash scripts/install_telemetry_server.sh
PX4_NUM_DRONES=1 bash scripts/run_telemetry_native.sh

# control computer: ROS 2 (Jazzy on 24.04, Humble on 22.04) + mavsdk-grpc in Drone-Control/.venv
bash scripts/install_control_computer.sh
source /opt/ros/jazzy/setup.bash && source Drone-Control/.venv/bin/activate
cd Drone-Control && python3 flight_test.py          # DRONE_ID / NUM_DRONES as in Docker

# native vs Docker speed on the VM
bash docker/bench_sim.sh both
```

Remove again: `scripts/uninstall_sim_server.sh [--purge-build-deps]`, `scripts/uninstall_control_computer.sh`.

---

## 2. Start the Simulated Drones

With Docker the sim starts automatically with `up -d`. Configure it in `.env`
(`docker/.env` locally, `~/aquarius/.env` on the VM):

| `.env` | Example | Effect |
| :--- | :--- | :--- |
| `PX4_NUM_DRONES` | `3` | 3 drones, 3 m apart, MAV_SYS_ID 1..3 |
| `PX4_GZ_WORLD` | `baylands` / `default` | 3D terrain / light flat world |
| `PX4_SIM_MODEL` + `PX4_SYS_AUTOSTART` | `gz_x500_mono_cam_down` + `4014` | drone with a downward camera (section 4) |
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

### 3a+. MCPP coverage flight (auto path over the map)

`mcpp_mission.py` plans a coverage path (MCPP: balanced area split + Spanning Tree Coverage
per drone), uploads it to PX4 as a **mission** (so QGroundControl draws it on the map) and flies
it: takeoff → lawnmower path over the whole area → RTL → land.

```bash
$DC drone-control bash -c "$ROS && python3 mcpp_mission.py"                    # drone 0
$DC -e NUM_DRONES=2 drone-control bash -c "$ROS && python3 mcpp_mission.py"    # drones 0+1 share the area
$DC drone-control python3 mcpp_mission.py --plan-only -n 3                     # just print the plan
```

Or let it start by itself with the container: `AUTO_RUN=python3 mcpp_mission.py` in `docker/.env`
(see `docker/README.md` → "MCPP coverage flight"). Area / spacing / altitude: `AREA_W`, `AREA_H`,
`AREA_N`, `AREA_E`, `SWEEP`, `ALT`, `SPEED` (env) or `--help`.
Watch it: Gazebo <http://localhost:8080/vnc.html>, QGroundControl <http://localhost:8082/vnc.html>
(VM: ports 8081 / 8082).

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

## 4. Telemetry & Camera View (server side)

The **`telemetry`** container runs next to px4-sim (in `compose.server.yml` and `compose.local.yml`,
started by `up -d`). It turns every drone's data into ROS 2 topics and serves the drone camera to the
browser. Details and the full topic list: `docker/telemetry_plan.md` §4.

**1. Put a camera on the drones** (default `gz_x500` has none). In `.env`:

```bash
PX4_SIM_MODEL=gz_x500_mono_cam_down     # camera looking down (coverage / mapping)
PX4_SYS_AUTOSTART=4014                  # forward: gz_x500_mono_cam + 4010, gimbal: gz_x500_gimbal + 4019
```

```bash
docker compose -f compose.server.yml up -d --force-recreate px4-sim telemetry    # VM
docker compose -f compose.local.yml  up -d --force-recreate px4-sim telemetry    # local test (in docker/)
```

The camera is set to 640x480 @ 10 Hz (`CAMERA_WIDTH/HEIGHT/RATE` in `.env`) — bigger images without a GPU
slow the whole simulation down (RTF 0.03 at the model's default 1280x960 @ 30 Hz).

**2. Watch / read it:**

| What | Local test | VM server |
| :--- | :--- | :--- |
| Drone camera in the browser | <http://localhost:8083/> | `http://<VM_IP>:8083/` |
| One camera stream | `http://localhost:8083/stream?topic=/uav_0/camera/image_raw` | same with `<VM_IP>` |
| All telemetry (plots, map, images) | [Foxglove](https://app.foxglove.dev) → Open connection → `ws://localhost:8765` | `ws://<VM_IP>:8765` |
| Topic list / values | `docker exec telemetry with-ros ros2 topic list` | same, on the VM |

Main topics (drone `i`): `/fmu/out/*` (drone 0) and `/uav_i/fmu/out/*` — PX4 battery, GPS, attitude,
odometry, vehicle status (versioned names like `vehicle_status_v4`); `/uav_i/gz/{imu,navsat,air_pressure,magnetometer}`
— raw Gazebo sensors; `/uav_i/gz/ground_truth` — true pose of each drone; `/uav_i/camera/image_raw`.

```bash
docker exec telemetry with-ros ros2 topic echo --once /fmu/out/battery_status_v1
docker exec telemetry with-ros ros2 topic hz /uav_0/camera/image_raw
```

**3. Record an experiment:** `RECORD=1` in `.env`, then `up -d --force-recreate telemetry` → `ros2 bag`
(mcap) in `docker/data/bags/` (VM: `~/aquarius/data/bags/`). Open it in Foxglove or `ros2 bag play`.

**4. Other ways to the camera:**

- QGroundControl video: PX4 streams RTP/H.264 to `PX4_VIDEO_HOST_IP:5600+i` (default `127.0.0.1` = QGC
  in noVNC; set the control computer IP to view it there). QGC → Application Settings → Video → UDP h.264, port 5600.
- `view_cam.py` (OpenCV window) subscribes `/uav_<DRONE_ID>/camera/image_raw` (or `CAMERA_TOPIC`). It works
  on the VM or on a computer that sees the VM's ROS 2 topics (same LAN + `ROS_DOMAIN_ID`, DDS multicast).
  Across networks use the browser / Foxglove views above.

> Native (no Docker): `bash scripts/install_telemetry_server.sh` once, then `bash scripts/run_telemetry_native.sh`
> next to `run_sim_native.sh` (same env) — same topics and ports.

---

## 5. File Reference

```
Project-Aquarius/
├── docker/                         everything Docker / simulator
│   ├── README.md                   containers, port map, publish images, multi-drone roadmap
│   ├── baylands_setup_guide.md     Baylands world: pre-cache + troubleshooting
│   ├── .env.example                REGISTRY, CONTROL_HOST, PX4_NUM_DRONES, PX4_GZ_WORLD, HEADLESS, ...
│   ├── compose.local.yml           local test: all containers on your computer (+ build & push)
│   ├── compose.server.yml          VM server: pull image only (px4-sim, noVNC, telemetry)
│   ├── telemetry_plan.md           telemetry / camera: what exists, plan, telemetry service (§4)
│   ├── compose.control.yml         control computer: drone-control
│   ├── px4-sim/
│   │   ├── Dockerfile              Gazebo Harmonic + PX4 (pre-built) + Baylands cache + VNC
│   │   └── entrypoint.sh           starts N PX4 instances, MAVLink links → CONTROL_HOST
│   ├── telemetry/
│   │   ├── Dockerfile              ROS 2 Jazzy + XRCE agent + px4_msgs + ros_gz_bridge + web_video / foxglove
│   │   └── entrypoint.sh           PX4 + Gazebo → ROS 2 topics, camera :8083, Foxglove :8765, ros2 bag
│   └── drone-control/
│       ├── Dockerfile              ROS 2 Jazzy + MAVSDK + Drone-Control/
│       ├── entrypoint.sh           sources ROS 2, runs $AUTO_RUN once (e.g. mcpp_mission.py)
│       └── Dockerfile.dockerignore
├── scripts/
│   ├── aquarius                    CLI: init / start mission LAT,LON / start coverage / status / rtl / land
│   ├── install_sim_server.sh       native VM server install (Gazebo apt + PX4 built + Baylands)
│   ├── run_sim_native.sh           native twin of px4-sim/entrypoint.sh (N drones, same ports)
│   ├── install_telemetry_server.sh native telemetry stack (ROS 2 + XRCE agent + px4_msgs from PX4)
│   ├── run_telemetry_native.sh     native twin of the telemetry container
│   ├── install_control_computer.sh native control computer install (ROS 2 + mavsdk-grpc venv)
│   └── uninstall_*.sh              remove the native installs
└── Drone-Control/
    ├── teleop_drone.py             keyboard controller → PX4 via MAVSDK (DRONE_ID)
    ├── flight_test.py              automated flight test (arm/takeoff/move/land, DRONE_ID)
    ├── mcpp_mission.py             MCPP coverage path → PX4 mission, 1..N drones (NUM_DRONES, --center LAT,LON)
    ├── mission_cli.py              goto LAT,LON ... / status / rtl / land (used by scripts/aquarius)
    └── view_cam.py                 ROS 2 subscriber (/uav_<DRONE_ID>/camera/image_raw) → OpenCV window
```

---

## 6. Component Dependency Map

```
Control computer                                    VM server
teleop_drone.py ─┐                                  ┌─► PX4 instance 0 ─┐
flight_test.py  ─┤                                  │
mcpp_mission.py ─┼─► MAVSDK ◄── udp 14540+i ────────┼─► PX4 instance 1 ─┼─► Gazebo (baylands)
QGroundControl  ◄────────────── udp 14550 ──────────┘   ...             ─┘
browser :8083 / Foxglove :8765 ◄── telemetry ◄── XRCE agent ◄── PX4 uORB   (VM, same network as px4-sim)
view_cam.py ──► ROS 2 DDS  ◄──────┘          ◄── ros_gz_bridge ◄── Gazebo sensors + camera (gz_x500_mono_cam_down)
```

---

## 7. Roadmap

### Multi-drone on the VM server
See `docker/README.md` (Future plan):
1. **Now:** `PX4_NUM_DRONES=N` in one container (up to 10 with the default port ranges).
2. **Next:** a swarm script (one MAVSDK `System()` per drone with `asyncio.gather`),
   DDS between VM and control computer (telemetry topics + camera are on the VM since 2026-10-17).
3. **Later:** one container per drone plus a separate Gazebo server container, spread over several VMs.

### Real drone deployment
1. Point the scripts at the real drone with `MAVLINK_URL`, no code change needed:
   `MAVLINK_URL=serial:///dev/ttyUSB0:57600 python3 teleop_drone.py`
   (pass the device into the container with `devices: ["/dev/ttyUSB0"]` if you use Docker)
2. Remove `await drone.action.arm()` auto-arm and arm manually, for safety
3. Test in an open area with a spotter
