# Communication plan — telemetry, control and video

How data moves between the drones (simulated now, real later), the VM server, the control computer and
dashboards. It builds on [`telemetry_plan.md`](telemetry_plan.md), which lists what data exists and
documents the ROS 2 `telemetry` service (§4).

Status 2026-10-17: **plan only**. Nothing in §4–§6 is implemented. The ports were checked against the
running px4-sim (`px4-rc.mavlink`). The resource numbers in §7 are estimates.

---

## 1. Today

```
PX4 (px4-sim, VM)
 ├─ MAVLink 14640+i → CONTROL_HOST:14540+i ──► MAVSDK scripts (flight_test, teleop, mcpp_mission)   control + telemetry
 ├─ MAVLink 18640+i → CONTROL_HOST:14550   ──► QGroundControl                                         control + telemetry
 ├─ uXRCE-DDS → 127.0.0.1:8888 ──► telemetry container ──► ROS 2 topics ──► Foxglove :8765, ros2 bag   telemetry (VM only)
 ├─ Gazebo ──► ros_gz_bridge / ground_truth.py ──► ROS 2 (/uav_i/gz/*, /uav_i/camera/image_raw)
 └─ camera ──► web_video_server :8083 (MJPEG) / GstCameraSystem RTP H.264 → PX4_VIDEO_HOST_IP:5600+i
```

Gaps:
- **No dashboard and no history**: Foxglove is live-only, and bags are files you have to open by hand.
- **ROS 2 doesn't cross networks easily**: DDS discovery uses multicast, so VM ↔ control computer needs a discovery server or Zenoh (T3).
- **Every consumer is a point-to-point UDP link**: adding a new consumer means adding a new PX4 MAVLink link.
- **Real drones**: they speak MAVLink over a radio or companion computer. A ROS 2 / DDS link from a drone to the ground over WiFi or 4G is fragile.

---

## 2. Requirements

| # | Requirement | Notes |
| :--- | :--- | :--- |
| R1 | Live telemetry for N drones in a browser dashboard | battery, position, altitude, speed, attitude, mode, armed, GPS, health |
| R2 | History: compare flights, export data | at least weeks; MCPP evaluation |
| R3 | Same path for sim and real drones | only the source changes |
| R4 | Works across networks (VM ↔ control computer, later 4G / internet) | outbound connections only, survives drops |
| R5 | Many consumers without touching PX4 | dashboard, MCPP analysis, alerts, a future web app |
| R6 | Control stays low-latency and safe | arm / mission / offboard never behind a broker |
| R7 | Video separate from telemetry | bandwidth, latency |
| R8 | Light enough for one VM | the sim already uses ~4–5 GB RAM with the camera |

---

## 3. Options compared

| | **ROS 2 / DDS** (current) | **MAVLink + MQTT** | **MAVLink + MQTT + Kafka** |
| :--- | :--- | :--- | :--- |
| Source | PX4 uORB via XRCE agent | PX4 MAVLink via a bridge | same as MQTT |
| Data | all uORB topics, raw Gazebo sensors, ground truth, camera | MAVLink set (battery, GPS, attitude, mode, status, …), 1–50 Hz | same as MQTT |
| Real drones (R3) | needs ROS on a companion computer | **standard**: every PX4 / ArduPilot drone and radio | standard |
| Across networks (R4) | hard (multicast discovery) | **easy**: clients connect out to one broker over TCP; QoS, reconnect, last-will | MQTT at the edge, Kafka inside the backend |
| Many consumers (R5) | yes, but each one needs ROS + matching `px4_msgs` | topic tree + wildcards, any language, browser via WebSocket | partitions by drone, consumer groups, own pace |
| History (R2) | `ros2 bag` files | none in the broker (only the retained last value) → needs a database | **durable log, replay any range** |
| Grafana | needs a custom ROS → DB writer | **Telegraf `mqtt_consumer` → InfluxDB, config only** | Telegraf `kafka_consumer` → InfluxDB |
| Video (R7) | image topics (heavy) | not suitable | not suitable |
| Control (R6) | possible (offboard via `/fmu/in/*`) | not through the broker | not through Kafka |
| Weight (R8) | ~350 MB (running) | Mosquitto ~10 MB | **+1–2 GB** (JVM, KRaft) and more ops work |

**Decision:** MAVLink → MQTT is the telemetry backbone, and Grafana reads it through Telegraf and InfluxDB.
ROS 2 stays for the data only it has: sim ground truth, raw sensors, camera, Foxglove and bags.
Kafka is only added when the triggers in §6 are met.

---

## 4. Target architecture

```
                         ┌── MAVLink 14640+i ──► MAVSDK scripts         (control, unchanged)
                         ├── MAVLink 18640+i ──► QGroundControl          (control, unchanged)
 PX4 drone i  ───────────┤
 (sim or real)           └── MAVLink 14740+i → 127.0.0.1:14700 ──► mavlink_mqtt.py ──┐   (telemetry, NEW)
                                                                                     ▼
 telemetry container (ROS 2) ── ros_mqtt.py (ground truth, sim only) ──────────► Mosquitto :1883 / ws :9001
                                                                                     │  aquarius/drone/<i>/...
                                       ┌─────────────────────────────────────────────┼──────────────────────┐
                                       ▼                                             ▼                      ▼
                                Telegraf → InfluxDB :8086 → Grafana :3000     web app / alerts       MCPP analysis
                                (history)                  (dashboards)       (MQTT over WS)         (Python, paho)

 video: GstCameraSystem RTP H.264 :5600+i  /  web_video_server MJPEG :8083   (never through MQTT / Kafka)
```

### 4.1 Telemetry link from PX4 (sim)

`docker/px4-sim/entrypoint.sh` and `scripts/run_sim_native.sh` add a third link per drone, next to the existing
MAVSDK / QGC links:

```bash
mavlink start -u $((14740+i)) -o 14700 -t 127.0.0.1 -m normal -r 200000   # bind 14740+i, all drones -> 14700
```

- Free ports: PX4 already uses 13030/13280/14030/14280/14540/14580/14640/18570/18640/19410/19450 (+i) and 14550.
- All drones send to **one** port (14700). The bridge tells them apart by `sysid` (= drone i + 1), so there's one socket and one process for N drones.
- Env: `TELEM_HOST` (default `127.0.0.1`, because the bridge runs on the VM), `TELEM=0` turns the link off.

### 4.2 Bridge `mavlink_mqtt.py` (in the telemetry image, also native)

- **pymavlink** (not MAVSDK): one UDP socket for all drones, no `mavsdk_server` per drone, and it doesn't touch the ports the control scripts use.
- Requests stream rates (`MAV_CMD_SET_MESSAGE_INTERVAL`), decodes, rate-limits per stream, and publishes JSON to MQTT (paho-mqtt).
- Sets a last-will per drone (`online=false`) and marks a drone offline after 3 s without a HEARTBEAT.
- Env: `MAVLINK_IN=udpin:0.0.0.0:14700`, `MQTT_URL=mqtt://127.0.0.1:1883`, `MQTT_USER/PASS`, `FLEET=sim`.

### 4.3 Real drones (phase M5)

| Setup | Where the bridge runs |
| :--- | :--- |
| Telemetry radio (SiK) → ground station | ground PC: `mavlink-router` splits the radio stream to QGC + MAVSDK + `mavlink_mqtt.py` |
| Companion computer (Pi / Jetson) on WiFi / 4G | companion: `mavlink-router` + `mavlink_mqtt.py` → broker over the internet (TLS) |

The topic schema stays the same, so the dashboards don't change. `FLEET=real` keeps the data apart from the sim.

---

## 5. MQTT contract

### 5.1 Topics

```
aquarius/<fleet>/drone/<i>/<stream>          fleet = sim | real, i = drone index (sysid - 1)
```

| Stream | Content (JSON fields) | Source (MAVLink) | Rate | QoS / retain |
| :--- | :--- | :--- | :--- | :--- |
| `online` | `true` / `false` | HEARTBEAT, last-will | on change | 1 / retained |
| `status` | `armed`, `mode`, `system_status`, `landed` | HEARTBEAT, EXTENDED_SYS_STATE | 1 Hz + on change | 1 / retained |
| `position` | `lat`, `lon`, `alt_amsl`, `alt_rel`, `vn`, `ve`, `vd`, `hdg` | GLOBAL_POSITION_INT | 10 Hz | 0 |
| `attitude` | `roll`, `pitch`, `yaw` (deg) | ATTITUDE | 10 Hz | 0 |
| `battery` | `pct`, `voltage`, `current` | BATTERY_STATUS, SYS_STATUS | 1 Hz | 0 / retained |
| `gps` | `fix_type`, `sats`, `eph`, `epv` | GPS_RAW_INT | 1 Hz | 0 |
| `health` | `gyro_ok`, `accel_ok`, `mag_ok`, `gps_ok`, `home_ok`, … | SYS_STATUS sensor bits | 1 Hz | 0 |
| `mission` | `seq`, `total` | MISSION_CURRENT | on change | 1 |
| `event` | `severity`, `text` | STATUSTEXT | on message | 1 |
| `ground_truth` (sim) | `x`, `y`, `z` (ENU m), `qx..qw` | `/uav_i/gz/ground_truth` via `ros_mqtt.py` | 5 Hz | 0 |

Payload: `{"t": <unix ms>, ...fields}` (+ `"sim_t"` for sim data from Gazebo). SI units, angles in degrees, one flat object.
Raw IMU and the camera are deliberately **not** on MQTT; they stay in ROS 2 / bags.

### 5.2 Rules
- **No commands over MQTT** in M1–M5. Arm, mission and offboard stay on direct MAVLink (MAVSDK / QGC), for R6. A future `…/cmd` topic would need auth, ACL, acknowledgements and a geofence check first.
- Wildcards for consumers: `aquarius/sim/drone/+/battery`, `aquarius/+/drone/#`.

---

## 6. Kafka — when, and how

Add Kafka only when **at least one** of these is true:
- Analyses need to replay or reprocess flights from the raw stream, and the InfluxDB data isn't enough.
- More than ~5 independent backend services consume the stream, at different speeds.
- Many VMs or sites, or tens of drones, with throughput the single broker can't handle.
- Stream processing is needed (Kafka Streams / Flink, e.g. live coverage % of MCPP).

How: Mosquitto → Kafka through a Kafka Connect MQTT source (or switch the broker to EMQX with its Kafka sink).
Topic `aquarius.telemetry`, key = `<fleet>/<i>` (one partition per drone keeps order), retention e.g. 30 days.
Telegraf then reads from Kafka instead of MQTT. Nothing on the drone side changes.

---

## 7. Services and ports (VM)

| Service | Image | Port | Exposed | RAM (est.) |
| :--- | :--- | :--- | :--- | :--- |
| Mosquitto | `eclipse-mosquitto:2` | 1883 (MQTT), 9001 (WebSocket) | LAN / VPN, password | ~10 MB |
| `mavlink_mqtt.py` | `aquarius-telemetry` (2nd process) or own small image | udp 14700 (local) | no | ~50 MB |
| Telegraf | `telegraf:1.3x` | – | no | ~50 MB |
| InfluxDB 2 | `influxdb:2` | 8086 | localhost only | ~200–300 MB |
| Grafana | `grafana/grafana-oss` | 3000 | LAN / VPN, login | ~100 MB |
| Kafka (only §6) | `apache/kafka` (KRaft) | 9092 | no | 1–2 GB |

Firewall additions: 1883 / 9001 / 3000 tcp from the control computer. Secrets (`MQTT_PASS`, `GRAFANA_ADMIN_PASSWORD`,
`INFLUX_TOKEN`) go in `docker/.env`, never in compose files.

Grafana is provisioned from files (`docker/grafana/provisioning/`): an InfluxDB datasource plus an **Aquarius** dashboard
with a drone selector variable and these panels:
- Geomap: positions and flight path.
- Time series: altitude, speed, battery.
- Stat: battery %, satellites, online.
- State timeline: mode and armed.
- Table: events.
- Camera: HTML panel showing the MJPEG stream from `:8083`.

---

## 8. Phases

| Phase | Work | Done when |
| :--- | :--- | :--- |
| **M1** | 3rd PX4 link (§4.1) in entrypoint + native; `mavlink_mqtt.py`; Mosquitto in `compose.local/server.yml` | `mosquitto_sub -t 'aquarius/#' -v` shows every stream for 2 drones during `flight_test.py`; `online` goes false when px4-sim stops |
| **M2** | Telegraf + InfluxDB + Grafana, provisioned dashboard | dashboard shows a live flight and the previous one after restart; refresh ≤ 1 s |
| **M3** | `ros_mqtt.py`: ground truth (sim) → MQTT; Grafana panel: estimate vs truth error | error panel during an MCPP flight |
| **M4** | Security: Mosquitto passwords + ACL (bridge writes, others read), Grafana login, TLS if over internet; split VM / control test | control computer opens Grafana and subscribes MQTT across the real network |
| **M5** | Real drone path (§4.3) with `mavlink-router` | same dashboard, `fleet=real` |
| **M6** | Kafka, only if §6 applies | consumer replays a past flight from Kafka |

Each phase also: native script twin (`scripts/`), `.env.example`, `README.md` / `WORKFLOW.md`, and a CLAUDE.md change log.

---

## 9. Open questions

1. Where do dashboards live: on the sim VM (simplest) or a separate backend VM? This also decides where MQTT and InfluxDB run.
2. LAN / VPN only, or reachable over the internet (TLS + domain needed)?
3. Target fleet size for the project (5? 10? more?), which decides whether §6 ever applies.
4. Retention: how long to keep flight data (InfluxDB retention policy)?
5. Real drone hardware: radio-only or a companion computer? This decides where the bridge runs (§4.3).
