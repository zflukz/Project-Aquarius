# Docker — simulation, telemetry, dashboards, data pipeline

Everything that runs in a container lives here. App code that is not a container: `../Drone-Control/` (flight
scripts), `../Data-Pipeline/` (ROS 2 → Kafka bridge source, JSON schemas, test tools).

```
docker/
├── compose.local.yml      LOCAL: everything on your computer, builds + pushes the images
├── compose.server.yml     VM: pull only, host network — sim, telemetry, dashboards, pipeline
├── compose.control.yml    CONTROL COMPUTER: drone-control only
├── .env.example           copy to .env (all settings, comments explain each one)
├── px4-sim/               PX4 SITL ×N + Gazebo Harmonic + Baylands cache + Xvfb/VNC + QGroundControl
├── telemetry/             ROS 2: XRCE agent, Gazebo bridge, camera web view, MAVLink→MQTT, ROS 2→Kafka bridge
├── grafana/               Grafana + datasource + dashboards (aquarius.json Fleet, mission.json Mission)
├── db/migrations/         pipeline tables / functions (applied by db-migrate), db/tests/ = checks
├── ingestion/             Kafka → TimescaleDB consumer + migration runner (image aquarius-ingestion)
├── imagestream/           Kafka image-frames → MJPEG for the Mission dashboard
├── drone-control/         ROS 2 + MAVSDK + Drone-Control/*.py
├── bench_sim.sh           native vs Docker performance test
├── telemetry_plan.md · communication_plan.md · baylands_setup_guide.md   background / plans
```

| Image (`REGISTRY`/`TAG` in `.env`) | Built from | Runs in |
| :--- | :--- | :--- |
| `aquarius-px4-sim` | `px4-sim/` | local, server |
| `aquarius-telemetry` | `telemetry/` + `../Data-Pipeline/ros2_ws` (build context `pipeline`) | local, server |
| `aquarius-grafana` | `grafana/` | local, server |
| `aquarius-ingestion` | `ingestion/` + `db/migrations/` + `../Data-Pipeline/schemas/` | local, server (profile pipeline) |
| `aquarius-imagestream` | `imagestream/` | local, server (profile pipeline) |
| `aquarius-drone-control` | `drone-control/` + `../Drone-Control/` | local, control |

Public images: `eclipse-mosquitto:2`, `telegraf:1.36`, `timescale/timescaledb-ha:pg17.6-ts2.22.1` (TimescaleDB +
PostGIS), `apache/kafka:3.8.0`, `provectuslabs/kafka-ui`, `dougw/novnc`.

The `aquarius` command (`../scripts/aquarius`, guide [`../AQUARIUS_CLI.md`](../AQUARIUS_CLI.md)) wraps everything
below: `aquarius init`, `drones N`, `pipeline up`, `start mission …`, `status`, `rtl`, `urls`, `update`.

---

## Data flow

```
                    ┌ MAVLink 14640+i ─► drone-control (MAVSDK scripts)          control
                    ├ MAVLink 18640+i ─► QGroundControl                          control
 PX4 drone i ───────┼ MAVLink 14740+i ─► telemetry: mavlink_mqtt.py ─► Mosquitto ─► Telegraf ─┐
 (px4-sim, Gazebo)  │                                                                         ├► TimescaleDB ─► Grafana :3000
                    └ uXRCE-DDS :8888 ─► telemetry: ROS 2 topics ─► aquarius_bridge ─► Kafka ─► ingestion ┘   (+PostGIS)
                      Gazebo camera ───► ros_gz_bridge ─┬► web_video_server :8083           └► imagestream :8090
                                                        └► aquarius_bridge (JPEG, 2 fps) ─► Kafka image-frames
```

- **Always on:** MAVLink → MQTT → Telegraf → TimescaleDB → Grafana **Fleet telemetry** (live state, map, history).
- **Profile `pipeline`:** the Gazebo drones' ROS 2 topics → Kafka → TimescaleDB → Grafana **Mission monitor**
  (missions, search grid, coverage %, detections, survivor pins, camera of every drone). Same database, one Grafana.
- Commands (arm, mission, offboard) never go through MQTT or Kafka — only direct MAVLink.

---

## ① Local — everything on your computer

```bash
cd docker && cp .env.example .env          # first time
docker compose -f compose.local.yml up -d --build      # or: aquarius init --camera
docker compose -f compose.local.yml logs -f px4-sim    # ready: "drone0 (sysid 1) -> ..."
```

| What | URL |
| :--- | :--- |
| Gazebo / QGroundControl (noVNC) | <http://localhost:8080/vnc.html> / <http://localhost:8082/vnc.html> |
| Drone camera (ROS) | <http://localhost:8083/> (camera model only) |
| Grafana (admin / `GRAFANA_ADMIN_PASSWORD`) | <http://localhost:3000> — Fleet is the home page |
| Pipeline: Mission camera / Kafka UI | <http://localhost:8090/> / <http://localhost:8084> |
| SQL | `psql -h localhost -U aquarius aquarius` (password `TSDB_PASSWORD`) |

Run scripts in drone-control (`CONTROL_HOST=drone-control`, so MAVLink takes the same path as in the split setup):

```bash
docker compose -f compose.local.yml exec drone-control bash -c "source /opt/ros/jazzy/setup.bash && python3 flight_test.py"
docker compose -f compose.local.yml exec -e DRONE_ID=1 drone-control python3 teleop_drone.py
```

After editing `Drone-Control/*.py`: `up -d --build drone-control`. Stop: `down` (`-v` also deletes the Fuel cache,
the database and Kafka data).

## ② Publish images

```bash
echo <TOKEN> | docker login ghcr.io -u zflukz --password-stdin     # token with write:packages
cd docker
export PX4_REF=$(docker run --rm --entrypoint git ghcr.io/zflukz/aquarius-px4-sim -C /px4 rev-parse HEAD)  # px4_msgs = PX4
docker compose -f compose.local.yml --profile pipeline build
docker compose -f compose.local.yml --profile pipeline push px4-sim telemetry grafana ingestion imagestream drone-control
```

`TAG=v0.3` in front of both commands for a versioned release. GHCR packages start **private**: make them public or
`docker login ghcr.io` on the VM (token with `read:packages`).

## ③ Split — VM server + control computer

| Machine | File | Runs |
| :--- | :--- | :--- |
| VM | `compose.server.yml` | px4-sim, novnc :8081, novnc-qgc :8082, telemetry (:8083), mosquitto :1883/:9001, timescaledb (127.0.0.1:5432), telegraf, grafana :3000; profile pipeline: kafka :9092, ingestion, imagestream :8090, kafka-ui :8084 |
| Control computer | `compose.control.yml` | drone-control, udp 14540-14549 |

VM — pull only, never builds. Clone the repo (recommended) or copy `compose.server.yml`, `.env.example` and
`scripts/aquarius` into one folder:

```bash
git clone -b feature-Network-flukez https://github.com/zflukz/Project-Aquarius.git ~/Project-Aquarius
bash ~/Project-Aquarius/scripts/aquarius install
aquarius init server --control-host <CONTROL_IP> --drones 2      # + --headless --camera --pipeline
aquarius drones 4         # change the drone count (recreates px4-sim + telemetry)
aquarius update           # git pull / download compose file → pull → up -d → prune
```

Without the CLI: `cp .env.example .env`, set `CONTROL_HOST`, `GRAFANA_ADMIN_PASSWORD`, `TSDB_PASSWORD` (only applied to
empty volumes), then `docker compose -f compose.server.yml pull && … up -d`.

Control computer: `aquarius init control` (or `docker compose -f compose.control.yml up -d`). Open udp 14540-14549 +
14550 for the VM. Links: Gazebo `http://<VM_IP>:8081/vnc.html`, QGC `:8082`, Grafana
`http://<VM_IP>:3000/d/aquarius-fleet?var-cam=http://<VM_IP>:8083` (`aquarius urls` prints all of them).

VM firewall (ufw): allow from the control computer udp 14640:14649, 18640:18649 and tcp 8081-8084, 8090, 3000
(1883 / 9092 only if it subscribes MQTT / Kafka). noVNC, web video, imagestream, Kafka UI, Mosquitto and Kafka have
**no login** — LAN / VPN only.

---

## Data pipeline (profile `pipeline`)

Real data from the Gazebo drones — no fake producer needed. Off by default (Kafka costs ~0.5–1 GB RAM).

| Way | How | When |
| :--- | :--- | :--- |
| **A — on demand** | `aquarius pipeline up` / `aquarius pipeline down` (= `docker compose -f <file> --profile pipeline up -d`) | experiments, VM short on RAM |
| **B — always on** | `aquarius pipeline on` / `off` or `aquarius init --pipeline` (= `COMPOSE_PROFILES=pipeline` in `.env`) | every `up`, `update`, reboot |

Services: `kafka` (KRaft, one node), `kafka-init` (5 topics, exits), `db-migrate` (applies `db/migrations`, exits),
`ingestion`, `imagestream`, `kafka-ui`. The sender is **`aquarius_bridge` inside `telemetry`**: it waits until Kafka
and its topics exist, then publishes every drone — so `pipeline up` works without restarting the sim.

| Drone | `drone_id` | PX4 topics | Camera |
| :--- | :--- | :--- | :--- |
| 0 | `uav0` | `/fmu/out/*` | `/uav_0/camera/image_raw` |
| i | `uav<i>` | `/uav_i/fmu/out/*` | `/uav_i/camera/image_raw` |

- Telemetry 10 Hz per drone (position, attitude, battery, nav state, armed); camera as JPEG at `BRIDGE_IMAGE_FPS`
  (camera model only: `aquarius init --camera`).
- Time (A4): sim time. PX4 timestamps are mapped onto the Gazebo clock (offset estimate), images keep their Gazebo
  stamp, so both share one clock. When Gazebo runs slower than real time (RTF < 1) `ts` falls behind the wall clock —
  latency numbers are only meaningful at RTF ≈ 1.
- One mission per telemetry start (`MISSION_ID` empty = new UUID; `aquarius drones` / restart → new mission).
- Check it: `aquarius logs pipeline` (bridge `[stats]` every 10 s, ingestion counts), Kafka UI, or SQL
  `SELECT drone_id, count(*), max(ts) FROM telemetry WHERE received_at > now() - interval '1 minute' GROUP BY 1;`

**Kafka topics** (`KAFKA_PARTITIONS` = max drones, set before the first start): `telemetry` (7 d), `image-frames`
(1 d, ≤ 2 GB/partition, 2 MB messages), `detections` (7 d), `survivors` (30 d), `replanning-events` (1 partition, 30 d).
Messages follow `../Data-Pipeline/schemas/*.schema.json`; ingestion validates each one, bad ones go to
`ingest_rejects` with the reason. Inserts are batched (500 / 1 s) with `ON CONFLICT DO NOTHING`, Kafka offsets
committed after the DB commit → a crash replays but never duplicates.

**Tables** (`db/migrations`, add a new numbered file — never edit an applied one): `missions`, `drones`, `telemetry`
(hypertable, compressed after 7 d, never deleted), `detections`, `survivors`, `replanning_events`, `ingest_rejects`,
`search_area`, `grid_cells`, `flight_paths` (planned / flown), `persons` (merged survivor pins, `MERGE_RADIUS_M`),
`ground_truth_persons`, view `survivor_pins`. Telegraf's tables (`"position"`, `"status"`, …) sit in the same DB.

```sql
SELECT aq_create_search_area('<mission>', '<geojson polygon>', 'Zone A', 20);   -- area + 20 m grid
SELECT * FROM aq_coverage('<area>', 'flown', 30);    -- coverage % from the flown track, 30 m camera swath
SELECT * FROM aq_flown_paths('<mission>');           SELECT aq_save_flown_paths('<mission>');
SELECT aq_remerge_survivors('<mission>', 5.0);
```

Coordinates are stored as EPSG:4326 and measured in the area's UTM zone (`aq_utm_srid`, Baylands → 32610).

**Mission monitor dashboard** (`grafana/dashboards/mission.json`, generated by `grafana/build_dashboards.py`, panel SQL
in migration 0007): coverage, survivors, detections, active drones, rejects; map with grid / planned / flown paths /
drones / pins; drone status table; battery / altitude; camera grid (`stream_url` variable — off this computer set
`http://<host>:8090`, `aquarius urls` prints the link); pipeline latency p50/p95 and Hz per drone.

**Tools** (`../Data-Pipeline/tools/`, run from `Data-Pipeline/`; `COMPOSE=compose.server.yml` on the VM):

| Tool | Does |
| :--- | :--- |
| `phase_c_check.sh` | migrations on a throwaway DB + every check in `db/tests` + all Mission panel queries |
| `phase_b_check.sh [s]` | services, topics, data per drone, images, time sync, rows, duplicates, rejects |
| `check_dedupe.sh` | replays all Kafka data, must create 0 duplicate rows |
| `pipeline_stats.py`, `check_time_sync.py`, `save_images.py` | loss / latency / image size, image↔telemetry sync, save JPEGs |
| `demo_mission.py` | area + plan + 8 planted people; `--print-fly-command` flies fake drones (no Gazebo) |
| `sim_producer.py` | fake drones straight into Kafka — offline testing only |
| `dds/` | Fast DDS discovery server + `netem` bad Wi-Fi, for ROS 2 across machines (T3) |
| `sitl/start_swarm.sh`, `make_cam_bridge.py` | native SITL without Docker (`config/bridge_sitl.yaml`) |

Unit tests: `python3 -m pytest Data-Pipeline/ros2_ws/src/aquarius_bridge/test Data-Pipeline/tools docker/ingestion/tests docker/imagestream/tests`.

Bandwidth at 10 drones: telemetry ~30 KB/s; images 2 fps × 30–165 KB ≈ 0.6–3.3 MB/s — lower `BRIDGE_IMAGE_FPS` first.

---

## Dashboards (Grafana)

- **Fleet telemetry** (`aquarius.json`, uid `aquarius-fleet`): link / armed / mode / battery / altitude / satellites
  per drone, satellite map with track, camera (`var-cam`), altitude / speed / climb, mode timeline, mission waypoint,
  battery, GPS, sensors, events, roll / pitch. Telegraf turns MQTT `aquarius/<fleet>/drone/<i>/<stream>` into one
  hypertable per stream (1-day chunks, `TSDB_RETENTION`).
- **Mission monitor** (`mission.json`, uid `aquarius-mission`): see the pipeline section.
- Datasource `TimescaleDB` (uid `aquarius-tsdb`) for both. Edit Fleet in the UI → Export JSON into
  `grafana/dashboards/aquarius.json`; Mission in `build_dashboards.py`. Then rebuild `aquarius-grafana`.
- Raw: `docker exec mosquitto mosquitto_sub -t 'aquarius/#' -v`, `docker exec -it timescaledb psql -U aquarius aquarius`
  (VM: `ssh -L 5432:127.0.0.1:5432 <vm>`). Quote `"position"` (SQL keyword).
- Mosquitto / Telegraf / DB-init / Kafka-topics configs are inline `configs:` in both compose files (Compose ≥ 2.23.1,
  `mode: 0444`) — keep both in sync.

---

## MCPP coverage flight

`Drone-Control/mcpp_mission.py`: area `AREA_W`×`AREA_H` around drone 0 → grid of `2·SWEEP` cells → balanced split per
drone → spanning tree → Spanning Tree Coverage path → PX4 mission (QGC shows it) → takeoff, mission, RTL. Drone `i` flies
at `ALT + 3·i`. Run: `aquarius start coverage [LAT,LON] --size 40x30 --drones 2`, or `exec drone-control python3
mcpp_mission.py` (`--plan-only` prints the plan), or `AUTO_RUN=python3 mcpp_mission.py` in `.env` (runs at every
container start).

## Ports (drone `i` = PX4 instance `i` = sysid `i+1`)

| Link | PX4 binds | PX4 sends to | Used by |
| :--- | :--- | :--- | :--- |
| MAVSDK | 14640+i | `CONTROL_HOST`:14540+i | `DRONE_ID=i` scripts |
| QGC | 18640+i | `CONTROL_HOST`:14550 | QGroundControl |
| Telemetry (`TELEM=1`) | 14740+i | 127.0.0.1:14700 | `mavlink_mqtt.py` |
| uXRCE-DDS | — | 127.0.0.1:8888 | XRCE agent (telemetry) |

PX4 caps MAVLink at 6 instances → the entrypoint stops the unused payload link (14280+i) for the telemetry link.
Port ranges fit 10 drones.

## Containers

**px4-sim** — PX4 built and Baylands cached at image build. Start: kill stale processes / locks → Xvfb `:99` (Gazebo,
VNC 5900) + `:100` (QGC, 5901) → `px4 -i i -d` per drone (instance 0 starts Gazebo) → wait for rcS → open the links →
exit if any PX4 dies. Logs `docker logs px4-sim`, `/var/log/px4/px4-i.log`. Changing env needs no rebuild:
`up -d --force-recreate px4-sim telemetry`.

| Env | Default | |
| :--- | :--- | :--- |
| `CONTROL_HOST` | – | control computer (resolved once at start) |
| `PX4_NUM_DRONES` · `DRONE_SPACING` | 1 · 3 | drones, metres apart |
| `PX4_GZ_WORLD` | baylands | `default` = flat and light |
| `PX4_SIM_MODEL` / `PX4_SYS_AUTOSTART` | gz_x500 / 4001 | camera: `gz_x500_mono_cam_down` / 4014 |
| `HEADLESS` · `QGC` · `SCREEN_SIZE` | – · 1 · 1920x1080x24 | GUI off · QGC screen · Xvfb size |
| `GZ_FOLLOW` · `PX4_GZ_FOLLOW_OFFSET_X/Y/Z` | 0 · -8/-8/5 | Gazebo camera follows drone N |
| `CAMERA_WIDTH/HEIGHT/RATE` | 640/480/10 | model default 1280x960@30 → RTF 0.03 without GPU |
| `GZ_PARTITION` · `ROS_DOMAIN_ID` | aquarius · 0 | same in telemetry |
| `PX4_VIDEO_HOST_IP` | 127.0.0.1 | RTP/H.264 udp 5600+i (untested) |
| `TELEM` | 1 | 3rd MAVLink link for the dashboard |

Don't set `PX4_HOME_*` with Baylands — GPS comes from the world (37.4122, -121.9989), so QGC's map = Gazebo.

**telemetry** — shares px4-sim's network (`host` on the VM, `service:px4-sim` locally → after recreating px4-sim also
`restart telemetry`). Runs the XRCE agent (`/fmu/out/*`), `ros_gz_bridge` (`/uav_i/gz/{imu,navsat,air_pressure,
magnetometer}`, camera, `/clock`, lazy), `ground_truth.py` (`/uav_i/gz/ground_truth`), `web_video_server`,
`mavlink_mqtt.py`, `aquarius_bridge` (Kafka) and optionally `ros2 bag` (`RECORD=1` → `data/bags/<date>`). Exits if one
dies. ROS CLI: `docker exec telemetry with-ros ros2 topic list`. Topic details: [`telemetry_plan.md` §4](telemetry_plan.md).

| Env | Default | |
| :--- | :--- | :--- |
| `GZ_CAMERA` | from model | `<link>/<sensor>`, `none` = off |
| `WEB_VIDEO_PORT` · `GT_RATE` | 8083 · 30 | empty = off |
| `MQTT_URL` · `FLEET` | mosquitto · sim | empty = no MQTT |
| `KAFKA_BOOTSTRAP` | kafka:19092 / 127.0.0.1:19092 | empty = no Kafka bridge |
| `MISSION_ID` · `BRIDGE_IMAGE_FPS` | new UUID · 2 | pipeline |
| `RECORD` · `RECORD_REGEX` | – · PX4 + gz topics | ros2 bag (mcap) |

**timescaledb** — PostgreSQL 17 + TimescaleDB 2.22 + PostGIS 3.6, DB / user `aquarius`, volume `tsdb-data`,
`TSDB_MEMORY` (1 GB) for tuning, VM listens on 127.0.0.1 (`TSDB_PORT`). `TSDB_PASSWORD` / `GRAFANA_ADMIN_PASSWORD`
apply to empty volumes only.

**drone-control** — idles; `docker compose exec` runs scripts; `DRONE_ID` → `udpin://0.0.0.0:14540+i`, `MAVLINK_URL`
overrides; `AUTO_RUN` runs once at start.

## Performance test — `bench_sim.sh`

`./bench_sim.sh [native|docker|both]` (env `DRONES`, `DURATION`, `WORLD`, `HEADLESS`, `COMPOSE_FILE`): N idle drones,
reports `boot_s`, `ready_s`, `rtf` (main number), `cpu_%`, `rss_mb` → `bench_results.csv`. Compare only on the Linux VM
(Docker Desktop falls back to `docker stats`). Measured on this PC: native boot 17.7 s / RTF 1.006, Docker 40.3 s / 1.018.

Load notes (6 vCPU / 7.7 GB WSL): 1 camera drone headless RTF ~0.9–1.0; GUI + QGC ~0.8; **2 camera drones +
pipeline ≈ 0.34** (CPU rendering). Use `HEADLESS=1`, `QGC=0`, fewer camera drones or a lower `CAMERA_RATE`.

## Troubleshooting

| Symptom | Fix |
| :--- | :--- |
| Drone moves in QGC, not in Gazebo | camera follow: `GZ_FOLLOW=0`, wait ~20 s for "Gazebo camera follows" |
| `restart` → no Gazebo screen / "QGC already running" | fixed in the entrypoint (stale X / QGC locks) — recreate if it persists |
| Scripts hang, udp 14540 busy | leftover `mavsdk_server`: `docker exec drone-control pkill -f mavsdk_server` |
| Git Bash mangles `/tmp/...` | `MSYS_NO_PATHCONV=1` |
| No ROS topics after px4-sim recreate (local) | `docker compose -f compose.local.yml restart telemetry` |
| Pipeline: no rows | `aquarius logs pipeline`: `waiting for Kafka` = profile not running; `px4 offset n/a` = PX4 not connected yet (~30 s after a telemetry restart); wrong topic names = `px4_msgs` ≠ PX4 (`PX4_REF`) |
| `db-migrate` exited 1 | `docker logs db-migrate` names the failing file |
| `KAFKA_PARTITIONS` change ignored | topics exist: `pipeline down`, `docker volume rm aquarius-<mode>_kafka-data`, `pipeline up` |
| Kafka unreachable from another machine | `KAFKA_ADVERTISED_HOST=<this host's IP>` + recreate kafka |
| Old DB volume | before 2026-11-08 the DB was `timescaledb-data` (no PostGIS): `docker volume rm aquarius-<mode>_timescaledb-data` |

## Future plan

1. Now: N drones in one px4-sim (VM sizing: 1–2 drones 4 vCPU / 8 GB; 3–5 → 8 / 16 GB, headless; 5–10 → flat world).
2. Swarm control from the control computer (`asyncio.gather` over one MAVSDK `System` per drone); DDS across machines
   with the discovery server in `Data-Pipeline/tools/dds/` (T3).
3. Scale out: one container per drone (`ROLE=gz-server` / `px4-instance`, `GZ_PARTITION` + `GZ_IP` across VMs), health
   check per drone, CI that builds + pushes the images.
4. Communication phases M3–M6 (ground truth to MQTT, MQTT auth / TLS, real drones via `mavlink-router`):
   [`communication_plan.md`](communication_plan.md). Telemetry / camera options: [`telemetry_plan.md`](telemetry_plan.md).
