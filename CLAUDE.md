# CLAUDE.md — Project Aquarius

Guidance for Claude Code (and humans) working in this repo, plus a record of the Docker /
simulation rework and how each part was verified.

## Project

Drone project (KMUTT senior project, "Xenoir"): PX4 SITL + Gazebo Harmonic simulation, controlled
with MAVSDK / ROS 2 scripts. Goal: multi-drone (swarm) simulation on a VM server, controlled from
a separate computer, later real drones.

| Path | What |
| :--- | :--- |
| `docker/` | **everything Docker / simulator / infra** — see `docker/README.md` (incl. data pipeline); telemetry + camera plan: `docker/telemetry_plan.md`; MAVLink → MQTT → Grafana plan: `docker/communication_plan.md`; DB migrations `docker/db/`, `ingestion/`, `imagestream/`; **merge status / resume: `docker/merge_status.md`** |
| `Data-Pipeline/` | pipeline code (was `data/`): `ros2_ws/src/aquarius_bridge` (ROS 2 → Kafka, built into the telemetry image), `schemas/` (A2 JSON schemas), `tools/` (checks, fake drones, DDS discovery server, native SITL), `results/` |
| `Drone-Control/` | `flight_test.py`, `teleop_drone.py`, `mcpp_mission.py`, `mission_cli.py` (MAVSDK), `view_cam.py` (ROS 2 camera) |
| `WORKFLOW.md` | end-to-end user workflow (setup → fly → camera → roadmap) |
| `AQUARIUS_CLI.md` | user guide for the `aquarius` command (`scripts/aquarius`): every command, option, example, troubleshooting |
| `scripts/` | `aquarius` CLI (init / drones N / update / start mission LAT,LON / coverage / status / rtl / land); **native** (non-Docker) install / run / uninstall — same software + ports as the images |
| `Document/` | LaTeX proposal (Thai, XeLaTeX, devcontainer in `.devcontainer/`) — unrelated to sim |
| `Robomaster-TT/`, `InfoData/` | side experiments / Obsidian notes |

## Project phases (roadmap, 2026-11-13)

Concept: user picks the search area → **central planner** splits it into one coverage path per drone (MCPP) → drones fly
→ camera images → AI (YOLO) finds people → geo-localization → pins on the dashboard → central **re-plans** when the
fleet changes. AI + image processing (P6, P7) are done by other team members; this repo provides their inputs / outputs.

Status confirmed by the user 2026-11-14 (P6 / P7 = other team, not asked).
✅ done · 🟡 partial · ⬜ not started · 👥 other team. Team data-plan phase names in brackets (A schemas, B pipeline,
C database, D dashboard, E survivors / post-mission, F evaluation); M = `communication_plan.md`, T / C = `telemetry_plan.md`.

| Phase | Work | Owner | Status | Done / missing |
| :--- | :--- | :--- | :--- | :--- |
| P0 Simulation | PX4 SITL ×N + Gazebo Baylands in Docker, QGC / noVNC, camera model | us | ✅ | done with Gazebo (user) |
| P1 Infra / deploy | compose local / server / control, GHCR images, `aquarius` CLI, VM pull, split mode | us | 🟡 | running on the VM server but an **old version** → push current images + `aquarius update` on the VM |
| P2 Area input | user picks the search area | us | 🟡 | map UI done on branch **`FEAT-WEB-DASHBOARD`** (not merged; polygon is only copied to the clipboard, not sent to the planner — see "P2 area input" below); this branch: CLI rectangle `LAT,LON --size WxH` only; DB `search_area` not created for real flights |
| P3 Central planning (MCPP) | partition + STC per drone → PX4 missions over MAVLink | us | ✅ | **2-drone flight PASSED 2026-11-14** (`aquarius start coverage --size 40x30 --drones 2`, see change log); 1-drone flight PASSED, `--plan-only` 1–4 drones; still missing: polygon area, planned paths not written to `flight_paths`; Grafana checked via DB only (not by eye) |
| P4 Telemetry + data pipeline | MAVLink → MQTT → Telegraf, ROS 2 → Kafka → ingestion → TimescaleDB + PostGIS (A, B, C; M1, M2; T2, T5) | us + data team | 🟡 | built, **not tested by the user** (Claude's local tests only, VM not tested) |
| P5 Dashboard | Grafana Fleet, Mission monitor, Path compare (D) | us + data team | ✅ | Grafana runs (user); gap: coverage % / planned path empty for real flights until P2 / P3 write `search_area` / `flight_paths` |
| P6 AI detection | YOLO on Kafka `image-frames` → Kafka `detections` | 👥 | 🟡 | **template merged 2026-11-15** (PR #2, `Data-Pipeline/ai/`): Kafka consumer + schema check + evidence JPEG + `fake` / YOLO (`ultralytics`) detector, 16 unit tests pass; real model = other team; not run against live Kafka; not in compose (run by hand); sim-time lag issue, see change log 2026-11-15 |
| P7 Geo-localization | detection + drone pose + camera model → lat/lon → Kafka `survivors` | 👥 | ⬜ | not in this repo; contract `survivors.schema.json`; receiving side done (ingestion → `persons` merge C5 → pins) |
| P8 People in the sim | person models in the Gazebo world + `ground_truth_persons` (F3) | ? | ⬜ | needed to test P6 / P7 (Baylands has no people) |
| P9 Re-planning | central re-plan when the fleet changes, see below | us | ⬜ | `replanning-events` schema / topic / table exist, nothing sends them |
| P10 Evaluation | post-mission summary (E4), benchmarks: coverage, time, detection accuracy (F4); `bench_sim.sh` / `bench_paths.sh` on the VM | ? | ⬜ | not started (user); tools exist (`bench_sim.sh`, `bench_paths.sh`, `pipeline_stats.py`) |
| P11 Security + network | MQTT auth / ACL, Grafana login, VPN / TLS (M4); DDS across machines (T3) | us | ⬜ | |
| P12 Real drones | `mavlink-router`, companion computer, `FLEET=real` (M5) | us | ⬜ | |

### P2 area input — branch `FEAT-WEB-DASHBOARD` (read 2026-11-14)

One commit `ddef605` "initialize React interface with Google Maps area selection", branched from an **old** commit
(before MCPP / telemetry / pipeline; its CLAUDE.md lists only `flight_test.py` / `teleop_drone.py`).

- Files: `Interface/` (React 18 + Vite 5 + `@vis.gl/react-google-maps`; `App.jsx`, `AreaPolygon.jsx`),
  `docker/interface/` (node build → nginx 1.27, API key injected into `index.html` at start), `docker/compose.map.yml`
  (service `interface`, image `aquarius-interface`, port `MAP_PORT` 8090), `.env.example` keys `GOOGLE_MAPS_API_KEY`,
  `MAP_PORT`.
- What it does: hybrid satellite map, centred on KMUTT (13.6513, 100.4964); click to add polygon points, drag to move,
  right-click to delete, Undo / Clear / **Copy**. Output JSON:
  `{"points": [{"lat", "lng"}, ...], "center": {"lat", "lng"}, "area_m2": n}` (7 decimals, area from Google geometry).
- **Gaps for the concept:**
  1. Output goes only to the **clipboard** — no backend / API, nothing sends the area to the planner (central).
  2. **Polygon vs rectangle:** `mcpp_mission.py` / `aquarius start coverage` take only a rectangle (`LAT,LON --size WxH`);
     MCPP needs polygon support (keep grid cells inside the polygon, like `aq_make_grid` in the DB).
  3. **Location:** map starts at KMUTT, the sim is at Baylands (37.4122, -121.9989); `mission_cli.py` refuses points
     > `MAX_DIST` 1000 m from home → the map should start at the drones' home (from telemetry / DB) in sim mode.
  4. **Port clash:** `MAP_PORT` 8090 = imagestream 8090 on this branch → pick another (e.g. 8085).
  5. **Merge:** based on an old commit; expect conflicts in `CLAUDE.md` and `docker/.env.example`; the service should
     move into `compose.local/server.yml` (VM) instead of a separate `compose.map.yml`.
  6. Field names: `lng` here vs `lon` in the Kafka schemas; GeoJSON (`aq_create_search_area`) needs `[lon, lat]` order
     and a closed ring.
  7. The Google Maps key is visible in the page (normal for the JS API) → restrict it by HTTP referrer in Google Cloud.
- **2026-11-14 local state:** Docker Desktop (now 16 CPU / 8 GB) had lost every Aquarius image except ingestion /
  interface and all `aquarius-local_*` volumes; not logged in to GHCR → user chose **rebuild locally**. `docker/.env`
  created from the example: `PX4_NUM_DRONES=2`, `NUM_DRONES=2`, `HEADLESS=1`, `QGC=0`, `AQUARIUS_MODE=local`.
  Building px4-sim + drone-control first, then telemetry (`PX4_REF` = px4-sim's PX4 commit — the Dockerfile clones
  PX4 `main` unpinned, so a rebuild picks up a newer PX4), grafana, timescaledb.
  First build run: `drone-control` image done (3.46 GB); `px4-sim` got through all 10 steps but the build was **stopped
  while exporting layers** — Claude Code killed the background job because the PC ran low on memory. Layers are in the
  build cache → a re-run should only redo the export. Re-run (user asked): **`px4-sim` built** (11.9 GB, export only),
  PX4 `0bd95058` (main, 2026-11-13) → `PX4_REF=0bd95058aa0453086f68817f6aa7d76df47d56f9` in `docker/.env`;
  **`grafana` (975 MB) + `timescaledb` (753 MB) built**; `telemetry` build started. C: has only ~50 GB free (90 %).
  **`telemetry` build FAILED — Docker engine crashed** at 28 % of the Micro XRCE-DDS Agent compile (Fast-DDS C++,
  `failed to receive status: rpc error: code = Unavailable … EOF`, then the engine answers 500). Cause (likely): the
  Dockerfile compiles with `-j"$(nproc)"` = **16 parallel C++ jobs** (colcon also uses all cores) inside the 8 GB WSL
  VM → out of memory → engine killed (same symptom as 2026-10-13). Fix options: restart Docker Desktop, then
  (a) cap build jobs (Dockerfile `ARG BUILD_JOBS=4` for `cmake --build -j` + `MAKEFLAGS` / colcon
  `--parallel-workers`), and/or (b) `%USERPROFILE%\.wslconfig` `[wsl2] memory=12GB` (PC has 15.7 GB) + `wsl --shutdown`.
  Waiting for the user.
- **Decision (user, 2026-11-14): skip the web UI for now.** The area is **mocked with a JSON file** in the same
  format as the UI's output (`points` / `center` / `area_m2`) so the UI can plug in later; focus = 2-drone sim (P3).
- Later link (proposal, not decided): web UI → small API on the control computer / VM → mission manager (P9) runs
  polygon MCPP + uploads missions; same area → `aq_create_search_area()` so the Mission dashboard shows coverage.

### P9 Re-planning — design (decided by the user 2026-11-14, not implemented)

Today: plan once → upload the whole mission to PX4 over MAVLink → PX4 flies alone; nothing reacts to a drone leaving.

- **Mission manager** on the **control computer** (decided; may move to the VM later): a long-running central process
  (grows out of `mcpp_mission.py`) with one MAVSDK connection per drone for the whole mission.
- **State**: the area's cell grid; a cell is covered once its drone passed those waypoints (`mission_progress`).
  Read live over MAVLink; DB / Kafka only for logging and the dashboard.
- **Triggers — all five (decided):**
  1. **Low battery** — battery only *just enough to get back to base*: trigger when
     `remaining % ≤ needed_home % + reserve %`, `needed_home % = (distance to home / speed + landing time) × drain rate`
     (drain rate measured live from the battery stream, % per s); reserve configurable (start ~10 %). Drone → RTL,
     its uncovered cells go to the others. PX4's own low-battery failsafe (`BAT_LOW_THR`, `COM_LOW_BAT_ACT`) stays as
     the safety net and must trigger *later* than ours.
  2. **Drone lost** — no heartbeat for N s or PX4 failsafe → its cells go to the others.
  3. **Finished early** — a drone done before the rest takes cells from the drone with the most left.
  4. **Area changed / drone added** by the user → re-plan everything still uncovered.
  5. **Survivor found** (needs P7) → nearest drone flies there to confirm, then the rest is re-split.
- **Action**: uncovered cells → MCPP partition again with the active drones' current positions as seeds → new missions
  uploaded over **MAVLink** only to drones whose path changed (PX4 in-flight mission upload — verify behaviour) →
  Kafka `replanning-events` → `flight_paths` planned `version + 1`.
- **Commands never over MQTT / Kafka** (`communication_plan.md` R6); brokers only report.
- **Control computer needs from the VM**: Kafka 9092 reachable (`KAFKA_ADVERTISED_HOST=<VM_IP>`, firewall) to send
  `replanning-events`. The DB listens on 127.0.0.1 only → the manager cannot write `flight_paths` directly: either a
  Kafka topic for planned paths (ingestion writes them — new schema, data team) or an SSH tunnel. **Open.**
- **Schema v2 draft for `replanning-events`** — *not agreed; the user will discuss it with the data team*:
  `event_type` adds `low_battery` (today `rtl_triggered`), `drone_lost`, `finished_early`, `area_changed`,
  `drone_added`, `survivor_confirm`; optional fields `reason` (text), `area_id`, `plan_version`. Until agreed, use v1
  only (`rtl_triggered` / `reassigned` / `mission_complete`).
- **Before building P9 (next steps, agreed 2026-11-14):**
  1. ~~Read P2 on branch `FEAT-WEB-DASHBOARD`~~ → done 2026-11-14: polygon, clipboard only (see "P2 area input").
     → P9 must re-split a **polygon**, so polygon MCPP comes first.
  2. Test a **2-drone MCPP flight** (closes P3's ❓): `aquarius drones 2` → `aquarius start coverage --size 40x30
     --drones 2` → both drones fly their own part, RTL, `MISSION DONE`; check QGC shows both missions and Grafana both
     tracks. Record the result in the P3 row.

## Resume here (2026-11-15) — P3 done, PRs #1 / #2 merged + reviewed; next: mock-area JSON + polygon MCPP

Branch `feature-Network-flukez` at `617745a` (A–G work committed in `1355a50`, then PR #1 fix-bridge-clock + PR #2
ai-template merged). Review + tests: change log 2026-11-15 at the end. To deploy PR #1, rebuild `telemetry` (only
the last layers) — the running image still has the old bridge.

**Steps A–G below are DONE** (results: change log "2-drone MCPP flight" at the end of this file). The stack is still
running locally with 2 drones (`TSDB_PORT=5433`). Next: mock-area JSON in the UI format (`points` / `center` /
`area_m2`) + polygon MCPP → P9 re-planning. Not done yet: look at Grafana Fleet in a browser (only checked via DB /
API), and QGC with 2 drones (`QGC=0` this run).

### (older) state before steps A–G

**State now**
- Branch `feature-Network-flukez`, last commit `38cd3eb` (user); only `CLAUDE.md` uncommitted. P2 web UI is on
  `FEAT-WEB-DASHBOARD` and is skipped for now (area = mock JSON later).
- `docker/.env` exists (gitignored): `PX4_NUM_DRONES=2`, `NUM_DRONES=2`, `HEADLESS=1`, `QGC=0`, `AQUARIUS_MODE=local`,
  rest = `.env.example` defaults (`gz_x500`, no camera, `TELEMETRY_PATH=both`, `COMPOSE_PROFILES` empty = no pipeline).
- `docker/.env` also has `PX4_REF=0bd95058aa0453086f68817f6aa7d76df47d56f9` (= PX4 inside the new px4-sim).
- Docker Desktop 28.3.2, 16 CPU / 8 GB WSL (no `.wslconfig`), PC 15.7 GB RAM, C: ~50 GB free.
  **Session ended with the Docker engine crashed** (API answers 500) after the telemetry build ran out of memory.
- Images (2026-11-14): ✅ `aquarius-px4-sim` 11.9 GB, ✅ `aquarius-drone-control` 3.46 GB, ✅ `aquarius-grafana` 975 MB,
  ✅ `aquarius-timescaledb` 753 MB, ❌ **`aquarius-telemetry`** (build crashed the engine, see 2026-11-14 local state
  above). `aquarius-ingestion` / `aquarius-interface` old. Not logged in to GHCR. No `aquarius-local_*` volumes.
- Not decided yet: Claude may add `ARG BUILD_JOBS=4` to `docker/telemetry/Dockerfile` (step B) — **ask the user first**.
- **Update 2026-11-14 (later session):** step A done — `C:\Users\Flukz\.wslconfig` `[wsl2] memory=12GB` created,
  `wsl --shutdown`, Docker Desktop restarted → engine 28.3.2, 16 CPU / 11.7 GB, all built images still present.
  Step B done (user chose **8**): `ARG BUILD_JOBS=8` (placed after the ROS layer so its cache stays valid) →
  XRCE `cmake --build -j${BUILD_JOBS}` + `MAKEFLAGS`, px4_msgs `colcon --parallel-workers` + `MAKEFLAGS`,
  aquarius_bridge `colcon --parallel-workers`. Step C (telemetry build): **engine survived** — ROS layer redone
  (cache lost, 1148 s), XRCE agent `-j8` 167 s, px4_msgs 254 s, pip 158 s, export + `naming to …telemetry:latest done`;
  Claude Code then killed the background job for low **host** memory during the final "unpacking" step. Image listed
  (946 MB); unpack possibly incomplete → re-run (user asked): cached, export 186 s incl. unpack 123 s, exit 0 →
  **`aquarius-telemetry` 4.34 GB built — step C done.**

**Next steps — each with the state it must reach**

| # | Do | Must end in |
| :--- | :--- | :--- |
| A | Restart Docker Desktop (tray → Quit, open again). Recommended first: `C:\Users\Flukz\.wslconfig` = `[wsl2]` + `memory=12GB`, then `wsl --shutdown`, then start Docker Desktop | `docker info` works; `docker images` still lists the 4 built images |
| B | With the user's OK: cap compile jobs in `docker/telemetry/Dockerfile` (`ARG BUILD_JOBS=4` → `cmake --build -j${BUILD_JOBS}`, `MAKEFLAGS=-j${BUILD_JOBS}`, colcon `--parallel-workers ${BUILD_JOBS}`) | Dockerfile no longer uses `$(nproc)` |
| C | Close heavy apps, then `cd docker && docker compose -f compose.local.yml build telemetry` (≈ 50 min+ cold; if Claude's background job is killed for memory, user runs it with `!`) | `aquarius-telemetry` image present (~4 GB); engine still up |
| D | `aquarius init local --drones 2 --headless` (or `docker compose -f compose.local.yml up -d`) | `docker logs px4-sim` shows `drone0 (sysid 1) -> …` **and** `drone1 (sysid 2) -> …`; `aquarius status` lists 2 drones, GPS fix, ready |
| E | `aquarius start coverage --size 40x30 --drones 2` | each drone gets its own region, uploads its mission, takes off (drone1 3 m higher), flies all waypoints, RTL, lands; log ends `=== … DONE ===`, exit 0; no leftover `mavsdk_server` |
| F | Grafana <http://localhost:3000> → Fleet | drone 0 + drone 1 online, two tracks on the map, mission progress for both |
| G | Write results into this file: P3 row (❓ → ✅ / 🟡 with what failed) + a dated change-log entry (times, RTF, problems) | P3 status settled |

After P3: mock-area JSON (UI format `points` / `center` / `area_m2`) + polygon MCPP → P9 re-planning (design above).
Known risks: Docker OOM during builds (happened 2026-10-13 and 2026-11-14); new PX4 `main` may change topic versions
or behaviour (check `flight_test.py` first if the coverage flight misbehaves); 2 drones without camera should be
RTF ≈ 1 headless.

## Conventions

- Docker / simulator files go in `docker/`, nowhere else. Compose files are `docker/compose.*.yml`;
  their `.env` is `docker/.env` (gitignored, template `docker/.env.example`).
- `*.sh` must stay LF (`.gitattributes`); the machine uses `core.autocrlf=true`.
- `teleop_drone.py` has Thai comments/messages — keep that style in that file.
- Python MAVSDK package is **`mavsdk-grpc`** (`import mavsdk_grpc`). Plain `mavsdk` on PyPI is now a
  different native API — don't "fix" imports to `mavsdk`.
- Scripts pick the drone via env: `DRONE_ID` (→ `udpin://0.0.0.0:14540+DRONE_ID`) or `MAVLINK_URL`.
- Don't commit without being asked. Branch: `feature-Network-flukez`, main branch: `main`.
- **Every change or decision goes into this file** (user, 2026-11-14): code / config changes → a dated change log
  entry; design decisions or status changes → update "Project phases" (status table, P9 design).

## Architecture (Docker)

Three modes, same images (`ghcr.io/zflukz/aquarius-px4-sim`, `…/aquarius-telemetry`, `…/aquarius-grafana`, `…/aquarius-drone-control`; `…/aquarius-timescaledb`; profile `pipeline` adds `…/aquarius-ingestion`, `…/aquarius-imagestream`):

| Compose file | Runs on | Notes |
| :--- | :--- | :--- |
| `docker/compose.local.yml` | your computer | **local test**: px4-sim + novnc (8080 Gazebo) + novnc-qgc (8082 QGC) + telemetry (8083, in px4-sim's netns) + mosquitto (1883/9001) + timescaledb (TimescaleDB + PostGIS, 127.0.0.1:5432) + telegraf + grafana (3000) + drone-control on one bridge network; profile `pipeline`: kafka (9092) + kafka-init + db-migrate + ingestion + imagestream (8090) + kafka-ui (8084); also builds & pushes images |
| `docker/compose.server.yml` | VM server | **pull-only** (no build, no git): px4-sim + novnc (8081) + novnc-qgc (8082) + telemetry (camera 8083) + mosquitto / timescaledb / telegraf / grafana (3000) + profile `pipeline` (same services as local), `network_mode: host` |
| `docker/compose.control.yml` | control computer | drone-control, publishes udp 14540-14549 |

MAVLink (drone `i` = PX4 instance `i` = sysid `i+1`), opened by `docker/px4-sim/entrypoint.sh`:

| Link | PX4 binds (sim side) | PX4 sends to |
| :--- | :--- | :--- |
| MAVSDK / offboard | `14640+i` | `CONTROL_HOST:14540+i` |
| QGroundControl | `18640+i` | `CONTROL_HOST:14550` |
| Telemetry → MQTT / Grafana (`TELEM=1`) | `14740+i` | `TELEM_HOST:14700` (127.0.0.1, `mavlink_mqtt.py`) |

PX4's own default links (14580+i → localhost:14540+i, 18570+i, 13030+i) still exist; ours are extra. PX4 allows max
6 MAVLink instances → the entrypoint stops the default onboard-payload link (14280+i) before opening the telemetry link.

`entrypoint.sh` flow: kill stale px4/gz/QGC/Xvfb + `/tmp/px4_lock-*`, X + QGC locks → ensure Baylands Fuel cache →
Xvfb/fluxbox/x11vnc per screen (`:99`→5900 Gazebo, `:100`→5901 QGC) → for each drone `px4 -i i -d` with `PX4_GZ_MODEL_POSE="0,i*DRONE_SPACING"`,
wait until booted, add the two `mavlink start ... -t CONTROL_IP` links + telemetry link (stop payload link first) → `wait -n`, exit if any
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
**Since 2026-10-17 T2, T5 (ROS topic), C3 + browser camera / bag are implemented** in the `telemetry` service (change log below);
**since 2026-10-18 T1 + T4 as MAVLink → MQTT → TimescaleDB → Grafana** (Foxglove removed); T3, C1, C2, C4 still open.

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

## Change log — Grafana dashboard, Foxglove removed (2026-10-18)

Implements `docker/communication_plan.md` M1 + M2. Details: `docker/README.md` → "Dashboard (Grafana)".

- **px4-sim entrypoint** (+ `run_sim_native.sh`): 3rd MAVLink link per drone `14740+i → TELEM_HOST:14700` (`TELEM=1`,
  `TELEM_HOST=127.0.0.1`), PX4 Normal mode, 200 kB/s. PX4 caps MAVLink at **6 instances** (4 from `px4-rc.mavlink` +
  MAVSDK + QGC) → stop the unused onboard-payload link 14280+i first. Link failure is now a WARN, not `set -e` exit
  (first try crash-looped px4-sim). `-m normal` is not a valid mode (Normal = no `-m`).
- **`docker/telemetry/mavlink_mqtt.py`** (pymavlink + paho-mqtt v2, pip layer at the end of the Dockerfile — noble's
  python3-paho-mqtt is 1.6): one `udpin:0.0.0.0:14700` socket for all drones (sysid i+1), SET_MESSAGE_INTERVAL
  (position / attitude 10 Hz, rest 1 Hz), **1 Hz GCS heartbeat** (PX4 sends STATUSTEXT only to GCS links,
  `streams/STATUSTEXT.hpp`), joins chunked STATUSTEXT. MQTT `aquarius/<FLEET>/drone/<i>/{online,status,position,
  attitude,battery,gps,health,mission,event}`, JSON `{"t": ms, ...}`, booleans 0/1. Rate limit uses 0.8/rate (1/rate
  dropped half of PX4's jittery 10 Hz). Newer pymavlink `udpin` has no `last_address` → commands via `mav.*_send`
  (to all clients; PX4 ignores other sysids). Started by the telemetry entrypoint when `MQTT_URL` is set.
- **Foxglove removed**: `ros-jazzy-foxglove-bridge`, `FOXGLOVE_PORT`, port 8765 (Dockerfile, entrypoint, compose,
  `.env.example`, CLI, native install, docs). Changing the apt line invalidates the ROS layer → full telemetry rebuild.
- New services in `compose.local.yml` / `compose.server.yml`: `mosquitto` (eclipse-mosquitto:2, anonymous, no
  persistence), database (TimescaleDB since 2026-10-31, see below; VM binds 127.0.0.1),
  `telegraf` (1.36, `mqtt_consumer` + `topic_parsing` → table = stream, columns fleet / drone, `json_time_key=t`),
  `grafana` (new image **`aquarius-grafana`** = `grafana/grafana:12.2.0` + provisioning; VM stays pull-only).
  Mosquitto / Telegraf configs inline (`configs: content`, Compose ≥ 2.23.1, **`mode: 0444`** — default 0400 →
  telegraf "permission denied"); same text in both compose files.
- **Dashboard** `docker/grafana/dashboards/aquarius.json` (uid `aquarius-fleet`, home page, SQL): stat row (link
  online if a message < 5 s old, armed, mode, battery, altitude, sats), geomap (Esri satellite, track + last
  position), camera (HTML `<img>` MJPEG from `${cam}` textbox variable — browser-side, so off the VM set
  `var-cam=http://<VM_IP>:8083`), altitude / ground speed / climb, mode state timeline, mission waypoint, battery % /
  voltage / eph, sensors / CPU / GPS fix / mission progress, events table, roll / pitch. Drone i = categorical
  slot i (fixed order), status colors only for OK / warning / fault. Stat panels with string values need
  `reduceOptions.fields: "/^drone /"` (matched against the display name). Unit `lengthm` auto-scales to mm → `suffix: m`.
- CLI: `aquarius urls` prints Grafana (with `var-cam`), `status` lists the dashboard containers.
- Native: `install_telemetry_server.sh` pip-installs pymavlink / paho, no foxglove; `run_telemetry_native.sh` sets
  `MQTT_SCRIPT`; broker / DB / Grafana only as containers (`compose.server.yml up -d mosquitto timescaledb telegraf grafana`).

Verified 2026-10-18 (`compose.local.yml`, Docker Desktop, `gz_x500_mono_cam_down`, `HEADLESS=1 QGC=0`; px4-sim /
telemetry first with the new scripts `docker cp`'d into the old images + pip in the container): PX4 log
`udp port 14740 remote port 14700`; `mosquitto_sub` rates position / attitude 10 Hz, status / battery / gps / health /
online 1 Hz; the database had all streams for fleet=sim, drone=0; Grafana datasource health OK, all 22 panel
queries OK via `/api/ds/query`; two `aquarius start coverage --size 30x20` flights **PASSED** with the bridge's GCS
heartbeat on the link; headless-Chrome screenshot: every panel filled (ONLINE, LOITER, 100 %, map track over
Baylands, camera image, mode timeline TAKEOFF/MISSION/RTL/LOITER, mission 19 / 19, events "Landing detected").
Then `telemetry` (4.3 GB, no foxglove package, paho 2.1.0) + `px4-sim` images rebuilt (stack `down` first — the first
build attempt was killed for low memory), `up -d` with the new images: telemetry link, MQTT streams, all panel queries OK.
Fix: MISSION_CURRENT `total` = 65535 means no mission → sent as 0 (panel shows "-", was "1 / 65535").
Load (6 vCPU / 7.7 GB WSL, mono_cam_down, telemetry on, RTF over 30 s): `HEADLESS=1 QGC=0` RTF 0.92, px4-sim ~180 % CPU,
2 GB; GUI + QGC at 1920x1080 RTF 0.78, ~485 % CPU, 4.2 GB (gz GUI ~176 %, QGC ~103 %); same at `SCREEN_SIZE=1280x720x24`
RTF 0.89, ~465 %. Both noVNC screens render (screenshots), QGC Ready / Hold. Slow sim time with GUI = CPU limit, not a bug.
Not tested: `compose.server.yml` on the VM, `PX4_NUM_DRONES>1` (several drones on one socket), native scripts,
real browser login flow, images not pushed.

## Change log — server workflow: `aquarius drones` / `update` (2026-10-31)

User: local stack works, server / control docs + workflow lagged behind; wants to run compose on the VM, update it
and pick the drone count there, with Grafana on the server. Compose files themselves were already in sync
(server = local stack minus build / drone-control, host network) — Grafana stack stays in `compose.server.yml`.

- **`aquarius drones [N]`**: shows / sets `PX4_NUM_DRONES` + `NUM_DRONES` (1–10) in `.env`, then
  `up -d --force-recreate px4-sim telemetry` (telemetry builds topics / camera bridge / MQTT per drone — docs used
  to say recreate only px4-sim) and waits for the last drone.
- **`aquarius update [mode] [--no-files]`**: server / control: `git pull --ff-only` if inside this repo, else raw
  GitHub download of `compose.<mode>.yml`, `.env.example` and a copy of `aquarius` next to them
  (`AQUARIUS_BRANCH`, default main) → `pull` → `up -d --remove-orphans` → `image prune -f` → wait. Local: `up -d --build`,
  no git. `.env` never touched; keys new in `.env.example` are listed.
- `init` saves **`AQUARIUS_MODE`** in `.env`; `detect_mode` = running compose project label → `AQUARIUS_MODE` → files
  present. Needed because a VM repo clone also has `compose.local.yml` (`update` would otherwise build).
- `DOCKER_DIR` also = script dir when `compose.*.yml` sits next to the script (VM folder without the repo);
  git pull only when the git toplevel has `scripts/aquarius` (scratch dir inside another repo hit `git pull HEAD`).
- `urls` on host network prints `hostname -I` instead of `<VM_IP>`; `init server` warns if `CONTROL_HOST` is still
  the example `192.168.1.50`; wait timeout scales with the drone count.
- Docs: `docker/README.md` ③ ("What runs where", clone or scp, `drones`, `update`), `WORKFLOW.md` 0 / 1b / 1c / 2,
  `AQUARIUS_CLI.md` (commands, `drones`, `update`, troubleshooting), `.env.example`. **WORKFLOW 1b pushed no
  `grafana` image** → fixed (VM pull of `aquarius-grafana` would fail).

Tested (Git Bash, Docker Desktop stopped): `bash -n`; `drones` show / `11` rejected / `3` writes both keys;
`AQUARIUS_MODE=server` folder with all compose files → `update` picks server; missing-key hint; raw GitHub
download → **404 on main and on feature-Network-flukez (repo private)** → warns, keeps files. GHCR
`docker manifest inspect` → `denied` for all four images (private or not pushed). Not tested: anything with
Docker running, a real VM.

## Change log — TimescaleDB as the dashboard database (2026-10-31)

User asked to store the dashboard data in TimescaleDB. MQTT / Telegraf / Grafana stay.

- Service **`timescaledb`** (`timescale/timescaledb:2.22.1-pg17`, user / DB `aquarius`, `TSDB_PASSWORD`) in both
  compose files; local `127.0.0.1:5432`, VM host network with `listen_addresses=127.0.0.1`,
  `TSDB_PORT` if 5432 is taken. `TS_TUNE_MEMORY=${TSDB_MEMORY:-1GB}` (image's timescaledb-tune would size Postgres
  for the whole host), telemetry off, healthcheck `pg_isready -h 127.0.0.1` (init server listens only on the socket)
  → telegraf `depends_on: service_healthy`. Volume `timescaledb-data`.
- Telegraf `outputs.postgresql` (`TSDB_DSN` key/value DSN, `timestamptz`): one table per stream, columns `time`,
  `fleet`, `drone` (text) + fields (double / text, new fields → `ALTER TABLE ADD COLUMN`). `create_templates` calls
  **`aquarius_hypertable(tbl, retention)`** from the inline config `timescaledb_init` (→ `/docker-entrypoint-initdb.d/zz-aquarius.sql`,
  plpgsql body quoted `$$body$$` in compose = `$body$`): hypertable 1-day chunks, index `(fleet, drone, time DESC)`,
  `add_retention_policy` unless `TSDB_RETENTION` is ''/0.
- Grafana: datasource `provisioning/datasources/timescaledb.yml` (`grafana-postgresql-datasource`, uid `aquarius-tsdb`,
  `timescaledb: true`); Dockerfile env `TSDB_*`.
- Dashboard: all 22 queries in SQL (converter kept the panel layout / overrides). Numbers: format time_series
  with a text column `drone` → Grafana turns string columns into labels → existing `displayName ${__field.labels.drone}`
  and stat filter `/^drone /` keep working. Stats = `DISTINCT ON (drone::int) … ORDER BY drone::int, time DESC`
  wrapped in `ORDER BY time` (long→wide needs ascending time). Strings (mode stat, mode timeline, mission progress):
  format table + `partitionByValues` (drone, asLabels); the two **string stats must not return `time`** (it got the
  "drone i" display name too and showed up as 4 tiny values). Time series `$__timeGroupAlias` + `avg` / `last(x, time)`.
  Variables: `SELECT DISTINCT … FROM "online"`, drones sorted `drone::int`. Tables quoted (`"position"` is a keyword).
- `.env.example` (`TSDB_PASSWORD`, `TSDB_RETENTION`, `TSDB_MEMORY`, `TSDB_PORT`), CLI `status`,
  native scripts' hints, telemetry comments, docs (README dashboard section + env table, WORKFLOW, AQUARIUS_CLI,
  communication_plan note, telemetry_plan). The old database's container, image, volume and every mention were
  deleted on request (git history has them).

Verified 2026-10-31 (local, Docker Desktop, dashboard stack only — px4-sim / telemetry not running): grafana image
rebuilt; init script ran (`CREATE FUNCTION`), TimescaleDB 2.22.1, `shared_buffers` 256MB; fake `mavlink_mqtt.py`
streams for 2 drones (mosquitto_pub, 2 Hz) → 9 hypertables with index + 30-day retention jobs; all 22 panel queries +
3 variables OK via `/api/ds/query`; headless-Chrome screenshot of the dashboard: every panel filled (ONLINE / ARMED /
MISSION / battery / alt / sats, map track, series, mode timeline, events, roll / pitch, mission `4 / 14`); after
stopping the publisher Link → OFFLINE (0) after 5 s. Not tested: real PX4 flight into TimescaleDB, `compose.server.yml` on the VM, image not pushed.

## Change log — data pipeline merged into docker/, real Gazebo data (2026-11-08)

User asked to read all md files in `docker/` + `data/`, merge the infra into `docker/` and decide each config choice
(pros / cons given, user picked). Mid-task: the pipeline must take **real data from the Gazebo drones**, not fake data.
Full status, decisions, file list, verification and next steps: **`docker/merge_status.md`**. Nothing committed.

- Layout: infra → `docker/` (`db/migrations`, `db/tests`, `ingestion/`, `imagestream/`, `grafana/build_dashboards.py`,
  `grafana/dashboards/mission.json`, Kafka topics inline in compose), code → **`Data-Pipeline/`**. `data/` removed;
  its README merged into `docker/README.md` (rewritten: 312 lines, was 551).
- **One DB:** own small image **`aquarius-timescaledb`** (`docker/timescaledb/`: `postgis/postgis:17-3.5-alpine` +
  TimescaleDB 2.22.1 .so / control / install SQL + `timescaledb-tune` + official init scripts copied from
  `timescale/timescaledb:2.22.1-pg17`, `shared_preload_libraries` in the conf sample). ~200 MB download / 753 MB disk
  vs `timescaledb-ha` 1.5 GB / 6.8 GB (first tried, committed in dd5a2d4). Volume **`tsdb-pgdata`**
  (`/var/lib/postgresql/data`). Telegraf tables + migration tables side by side.
- **One Grafana** (`aquarius-grafana` 12.2): Fleet (home) + Mission monitor, datasource uid `aquarius-tsdb`,
  `GF_PANELS_DISABLE_SANITIZE_HTML=true`.
- **Profile `pipeline`** in both compose files: kafka (KRaft, heap 512 MB), kafka-init, db-migrate, ingestion,
  imagestream :8090, kafka-ui :8084. Two ways: on demand `aquarius pipeline up/down`, always `COMPOSE_PROFILES=pipeline`
  (`aquarius pipeline on/off`, `init --pipeline`).
- **`aquarius_bridge` inside `telemetry`** (build context `pipeline` = `../Data-Pipeline`): drones `uav0..uavN-1`,
  PX4 `/fmu/out` (drone 0) / `/uav_i/fmu/out`, camera `/uav_i/camera/image_raw` → JPEG, sim time. Supervisor waits for
  Kafka + topics, stops the bridge (SIGTERM to the node itself — `ros2 run` left an orphan node, subshell jobs ignore
  SIGINT) when Kafka goes away, then waits again.
- **Gotcha found:** PX4 SITL **segfaulted** after its uXRCE session reconnected when `telemetry` was restarted alone
  (intermittent) → the CLI never restarts telemetry for the pipeline.
- Verified: compose config, images built, 2 Gazebo drones → Kafka → TimescaleDB (Baylands coords, 0 rejects), camera
  frames in imagestream, real mission → RTL recorded, `phase_c_check.sh` all PASS (18 dashboard queries), Grafana
  Mission query on live data, unit tests 45/46 (1 = missing pyyaml in the test container). Resumed: `aquarius pipeline
  down` → `up` with 2 drones: bridge 1 → 0 → 1 process, px4-sim / telemetry not restarted, rows again for uav0 + uav1.
- Docs: `WORKFLOW.md` (1b push list, 4a pipeline, file map), `AQUARIUS_CLI.md` (`pipeline`, `--pipeline`,
  `logs pipeline`, troubleshooting), notes in `communication_plan.md` §6 and `telemetry_plan.md` §4.
- Small DB image verified: extensions timescaledb 2.22.1 + postgis 3.5.7, tune (shared_buffers 256MB), migrations,
  8 Telegraf hypertables + pipeline `telemetry` for uav0 / uav1 from the running sim, `phase_c_check.sh` all PASS,
  Grafana datasource OK.
- Open: push of the three new images (timescaledb, ingestion, imagestream),
  VM test of `compose.server.yml` with the profile, 2 old negative-`ts_us` rejects (bridge clock edge case).

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
- [x] `mcpp_mission.py` with `NUM_DRONES=2` in the sim (2026-11-14, via `aquarius start coverage --drones 2`).
      `AUTO_RUN` start via compose still not tested.
- [x] `PX4_NUM_DRONES=2`: both drones spawn in one world, reachable on 14540 / 14541 (2026-11-14). QGC not checked (`QGC=0`).
- [ ] `docker compose push` to GHCR and pull on a real VM; split-mode flight from the control computer.
- [~] Docker Desktop (Windows) published UDP range 14540-14549 delivers PX4's packets to the container — works, but the
      Windows listener of a port can vanish (2026-11-17, see change log); recreate drone-control. Real VM split still untested.

Suggested test order: local build → `flight_test.py` (1 drone) → 2 drones → push → VM pull → split test.

## Known issues / TODO

- Native path (`scripts/`) rewritten 2026-10-16; `install_sim_server.sh` (full PX4 build) not run end-to-end yet.
- Camera / telemetry topics live on the VM (`telemetry` service). `view_cam.py` on the control computer needs
  DDS between the machines (T3, not done) — use the browser (8083) / Grafana (3000) across networks.
- Local: after recreating/restarting px4-sim also `restart telemetry` (`service:px4-sim` netns is not re-joined).
- `CONTROL_HOST` is resolved once at container start; if the control computer's IP changes, recreate `px4-sim`.
- noVNC / x11vnc / web_video_server / Mosquitto have no password — keep 8081-8083 / 1883 / 9001 / 3000 on LAN / VPN only.
  Grafana default login admin/admin and TimescaleDB dev password → set `GRAFANA_ADMIN_PASSWORD` / `TSDB_PASSWORD` in `.env` on the VM
  before the first start (only applied to empty volumes).
- GHCR packages are private by default → make public or `docker login` on the VM.
- Raw-GitHub `curl` of `compose.server.yml` works only after these files are on `main` and the repo is public.
- `git status` shows `Visualization/Python/*` deleted — not done by Claude; confirm before committing.
- Data pipeline merge (2026-11-08): local work done; open points in `docker/merge_status.md` → "Next".
- Future plan (multi-drone phases, swarm script, MicroXRCE agent, per-drone containers, CI build/push):
  see `docker/README.md` → "Future plan".

## Change log — 2-drone MCPP flight, telemetry rebuild, local DB port (2026-11-14)

- **Docker recovery:** `C:\Users\Flukz\.wslconfig` `[wsl2] memory=12GB` (outside the repo) → engine 16 CPU / 11.7 GB.
- **`docker/telemetry/Dockerfile`:** `ARG BUILD_JOBS=8` (user's choice; after the ROS layer) instead of `$(nproc)` for
  the XRCE agent (`cmake --build -j` + `MAKEFLAGS`), px4_msgs (`colcon --parallel-workers` + `MAKEFLAGS`) and
  aquarius_bridge. Build: ROS layer redone 1148 s (cache lost in the crash), agent 167 s, px4_msgs 254 s, **engine
  stayed up**. The first run was killed by Claude Code for low *host* memory during "unpacking"; a cached re-run
  finished → `aquarius-telemetry` 4.34 GB. Host RAM is tight with WSL at 12 of 15.7 GB → lower to 10 GB if it repeats.
- **`docker/compose.local.yml`:** timescaledb host port `127.0.0.1:${TSDB_PORT:-5432}:5432` (was hard-coded 5432).
  Reason: a native Windows PostgreSQL (`postgres.exe`) holds 5432 → "bind: forbidden by its access permissions".
  `.env.example` comment no longer says "VM only"; `docker/.env` `TSDB_PORT=5433` (psql `-p 5433`). Inside the
  network everything still uses `timescaledb:5432`.
- **P3 verified — 2-drone MCPP flight PASSED** (`aquarius init local --drones 2 --headless`, `gz_x500`, no camera,
  `QGC=0`, then `aquarius start coverage --size 40x30 --drones 2`, default centre = home):
  both drones linked (`drone0 (sysid 1) -> …:14540`, `drone1 (sysid 2) -> …:14541`), `status --drone 0/1`: HOLD,
  100 %, FIX_3D 10 sats, armable. Flight: drone0 14 waypoints @ 20 m, drone1 13 @ 23 m; both armed / took off together,
  14/14 + 13/13, RTL, both landed, `=== MCPP coverage flight PASSED ===`, exit 0, CLI wall time 1 min 40 s, no leftover
  `mavsdk_server`. TimescaleDB `position`: 1538 rows per drone, max `alt_rel` 20.1 / 23.0 m; separate regions —
  drone0 lon −121.998882…−121.998679, drone1 −121.999077…−121.998877 (same lat band 37.412059…37.412287, no overlap);
  `mission` max seq 28 / 26. Grafana `/api/health` ok, Fleet dashboard 200.
  Not checked: dashboard panels by eye, QGC with both missions, RTF number.

## Change log — review of merged PRs #1 / #2 (2026-11-15)

Merged by the user: `2068f43` PR #1 **fix-bridge-clock** (`c0b90ac`) and `617745a` PR #2 **ai-template**
(`b9fad5d`, `e6f9f1c`). Reviewed + unit-tested by Claude (throwaway `python:3.12-slim`, repo mounted); no code changed.

- **PR #1 bridge clock** (`aquarius_bridge/core.py`, `bridge_node.py`): `ClockOffsetEstimator` ready after 20
  samples, restarts after 20 samples > 1 s above the minimum (PX4 clock step back); `plausible_time()` drops mapped
  samples outside now −10 s … +1 s (`BAD_TIME`, counted as `telemetry(bad-time)`); `EpochMapper` re-latches when the
  sim clock jumps back > 1 s (Gazebo restart). Review: logic OK — `_px4_to_epoch_us` has one caller and it handles
  `BAD_TIME`; `latch()` runs on every message so the re-latch triggers. Tests **24/24 OK** (needs `pillow`).
  → fixes `merge_status.md` problem 7 (negative `ts_us`). **Not deployed:** the running `aquarius-telemetry` image
  (built 2026-11-14 before the merge) has no `plausible_time` → rebuild telemetry (cached up to the aquarius_bridge
  COPY layer). Note: recreating telemetry alone once made PX4 segfault (2026-11-08 gotcha) → recreate px4-sim too.
- **PR #2 AI template** (`Data-Pipeline/ai/`): consumer group `ai-detection` on `image-frames`, `fake[:N]` / YOLO
  detector (`detect(image, meta=None)`, old 1-arg signature still works), message checked against
  `detections.schema.json`, evidence `results/evidence/<drone>/<ts_us>_<frame>.jpg`, manual commits. Wiring OK:
  topic `detections` created by kafka-init in both compose files, ingestion writes it to the `detections` table.
  Tests **16/16 OK** (incl. the ingestion row builder with `PYTHONPATH=docker/ingestion`).
  Review findings (not fixed — data / AI team's code, ask first):
  1. **`--max-lag-s` (default 5) vs sim time (medium):** `too_old()` compares `ts_us` with the wall clock, but in the
     sim `ts_us` = sim time + an offset taken once (`EpochMapper`). At RTF < 1 it falls behind the wall clock by
     (1 − RTF) × elapsed → at RTF 0.9 every frame counts as "old" after ~50 s and nothing is detected. Real drones
     are fine (ROS clock = UTC). Fix idea: measure lag with the Kafka message timestamp (`m.timestamp()`, producer
     wall time) instead of `ts_us`, or `--max-lag-s 0` in the sim. Found by reading the code, not reproduced live.
  2. **Schema error stops the service (low):** `validator.validate()` raises `ValidationError`, only
     `KafkaException` is caught → one bad message (e.g. a detector returning NaN conf) ends the loop. Better: log +
     count it as `bad`, skip the message.
  Not tested: AI against live Kafka / camera frames (pipeline profile + camera model not running in this session).

## Change log — Mission monitor / Path compare empty: pipeline was never started (2026-11-15)

User: Mission monitor + Path compare dashboards don't work, suspected telemetry / DB writes broken.
- **Cause:** not a bug in telemetry or ingestion. `docker/.env` had `TELEMETRY_PATH=both` but `COMPOSE_PROFILES=`
  empty (both are the `.env.example` defaults; `init` sets the profile only with `--pipeline`, `aquarius path` sets
  both). So Kafka / db-migrate / ingestion never ran → DB had only the Telegraf tables (`position`, `battery`, …),
  none of the 12 pipeline tables those dashboards read (`telemetry`, `missions`, `flight_paths`, `persons`, …).
  telemetry's log: `[kafka] waiting for Kafka kafka:19092 (compose profile pipeline) ...` for 31 h. Fleet dashboard
  (Telegraf tables) was fine all along.
- **Fix (no code change):** `aquarius pipeline on --build` → `COMPOSE_PROFILES=pipeline` in `.env`, kafka,
  kafka-init (exit 0), db-migrate (exit 0, 0001–0008 applied), ingestion + imagestream built and started, kafka-ui;
  px4-sim / telemetry **not** restarted. Bridge picked Kafka up by itself (`uav0..uav1`, sim_time, kafka errors 0).
- **Verified:** `telemetry` table rows for uav0 + uav1, `ts` ≈ `now()` − 0.1 s, 0 `ingest_rejects`; all 17 Mission
  monitor + 5 Path compare panel queries OK via Grafana `/api/ds/query` (vars: newest mission, swath 30). Empty but
  expected: coverage, search area, planned paths, survivor pins, ground truth (P2 / P3 / P7 don't write them yet).
  Not checked: dashboards by eye in a browser, a flight with the pipeline on, PR #1 bridge (image not rebuilt).
- **Suggestion (not done):** `.env.example` defaults are inconsistent (`both` without the profile) → either make
  `init` set `COMPOSE_PROFILES=pipeline` when `TELEMETRY_PATH` ≠ `mqtt`, or warn in `init` / `status`.

## Change log — ROS check: Kafka bridge subscribed to a dead topic (vehicle_status_v4) (2026-11-15)

User asked whether ROS in compose works. All compose services run (`db-migrate` / `kafka-init` = one-shot, exit 0);
ROS 2 lives in `telemetry` (XRCE agent, ros_gz_bridge, web_video_server, ground_truth, aquarius_bridge) and
`drone-control`; both see the 125 topics. **Bug found:** PX4 `main` `0bd95058` publishes `vehicle_status_v5`, the
bridge subscribed to hard-coded `fmu/out/vehicle_status_v4` (0 publishers) → `nav_state` / `armed` **null in all
21 615 Kafka telemetry rows per drone** (Mission monitor drone status had no mode / armed). Other 4 topics fine.
- **Fix** (`Data-Pipeline/ros2_ws/src/aquarius_bridge`): `core.px4_topic(base, version)`; `bridge_node.py` topic
  params default `""` = name from the built px4_msgs' `MESSAGE_VERSION` (px4_msgs is built from the same `PX4_REF`
  as px4-sim, so it always matches; v0 = no suffix); a set name still overrides; resolved names logged in
  `bridge up: … px4 topics=[…]`. `config/bridge.yaml` + `bridge_sitl.yaml` topic values → `""`. New unit test
  `TestPx4Topic` → **25/25 OK**. v5 message still has `nav_state` / `arming_state` (same constants).
- **Deployed:** `telemetry` rebuilt (3 min, bridge layer only; also brings PR #1's clock fix live) → log
  `px4 topics=['battery_status_v1', 'vehicle_attitude', 'vehicle_global_position', 'vehicle_local_position_v1',
  'vehicle_status_v5']`; new rows: `nav_state` AUTO_LOITER, `armed` false for uav0 + uav1 on every row, 0 rejects.
- **Startup race found (not fixed):** recreating `px4-sim` **and** `telemetry` at the same moment
  (`up -d --force-recreate --no-deps px4-sim telemetry`) → PX4 `gazebo already running world: baylands` → `Timed
  out waiting for Gazebo world`, no gz server. Cause: `px4-rc.gzsim` decides "Gazebo running" from
  `gz topic -l | grep /world/.*/clock`, and telemetry's ros_gz_bridge / ground_truth.py (same netns + GZ_PARTITION)
  had already *subscribed* to `/world/baylands/clock` → listed. Recovered: stop telemetry → restart px4-sim → wait
  for `drone1 (sysid 2)` → recreate telemetry. Timing-dependent (`init` worked); `aquarius drones N` recreates both
  together too. Suggested fix: telemetry entrypoint waits until the service `/world/$PX4_GZ_WORLD/scene/info`
  exists (only the real server offers it) before starting the gz bridge / ground_truth.

## Change log — Mission monitor re-check: Kafka OK, map basemap broken (2026-11-15)

User: Mission monitor (Kafka dashboard) still "not working", asked to re-check Kafka and its data.
- **Kafka / data: healthy.** Topics telemetry / image-frames / detections / survivors / replanning-events exist;
  bridge `[stats] telemetry=200 | kafka errors=0` per 10 s (2 drones × 10 Hz, partitions 3 + 7); consumer group
  `db-ingestion` lag 3; ingestion ~200 rows / 10 s; `telemetry` table: uav0 / uav1 rows up to `now()`, `nav_state` +
  `armed` filled on every row of the current mission (`1bfcb3d3…`), 0 `ingest_rejects`. `image-frames` = 0 msgs
  (no camera model, `gz_x500`) → camera panel "waiting for images" is expected.
- **Rendered the dashboard** (headless Chrome via CDP, scratchpad script; needs `Emulation.setDeviceMetricsOverride`,
  else Grafana lazy-loads nothing): stats, drone status table, battery, altitude, pipeline health all filled.
  **Bug: the mission map showed "API KEY REQUIRED" tiles** — the panel used Grafana's `default` basemap (CARTO), which
  now needs a key. **Fix:** `docker/grafana/build_dashboards.py` basemap → Esri World Imagery xyz (same as Fleet),
  regenerated `dashboards/mission.json` (only that diff — generator was in sync), `aquarius-grafana` rebuilt +
  recreated → satellite map of Baylands with the drone markers. Compare dashboard has no geomap.
- Side effect: `compose up -d --build grafana` also **recreated `timescaledb`** (container config was stale since
  the `TSDB_PORT` change) → volume kept, ingestion lost its connection, restarted and caught up by itself
  (short latency spike on the D3 panel). Use `up -d --no-deps <svc>` to avoid this.
- **Still empty, expected:** Coverage / survivor pins / detections (no `search_area`, no P6 / P7 output yet).
- **Noted, not fixed:** (1) D3 "drone → DB latency" grows slowly (1.4 → 2.4 s over 30 min): `ts_us` = sim time +
  one-time epoch offset (`EpochMapper`), sim runs slightly slower than wall clock → same root cause as the AI
  `--max-lag-s` finding; real drones unaffected. (2) Old mission `dccf482c…` (pre-PR #1 bridge) has `first_ts`
  1970-01-01 from a bad sample and null `nav_state` / `armed` — old data, can be deleted if it bothers the mission list.

## Change log — Mission monitor: no mission → 0 / "not ready" instead of errors (2026-11-16)

User: when there is no mission (or no data) the panels should show 0 or "not ready".
- **Bug:** every query used `mission_id = '$mission'`; with no mission the variable is `''` → `invalid input syntax
  for type uuid: ""` on **all 16 mission panels** (stats, map, tables, series). Checked in psql.
- **Fix** (`docker/grafana/build_dashboards.py` → regenerated `dashboards/mission.json`): `M = NULLIF('$mission',
  '')::uuid` → NULL → counts 0 / empty sets (all `aq_*` helpers take UUID and accept NULL). `panel()` sets
  `fieldConfig.defaults.noValue` (default "ยังไม่มีข้อมูล"); stats default "0"; Coverage "ยังไม่พร้อม" (= no
  `search_area` for the mission, explained in the panel description); drone status table "ไม่มีโดรนส่งข้อมูล";
  survivor pins "ยังไม่พบผู้ประสบภัย". Path compare has no `$mission` → unchanged.
- **Verified:** `aquarius-grafana` rebuilt, `up -d --no-deps grafana`; all 17 panel queries via `/api/ds/query`:
  current mission 17 OK / 0 errors, empty mission 17 OK / 0 errors (13 empty → noValue texts). Screenshot: Coverage
  "ยังไม่พร้อม", pins "ยังไม่พบผู้ประสบภัย", rest live.
- Seen again: D3 latency stuck at ~6.3 s after the image build (Kafka lag 5 msgs, ingestion keeps up) — the CPU-heavy
  build slowed the sim, and `EpochMapper` keeps its one-time offset, so sim-time `ts` stays ~5 s behind wall clock
  until the bridge restarts. Sim-only (see 2026-11-15 note); possible fix = bridge re-latches the epoch when drift > N s.

## Change log — Kafka carries the MQTT data too: landed / GPS / health + camera (2026-11-16)

User: Gazebo data should go both ways (MQTT and Kafka) with the same data. Before: Kafka `telemetry` had position /
attitude / velocity / battery / mode / armed only; MQTT also has landed, GPS, health, mission progress, events;
`image-frames` empty (no camera model).
- **Schema** `Data-Pipeline/schemas/telemetry.schema.json`: new **optional, nullable** fields `landed`,
  `gps_fix_type`, `gps_satellites`, `gps_eph`, `gps_epv`, `health_ok`, `failsafe_flags` (string array);
  `schema_version` stays 1, older messages stay valid (tested). Data team's contract → tell them.
- **Bridge** (`aquarius_bridge`): subscribes PX4 `vehicle_land_detected`, `vehicle_gnss` (`receiver.fix_type /
  satellites_used / eph / epv`, 30 Hz), `failsafe_flags` (names from px4_msgs, same `px4_topic()` versioning; params
  `topic_land_detected / topic_gnss / topic_failsafe`, YAMLs updated). `failsafe_flags` = every true bool;
  `health_ok` = none of the critical ones (attitude / position / altitude / velocity invalid, gnss_lost,
  battery_unhealthy, fd_*) — situations like no mission / RC / GCS lost are listed but don't count (normal on the
  ground in SITL). NaN / negative accuracy → null. Tests 28/28 (3 new).
- **DB** migration `0009_telemetry_gps_health.sql`: 7 nullable columns + `aq_drone_status()` returns landed / GPS /
  health (drop + create). **Ingestion** `rows.py` inserts them with `m.get()`; test 13/13 (1 new).
- **Deploy order** (the schema has `additionalProperties: false`, so an old ingestion would reject new messages):
  build ingestion / grafana / telemetry → `db-migrate` (0009 applied) → ingestion → then the new bridge.
- **Camera:** `docker/.env` `PX4_SIM_MODEL=gz_x500_mono_cam_down`, `PX4_SYS_AUTOSTART=4014` (640x480 @ 10 Hz);
  restarted in the safe order (stop telemetry → recreate px4-sim → wait for `drone1 (sysid 2)` → recreate telemetry,
  no Gazebo race). Bridge already forwarded `/uav_i/camera/image_raw` → JPEG → `image-frames` at 2 fps per drone.
  **RTF 0.89** averaged over 30 s with 2 cameras (single `real_time_factor` readings jump 0.1–1.0 — don't trust one).
- **Grafana Mission monitor:** drone status table + columns บนพื้น / ดาวเทียม / สุขภาพ (OK green / FAULT red),
  `minWidth 50` so the 10 columns fit; **D3 latency now `received_at − sent_at`** (wall clock both, 0008), falls
  back to `ts` for old rows — with the camera's RTF 0.89 the old `received_at − ts` climbed 0 → 23 s in 3 min.
- **Verified live:** bridge `px4 topics=[… failsafe_flags, vehicle_gnss, vehicle_land_detected …]`, `image-frames=37
  telemetry=200` per 10 s, kafka errors 0; Kafka message has all new fields (landed true, fix 3, 10 sats, eph 0.9,
  health_ok true); DB: every row of the last minute has landed / GPS / health (204 / 205 rows), 0 rejects;
  imagestream + dashboard show both cameras; latency p50 ≈ 0.5 s, p95 ≈ 1 s.
- **Still MQTT-only:** mission progress (waypoint i / n) and PX4 text events ("Landing detected") — PX4 does not
  export `mission_result` / `mavlink_log` over uXRCE-DDS (not in the agent's `/fmu/out` list). Needs PX4
  `src/modules/uxrce_dds_client/dds_topics.yaml` patched in the px4-sim image + rebuild, and a schema decision
  (mission fields in telemetry; events = a new Kafka topic?) with the data team.
- Note: with RTF < 1 the sim-time `ts_us` falls behind wall clock faster now → the AI template's `--max-lag-s 5`
  (2026-11-15 finding) will drop frames after ~45 s; run it with `--max-lag-s 0` in the sim until fixed.

## Change log — Mission monitor: battery / GPS / health section like Fleet (2026-11-16)

User: the Kafka dashboard should show battery and GPS status like the MQTT (Fleet) one. Mission monitor had only
battery % (series + table column); no GPS / health panels.
- `docker/grafana/build_dashboards.py` → new row **"แบตเตอรี่ · GPS · สุขภาพ (จาก Kafka)"** (before the cameras):
  per-drone stats GPS fix (1 none red, 2 2D orange, 3 3D / 4 DGPS / 5-6 RTK green, 8 extrapolated orange),
  GPS satellites (< 4 red, < 6 orange), health OK / FAULT, on ground / flying; series battery voltage, GPS
  satellites, GPS eph + epv; table of active PX4 failsafe flags. Helper `per_drone_stat()` reads
  `aq_drone_status()` (newest row per drone, 0009 columns).
- **Grafana gotcha:** stat with `values: true` + a renamed field: `reduceOptions.fields` is matched against the
  display name, so `/^value$/` (and `/^uav/` with `${__data.fields.drone_id}`) showed "no data". Working pattern:
  numeric value only (booleans `::int` → 1 / 0 + value mappings), `fields: ""` (numeric fields → the text drone_id
  column is skipped), display name override `${__data.fields.drone_id}` on the `value` field.
- Verified: grafana rebuilt (`up -d --no-deps grafana`), all 25 Mission panel queries OK with the current mission
  and with no mission; screenshot: uav0 / uav1 3D, 10 sats, OK, บนพื้น; voltage 16.8 V; eph 0.9 m / epv 1.78 m;
  flags table. Not in Kafka (so not on this dashboard): CPU load, mission progress, PX4 text events.

## Change log — px4-sim rebuilt: CPU load, mission progress, PX4 text events on Kafka (2026-11-16)

User asked to add the last MQTT-only data to Kafka and rebuild. PX4 did not publish `cpuload`, `mission_result`,
`mavlink_log` over uXRCE-DDS (not in `dds_topics.yaml`), so this needed a px4-sim rebuild.
- **px4-sim** (`docker/px4-sim/`): `dds_topics_extra.yaml` (cpuload 1 Hz, mission_result 5 Hz, mavlink_log) +
  `patch_dds_topics.sh` (idempotent, inserts them at the end of `publications:`, checks 3 found); Dockerfile layer
  **after** `make px4_sitl_default` → `RUN patch && make` (incremental). Build: apt / gz / clone layers CACHED (PX4
  stays `0bd95058` = `PX4_REF`, so telemetry's px4_msgs still match), but the `make` layer re-ran in full (WORKDIR
  not cached, 365 s), then patch + make, Baylands / QGC layers again, export 197 s → 12 GB. Engine stayed up.
- **Bridge:** subscribes `cpuload`, `mission_result`, `mavlink_log` (version 0 → no suffix; params
  `topic_cpuload / topic_mission_result / topic_mavlink_log`, YAMLs). telemetry gets optional `cpu_load` (%),
  `mission_seq`, `mission_total` (0 = none), `mission_finished`. `mavlink_log` → **new Kafka topic `events`**
  (`kafka_out.send_event`, QoS depth 10): `{schema_version, drone_id, mission_id, ts_us, severity 0-7, level, text}`;
  `decode_px4_text()` handles char[127] as str / bytes / ints, NUL-terminated; ts = arrival on the bridge clock
  (sim-time mapped, like telemetry). Tests 31/31 (3 new).
- **Schemas:** `telemetry.schema.json` + 4 optional fields; new `events.schema.json`. **kafka-init** (local +
  server compose) creates `events` (10 partitions, 30 days).
- **DB** `0010_telemetry_cpu_mission_events.sql`: 4 telemetry columns, hypertable `events` (PK drone_id, ts, text;
  index mission_id, ts), `aq_drone_status()` + cpu / mission (drop + create). **Ingestion** subscribes `events`
  too (`JSON_TOPICS`), rows for both; tests 15/15 (2 new).
- **Grafana Mission monitor:** row renamed "แบตเตอรี่ · GPS · สุขภาพ · ภารกิจ (จาก Kafka)": 6 status boxes (GPS fix,
  sats, health, on ground, CPU %, mission % = (seq+1)/total, "ไม่มีภารกิจ" when none), PX4 events table (level
  colours), waypoint series.
- **Deploy order:** build images → kafka-init (topic `events`) → db-migrate (0010) → ingestion + grafana → stop
  telemetry → recreate px4-sim (ready 129 s) → recreate telemetry. Bridge log `px4 topics=[… cpuload, mavlink_log,
  mission_result …]`, `[stats] events=3 image-frames=15 telemetry=60`, kafka errors 0.
- **Verified with a flight** (`aquarius start coverage --size 20x15 --drones 2`, PASSED): Kafka `events` had the
  whole flight for both drones (Armed → Takeoff detected → Executing Mission → Mission finished → Return → Landing
  detected → Disarmed by landing), same texts as the MQTT `event` table; telemetry mission_total 9, max seq 8,
  finished true, CPU up to 96-100 % during flight; 0 rejects; dashboard screenshot shows all of it.
- Notes: (1) PX4 logged "Accel #0 fail: TIMEOUT!" / "Preflight Fail" right after start on both paths — real PX4
  warnings while the camera sim stalls at startup (RTF 0.83 after); drones were armable. (2) Kafka event times are
  ~12-15 s behind MQTT's (sim-time ts drift, same as telemetry); a few repeated "Accel TIMEOUT" texts seen on MQTT
  were missing on Kafka (best-effort DDS / PX4 uORB queue) — flight events all arrived. (3) Grafana logged 500
  "database is locked" (its internal SQLite, sessions) with several tabs on 2 s refresh during the CPU-heavy
  flight — transient, not the TimescaleDB queries. (4) **VM:** push the new `aquarius-px4-sim`, `aquarius-telemetry`,
  `aquarius-ingestion`, `aquarius-grafana` images, then `aquarius update` (kafka-init + db-migrate run on up).
- **Kafka now carries everything MQTT has** (position, attitude, battery, mode, armed, landed, GPS, health, CPU,
  mission progress, PX4 texts) **plus camera images**.

## Change log — compose.server / compose.control checked against compose.local (2026-11-17)

User asked to bring `compose.server.yml` and `compose.control.yml` in line with `compose.local.yml` (local = base).
Compared the resolved configs (`docker compose config --format json`, `COMPOSE_PROFILES=pipeline`) per service.
- **Already in sync:** same services (server = local minus `drone-control`; control = `drone-control` only), same env
  keys / defaults, images, profiles, healthchecks, depends_on; the inline `configs:` (mosquitto, telegraf,
  timescaledb init, kafka topics incl. `events`) are identical. Every build context / file path exists.
- **Remaining differences are on purpose** (host network on the VM): `127.0.0.1` instead of service names,
  `KAFKA_LISTENERS` / controller on 127.0.0.1, DB `listen_addresses=127.0.0.1` + `port=${TSDB_PORT}`,
  `PORT` / `SERVER_PORT` for imagestream / kafka-ui (no port mapping), novnc-qgc `--listen 8082`, `VNC_LISTEN`,
  telemetry `ipc: host`, `restart: unless-stopped` on the always-on VM / control services, `CONTROL_HOST` required;
  telemetry has no `depends_on … restart: true` (only needed for local's shared netns).
- **Fixed (comments only):** kafka-init "5 topics" → 6 (both files); local header push example listed 4 images, the
  VM also pulls timescaledb / ingestion / imagestream → explicit list of all 7 own images (`--profile pipeline`; a bare `push` would also try the third-party images).
- `config -q` OK for all three files.

## Change log — split test on one PC: compose.server + compose.control separately (2026-11-17)

User asked to run server and control as separate stacks and fly from control to server. Same Windows PC, Docker
Desktop 28.3.2 (host networking **not** enabled in Docker Desktop → `network_mode: host` = the Docker VM's network).
No repo file changed; env files in the scratchpad (copies of `docker/.env`): server `CONTROL_HOST=10.35.21.101`
(this PC's Wi-Fi IP, like a LAN control computer), `TSDB_PORT=5432`; control unchanged. Local stack `down` first
(same container names; volumes kept), then `compose.server.yml --env-file … up -d` (project `aquarius-server`, fresh
volumes) and `compose.control.yml --env-file … up -d` (`aquarius-control`). 2 drones, `gz_x500_mono_cam_down`, pipeline on.
- px4-sim: `drone0 (sysid 1) -> 10.35.21.101:14540`, `drone1 … :14541`; `aquarius status --drone 0/1` from the control
  container: HOLD, 100 %, FIX_3D 10 sats, armable.
- **Problem 1 — duplicate path (Docker Desktop only):** drone-control got every PX4 message twice: from our link (via
  Windows port forward) **and** from PX4's default link 14580+i → VM `localhost:14540+i`, which the control's published
  port also catches because the server's host network is the same VM. → first coverage run failed in 8 s:
  `upload_mission() PROTOCOL_ERROR`. A real VM has no such shortcut. Test fix: temporary rule in the Docker VM
  `docker run --rm --net host --privileged alpine:3.20 sh -c "apk add -q iptables; iptables -I OUTPUT -o lo -p udp
  --dport 14540:14549 -j DROP"` (gone after a Docker Desktop restart; remove with `-D` instead of `-I`). After it: one
  source per drone, full rate.
- **Problem 2 — Docker Desktop's Windows UDP forward dropped port 14540** twice (`netstat`: com.docker.backend still
  bound 14541-14549, not 14540; `docker port` still listed it) → drone0 stuck at `connecting udpin://0.0.0.0:14540`.
  Seems to happen while nothing in the container listens on the port and PX4 keeps sending (between scripts).
  Fix: `up -d --force-recreate drone-control`, then start the mission right away. Docker Desktop (Windows) only; a
  Linux control computer publishes with iptables.
- **Result: `aquarius start coverage --size 40x30 --drones 2` PASSED over the split link**, exit 0, 143 s, both
  landed, no leftover `mavsdk_server`, both Windows forwards still bound afterwards. Server side (last 5 min):
  MQTT → Telegraf `position` 2263 / 2264 rows, max alt 20.1 / 23.1 m, separate lon bands; MQTT `event` + Kafka
  `events` both have Armed → Takeoff → Executing Mission → Mission finished → Return → Landing → Disarmed for both;
  Kafka → ingestion `telemetry` 2729 rows per drone, mission 28/29 + 26/27, finished true; bridge
  `image-frames=38 telemetry=200 kafka errors=0`; 0 `ingest_rejects`; Grafana health ok; kafka-ui 8084 / imagestream
  8090 / camera 8083 answer 200 (from inside the VM — not reachable from Windows without Docker Desktop host networking).
- Left running: server + control stacks, iptables rule. Back to local: `docker compose -f compose.control.yml down`,
  `docker compose -f compose.server.yml down`, `docker compose -f compose.local.yml up -d`.
- Still open: real VM + separate control computer (and its firewall for udp 14540-14549), QGC on 14550 in split mode.

## Change log — Grafana dashboard links use `HOST_IP` from `.env` (2026-11-17)

User: on the server the dashboards' browser-side links point at `localhost` → should come from the host IP in `.env`.
Grafana cannot read env vars inside dashboard JSON, so the image does it at start.
- **`docker/grafana/start.sh`** (new, ENTRYPOINT of `aquarius-grafana`, then `exec /run.sh`): copies the dashboards
  from `/usr/share/aquarius/dashboards` to `/etc/grafana/dashboards` on every start and replaces
  `http://localhost:` with `http://$HOST_IP:` → Mission `stream_url` (imagestream 8090) and Fleet `cam` (camera 8083).
  `HOST_IP` empty / `localhost` = unchanged. Dashboard JSON files and `build_dashboards.py` unchanged.
- Dockerfile: dashboards → `/usr/share/aquarius/dashboards`, `/etc/grafana/dashboards` owned by 472, CRLF stripped
  from the script. `compose.local.yml` / `compose.server.yml` grafana env `HOST_IP=${HOST_IP:-localhost}`;
  `.env.example` `HOST_IP=localhost` ("CHANGE on the VM").
- Verified: image built; `HOST_IP=192.168.1.20` → links `http://192.168.1.20:8083` / `:8090`; empty → localhost;
  all 3 dashboards provisioned. Not done: push, VM `.env`, `aquarius urls` still adds `var-*` (harmless).
- **Follow-up (same day): Kafka external address falls back to `HOST_IP`.** User asked to scan `docker/` code for
  localhost / 127.0.0.1 and use `HOST_IP`. Only two kinds of address are used by other machines / browsers: the
  dashboard links (above) and Kafka `EXTERNAL` → `KAFKA_ADVERTISED_LISTENERS EXTERNAL://${KAFKA_ADVERTISED_HOST:-${HOST_IP:-…}}:9092`
  (local default localhost, server 127.0.0.1); `.env.example` `KAFKA_ADVERTISED_HOST=` (empty = HOST_IP). Tested with
  `compose config`: none → localhost / 127.0.0.1, HOST_IP → HOST_IP, both set → KAFKA_ADVERTISED_HOST.
  **Left on purpose:** every service-to-service 127.0.0.1 on the VM's host network (DB `listen_addresses`, Kafka
  INTERNAL 19092 / controller, MQTT, TSDB DSNs, PX4 → telemetry 14700, uXRCE 8888, VNC_LISTEN, noVNC → VNC) — the host
  IP would break them or expose the DB / VNC; code defaults (`localhost:9092` etc.) are overridden by compose;
  `PX4_VIDEO_HOST_IP` is the video *target* (control computer), not this host; comments / docs unchanged.

## Change log — dashboards: "Kafka" path renamed "uXRCE" (2026-11-18)

User asked to rename Kafka → uXRCE in the dashboards (the path = PX4 uXRCE-DDS → ROS 2 → Kafka → ingestion).
- `grafana/dashboards/compare.json` (hand-made): title "Path compare (MQTT vs uXRCE)", SQL path / stack label
  `'Kafka'` → `'uXRCE'` (5 queries) **together with** the colour mappings (`"uXRCE": {…}`) and override regex
  `.*uXRCE.*` (they match the label), descriptions "uXRCE stack". `build_dashboards.py` row "… ภารกิจ (จาก uXRCE)"
  → regenerated `mission.json`.
- Kept: real component names (Kafka topic `events` / `image-frames`, "bridge → Kafka → ingestion", CLI
  `aquarius path mqtt|kafka|both`), camera title (camera is Gazebo → ROS 2, not uXRCE). Table names unchanged.
- Verified: grafana rebuilt + recreated locally, 3 dashboards provisioned, new titles served. Not pushed.
- Same session: user reported the MQTT path going down after ~20 min on the server (suspects Kafka / ROS 2
  bandwidth). Not reproduced — no access to the VM; diagnosis commands given, waiting for logs.

## Change log — Gazebo window on demand with HEADLESS=1 (2026-11-18, no code change)

User wants to see the Gazebo GUI (watch drones, add "accidents") but the VM is overloaded (px4-sim 637 % of 8 cores,
RTF ≈ 0.3 with GUI + QGC + 2 cameras). Tested locally (1 drone, `HEADLESS=1 QGC=0`, camera model):
- Screen `:99` + x11vnc + noVNC run even with `HEADLESS=1` → the GUI can be opened / closed on the running sim:
  `docker exec -d px4-sim bash -c '. /px4/build/px4_sitl_default/gz_env.sh; DISPLAY=:99 gz sim -g'` (noVNC 8080
  local / 8081 VM), close: `docker exec px4-sim pkill -f "gz sim -g"` → server keeps running. Follow the drone:
  `/gui/follow` with the model name `<model without gz_>_<i>` (e.g. `x500_mono_cam_down_0`) + `/gui/follow/offset`.
- Obstacles / "accidents" without the GUI: `gz service -s /world/baylands/create` (EntityFactory with an SDF) →
  red 1×1×3 m box appeared next to the drone; `/world/baylands/remove` to delete.
- **Found: PX4 spawn can fail silently.** `px4-rc.gzsim` calls `/world/<w>/create` once (5 s timeout, output
  discarded). This run: "Spawning Gazebo model" but no model in `gz model --list`, then `not found in scene/info` +
  `Preflight Fail: No valid data from Accel 0 / barometer 0 missing` forever. Manual create with the same SDF fixed
  it (PX4 → `home set`). Symptom to look for on the VM; fix = restart px4-sim or spawn by hand. Possible entrypoint
  fix (not done): after rcS, check `gz model --list` and re-spawn.
- Also: stopped split-test leftover containers (`aquarius-control` project) removed with `down` (no volumes).
