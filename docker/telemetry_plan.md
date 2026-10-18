# Telemetry & camera plan — getting drone data out of the simulation

Goal: every sensor / battery / state value **and** the camera view of every drone, live and recorded,
on the server side. Use the same path that real drones will use later.

Everything below was listed from the **running** `px4-sim` container (PX4 `main` in the image,
Gazebo Harmonic 8.15, gz-gui 8.4, model `gz_x500`, world `baylands`) on 2026-10-16, unless a line says
**(not tested)**.

> **2026-10-17 — implemented:** service **`telemetry`** (`docker/telemetry/`, image `aquarius-telemetry`)
> in `compose.server.yml` + `compose.local.yml` = steps **T2** (uXRCE agent + `px4_msgs`), **T5** (ground
> truth as ROS topic), **C3** (camera → ROS 2 Image) plus camera in the browser and `ros2 bag`
> (part of **T4**). See **§4**. Still open: T3 (DDS VM ↔ control), C2, C4.
>
> **2026-10-18:** T1 + T4 done the [`communication_plan.md`](communication_plan.md) way: MAVLink → `mavlink_mqtt.py`
> → MQTT → Telegraf → InfluxDB → **Grafana** :3000 (dashboard, history). Foxglove (`foxglove_bridge`, :8765) **removed**.

```
 Gazebo (sensors, ground truth, camera)
   │ gz-transport topics          (A)  ── only inside px4-sim (or GZ_RELAY)
   ▼
 PX4 SITL (uORB)                  (B)  ── uXRCE-DDS → ROS 2  (telemetry container, §4)
   │ MAVLink                      (C)  ── 14540+i (MAVSDK), 14550 (QGC)   ✅ works today
   ▼
 MAVSDK telemetry API             (D)  ── drone-control, Python
 Camera: GstCameraSystem          (E)  ── RTP/H.264 UDP 5600+i  (plugin already loaded)
```

---

## 1. What telemetry exists

### A. Gazebo sensors (gz-transport) — raw simulated sensors + ground truth

Prefix `/world/<world>/model/x500_<i>/link/base_link/sensor/` (`<world>` = `baylands`).

| Topic | Type | Rate | Notes |
| :--- | :--- | :--- | :--- |
| `.../imu_sensor/imu` | `gz.msgs.IMU` | 250 Hz | accel + gyro + orientation |
| `.../magnetometer_sensor/magnetometer` | `gz.msgs.Magnetometer` | 100 Hz | |
| `.../air_pressure_sensor/air_pressure` | `gz.msgs.FluidPressure` | 50 Hz | barometer |
| `.../navsat_sensor/navsat` | `gz.msgs.NavSat` | 30 Hz | GPS lat/lon/alt/vel from world `spherical_coordinates` |
| `/world/<world>/dynamic_pose/info` | `gz.msgs.Pose_V` | sim rate | **ground truth** pose of every moving model (all drones) |
| `/world/<world>/pose/info` | `gz.msgs.Pose_V` | sim rate | all models incl. static |
| `/world/<world>/clock`, `/clock` | `gz.msgs.Clock` | | sim time |
| `/world/<world>/stats` | `gz.msgs.WorldStatistics` | | real-time factor (sim health) |
| `/world/<world>/wind` | `gz.msgs.Wind` | | |
| `/x500_<i>/command/motor_speed` | `gz.msgs.Actuators` | | motor commands PX4 → Gazebo |

Only with another model (`PX4_SIM_MODEL` / `PX4_SYS_AUTOSTART`):

| Model (airframe) | Extra sensor | Size / rate |
| :--- | :--- | :--- |
| `gz_x500_mono_cam` (4010) | `mono_cam` RGB camera, forward | 1280×960, 30 Hz, HFOV 1.74 rad |
| `gz_x500_mono_cam_down` (4014) | same, looking down (mapping / coverage photos) | 1280×960, 30 Hz |
| `gz_x500_depth` (4002) | OakD-Lite: RGB `IMX214` + depth `StereoOV7251` (`depth_camera`) | 1920×1080 + 640×480, 30 Hz |
| `gz_x500_gimbal` (4019) | 3-axis gimbal camera + `camera_imu`; `/model/x500_i/command/gimbal_{pitch,roll,yaw}` | 1280×720, 30 Hz |
| `gz_x500_flow` (4021) | optical flow + LW20 rangefinder | |
| `gz_x500_lidar_down` / `_front` / `_2d` (4016 / 4017 / 4013) | LW20 / 2D lidar `.../scan` | |

PX4's `gz_bridge` subscribes to all optional topics (airspeed, optical flow, odometry, lidar), so they
appear in `gz topic -l` even when no sensor publishes them — check with `gz topic -i -t <topic>`.

Read inside px4-sim:
```bash
docker exec px4-sim gz topic -e -t /world/baylands/model/x500_0/link/base_link/sensor/navsat_sensor/navsat -n 1
```
Python bindings `gz.transport13` are installed in px4-sim (`import gz.transport13` works).

### B. PX4 uORB → ROS 2 (uXRCE-DDS)

PX4 SITL **already starts** `uxrce_dds_client` for each instance (log: `init UDP agent IP:127.0.0.1,
port:8888`). Namespace: drone 0 none, drone i `uav_i` (override with `PX4_UXRCE_DDS_NS`). DDS domain
= `ROS_DOMAIN_ID` (default 0). Agent + `px4_msgs` now run in the **`telemetry`** container (§4).
Since PX4 v1.16 messages with a `MESSAGE_VERSION` get a suffix on the ROS topic, e.g.
`/fmu/out/vehicle_status_v4` (unversioned ones keep the plain name) → check with `ros2 topic list`.

Published by default (`/px4/src/modules/uxrce_dds_client/dds_topics.yaml`, 31 topics):

| Group | Topics `/fmu/out/...` |
| :--- | :--- |
| Power | `battery_status` |
| IMU | `sensor_combined` (accel + gyro) |
| GPS | `vehicle_gnss`, `vehicle_global_position`, `home_position` |
| Pose / state estimate | `vehicle_attitude`, `vehicle_local_position`, `vehicle_odometry`, `estimator_status_flags`, `wind`, `airspeed_validated` |
| Vehicle state | `vehicle_status`, `vehicle_control_mode`, `vehicle_land_detected`, `failsafe_flags`, `vehicle_command_ack`, `mode_completed`, `arming_check_request` |
| Navigation | `position_setpoint_triplet`, `collision_constraints` |
| RC / manual | `input_rc`, `manual_control_setpoint` |
| Other | `gimbal_device_attitude_status`, `transponder_report`, `timesync_status`, `vtol_vehicle_status`, `vtx`, `register_ext_component_reply`, `setpoint_config_reply`, `config_overrides_confirm`, `message_format_response` |

38 `/fmu/in/...` topics accept commands (`trajectory_setpoint`, `offboard_control_mode`,
`vehicle_command`, `vehicle_visual_odometry`, `distance_sensor`, ...).

Not exported by default but in uORB: raw `sensor_baro`, `sensor_mag`, `sensor_gps`, `vehicle_air_data`,
`vehicle_magnetometer`, `esc_status`, `actuator_outputs`, `cpuload`, ... → add them to
`dds_topics.yaml` and rebuild PX4 (image layer `make px4_sitl_default`). Any uORB topic can always be
read on the sim with `px4-listener --instance i <topic>`.

### C. MAVLink streams (already reach drone-control / QGC)

Streams configured on drone 0's links (`px4-mavlink --instance 0 status streams`):

`HEARTBEAT` `SYS_STATUS` (battery %, voltage, sensor health) `BATTERY_STATUS` `EXTENDED_SYS_STATE`
`CURRENT_MODE` `AVAILABLE_MODES` `STATUSTEXT` · `ATTITUDE` `ATTITUDE_QUATERNION` `ATTITUDE_TARGET`
· `GLOBAL_POSITION_INT` `LOCAL_POSITION_NED` `ODOMETRY` `ALTITUDE` `VFR_HUD` `HOME_POSITION`
`GPS_GLOBAL_ORIGIN` · `GPS_RAW_INT` `GPS2_RAW` `GPS_STATUS` `GNSS_INTEGRITY` · `HIGHRES_IMU`
`SCALED_PRESSURE` `VIBRATION` `DISTANCE_SENSOR` `OPTICAL_FLOW_RAD` `OBSTACLE_DISTANCE` ·
`ESTIMATOR_STATUS` `ESTIMATOR_SENSOR_FUSION_STATUS` `WIND_COV` · `NAV_CONTROLLER_OUTPUT`
`POSITION_TARGET_GLOBAL_INT` `POSITION_TARGET_LOCAL_NED` `TIME_ESTIMATE_TO_TARGET` · `SERVO_OUTPUT_RAW_0`
`ESC_INFO` `ESC_STATUS` `RAW_RPM` · `RC_CHANNELS` `MANUAL_INPUT_STATUS` · `GIMBAL_*` `MOUNT_ORIENTATION`
`CAMERA_TRIGGER` `CAMERA_IMAGE_CAPTURED` · `ADSB_VEHICLE` `OPEN_DRONE_ID_*` · `SYSTEM_TIME` `TIMESYNC`
`PING` `LINK_NODE_STATUS` `DEBUG*` `NAMED_VALUE_FLOAT`.

Some only send when the data exists (e.g. `DISTANCE_SENSOR` needs a rangefinder model).

### D. MAVSDK telemetry (`mavsdk_grpc.telemetry`, drone-control)

`battery` `health` `health_all_ok` `armed` `in_air` `landed_state` `flight_mode` `status_text`
`vtol_state` · `position` `home` `altitude` `heading` `gps_info` `raw_gps` · `attitude_euler`
`attitude_quaternion` `attitude_angular_velocity_body` · `velocity_ned` `position_velocity_ned`
`odometry` `ground_truth` · `imu` `raw_imu` `scaled_imu` `scaled_pressure` `distance_sensor` `wind`
· `rc_status` `actuator_control_target` `actuator_output_status` `fixedwing_metrics` `unix_epoch_time`.
Rates: `telemetry.set_rate_<name>(hz)`.

### Battery in SITL

Simulated by PX4 `battery_simulator`: drains over time, never below `SIM_BAT_MIN_PCT`; drain time
`SIM_BAT_DRAIN` (s). Set e.g. `param set SIM_BAT_MIN_PCT 20` to test low-battery failsafe / RTL.

---

## 2. Telemetry plan

| Step | What | Where | Effort |
| :--- | :--- | :--- | :--- |
| **T1** | `Drone-Control/telemetry_bridge.py`: one MAVSDK `System(port=50051+i)` per drone (like `mcpp_mission.py`), subscribe the D list, set rates, write JSON lines `{drone_id, t, topic, data}` to stdout + `/data/telemetry-<date>.jsonl` (volume) and push to the server (MQTT or WebSocket) | drone-control | script only — works local, split and with real drones |
| **T2** | full uORB over ROS 2: build `Micro-XRCE-DDS-Agent` (v2.4.x) into px4-sim, start `MicroXRCEAgent udp4 -p 8888` in `entrypoint.sh` (one agent for all instances); build `px4_msgs` (same PX4 commit) with colcon into drone-control; extend `dds_topics.yaml` for raw baro/mag/gps if needed | both images | rebuild both |
| **T3** | DDS across VM ↔ control: multicast usually blocked → Fast DDS discovery server (`fastdds discovery -i 0 -p 11811` on VM, `ROS_DISCOVERY_SERVER=<VM_IP>:11811` both sides) or unicast peers; same `ROS_DOMAIN_ID`; open DDS UDP ports | compose + firewall | env only |
| **T4** | server: `telemetry-server` service subscribes (T1 JSON / T2 ROS 2) → TimescaleDB or InfluxDB → Grafana dashboard + REST/WebSocket for a web UI; `ros2 bag record` (mcap) per experiment | new compose service | new image |
| **T5** | ground truth for evaluation (MCPP coverage, position error): read `/world/<world>/dynamic_pose/info` in px4-sim (Python `gz.transport13`, no ROS needed) → same JSON format as T1 | px4-sim | script + entrypoint option |

Order: T1 → T5 → T4 → T2 + T3 (only when raw sensors at full rate or ROS 2 algorithms are needed).

Done 2026-10-17 (§4): **T2**, **T5** (ROS topic `/uav_i/gz/ground_truth`, `ground_truth.py`), `ros2 bag` part of **T4**.

Open question: "server" = the VM running px4-sim, or a separate backend (DB / web)? T4 placement depends on it.

**2026-10-17:** T1 + T4 are replaced by [`communication_plan.md`](communication_plan.md): MAVLink → MQTT (`mavlink_mqtt.py`)
→ Telegraf → InfluxDB → Grafana, Kafka optional.

---

## 3. Camera view plan

Two different views:

| View | What it is | Today |
| :--- | :--- | :--- |
| **Scene view** | Gazebo GUI third-person camera (follows drone `GZ_FOLLOW`) | ✅ noVNC 8080 / 8081 |
| **Drone camera** | camera sensor on the drone (FPV / downward) | ❌ `gz_x500` has no camera |

### Step 0 — put a camera on the drone

`.env`: `PX4_SIM_MODEL=gz_x500_mono_cam_down` + `PX4_SYS_AUTOSTART=4014` (downward, for coverage /
mapping), or `gz_x500_mono_cam` / 4010 (forward FPV), or `gz_x500_gimbal` / 4019 (pointable).
Then `up -d --force-recreate px4-sim`. Rendering a camera costs CPU (software rendering): try 1 drone first.

### C1 — live video stream: GStreamer RTP/H.264 (recommended first) (not tested)

PX4 already builds and loads `libGstCameraSystem.so` (`gz_bridge/server.config`, via
`GZ_SIM_SERVER_CONFIG_PATH`). It picks up camera sensors and streams RTP/H.264 over UDP to
`PX4_VIDEO_HOST_IP` (default `127.0.0.1`), port `5600 + instance` (in launch order). `x264enc`, `rtp`,
`udp` GStreamer plugins are installed in px4-sim; no NVIDIA → software x264.

- Local: default `127.0.0.1:5600` = QGC inside px4-sim → QGC (noVNC 8082) shows the video
  (QGC: Application Settings → Video → Source "UDP h.264 Video Stream", port 5600).
- Split: set `PX4_VIDEO_HOST_IP=<control computer IP>` in `.env` (passed to px4-sim since 2026-10-17,
  default `127.0.0.1`); open `5600-5609/udp` on the control computer. View with QGC, `ffplay`, VLC or OpenCV:
  ```bash
  gst-launch-1.0 udpsrc port=5600 ! application/x-rtp,encoding-name=H264,payload=96 \
    ! rtph264depay ! avdec_h264 ! videoconvert ! autovideosink
  ```
- `rtmpLocation` → stream to an RTMP server (e.g. a media server on the backend) for web viewing.
- Same format as a real drone camera (RTP/H.264), so the receive side does not change later.

### C2 — frames for processing / dataset (not tested)

Script in px4-sim with Python `gz.transport13`: subscribe
`/world/<world>/model/x500_i/link/camera_link/sensor/camera/image` (check exact name with `gz topic -l`),
save JPEG every N m or N s together with ground-truth pose + navsat lat/lon + sim time
(geo-tagged images → mosaic / coverage check of MCPP). Output to a volume, upload to the server.

### C3 — ROS 2 images — implemented in the `telemetry` container (§4)

`ros_gz_bridge parameter_bridge <topic>@sensor_msgs/msg/Image[gz.msgs.Image` has to run where Gazebo
runs (px4-sim; needs ROS 2 in that image or a sidecar container sharing the network). Publish to
`/uav_i/camera`; `view_cam.py` (subscribes `/camera`) then works on the control side. Raw 1280×960×3
@30 Hz ≈ 110 MB/s → use `image_transport` compressed or lower rate/size across the network.

### C4 — record the scene view (not tested)

`ffmpeg -f x11grab -video_size 1920x1080 -i :99 -r 15 out.mp4` in px4-sim (Gazebo screen) or `:100`
(QGC). Gazebo GUI also has `/gui/screenshot`. Good for demo videos of a mission.

Order: Step 0 + C1 (see the camera in QGC) → C2 (dataset for MCPP evaluation) → C3 only with ROS 2.
C3 is done first (2026-10-17) because the `telemetry` container needed ROS 2 anyway.

---

## 4. `telemetry` service — implemented 2026-10-17

```
 px4-sim (PX4 + Gazebo)                          telemetry (same network namespace)
   PX4 uxrce_dds_client ── udp 127.0.0.1:8888 ──► MicroXRCEAgent ──► ROS 2 /fmu/out/*, /uav_i/fmu/out/*
   Gazebo gz-transport (GZ_PARTITION) ──────────► ros_gz_bridge  ──► /uav_i/gz/*, /uav_i/camera/*
                                                  ground_truth.py ─► /uav_i/gz/ground_truth
                                                  web_video_server :8083   camera in the browser
                                                  mavlink_mqtt.py  udp 14700 → MQTT → Grafana :3000 (PX4 MAVLink, 3rd link)
                                                  ros2 bag (RECORD=1)      data/bags/<date>  (mcap)
```

- Files: `docker/telemetry/Dockerfile` (ROS 2 Jazzy, `ros_gz_bridge`, `web_video_server`,
  `rosbag2_storage_mcap`, pymavlink + paho-mqtt, Micro XRCE-DDS Agent v2.4.3 from source, `px4_msgs` built from PX4-Autopilot `msg/`
  at `PX4_REF`), `docker/telemetry/entrypoint.sh` (also used natively by `scripts/run_telemetry_native.sh`).
- **No px4-sim rebuild:** px4-sim only gets env (`GZ_PARTITION`, `ROS_DOMAIN_ID`, `PX4_VIDEO_HOST_IP`).
- Network: PX4's uXRCE client sends to `127.0.0.1:8888` → telemetry must share px4-sim's network:
  `network_mode: host` on the VM, `network_mode: service:px4-sim` locally (port 8083 is published
  on px4-sim). Gazebo transport needs the same `GZ_PARTITION` in both (default partition = hostname:user).
- `px4_msgs` must match the PX4 firmware. Build with `--build-arg PX4_REF=$(docker exec px4-sim git -C /px4 rev-parse HEAD)`
  (default `main`).
- Agent v2.4.3 brings its own Fast-DDS 2.14 / Fast-CDR in `/usr/local/lib`, same sonames as ROS Jazzy's →
  entrypoint starts it with `LD_LIBRARY_PATH=/usr/local/lib`.

ROS 2 topics (drone `i`; bridges are **lazy** — Gazebo is only read while someone subscribes):

| Topic | Type | From |
| :--- | :--- | :--- |
| `/fmu/out/*` (drone 0), `/uav_i/fmu/out/*` | `px4_msgs/msg/*` | PX4 uORB (§1 B): battery, GPS, attitude, odometry, status, ... |
| `/uav_i/gz/imu` | `sensor_msgs/Imu` | Gazebo IMU 250 Hz |
| `/uav_i/gz/navsat` | `sensor_msgs/NavSatFix` | Gazebo GPS 30 Hz |
| `/uav_i/gz/air_pressure` | `sensor_msgs/FluidPressure` | barometer 50 Hz |
| `/uav_i/gz/magnetometer` | `sensor_msgs/MagneticField` | magnetometer 100 Hz |
| `/uav_i/gz/ground_truth` | `geometry_msgs/PoseStamped` | `ground_truth.py`: `/world/<w>/dynamic_pose/info` filtered by model name `<model>_i`, frame `world` (ENU), sim time, `GT_RATE` Hz |
| `/clock` | `rosgraph_msgs/Clock` | sim time |
| `/uav_i/camera/image_raw`, `/uav_i/camera/camera_info` | `sensor_msgs/Image`, `CameraInfo` | camera models only |

Camera sensor per model (`GZ_CAMERA="<link>/<sensor>"`, derived from `PX4_SIM_MODEL`, `none` = off):
`gz_x500_mono_cam`, `gz_x500_mono_cam_down`, `gz_x500_gimbal` → `camera_link/camera`;
`gz_x500_depth` → `camera_link/IMX214`. Gazebo topic: `/world/<w>/model/<model>_<i>/link/<link>/sensor/<sensor>/image`
(the camera includes are `merge='true'`, so the link belongs to the drone model).

Use it:

```bash
# camera in the browser (list of all image topics on the index page)
http://<VM_IP>:8083/                                     # local: http://localhost:8083/
http://<VM_IP>:8083/stream?topic=/uav_0/camera/image_raw

# dashboard (MAVLink telemetry, map, camera, history): Grafana http://<VM_IP>:3000 — docker/README.md "Dashboard"

# ROS 2 CLI on the server
docker exec telemetry with-ros ros2 topic list
docker exec telemetry with-ros ros2 topic echo --once /fmu/out/battery_status_v1
docker exec telemetry with-ros ros2 topic hz /uav_0/gz/imu

# record an experiment: RECORD=1 in .env → up -d --force-recreate telemetry → data/bags/<date>/*.mcap
```

| Env (telemetry) | Default | Meaning |
| :--- | :--- | :--- |
| `PX4_NUM_DRONES` / `PX4_GZ_WORLD` / `PX4_SIM_MODEL` | as px4-sim | topic names |
| `GZ_PARTITION` / `ROS_DOMAIN_ID` | `aquarius` / `0` | must equal px4-sim |
| `GZ_CAMERA` | from model | camera `<link>/<sensor>`, `none` = no camera bridge |
| `WEB_VIDEO_PORT` | `8083` | empty = off |
| `MQTT_URL` / `MAVLINK_IN` / `FLEET` | broker / `udpin:0.0.0.0:14700` / `sim` | `mavlink_mqtt.py`, `MQTT_URL` empty = off |
| `GT_RATE` | `30` | Hz of `/uav_i/gz/ground_truth` (empty = off) |
| `RECORD` / `RECORD_REGEX` | – / PX4 + gz topics, no images | `1` = `ros2 bag record -s mcap` to `/data/bags` |

Notes:
- ROS 2 topics on the VM are also visible on the LAN (host network, DDS multicast, same `ROS_DOMAIN_ID`) —
  that is T3 without discovery server, only if multicast passes between VM and control computer.
- **Camera size / rate:** px4-sim sets the camera models to `CAMERA_WIDTH`x`CAMERA_HEIGHT` @ `CAMERA_RATE`
  (default 640x480 @ 10 Hz). Without GPU, the model default 1280x960 @ 30 Hz made Baylands run at RTF 0.025
  (lockstep waits for each CPU-rendered frame); 640x480 @ 10 Hz → RTF 1.0 (measured 2026-10-17).
  The gimbal camera is 16:9 by default — set `CAMERA_HEIGHT=360` to keep its aspect.
- A camera costs CPU twice when someone watches: Gazebo renders it, GstCameraSystem always encodes
  H.264 (C1), the bridge converts only while subscribed. Try 1 drone first.
- Local: if px4-sim is recreated / restarted, `docker compose -f compose.local.yml restart telemetry`
  (a `service:` network namespace is not re-joined automatically).
