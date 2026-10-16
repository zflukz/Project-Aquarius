# Docker — Simulation & Control

Everything for running the PX4 + Gazebo simulator and the drone-control station lives here.

```
docker/
├── README.md                    this file
├── baylands_setup_guide.md      Baylands world: pre-cache + troubleshooting
├── telemetry_plan.md            telemetry list + plan (sensors, battery, camera → server)
├── bench_sim.sh                 performance test: native PX4/Gazebo vs Docker compose on the VM
├── .env.example                 copy to .env — REGISTRY, CONTROL_HOST, PX4_NUM_DRONES, ...
├── compose.local.yml            LOCAL TEST: all containers on your computer (+ build & push images)
├── compose.server.yml           VM SERVER: pull image only, no git / no build
├── compose.control.yml          CONTROL COMPUTER: drone-control only
├── px4-sim/
│   ├── Dockerfile               Ubuntu 24.04 + Gazebo Harmonic + PX4 (pre-built) + Baylands cache + VNC
│   └── entrypoint.sh            starts N PX4 instances, MAVLink links → CONTROL_HOST
└── drone-control/
    ├── Dockerfile               Ubuntu 24.04 + ROS 2 Jazzy + MAVSDK + Drone-Control/*.py
    ├── entrypoint.sh            sources ROS 2, runs $AUTO_RUN once (e.g. MCPP flight), then idles
    └── Dockerfile.dockerignore  only Drone-Control/ goes into the build context
```

Images (set by `REGISTRY` / `TAG` in `.env`):

| Image | Built from | Used by |
| :--- | :--- | :--- |
| `ghcr.io/zflukz/aquarius-px4-sim:latest` | `px4-sim/` | `compose.local.yml`, `compose.server.yml` |
| `ghcr.io/zflukz/aquarius-drone-control:latest` | `drone-control/` + `../Drone-Control/` | `compose.local.yml`, `compose.control.yml` |

## The three modes

```
 ① LOCAL TEST (your computer)           ② publish            ③ SPLIT
 compose.local.yml                       docker compose push   VM server               Control computer
 ┌──────────────────────────────┐       ─────────────────►    compose.server.yml      compose.control.yml
 │ px4-sim ─UDP─► drone-control │        ghcr.io registry      (pull only)             (pull or build)
 │ novnc :8080                  │       ◄─────────────────     px4-sim ──── UDP ────►  drone-control
 └──────────────────────────────┘          docker pull         novnc :8081             QGroundControl
```

Develop and test everything locally (①). When it works, push the images (②). The VM only pulls
and runs them (③).

---

## ① Local test — all containers on your computer

```bash
cd docker
cp .env.example .env                     # first time only; CONTROL_HOST is not needed locally

docker compose -f compose.local.yml up -d --build
docker compose -f compose.local.yml logs -f px4-sim      # wait for "Ready for takeoff!"
```

- Gazebo view: <http://localhost:8080/vnc.html>
- QGroundControl view: <http://localhost:8082/vnc.html> (`QGC=1`)
- Run the control scripts inside `drone-control`:

```bash
docker compose -f compose.local.yml exec drone-control \
  bash -c "source /opt/ros/jazzy/setup.bash && python3 flight_test.py"

docker compose -f compose.local.yml exec -e DRONE_ID=1 drone-control \
  bash -c "source /opt/ros/jazzy/setup.bash && python3 teleop_drone.py"
```

Here `CONTROL_HOST=drone-control` (Docker DNS), so MAVLink takes the same path as in the split setup.
That means a passing local test is a good check before you deploy to the VM.

After editing `Drone-Control/*.py`, rebuild only the control image:
`docker compose -f compose.local.yml up -d --build drone-control`.

Stop: `docker compose -f compose.local.yml down` (add `-v` to also drop the Fuel cache volume).

---

## ② Publish images (from your computer)

One-time login to GitHub Container Registry. Create a token at GitHub → Settings → Developer settings →
Personal access tokens (classic) with the `write:packages` scope:

```bash
echo <TOKEN> | docker login ghcr.io -u zflukz --password-stdin
```

Build and push. These are the same images you just tested locally:

```bash
cd docker
docker compose -f compose.local.yml build
docker compose -f compose.local.yml push px4-sim drone-control

# versioned release (keep latest too)
TAG=v0.2 docker compose -f compose.local.yml build
TAG=v0.2 docker compose -f compose.local.yml push px4-sim drone-control
```

> `aquarius-px4-sim` is large (PX4 build + Gazebo + Baylands, several GB). The first push takes a while;
> later pushes upload only the changed layers.
>
> New GHCR packages are **private**. Either make them public (GitHub → Packages → package →
> Settings → Change visibility), or `docker login ghcr.io` on the VM with a `read:packages` token.

---

## ③ Split — VM server (pull only) + control computer

### VM server — no git, no source code

The VM needs Docker plus **two files** in one folder: `compose.server.yml` and `.env`.

```bash
mkdir -p ~/aquarius && cd ~/aquarius

# get the compose file: copy it from your computer ...
#   scp docker/compose.server.yml docker/.env.example user@<VM_IP>:~/aquarius/
# ... or download it (public repo)
curl -fsSLO https://raw.githubusercontent.com/zflukz/Project-Aquarius/main/docker/compose.server.yml
curl -fsSL  https://raw.githubusercontent.com/zflukz/Project-Aquarius/main/docker/.env.example -o .env

nano .env        # CONTROL_HOST=<control computer IP>, PX4_NUM_DRONES, PX4_GZ_WORLD, HEADLESS

docker compose -f compose.server.yml pull
docker compose -f compose.server.yml up -d
docker compose -f compose.server.yml logs -f px4-sim
```

Ready when each drone logs:

```text
[drone0] INFO  [commander] Ready for takeoff!
drone0 (sysid 1) -> 192.168.1.50:14540 [MAVSDK], :14550 [QGC]
```

Update to a newer image:

```bash
docker compose -f compose.server.yml pull && docker compose -f compose.server.yml up -d
```

Firewall on the VM (only if `ufw` is enabled):

```bash
sudo ufw allow from <CONTROL_IP> to any port 14640:14649 proto udp
sudo ufw allow from <CONTROL_IP> to any port 18640:18649 proto udp
sudo ufw allow from <CONTROL_IP> to any port 8081 proto tcp     # noVNC Gazebo
sudo ufw allow from <CONTROL_IP> to any port 8082 proto tcp     # noVNC QGroundControl
```

> noVNC / x11vnc have **no password**. Keep ports 8081 / 8082 limited to your LAN / VPN.

### Control computer

```bash
cd docker
docker compose -f compose.control.yml up -d               # pulls the image (or add --build)

docker compose -f compose.control.yml exec drone-control \
  bash -c "source /opt/ros/jazzy/setup.bash && python3 flight_test.py"

docker compose -f compose.control.yml exec -e DRONE_ID=1 drone-control \
  bash -c "source /opt/ros/jazzy/setup.bash && python3 teleop_drone.py"
```

- Open `14540-14549/udp` and `14550/udp` (QGroundControl) for the VM IP.
- Gazebo view: `http://<VM_IP>:8081/vnc.html`, QGroundControl view: `http://<VM_IP>:8082/vnc.html`
- Without Docker the scripts work natively too: `DRONE_ID=1 python3 Drone-Control/teleop_drone.py`
- Don't run `compose.local.yml` at the same time (same container name and ports).

---

## MCPP coverage flight — auto path over the map

`Drone-Control/mcpp_mission.py` = Multi-robot Coverage Path Planning:

1. area (rectangle `AREA_W` × `AREA_H` m around drone 0's home) → grid of `2·SWEEP` m cells
2. balanced split: each drone grows a connected region from its start, smallest region grows next
3. per region: spanning tree (long straight lines preferred) → **Spanning Tree Coverage** path that
   visits every `SWEEP` m sub-cell exactly once (lawnmower-like, `SWEEP` = line spacing)
4. path → lat/lon waypoints → PX4 **mission** → takeoff, mission, RTL, land.
   Drone `i` flies at `ALT + 3·i` m so transit legs cannot collide.

Because it is a real mission, **QGroundControl (noVNC) shows every drone's planned path** on the map
and the progress live (QGC view: <http://localhost:8082/vnc.html>).

```bash
# by hand (drone-control already running)
docker compose -f compose.local.yml exec drone-control   bash -c "source /opt/ros/jazzy/setup.bash && python3 mcpp_mission.py"
docker compose -f compose.local.yml exec -e NUM_DRONES=2 drone-control   bash -c "source /opt/ros/jazzy/setup.bash && python3 mcpp_mission.py"
docker compose -f compose.local.yml exec drone-control python3 mcpp_mission.py --plan-only -n 3

# automatically on start: in .env
#   AUTO_RUN=python3 mcpp_mission.py
#   NUM_DRONES=2            # = PX4_NUM_DRONES
docker compose -f compose.local.yml up -d --force-recreate drone-control
docker logs -f drone-control
```

```text
MCPP grid: 6 x 8 cells of 10 m  (north up, digit = drone)
  1 1 1 1 0 0 0 0
  ...
  drone0: 24 cells, 26 waypoints, 475 m
  drone1: 24 cells, 24 waypoints, 475 m
```

| Env (drone-control) | Default | Meaning |
| :--- | :--- | :--- |
| `AUTO_RUN` | – | command run once at container start |
| `NUM_DRONES` | `1` | drones flown together (drone `i` on udp `14540+i`, from `DRONE_ID`) |
| `AREA_W` / `AREA_H` | `80` / `60` | area east-west / north-south, m |
| `AREA_N` / `AREA_E` | `0` / `0` | area centre offset from drone 0 home, m |
| `SWEEP` | `5` | distance between flight lines, m |
| `ALT` / `SPEED` | `20` / `5` | altitude m (drone `i`: `+3·i`), speed m/s |

> With `compose.control.yml` (`restart: unless-stopped`) `AUTO_RUN` flies again every time the
> container restarts — leave it empty there unless that is what you want.
> Keep the area inside the Baylands park; trees / buildings are not modelled as obstacles.

---

## Performance test — native vs Docker (`bench_sim.sh`)

Runs the same workload (N drones idle in one world, no GUI / QGC) natively and in Docker, one after
the other, and compares them. Run on the VM with nothing else running:

```bash
# on the VM: compose.server.yml + .env next to the script; native PX4 built in ~/PX4-Autopilot
#   (bash scripts/install_sim_server.sh — installs Gazebo, builds PX4, caches Baylands)
chmod +x bench_sim.sh
./bench_sim.sh both                         # native, then docker -> table
DRONES=3 DURATION=120 ./bench_sim.sh both   # heavier load shows the difference better
./bench_sim.sh docker                       # only one mode
COMPOSE_FILE=compose.local.yml ./bench_sim.sh docker   # on your PC
```

```text
mode    drones  boot_s  ready_s     rtf    cpu_%    rss_mb  px4
native       1   17.70    17.72   1.006    76.83    731.12  9a1429ae
docker       1   40.34    40.71   1.018    79.88    690.70  9a1429ae
```

| Column | Meaning | Better |
| :--- | :--- | :--- |
| `boot_s` | start → all PX4 finished rcS | lower |
| `ready_s` | start → all have a valid home position (GPS + EKF) | lower |
| `rtf` | Gazebo real-time factor over `DURATION` s (1.0 = real time) | **higher, the main number** |
| `cpu_%` | CPU of px4 + gz processes (100 = 1 core) | lower |
| `rss_mb` | their memory | lower |

- Every run is appended to `bench_results.csv`. Repeat 3× and compare averages; with `DRONES` > 1 the
  RTF drops first where the overhead is.
- Same PX4 version in both? Check the `px4` column (`git describe`); a different PX4 commit is not a fair test.
- Docker `boot_s` includes container start, Xvfb / VNC and Baylands cache check.
- On Linux both modes are measured from `/proc`. On Docker Desktop (Windows / macOS) Docker mode falls
  back to `docker stats` (whole container), so only compare both modes on the Linux VM.
- Env: `DRONES` `WORLD` `MODEL` `AUTOSTART` `WARMUP` (20) `DURATION` (60) `HEADLESS` (1, empty = GUI)
  `PX4_DIR` (`~/PX4-Autopilot`) `COMPOSE_FILE` (`compose.server.yml`) `RESULTS`.

## MAVLink port map

Drone `i` = PX4 instance `i` = MAVLink system id `i+1`.

| Link | PX4 listens (sim) | PX4 sends to (control) | Used by |
| :--- | :--- | :--- | :--- |
| Offboard / API | `14640 + i` | `CONTROL_HOST : 14540 + i` | MAVSDK scripts (`DRONE_ID=i`) |
| GCS | `18640 + i` | `CONTROL_HOST : 14550` | QGroundControl (all drones on one port) |

PX4 opens both links itself (`mavlink start ... -t CONTROL_HOST` in `px4-sim/entrypoint.sh`).
The control side only **listens** (`udpin://0.0.0.0:14540+i`), so it never needs the VM IP.

---

## Containers

### px4-sim
- **Build time:** `make px4_sitl_default` and `gz fuel download` of **Baylands**
  (see `baylands_setup_guide.md` §3). Starting a container therefore needs no compile and no download.
- **Start (`entrypoint.sh`):**
  1. kill stale `px4` / `gz sim` / QGC / Xvfb processes, remove `/tmp/px4_lock-*`, X locks (`/tmp/.X99-lock`) and QGC lock (guide issue A; locks survive `docker restart`)
  2. if `PX4_GZ_WORLD=baylands` and the Fuel cache is empty → download it (timeout 15 min)
  3. `Xvfb :99` → `fluxbox` → `x11vnc :5900` (Gazebo); with `QGC=1` also `Xvfb :100` → `x11vnc :5901` (QGC)
  4. for `i` in `0..PX4_NUM_DRONES-1`: `px4 -i i -d` with `PX4_GZ_MODEL_POSE="0,i*DRONE_SPACING"`.
     Instance 0 starts the Gazebo server + GUI; the others join the same world.
  5. per instance: add the MAVSDK + QGC links towards `CONTROL_HOST`
  6. if any PX4 instance exits → stop everything → Docker restarts the container
- Logs: `docker logs px4-sim` (prefixed `[droneN]`) and `/var/log/px4/px4-N.log` in the container
- Volume `gz-fuel-cache` → `/root/.gz/fuel`
- PX4 shell of drone 1: `docker exec -it px4-sim /px4/build/px4_sitl_default/bin/px4-listener --instance 1 vehicle_status` (any `px4-<module> --instance N ...` works)

| Env | Default | Meaning |
| :--- | :--- | :--- |
| `CONTROL_HOST` | – | control computer IP / hostname (resolved once at start) |
| `PX4_NUM_DRONES` | `1` | number of PX4 instances |
| `PX4_GZ_WORLD` | `baylands` | Gazebo world (`default` for a light, flat world) |
| `PX4_SIM_MODEL` / `PX4_SYS_AUTOSTART` | `gz_x500` / `4001` | vehicle (`gz_x500_depth` / `4002` for camera) |
| `DRONE_SPACING` | `3` | metres between spawned drones |
| `HEADLESS` | – | `1` = no Gazebo GUI |
| `QGC` | `1` | `1` = QGroundControl runs next to Gazebo in noVNC, `0` = off |
| `SCREEN_SIZE` | `1920x1080x24` | Xvfb screen seen in noVNC |
| `VNC_LISTEN` | `0.0.0.0` | x11vnc bind (`127.0.0.1` on the VM) |
| `GZ_FOLLOW` | `0` | Gazebo camera follows drone N (same place as QGC's map); empty = free camera |
| `PX4_GZ_FOLLOW_OFFSET_X/Y/Z` | `-8` / `-8` / `5` | camera offset from that drone, m (east / north / up) |

> Gazebo and QGC show the same place: the drone's GPS comes from the world's `<spherical_coordinates>`
> (Baylands: 37.4122, -121.9989), so QGC's map = Gazebo's world. Don't set PX4's `PX4_HOME_LAT/LON/ALT`
> with Baylands: it moves the GPS origin but not the 3D park, so QGC's satellite map and Gazebo would differ.

Changing these needs no rebuild. Edit `.env`, then
`docker compose -f <file> up -d --force-recreate px4-sim`.

### novnc / novnc-qgc — two browser views
- Image `dougw/novnc`: bridges VNC → WebSocket/HTTP. Two instances, one per screen:

| Service | Screen | VNC | Local | VM (host network) |
| :--- | :--- | :--- | :--- | :--- |
| `novnc` | Gazebo `:99` | `5900` | <http://localhost:8080/vnc.html> | `http://<VM_IP>:8081/vnc.html` |
| `novnc-qgc` | QGroundControl `:100` | `5901` | <http://localhost:8082/vnc.html> | `http://<VM_IP>:8082/vnc.html` |

- Gazebo has no web viewer of its own. It renders into Xvfb, x11vnc streams that, and noVNC serves it to the browser.
- The image always listens on 8081; on the VM (host network) `novnc-qgc` overrides its command to listen on 8082.
- With `QGC=1` QGroundControl (extracted AppImage in `/opt/qgc`, run as user `qgc` because QGC
  refuses root) opens maximized on its own screen `:100`. It connects by itself through PX4's default GCS link
  (`18570+i` → `localhost:14550`), so every drone shows up — no QGC setup needed. Log:
  `/var/log/px4/qgc.log`. If you close its window, restart it with
  `docker compose -f <file> restart px4-sim`. The "not running latest stable firmware" popup is
  normal for SITL (reports version 0.0.0).

### drone-control
- Ubuntu 24.04 + ROS 2 Jazzy + MAVSDK, with `Drone-Control/` copied in at build time
- Idles (`sleep infinity`) as a control station; run scripts with `docker compose exec`
- `AUTO_RUN` (optional): command run once when the container starts, before idling — e.g.
  `AUTO_RUN=python3 mcpp_mission.py`. Output: `docker logs -f drone-control`.
- `DRONE_ID=N` → `udpin://0.0.0.0:14540+N`; `MAVLINK_URL` overrides the address

---

## Future plan — many drones on the VM server

### Phase 1 — now: N drones in one container
`PX4_NUM_DRONES=N` in `.env`. All PX4 instances + one Gazebo server live in `px4-sim`.
Port ranges are sized for **10 drones** (`14540-14549`, `14640-14649`, `18640-18649`).
Widen them in the compose files and firewall for more.

Rough VM sizing (software rendering, no GPU):

| Drones | World | vCPU | RAM | Notes |
| :--- | :--- | :--- | :--- | :--- |
| 1–2 | baylands | 4 | 8 GB | GUI ok |
| 3–5 | baylands | 8 | 16 GB | consider `HEADLESS=1` |
| 5–10 | default | 8–16 | 16–32 GB | `HEADLESS=1`, watch real-time factor |

Check the Gazebo real-time factor: `docker exec px4-sim gz topic -e -t /world/baylands/stats -n 1` (use your world name).
If it stays well below 1.0, run fewer drones, switch to `PX4_GZ_WORLD=default`, or add CPU.

### Phase 2 — swarm control from the control computer
- One script for several drones: one MAVSDK `System()` per drone on `udpin://0.0.0.0:14540+i`,
  run together with `asyncio.gather(...)` (e.g. `swarm_test.py`).
- QGroundControl already sees every drone on `:14550`.
- ROS 2 per drone: one `MicroXRCEAgent udp4 -p 8888` on the VM. Every PX4 instance publishes under
  its own namespace (drone 0: `/fmu/...`, drone i: `/uav_i/fmu/...`). Use the same `ROS_DOMAIN_ID` on
  both machines. Details: `telemetry_plan.md`.
- Camera: add `ros_gz_bridge` to the VM side (it has to run where Gazebo runs) and consume the ROS 2
  image topic with `view_cam.py` on the control computer.
- CI: a GitHub Actions job that builds and pushes both images on every push to `main`, so the VM
  only needs `pull`.

### Telemetry & camera plan

All sensors / battery / state + the drone camera to the server: **[`telemetry_plan.md`](telemetry_plan.md)**
(full list of telemetry available in Gazebo / PX4 / MAVLink / MAVSDK, steps T1–T5, camera C1–C4).

### Phase 3 — scale out (one container per drone / several VMs)
```
VM server
├── gz-server     Gazebo world only                       GZ_PARTITION=aquarius
├── px4-0         px4 -i 0, PX4_GZ_STANDALONE=1, CONTROL_HOST=...
├── px4-1         px4 -i 1, ...
└── px4-N         (one service per drone, same aquarius-px4-sim image with ROLE env)
```
- Split `entrypoint.sh` into `ROLE=gz-server` and `ROLE=px4-instance`.
- PX4 instances find Gazebo through Gazebo transport (`GZ_PARTITION`, plus `GZ_IP` for multi-host),
  so they can later run on **several VMs** sharing one world.
- Health check per drone (`px4-mavlink --instance i status`), so one drone can restart without
  touching the others.
- Move to Kubernetes / Nomad only if the drone or VM count keeps growing.
