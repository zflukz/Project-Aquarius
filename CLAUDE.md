# CLAUDE.md — Project Aquarius

Guidance for Claude Code (and humans) working in this repo, plus a record of the Docker /
simulation rework and how each part was verified.

## Project

Drone project (KMUTT senior project, "Xenoir"): PX4 SITL + Gazebo Harmonic simulation, controlled
with MAVSDK / ROS 2 scripts. Goal: multi-drone (swarm) simulation on a VM server, controlled from
a separate computer, later real drones.

| Path | What |
| :--- | :--- |
| `docker/` | **everything Docker / simulator** — see `docker/README.md`; telemetry + camera plan: `docker/telemetry_plan.md`; MAVLink → MQTT → Grafana (+ Kafka later) plan: `docker/communication_plan.md` |
| `Drone-Control/` | `flight_test.py`, `teleop_drone.py`, `mcpp_mission.py`, `mission_cli.py` (MAVSDK), `view_cam.py` (ROS 2 camera) |
| `WORKFLOW.md` | end-to-end user workflow (setup → fly → camera → roadmap) |
| `AQUARIUS_CLI.md` | user guide for the `aquarius` command (`scripts/aquarius`): every command, option, example, troubleshooting |
| `scripts/` | `aquarius` CLI (init / start mission LAT,LON / coverage / status / rtl / land); **native** (non-Docker) install / run / uninstall — same software + ports as the images |
| `Document/` | LaTeX proposal (Thai, XeLaTeX, devcontainer in `.devcontainer/`) — unrelated to sim |
| `Robomaster-TT/`, `InfoData/` | side experiments / Obsidian notes |

## Conventions

- Docker / simulator files go in `docker/`, nowhere else. Compose files are `docker/compose.*.yml`;
  their `.env` is `docker/.env` (gitignored, template `docker/.env.example`).
- `*.sh` must stay LF (`.gitattributes`); the machine uses `core.autocrlf=true`.
- `teleop_drone.py` has Thai comments/messages — keep that style in that file.
- Python MAVSDK package is **`mavsdk-grpc`** (`import mavsdk_grpc`). Plain `mavsdk` on PyPI is now a
  different native API — don't "fix" imports to `mavsdk`.
- Scripts pick the drone via env: `DRONE_ID` (→ `udpin://0.0.0.0:14540+DRONE_ID`) or `MAVLINK_URL`.
- Don't commit without being asked. Branch: `feature-Network-flukez`, main branch: `main`.

## Architecture (Docker)

Three modes, same three images (`ghcr.io/zflukz/aquarius-px4-sim`, `…/aquarius-telemetry`, `…/aquarius-drone-control`):

| Compose file | Runs on | Notes |
| :--- | :--- | :--- |
| `docker/compose.local.yml` | your computer | **local test**: px4-sim + novnc (8080 Gazebo) + novnc-qgc (8082 QGC) + telemetry (8083 / 8765, in px4-sim's netns) + drone-control on one bridge network; also builds & pushes images |
| `docker/compose.server.yml` | VM server | **pull-only** (no build, no git): px4-sim + novnc (8081) + novnc-qgc (8082) + telemetry (camera 8083, Foxglove 8765), `network_mode: host` |
| `docker/compose.control.yml` | control computer | drone-control, publishes udp 14540-14549 |

MAVLink (drone `i` = PX4 instance `i` = sysid `i+1`), opened by `docker/px4-sim/entrypoint.sh`:

| Link | PX4 binds (sim side) | PX4 sends to |
| :--- | :--- | :--- |
| MAVSDK / offboard | `14640+i` | `CONTROL_HOST:14540+i` |
| QGroundControl | `18640+i` | `CONTROL_HOST:14550` |

PX4's own default links (14580+i → localhost:14540+i, 18570+i, …) still exist; ours are extra.

`entrypoint.sh` flow: kill stale px4/gz/QGC/Xvfb + `/tmp/px4_lock-*`, X + QGC locks → ensure Baylands Fuel cache →
Xvfb/fluxbox/x11vnc per screen (`:99`→5900 Gazebo, `:100`→5901 QGC) → for each drone `px4 -i i -d` with `PX4_GZ_MODEL_POSE="0,i*DRONE_SPACING"`,
wait until booted, add the two `mavlink start ... -t CONTROL_IP` links → `wait -n`, exit if any
PX4 dies (Docker restarts the container).

## Change log — Docker / simulation rework (2026-10-13)

1. **Fixed MAVLink connection.** Before: scripts used `udpout://px4-sim:14540`, but PX4 *listens* on
   14580+i and *sends* to 14540+i, so nothing listened on the target port. Now: PX4 opens a link to
   `CONTROL_HOST` (`-t`), scripts listen with `udpin://0.0.0.0:14540+DRONE_ID`. Works the same for
   local, split and multi-drone.
2. **Baylands world** (from `docker/baylands_setup_guide.md`): pre-cached with `gz fuel download` at
   image build, re-downloaded at start if missing, `gz-fuel-cache` volume, `PX4_GZ_WORLD=baylands` default.
3. **PX4 built in the image** (`make px4_sitl_default` in Dockerfile) instead of on every container start.
4. **Multi-drone**: `PX4_NUM_DRONES` instances in one Gazebo world.
5. **Split VM / computer**: VM pulls images only; compose files + docs moved into `docker/`;
   `docker-composeflow.md` → `docker/README.md`; root `docker-compose.yml` / `.env` / `.env.example` /
   `.dockerignore` removed (now `docker/compose.*.yml`, `docker/.env*`,
   `docker/drone-control/Dockerfile.dockerignore`). px4-sim build context is `docker/px4-sim/`.
6. `drone-control` idles (`sleep infinity`); scripts run via `docker compose exec`.
7. Docs: `WORKFLOW.md` rewritten (local test → publish → split), `docker/README.md` incl. future
   multi-drone plan, Baylands guide paths fixed (`px4-sim` container, `/px4`).
8. `.gitattributes` (`*.sh eol=lf`).
9. `scripts/install_control_computer.sh`: `pip install mavsdk` → `mavsdk-grpc`.

## Change log — QGC split + MCPP coverage flight (2026-10-14)

1. **Two viewers:** QGC runs on its own Xvfb `:100` → x11vnc 5901 → `novnc-qgc` (local 8082, VM 8082 via
   command override because `dougw/novnc` hard-codes 8081). Gazebo stays on `:99` → 5900 → `novnc`.
   fluxbox `~/.fluxbox/apps` opens both maximized.
2. **`Drone-Control/mcpp_mission.py`:** MCPP = balanced region-growing partition of a coarse grid
   (2·SWEEP cells) + Kruskal spanning tree per drone + Spanning Tree Coverage (Hamiltonian cycle over
   SWEEP sub-cells) → lat/lon → PX4 mission (QGC shows it) → takeoff, mission, explicit RTL. N drones
   (`NUM_DRONES`, one `System(port=50051+i)` each), altitude `ALT + 3·i`. `--plan-only` prints the plan.
3. **drone-control entrypoint** (`docker/drone-control/entrypoint.sh`): sources ROS 2, runs `AUTO_RUN`
   once (e.g. `python3 mcpp_mission.py`), then CMD. MCPP env in compose files / `.env.example`.

## Change log — Gazebo camera follow + restart fixes + telemetry plan (2026-10-16)

Bug reported: drone moves in QGC but the Gazebo view does not. Causes found in the running sim:

1. **Camera never followed the drone.** PX4's `px4-rc.gzsim` publishes `/gui/track` once; in gz-gui 8.4
   the GUI ended up with an empty `follow_target` (`/gui/currently_tracked`), camera stayed at its start
   pose (-6,0,6) → drone flew out of view. `/gui/follow` service works, but a call before the GUI loaded
   the model is accepted and silently ignored. Now `entrypoint.sh` `follow_camera` (background) calls
   `/gui/follow` + `/gui/follow/offset` every 5 s until `/gui/camera/pose` actually changes.
   Env `GZ_FOLLOW` (drone index, default 0, empty = free camera), offset `PX4_GZ_FOLLOW_OFFSET_X/Y/Z`
   (default -8/-8/5). Compose files + `.env.example` + README updated.
2. **`docker restart` broke the screens:** stale `/tmp/.X99-lock` / `.X100-lock` → Xvfb "Server is
   already active" → no Gazebo GUI / VNC; stale `/tmp/qgc-*.lock` → "A second instance of
   QGroundControl is already running". Entrypoint now kills Xvfb/x11vnc/fluxbox and removes the locks.
3. **Same place in QGC and Gazebo:** already true — GPS comes from the world's `spherical_coordinates`
   (Baylands 37.4122, -121.9989); drone reported 37.412215, -121.999020, QGC map = Baylands Park.
   Don't set `PX4_HOME_*` with Baylands (moves GPS, not the 3D park).
4. **Telemetry + camera plan** → `docker/telemetry_plan.md` (summary below, "Telemetry & camera").

Verified 2026-10-16 (entrypoint `docker cp`'d + `docker restart`; then `px4-sim` rebuilt — only the
entrypoint layers, rest cached — and `up -d --force-recreate` → `Gazebo camera follows x500_0`; image
**not pushed** to GHCR yet): no X / QGC lock errors after restart, log `Gazebo camera follows x500_0` ~20 s after boot,
MCPP flight (`AREA_W=40 AREA_H=30`) screenshots of `:99` show the camera tracking the drone across the park;
QGC on `:100` connected, Baylands map.

## Telemetry & camera (plan, 2026-10-16) — details in `docker/telemetry_plan.md`

Listed from the running sim (`gz_x500`, Baylands). Four sources, from raw to high level:

| Source | Where / how | Status | Main content |
| :--- | :--- | :--- | :--- |
| A. Gazebo gz-transport | inside px4-sim, `gz topic`, Python `gz.transport13` | ✅ available | IMU 250 Hz, mag 100 Hz, baro 50 Hz, navsat 30 Hz; ground truth `/world/<w>/dynamic_pose/info`; clock, stats (RTF), wind; cameras/lidar/flow only on other models |
| B. PX4 uORB → ROS 2 | `uxrce_dds_client` already runs per instance → 127.0.0.1:8888, ns none / `uav_i` | ❌ needs `MicroXRCEAgent` (px4-sim) + `px4_msgs` (drone-control) | 31 `/fmu/out/*`: `battery_status`, `sensor_combined`, `vehicle_gnss`, `vehicle_global/local_position`, `vehicle_attitude`, `vehicle_odometry`, `vehicle_status`, `failsafe_flags`, ... |
| C. MAVLink | 14540+i (MAVSDK), 14550 (QGC) | ✅ works | `SYS_STATUS`, `BATTERY_STATUS`, `GPS_RAW_INT`, `HIGHRES_IMU`, `SCALED_PRESSURE`, `ATTITUDE*`, `GLOBAL_POSITION_INT`, `ODOMETRY`, `VIBRATION`, `ESTIMATOR_STATUS`, `ESC_*`, ... |
| D. MAVSDK telemetry | drone-control Python | ✅ works | 33 streams: `battery`, `health`, `position`, `gps_info`, `raw_gps`, `imu`, `scaled_pressure`, `attitude_*`, `velocity_ned`, `odometry`, `ground_truth`, `flight_mode`, `status_text`, `wind`, ... |

Battery in SITL = PX4 `battery_simulator` (`SIM_BAT_DRAIN`, `SIM_BAT_MIN_PCT`).

Telemetry steps: **T1** `telemetry_bridge.py` (MAVSDK → JSON lines → server) · **T5** ground truth from
`dynamic_pose/info` · **T4** telemetry-server (time-series DB + Grafana / WebSocket, `ros2 bag`) ·
**T2** XRCE agent + `px4_msgs` · **T3** DDS discovery server VM ↔ control.

Camera: default `gz_x500` has **no camera**. Step 0: `gz_x500_mono_cam_down` (4014) / `gz_x500_mono_cam`
(4010) / `gz_x500_depth` (4002) / `gz_x500_gimbal` (4019). **C1** PX4's `libGstCameraSystem.so` is already
built + loaded (`server.config`) → RTP/H.264 UDP to `PX4_VIDEO_HOST_IP:5600+i` (QGC video / ffplay);
x264/rtp/udp GStreamer plugins present. **C2** save geo-tagged frames via `gz.transport13`. **C3**
`ros_gz_bridge` → ROS 2 Image (with T2/T3, compress). **C4** `ffmpeg x11grab :99` records the scene view.
**Since 2026-10-17 T2, T5 (ROS topic), C3 + browser camera / Foxglove / bag are implemented** in the `telemetry` service (change log below); T1, T3, T4 DB, C1, C2, C4 still open.

## Change log — telemetry service: topics + camera view on the server (2026-10-17)

New service **`telemetry`** (`docker/telemetry/`, image `ghcr.io/zflukz/aquarius-telemetry`) in
`compose.server.yml` (`network_mode: host`) and `compose.local.yml` (`network_mode: service:px4-sim`,
ports 8083 / 8765 published on px4-sim). Implements T2, T5, C3 + browser camera, Foxglove, `ros2 bag`.
Details / topic table: `docker/telemetry_plan.md` §4.

- Dockerfile: ROS 2 Jazzy + `ros_gz_bridge` + `web_video_server` + `foxglove_bridge` + `rosbag2_storage_mcap`,
  Micro XRCE-DDS Agent v2.4.3 from source (own Fast-DDS in `/usr/local/lib` → started with
  `LD_LIBRARY_PATH=/usr/local/lib`), `px4_msgs` built from PX4-Autopilot `msg/` at build arg `PX4_REF`
  (compose arg from `.env`, must be px4-sim's commit). `with-ros <cmd>` helper for `docker exec`.
- entrypoint: agent udp 8888 (PX4 → `/fmu/out/*`, `/uav_i/fmu/out/*`), lazy `ros_gz_bridge` YAML generated
  per drone (`/uav_i/gz/{imu,navsat,air_pressure,magnetometer}`, `/uav_i/camera/{image_raw,camera_info}`,
  `/clock`), `ground_truth.py` (`/uav_i/gz/ground_truth`), web_video_server :8083, foxglove_bridge :8765, `RECORD=1` → mcap bag in
  `docker/data/bags`. Exits if any part dies. Camera sensor from `PX4_SIM_MODEL` (`GZ_CAMERA` override).
- px4-sim needs **no rebuild**: only env `GZ_PARTITION` (shared gz-transport partition), `ROS_DOMAIN_ID`
  (rcS maps it to `UXRCE_DDS_DOM_ID`), `PX4_VIDEO_HOST_IP`.
- Camera needs a camera model: `PX4_SIM_MODEL=gz_x500_mono_cam_down` + `PX4_SYS_AUTOSTART=4014`.
- `view_cam.py`: topic `/uav_<DRONE_ID>/camera/image_raw` (`CAMERA_TOPIC`), QoS BEST_EFFORT.
- Native twin: `scripts/install_telemetry_server.sh` (same stack, `px4_msgs` from `$PX4_DIR/msg`) +
  `scripts/run_telemetry_native.sh` (runs `docker/telemetry/entrypoint.sh` with native paths);
  `run_sim_native.sh` exports the same env; `uninstall_sim_server.sh` removes the telemetry parts.
- Checked against PX4 source in the px4-sim image: rcS `uav_$px4_instance` namespace for instance > 0,
  `ROS_DOMAIN_ID` → `UXRCE_DDS_DOM_ID`; x500_base sensors `imu_sensor` / `navsat_sensor` /
  `air_pressure_sensor` / `magnetometer_sensor` on `base_link`; cameras merged into the drone model:
  mono_cam / gimbal `camera_link/camera`, OakD-Lite (depth) `camera_link/IMX214`.

- **Camera size / rate (`px4-sim` entrypoint + `run_sim_native.sh`):** `CAMERA_WIDTH/HEIGHT/RATE` (default
  640/480/10, empty = model default) rewrite the `<sensor type="camera">` blocks of `mono_cam`, `gimbal`, `OakD-Lite`
  from `model.sdf.orig` at every start. Reason: no GPU → Gazebo renders the camera on the CPU and lockstep waits
  for each frame; mono_cam's default 1280x960 @ 30 Hz in Baylands gave **RTF 0.025** (camera 1.5 Hz).
- **`ground_truth.py`** (telemetry image, `gz.transport13` Python from ROS's `gz_transport_vendor`) →
  `/uav_i/gz/ground_truth` PoseStamped. Replaces the bridged `/gz/ground_truth` TFMessage: ros_gz_bridge's
  Pose_V → TF conversion leaves `child_frame_id` empty and mixes model + link poses (no PosePublisher /
  OdometryPublisher on the PX4 models; `/model/<m>/odometry_with_covariance` is only a PX4 subscription).
- `set -m` in the telemetry entrypoint: without job control, background jobs ignore SIGINT → `ros2 bag` never
  closed the mcap (no `metadata.yaml`).

Verified 2026-10-17 (`compose.local.yml`, Docker Desktop, `PX4_SIM_MODEL=gz_x500_mono_cam_down` / 4014, `QGC=0`):
- `aquarius-telemetry` built (930 MB, ROS layer ≈ 33 min, agent ≈ 6.5 min, px4_msgs ≈ 9 min) with
  `PX4_REF=9a1429ae…` = px4-sim's PX4. Docker Desktop printed `image ... already exists` after naming the image —
  containerd-store quirk, image was fine. px4-sim rebuilt (entrypoint layer only).
- XRCE session from PX4 (`client_key 0x00000001`), 31 `/fmu/out/*` topics, versioned names
  (`battery_status_v1`, `vehicle_status_v4`, `vehicle_local_position_v1`, ...), decoded with the built `px4_msgs`.
- Gazebo bridge: `/uav_0/gz/{imu,navsat,air_pressure,magnetometer}`, navsat 37.41217 / -121.99888 (Baylands),
  `/uav_0/camera/image_raw` 640x480 rgb8; web_video_server 8083 index + `/snapshot` 200 (JPEG of the park grass),
  foxglove_bridge listening on 8765 and advertising all topics (not opened in the Foxglove app yet).
- RTF with camera: 0.025 at 1280x960@30 → **1.0** at 640x480@10 (also with `RECORD=1`; dips for a few s while
  telemetry starts).
- `flight_test.py` PASSED with telemetry running; during flight `vehicle_status_v4` armed / nav_state 18 (land),
  `battery_status_v1` 0.94, `vehicle_odometry` ~68 Hz, ground truth ~23 Hz, camera ~3.4 Hz (CLI `ros2 topic hz`).
- `RECORD=1` → `docker/data/bags/<date>/` mcap + metadata, `ros2 bag info` OK (28 s, 16 327 msgs: PX4, gz sensors,
  ground truth).
- Not tested: compose.server.yml on the VM (`network_mode: host`), `PX4_NUM_DRONES>1` topic names, Foxglove app in a
  browser, QGC RTP video (C1), `view_cam.py` on the new topic, native `install_telemetry_server.sh` /
  `run_telemetry_native.sh`, image not pushed to GHCR.

## Change log — `aquarius` CLI (2026-10-17)

- **`scripts/aquarius`** (bash, LF via `.gitattributes` — no `.sh` extension): `init [local|server|control]`
  (`.env` from example, options `--drones/--world/--camera/--headless/--control-host` saved to `.env`, compose up,
  waits for `drone<N-1> (sysid` in px4-sim log, prints URLs), `start mission LAT,LON ...`, `start coverage [LAT,LON]
  --size WxH`, `status`, `rtl`/`stop`, `land`, `logs`, `urls`, `down`, `install` (→ `~/.local/bin`).
  Docker if `drone-control` runs, else native `Drone-Control/.venv`.
- Missions run **in the background** (`docker exec -d`, pid/log `/tmp/aquarius-<job>-<drone>.{pid,log}` in the
  container) and the CLI follows with `tail --pid` → Ctrl+C never leaves a half-killed script (gotcha below).
  `rtl`/`land`/new start first kill the job + `[m]avsdk_server.*:<port>` (bracket: `pkill -f` inside `bash -c`
  otherwise matched its own shell → exit 143). Exit 1 unless the log ends with `=== … DONE|PASSED ===`.
- **`Drone-Control/mission_cli.py`**: `goto LAT,LON ...` (reuses `mcpp_mission.connect/fly`: upload → takeoff →
  mission → RTL; refuses points > `MAX_DIST` 1000 m from home), `status`, `rtl`, `land`.
  `mcpp_mission.py`: `--center LAT,LON` / `AREA_CENTER`; `exit_clean()` stops every `mavsdk_server`
  (`System._stop_mavsdk_server`) + `os._exit` — before, any `SystemExit` / error left the process hanging on
  MAVSDK threads and holding udp 14540+i.
- `compose.local/control.yml`: drone-control `init: true` — PID 1 is `sleep infinity`, exited `mavsdk_server`s stayed zombies.

Verified (local, Baylands, `gz_x500_mono_cam_down`): `aquarius init --camera` (recreated px4-sim, ready + URLs);
`status` (HOLD, 100 %, FIX_3D 10 sats); `start mission "{37.41245,-121.99888}" 37.41245,-121.99850 --alt 15` →
2/2 waypoints, RTL, landed, `MISSION DONE` (2 min 54 s); `start coverage 37.41235,-121.99870 --size 30x20 --detach`
→ 9 waypoints, `status` mid-flight, `rtl` stopped the job and the drone returned; far point (2134 m) refused,
exit 1, no leftover process; bad args (`--drone x`, `--size 40by30`, lat 137) rejected; rebuilt drone-control
image (`init=true`) → full mission exit 0, 0 zombies. Not tested: `init server` / `init control`, native mode,
`--drones 2` mission/coverage, `install`.

- `--max-dist M` added to the CLI (passed as `MAX_DIST`; host env vars don't reach the container). Tested: 2 km point
  accepted with `--max-dist 5000`, then `aquarius land`.
- User guide: **`AQUARIUS_CLI.md`** (keep it in sync when the CLI changes; `aquarius --help` = header of the script).

## Benchmark native vs Docker — `docker/bench_sim.sh` (2026-10-16)

`./bench_sim.sh [native|docker|both]`: N PX4 instances idle in one world, headless, no QGC; measures
`boot_s` (rcS done), `ready_s` (`px4-listener home_position` → `valid_hpos: True`; "Ready for takeoff"
is not usable — PX4 only says it once a GCS is connected), `rtf` (Δ sim_time of `/world/<w>/stats` /
Δ wall time, two samples only so the gz CLI doesn't skew CPU), `cpu_%` / `rss_mb` (utime+stime and VmRSS
of `bin/px4 -i|gz sim` pids from `/proc`; Docker Desktop fallback: `docker stats`, whole container).
Native mode mirrors `entrypoint.sh` (`px4 -i i -d` from `$PX4_DIR`, wait per instance). Docker mode
starts `drone-control` too when the compose file has it (compose.local's `CONTROL_HOST=drone-control`
otherwise blocks px4-sim for minutes). Results → `bench_results.csv`.

Tested 2026-10-16: native mode inside a throwaway `aquarius-px4-sim` container (`PX4_DIR=/px4`) →
boot 17.7 s, RTF 1.006, CPU 77 %, 731 MB; docker mode with `compose.local.yml` on Docker Desktop →
boot 40.3 s, RTF 1.018, CPU 80 % (docker stats), 691 MB. **Not run on the real VM yet** — that is the
real comparison (both modes via `/proc`). Gotcha: Git Bash needs `MSYS_NO_PATHCONV=1` (set in the script).

## Change log — native scripts (`scripts/`, 2026-10-16)

Brought the native path in line with Docker (needed for a fair `bench_sim.sh` comparison):
- `install_sim_server.sh`: Gazebo Harmonic from the OSRF **apt** repo (was a multi-hour colcon source build
  in `~/gz_ws`), PX4 `main` (`PX4_REF`) + `make px4_sitl_default`, Baylands `gz fuel download`; no IP
  argument. Removes the old `mavlink start -x -u 14540 -t <IP>` line it used to append to `px4-rc.mavlink`
  (that port clashed with PX4's own links).
- **New `run_sim_native.sh`**: native twin of `docker/px4-sim/entrypoint.sh` — same env names, `wait_ready`
  per instance, links `14640+i → CONTROL_HOST:14540+i` and `18640+i → :14550`; no Xvfb/VNC/QGC
  (GUI on `$DISPLAY`, else forces `HEADLESS=1`); Ctrl+C stops all.
- `install_control_computer.sh`: ROS 2 Jazzy on 24.04 / Humble on 22.04 (was Humble only); no VM IP, no
  `sed` of `udp://:14540` (scripts use `DRONE_ID` / `MAVLINK_URL`); deletes the old `Drone-Control/.env`.
- `uninstall_*`: `pkill` only `$PX4_DIR/.../bin/px4` (old `pkill -f px4` / `ruby` hit unrelated processes,
  incl. the px4-sim container); leaves `gz sim` alone if px4-sim runs; old source-build `-dev` packages
  only with `--purge-build-deps`; removes ROS Jazzy and Humble; kills leftover `mavsdk_server`.
- All `scripts/*.sh` now LF (were CRLF in the working tree).

Tested: `bash -n` all; `run_sim_native.sh` with `PX4_NUM_DRONES=2` inside the px4-sim image
(`PX4_DIR=/px4`, no DISPLAY): instance 0 started Gazebo, instance 1 "gazebo already running world: baylands",
both links printed, SIGINT stopped cleanly. `install_control_computer.sh` in a fresh `ubuntu:24.04`
container (+ `sudo`): ROS 2 Jazzy + venv OK, `import mavsdk_grpc, rclpy, cv2, cv_bridge` OK,
`mcpp_mission.py --plan-only` OK; `uninstall_control_computer.sh` exit 0, venv gone (an almost empty
`/opt/ros/jazzy/lib` stays behind).

## Verification

### Checked against upstream source (PX4-Autopilot `main`, 2026-10-13)

| Claim | Source | Result |
| :--- | :--- | :--- |
| Offboard link: bind 14580+i, send to 14540+i | `ROMFS/.../init.d-posix/px4-rc.mavlink` | ✅ |
| `mavlink start -t <ip>` sets fixed partner; socket binds `INADDR_ANY` | `src/modules/mavlink/mavlink_main.cpp` (`case 't'`, `init_udp`) | ✅ — "MAVLink only on localhost" log is informational, `-t` still sends |
| PX4 latches/replaces client address from incoming packets | `mavlink_receiver.cpp` | ✅ — restarting a script with a new source port is handled |
| Instance >0 joins a running world, else starts its own gz server | `px4-rc.gzsim` | ✅ — **found a race**, fixed (below) |
| `HEADLESS` empty = GUI; `PX4_GZ_MODEL_POSE="x,y"` ok; 30 s "Waiting for Gazebo world" timeout | `px4-rc.gzsim` | ✅ |
| `px4 -i N -d` daemon mode; `px4-<module> --instance N ...` client syntax | `platforms/posix/src/px4/common/main.cpp` | ✅ |
| `mavlink status` prints `instance #N:` | `mavlink_main.cpp` | ✅ but printed by the **daemon**, not returned to the `px4-mavlink` client → unusable for `wait_ready` (see run 2026-10-13) |
| `commander status` exists | `Commander.cpp` usage | ❌ — no such command; docs changed to `px4-listener … vehicle_status` / `px4-mavlink … status` |
| `baylands.sdf` world exists | `PX4/PX4-gazebo-models/worlds` | ✅ |
| `mavsdk-grpc` = official MAVSDK-Python (v4.0.5), `import mavsdk_grpc` | PyPI | ✅ — `udpin://` supported |
| `dougw/novnc`: `REMOTE_HOST` / `REMOTE_PORT`, port 8081 | Docker Hub | ✅ (image last updated 2017) |

**Race fixed:** `wait_ready` originally passed as soon as the PX4 daemon socket answered, which
happens *before* `px4-rc.gzsim` finds the world → instance 1 could start a second gz server.
Now it waits until PX4's log `/var/log/px4/px4-i.log` contains `Startup script returned successfully`
(rcS finishes only after the world is ready). The first version grepped `px4-mavlink --instance i
status` for `instance #` — that never matched (see below).

### Local checks run

- `bash -n docker/px4-sim/entrypoint.sh` — OK.
- `docker compose -f <file> config -q` for `compose.local.yml`, `compose.control.yml`,
  `compose.server.yml` (with `CONTROL_HOST` set) — OK; contexts/images resolve correctly.
  `compose.server.yml` without `CONTROL_HOST` fails with a clear message (intended).
- `file entrypoint.sh` — LF line endings.

### Docker run — 2026-10-13 (Docker Desktop 28.0.4, Windows)

**Cleanup first:** removed the old images / containers from the former root `docker-compose.yml`
(`project-aquarius-px4-sim`, `project-aquarius-drone-control`, containers `px4-sim`, `drone-control`,
`novnc`) and the unused `theasp/novnc`. Kept `dougw/novnc` (still used) and the LaTeX devcontainer
images (`vsc-project-aquarius-*`, `texlive/texlive`).

- [x] `docker compose -f docker/compose.local.yml build` — **OK**, exit 0. Slow network: total ≈ 1.5 h
      (apt ≈ 21–28 min, gz-harmonic ≈ 38 min, ROS ≈ 12 min, PX4 clone + `ubuntu.sh` ≈ 12 min,
      `make px4_sitl_default` ≈ 2.5 min / 1196 targets, image export ≈ 6–10 min each).
      Sizes: `aquarius-px4-sim` 10.9 GB, `aquarius-drone-control` 3.43 GB.
      `ln: failed to create symbolic link '/etc/resolv.conf'` during apt is harmless (Docker locks it).
- [x] `mavsdk-grpc` installs from PyPI; `import mavsdk_grpc` works in the drone-control image.
- [x] `Dockerfile.dockerignore` works — `/drone-control` in the image contains only
      `flight_test.py`, `teleop_drone.py`, `view_cam.py`, `requirement.txt`.
- [x] Baylands pre-cache at build time works — `/root/.gz/fuel/.../baylands/3/model.sdf`, 393 MB in image.
- [x] `up -d`: Gazebo 8.15 loads `baylands.sdf`, "Gazebo world is ready" after ~20 × "Waiting…"
      (≈ 20 s, inside the 30 s timeout, with cache), `x500_0` spawned, rcS "Startup script returned
      successfully". `CONTROL_HOST=drone-control` resolved to the container IP (172.19.0.3).
- [x] **Bug found & fixed:** `wait_ready` never succeeded — `px4-mavlink --instance 0 status` prints
      nothing to the client (output goes to the daemon log), so after 180 s it gave up and the
      `-t CONTROL_IP` links (14640/18640) were **never opened** → `flight_test.py` could not have
      connected. Fixed `entrypoint.sh` to grep the instance log instead; `bash -n` OK, still LF.
- [x] Fix tested (new entrypoint `docker cp`'d into the running container + restart): log shows
      `mavlink ... udp port 14640 remote port 14540`, `... 18640 remote port 14550` and
      `drone0 (sysid 1) -> 172.19.0.3:14540 [MAVSDK], :14550 [QGC]`.
- [x] **`flight_test.py` PASSED** through `compose.local.yml` (connect, GPS, arm, takeoff, all 9
      offboard steps, land). First run failed `takeoff altitude too low (0.9 m)`: PX4 spools up ~5 s
      before climbing (measured: 0.1 m @5 s, 0.8 m @6 s, 1.5 m @7 s, 2.5 m @14 s; Gazebo RTF ≈ 1.0).
      Fixed `flight_test.py`: poll altitude > 1 m (20 s timeout) instead of fixed `sleep(6)`, then 3 s settle.
- [x] **QGroundControl in noVNC** — tried in the running container: QGC v5.1.5 AppImage extracted to
      `/opt/qgc`, run as user `qgc` on `:99` → shows next to Gazebo, auto-connected (Ready / Hold,
      GPS, Baylands map). Made permanent: Dockerfile layer (`ARG QGC_VERSION`), entrypoint starts it
      when `QGC=1` (default), Xvfb now `SCREEN_SIZE` (default 1920x1080x24, was 1280x720);
      compose files / `.env.example` / `docker/README.md` updated.
- [ ] **Rebuild of `px4-sim` with QGC FAILED — not a Dockerfile error.** Docker Desktop's engine died
      mid-build (`failed to receive status: rpc error: code = Unavailable desc = error reading from
      server: EOF`, during the first apt step — cache was missed again). Afterwards Docker Desktop was
      fully stopped (`wsl -l -v` → docker-desktop Stopped). Likely out of memory: WSL VM has ~7.7 GB and
      px4-sim (Gazebo + QGC, ~2.3 GB, ~300 % CPU) was running while building. Disk OK (105 GB free).
      **The current `ghcr.io/zflukz/aquarius-px4-sim:latest` image is the OLD one** (no QGC, old
      `wait_ready` bug). The fixes so far were only tested by `docker cp` into a running container.

### Docker run — 2026-10-14

- [x] `px4-sim` rebuilt (QGC layer + `wait_ready` fix), 11.7 GB. `up -d --force-recreate`: links open
      (`drone0 (sysid 1) -> 172.19.0.x:14540`), `QGroundControl started`, QGC auto-connects (Ready / Hold, Baylands map).
- [x] Split screens: x11vnc on 5900 + 5901, Gazebo window on `:99`, QGC on `:100`, both 1920x1033 (maximized).
      Checked by screenshot inside the container; `novnc-qgc` command override starts websockify on 8082.
- [x] `mcpp_mission.py --plan-only` for 1–4 drones: balanced regions, STC cycle covers every sub-cell (asserts).
- [x] **MCPP flight PASSED** (1 drone, `AREA_W=40 AREA_H=30`, 14 waypoints, 235 m, GUI on): upload, takeoff,
      14/14 waypoints, RTL, "Disarmed by landing".
- **Bugs found:** (a) `set_return_to_launch_after_mission` after upload has no effect and PX4 still just
  loiters → script now sets it before upload **and** calls `action.return_to_launch()`. (b) With Gazebo GUI +
  QGC on one screen px4-sim hit ~400 % CPU (6 vCPU WSL) and the sim **froze** (PX4 hung in gz-transport
  `Publish()`, lockstep → no MAVLink). Recovered by recreating px4-sim; later runs with the GUI were fine.
  `HEADLESS=1` (~95 % CPU) is the fallback when the PC is loaded.
- Leftover `mavsdk_server` from killed `docker exec` runs again blocked udp 14540 (gotcha below).

### Resume here (next session) — superseded by the 2026-10-14 run above

1. Start Docker Desktop.
2. `cd docker && docker compose -f compose.local.yml down` — free memory before building.
3. Optional, recommended: `C:\Users\Windows 10\.wslconfig` → `[wsl2]` / `memory=12GB` (fit to the PC's
   RAM), then `wsl --shutdown` and restart Docker Desktop.
4. `docker compose -f compose.local.yml build --progress=plain px4-sim` (≈ 1.5 h if cache is gone).
5. `docker compose -f compose.local.yml up -d --force-recreate` → `docker logs px4-sim` must show
   `drone0 (sysid 1) -> …` and `QGroundControl started`; open http://localhost:8080/vnc.html.
6. Run `flight_test.py` again (see `WORKFLOW.md`), then `PX4_NUM_DRONES=2`.
7. Tick the boxes below. Nothing has been committed — all changes are in the working tree.

**Gotchas found:**
- Killing `docker exec` on the host (e.g. `timeout docker exec …`) does **not** kill the script
  inside the container. Leftover `mavsdk_server` processes keep gRPC port 50051 + UDP 14540, so the
  next script silently talks to the old server and hangs. Clean up with
  `docker exec drone-control pkill -f mavsdk_server`.
- Git Bash rewrites `/tmp/...` args to Windows paths → use `MSYS_NO_PATHCONV=1` with `docker exec/cp`.
- Docker build cache was partly lost between builds (first rebuild re-ran apt from scratch) —
  BuildKit GC on a ~26 GB cache. A full `px4-sim` build ≈ 1.5 h on this network.

### NOT yet verified

- [ ] Gazebo (8080) and QGC (8082) checked in a real browser — so far only screenshots of `:99` / `:100`.
- [ ] `mcpp_mission.py` with `NUM_DRONES=2` in the sim, and `AUTO_RUN` start via compose.
- [ ] `PX4_NUM_DRONES=2`: both drones spawn in one world, each reachable on 14540 / 14541, both in QGC.
- [ ] `docker compose push` to GHCR and pull on a real VM; split-mode flight from the control computer.
- [ ] Docker Desktop (Windows) published UDP range 14540-14549 delivers PX4's packets to the container.

Suggested test order: local build → `flight_test.py` (1 drone) → 2 drones → push → VM pull → split test.

## Known issues / TODO

- Native path (`scripts/`) rewritten 2026-10-16; `install_sim_server.sh` (full PX4 build) not run end-to-end yet.
- Camera / telemetry topics live on the VM (`telemetry` service). `view_cam.py` on the control computer needs
  DDS between the machines (T3, not done) — use the browser (8083) / Foxglove (8765) across networks.
- Local: after recreating/restarting px4-sim also `restart telemetry` (`service:px4-sim` netns is not re-joined).
- `CONTROL_HOST` is resolved once at container start; if the control computer's IP changes, recreate `px4-sim`.
- noVNC / x11vnc / web_video_server / foxglove_bridge have no password — keep 8081-8083 / 8765 on LAN / VPN only.
- GHCR packages are private by default → make public or `docker login` on the VM.
- Raw-GitHub `curl` of `compose.server.yml` works only after these files are on `main` and the repo is public.
- `git status` shows `Visualization/Python/*` deleted — not done by Claude; confirm before committing.
- Future plan (multi-drone phases, swarm script, MicroXRCE agent, per-drone containers, CI build/push):
  see `docker/README.md` → "Future plan".
