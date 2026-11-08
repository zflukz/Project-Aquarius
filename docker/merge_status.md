# Merge `data/` → `docker/` + real Gazebo data pipeline — status (2026-11-08, local work done)

Local refactor done and tested; open: DB image decision, push, VM test. Nothing committed; all changes are in the working tree on `feature-Network-flukez`.
Change log entry: `CLAUDE.md` → "Change log — data pipeline merged into docker/ (2026-11-08)".

## Decisions (by the user)

| # | Topic | Choice |
| :--- | :--- | :--- |
| 1 | Layout | infra → `docker/`, code → `Data-Pipeline/` |
| 2 | Database | **one** TimescaleDB + PostGIS (`timescale/timescaledb-ha:pg17.6-ts2.22.1`) for Telegraf + pipeline tables |
| 3 | Kafka | both ways: profile `pipeline` on demand (`aquarius pipeline up/down`) **and** always on (`COMPOSE_PROFILES=pipeline` in `.env`, `aquarius pipeline on/off`, `init --pipeline`) |
| 4 | Grafana | one `aquarius-grafana` 12.2, both dashboards baked in; home = Fleet |
| 5 | Where | user unsure → Claude chose: both compose files, behind the profile (costs nothing when off) |
| 6 | Bridge | `aquarius_bridge` runs **inside `telemetry`**, docker naming: `uav0..uavN-1`, `/fmu/out` (drone 0), `/uav_i/...`, camera `/uav_i/camera/image_raw` |
| 7 | Ports / env | Kafka UI 8084, imagestream 8090, one `docker/.env.example` (`POSTGRES_*` → `TSDB_*`) |
| 8 | Docs | merge `data/README.md` into `docker/README.md` (English, shorter than the old 551 lines) |
| + | (mid-task) | pipeline must use **real data from the Gazebo drones**, not `sim_producer.py` |

## What moved

| From | To |
| :--- | :--- |
| `data/db/migrations`, `data/db/tests` | `docker/db/migrations`, `docker/db/tests` |
| `data/ingestion/`, `data/imagestream/` | `docker/ingestion/`, `docker/imagestream/` (own Dockerfiles; ingestion context = repo root + `Dockerfile.dockerignore`, migrations baked in) |
| `data/grafana/build_dashboards.py`, `.../aquarius/mission.json` | `docker/grafana/build_dashboards.py`, `docker/grafana/dashboards/mission.json` (datasource uid → `aquarius-tsdb`) |
| `data/kafka/create-topics.sh` | inline `configs: kafka_topics` in both compose files |
| `data/db/init/001_extensions.sql` | `CREATE EXTENSION postgis` added to the inline `timescaledb_init` |
| `data/docker-compose.yml`, `data/.env.example`, `data/grafana/provisioning/*` | deleted (merged into `docker/compose.*.yml`, `docker/.env.example`, `docker/grafana/`) |
| `data/ros2_ws`, `tools`, `schemas`, `results`, `.gitignore` | `Data-Pipeline/` |
| `data/README.md` | **still there** → delete (content is in `docker/README.md` "Data pipeline") |

## Changed files

- `docker/compose.local.yml`, `compose.server.yml`: `timescaledb` → ha image, volume `tsdb-data` (`/home/postgres/pgdata`);
  profile `pipeline`: `kafka`, `kafka-init`, `db-migrate`, `ingestion`, `imagestream`, `kafka-ui` (VM: host network,
  Kafka INTERNAL 127.0.0.1:19092, EXTERNAL 9092 `KAFKA_ADVERTISED_HOST`, kafka-ui `SERVER_PORT=8084`); telemetry env
  `KAFKA_BOOTSTRAP`, `MISSION_ID`, `BRIDGE_IMAGE_FPS`; local telemetry build `additional_contexts: pipeline: ../Data-Pipeline`.
- `docker/telemetry/Dockerfile`: last layers `python3-opencv`, `confluent-kafka`, colcon build of `aquarius_bridge` →
  `/opt/aquarius_ws`, `with-ros` sources it.
- `docker/telemetry/entrypoint.sh`: step 6 — generates the bridge params per drone, supervisor loop: wait for Kafka +
  topics → start bridge → stop it when Kafka disappears → wait again.
- `docker/grafana/Dockerfile` (`COPY dashboards/*.json`, `GF_PANELS_DISABLE_SANITIZE_HTML=true` for the camera iframe),
  datasource comment + `postgresVersion: 1700`, provider comment.
- `docker/ingestion/aquarius_ingest/rows.py` + `tests/test_rows.py` (schema path `../../../Data-Pipeline/schemas`),
  `migrate.py` default DSN 5432.
- `docker/.env.example`: section "Data pipeline" (`COMPOSE_PROFILES`, `KAFKA_*`, `BRIDGE_IMAGE_FPS`, `MISSION_ID`,
  `MERGE_RADIUS_M`, ports).
- `scripts/aquarius`: `pipeline [up|down|on|off]` (up/down only touch the pipeline services), `init --pipeline`,
  `logs pipeline`, `urls` (Mission + Kafka UI), `status` lists pipeline containers.
- `Data-Pipeline/tools/stack.sh` (new, shared by the check scripts: compose file, container names, DB password from
  `docker/.env`, `pwd -W` for Git Bash); `phase_b_check.sh`, `phase_c_check.sh` (+ `PYTHONIOENCODING=utf-8`),
  `check_dedupe.sh`, `dashboard_sql.py`, `demo_mission.py`, `pipeline_stats.py`, `sitl/start_swarm.sh`,
  `sitl/make_cam_bridge.py` (results → `Data-Pipeline/results`).
- `docker/README.md` rewritten: 312 lines (was 551), incl. data pipeline section.

## Verified 2026-11-08 (local, Docker Desktop, 6 vCPU / 7.7 GB WSL)

- `compose config -q` OK for local + server, with and without the profile.
- Images built: `aquarius-ingestion` (migrations + schemas inside), `aquarius-imagestream`, `aquarius-grafana`,
  `aquarius-telemetry` (ROS / agent / px4_msgs layers from cache, only bridge layers rebuilt).
- Pipeline up: 5 Kafka topics (10 partitions), `db-migrate` applied 0001–0007, DB = PostgreSQL 17 + TimescaleDB 2.22.1 +
  PostGIS 3.6 + toolkit, `shared_buffers` 256MB (tune works in the ha image), ingestion consuming.
- **2 Gazebo drones** (`gz_x500_mono_cam_down`, `HEADLESS=1 QGC=0`): bridge `uav0`, `uav1`, PX4 offset learned for both,
  `telemetry` rows with Baylands coordinates (37.4122, -121.9989), `missions` / `drones` rows, **0 `ingest_rejects`**,
  imagestream `/drones.json` 640x480 JPEGs for both drones.
- Real flight: `aquarius start mission 37.41245,-121.99870 --alt 12` → mission → RTL recorded (`AUTO_RTL`, alt 29 m).
- `tools/phase_c_check.sh`: all Phase C + D checks PASSED, 18 dashboard queries OK. `aq_utm_srid` Baylands = 32610.
- Grafana: both dashboards provisioned (`aquarius-fleet`, `aquarius-mission`), datasource health OK, Mission query
  `aq_drone_status` returns live uav0 / uav1 via `/api/ds/query`.
- Unit tests (python:3.12-slim container): 45 passed, 1 failed only because `pyyaml` was not installed in that container.
- `aquarius pipeline` (status) and `pipeline down` worked; `pipeline up` brought the services back.

### Resumed session (same day)

- Telemetry rebuilt with the bridge supervisor; full stack started with 2 camera drones (`HEADLESS=1 QGC=0`).
- **`pipeline down` → `up` cycle PASSED** with no side effects: bridge processes 1 → 0 ("Kafka gone -> stopping the
  bridge", "waiting for Kafka") → 1 ("Kafka up"), `px4-sim` and `telemetry` RestartCount unchanged, fresh
  `telemetry` rows for `uav0` + `uav1` (265 each per minute) after the cycle, `kafka errors=0`.
- Ingestion needs ~20 s after `pipeline up` (consumer group rejoin) before rows appear — normal.

## Problems found

1. **PX4 SITL segfault** ~5 s after its uXRCE client reconnected — triggered when the first version of
   `aquarius pipeline down` restarted `telemetry`. Intermittent, PX4-side. **Fixed on our side and tested:** the
   bridge supervisor in `telemetry/entrypoint.sh` starts / stops only the bridge, the CLI never restarts telemetry.
   Advice stays: avoid restarting telemetry alone mid-flight; after px4-sim restarts (local) telemetry must follow.
2. **`timescaledb-ha` is 6.8 GB** unpacked (estimate given to the user was ~1 GB more than the old 1.2 GB image).
   Alternative if too big for the VM: own small image `FROM timescale/timescaledb:2.22.1-pg17` + PostGIS (`apk add`),
   pushed as `aquarius-timescaledb`. **User decision pending.**
3. RTF 0.34 with 2 camera drones + pipeline on this PC (CPU rendering; 1 camera drone ≈ 0.9–1.0). Pipeline `ts` is
   sim time, so it lags the wall clock when RTF < 1.
4. Old volume `aquarius-local_timescaledb-data` (pre-PostGIS DB) is unused now — delete when sure:
   `docker volume rm aquarius-local_timescaledb-data`.
5. Found + fixed while resuming: `kill -INT` did not stop the bridge (background jobs of a subshell ignore SIGINT) →
   SIGTERM; `ros2 run` exits on SIGTERM but leaves the node running (orphan, 2 bridges after `up`) → the node
   executable is started directly (`$(ros2 pkg prefix aquarius_bridge)/lib/aquarius_bridge/bridge`).
6. Cold start of the whole stack at once: drone0's rcS hit the 30 s "Waiting for Gazebo world" timeout
   (`Startup script returned with return value: 256`) → px4-sim restarted by itself and came up fine; locally
   telemetry then sat in the dead network namespace until restarted (known `service:px4-sim` issue). Not new, but
   more likely with the pipeline starting at the same time on a loaded PC.
7. 2 rows in `ingest_rejects` from the first session: `ts_us` negative (-7 s / -75 s) — the bridge's time mapping
   before its PX4 offset settled. Rejected by the schema (no bad rows); none in the resumed session. Bridge-side
   edge case, not investigated.

## Next

1. Decide the DB image (problem 2).
2. Push images (README ②, incl. `ingestion`, `imagestream`) and test `compose.server.yml` on the VM
   (`aquarius init server --pipeline`, host network Kafka 127.0.0.1:19092).
3. Optional: `Data-Pipeline/tools/phase_b_check.sh` while flying 2 drones, `check_dedupe.sh`, headless-Chrome
   screenshot of the Mission dashboard, look at problem 7.
4. Commit when asked (branch `feature-Network-flukez`).

Done from the old "Resume here" list: telemetry rebuilt, down/up cycle tested, `data/README.md` removed (`data/` gone),
docs updated (`WORKFLOW.md` 1b + 4a + file map, `AQUARIUS_CLI.md` `pipeline` section / options / troubleshooting,
`communication_plan.md` §6 note, `telemetry_plan.md` §4 diagram, `CLAUDE.md`).

Free the PC: `cd docker && docker compose -f compose.local.yml --profile pipeline down` (keeps data; `-v` deletes it).
