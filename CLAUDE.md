# CLAUDE.md — Project Aquarius

Guidance for Claude Code (and humans) working in this repo, plus a record of the Docker /
simulation rework and how each part was verified.

## Project

Drone project (KMUTT senior project, "Xenoir"): PX4 SITL + Gazebo Harmonic simulation, controlled
with MAVSDK / ROS 2 scripts. Goal: multi-drone (swarm) simulation on a VM server, controlled from
a separate computer, later real drones.

| Path | What |
| :--- | :--- |
| `docker/` | **everything Docker / simulator** — see `docker/README.md` |
| `Drone-Control/` | `flight_test.py`, `teleop_drone.py` (MAVSDK), `view_cam.py` (ROS 2 camera) |
| `WORKFLOW.md` | end-to-end user workflow (setup → fly → camera → roadmap) |
| `scripts/` | legacy **native** (non-Docker) install scripts |
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

Three modes, same two images (`ghcr.io/zflukz/aquarius-px4-sim`, `ghcr.io/zflukz/aquarius-drone-control`):

| Compose file | Runs on | Notes |
| :--- | :--- | :--- |
| `docker/compose.local.yml` | your computer | **local test**: px4-sim + novnc + drone-control on one bridge network; also builds & pushes images |
| `docker/compose.server.yml` | VM server | **pull-only** (no build, no git): px4-sim + novnc, `network_mode: host` |
| `docker/compose.control.yml` | control computer | drone-control, publishes udp 14540-14549 |

MAVLink (drone `i` = PX4 instance `i` = sysid `i+1`), opened by `docker/px4-sim/entrypoint.sh`:

| Link | PX4 binds (sim side) | PX4 sends to |
| :--- | :--- | :--- |
| MAVSDK / offboard | `14640+i` | `CONTROL_HOST:14540+i` |
| QGroundControl | `18640+i` | `CONTROL_HOST:14550` |

PX4's own default links (14580+i → localhost:14540+i, 18570+i, …) still exist; ours are extra.

`entrypoint.sh` flow: kill stale px4/gz + `/tmp/px4_lock-*` → ensure Baylands Fuel cache →
Xvfb/fluxbox/x11vnc → for each drone `px4 -i i -d` with `PX4_GZ_MODEL_POSE="0,i*DRONE_SPACING"`,
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

### Resume here (next session)

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

- [ ] Rebuilt `px4-sim` image (QGC layer + both fixes) starts cleanly with `up -d --force-recreate`.
- [ ] Gazebo + QGC visible in the browser (http://localhost:8080/vnc.html) — so far only checked by
      screenshot of `:99` inside the container.
- [ ] `PX4_NUM_DRONES=2`: both drones spawn in one world, each reachable on 14540 / 14541, both in QGC.
- [ ] `docker compose push` to GHCR and pull on a real VM; split-mode flight from the control computer.
- [ ] Docker Desktop (Windows) published UDP range 14540-14549 delivers PX4's packets to the container.

Suggested test order: local build → `flight_test.py` (1 drone) → 2 drones → push → VM pull → split test.

## Known issues / TODO

- `scripts/install_sim_server.sh` / `install_control_computer.sh` (native path) still use the old
  single-port MAVLink idea (`mavlink start -u 14540 -t IP`, sed patch of `udp://:14540` that no longer
  matches anything). Docker is the maintained path; update or retire these.
- `px4-sim` image has no `ros_gz_bridge` → camera (`view_cam.py`) doesn't work in the Docker split yet.
- `CONTROL_HOST` is resolved once at container start; if the control computer's IP changes, recreate `px4-sim`.
- noVNC / x11vnc have no password — keep 8081 on LAN / VPN only.
- GHCR packages are private by default → make public or `docker login` on the VM.
- Raw-GitHub `curl` of `compose.server.yml` works only after these files are on `main` and the repo is public.
- `git status` shows `Visualization/Python/*` deleted — not done by Claude; confirm before committing.
- Future plan (multi-drone phases, swarm script, MicroXRCE agent, per-drone containers, CI build/push):
  see `docker/README.md` → "Future plan".
