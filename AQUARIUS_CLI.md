# `aquarius` command — user guide

One command to start the simulator, fly waypoint or coverage missions, check the drones and bring them home.
Script: [`scripts/aquarius`](scripts/aquarius). Full background on the setup: [`WORKFLOW.md`](WORKFLOW.md).

```bash
aquarius init                                          # start everything, wait for the drones
aquarius start mission 37.4125,-121.9985 37.4128,-121.9992
aquarius status
aquarius rtl
```

---

## 1. Install

Needs **bash** (Linux, macOS, or Git Bash on Windows) plus **Docker**, or the native install
(`scripts/install_control_computer.sh`).

```bash
cd Project-Aquarius
bash scripts/aquarius install         # links ~/.local/bin/aquarius -> scripts/aquarius
aquarius --help
```

If `aquarius` is not found, add `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc` and open a new terminal.
Without installing, run `bash scripts/aquarius ...` from the repo.

---

## 2. Commands

| Command | What it does |
| :--- | :--- |
| `aquarius init [local\|server\|control]` | start the containers for that machine and wait until PX4 is ready |
| `aquarius start mission LAT,LON [LAT,LON ...]` | fly to the points in order, then return home and land |
| `aquarius start coverage [LAT,LON]` | MCPP coverage flight (lawnmower path) over an area centred on the point |
| `aquarius status` | containers, then armed / flight mode / battery / position / GPS of a drone |
| `aquarius rtl` (= `aquarius stop`) | return to launch now |
| `aquarius land` | land where it is now |
| `aquarius logs [mission\|coverage\|px4\|telemetry]` | follow a log (default `mission`) |
| `aquarius urls` | links to Gazebo, QGroundControl, camera and the Grafana dashboard |
| `aquarius down` | stop all containers |
| `aquarius install` | put the command on your PATH |

### Options

| Option | Used by | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `--drone N` | start, status, rtl, land, logs | `0` (or `DRONE_ID`) | which drone (drone N = udp 14540+N, MAV_SYS_ID N+1) |
| `--alt M` | start | `20` | flight altitude in metres above home |
| `--speed V` | start | `5` | speed in m/s |
| `--max-dist M` | start mission | `1000` | refuse waypoints further than this from home |
| `--detach`, `-d` | start | – | start and return immediately, don't follow the log |
| `--size WxH` | start coverage | `80x60` | area width (east-west) x height (north-south) in metres |
| `--sweep M` | start coverage | `5` | distance between flight lines (camera footprint) |
| `--drones N`, `-n N` | init, start coverage | `1` | `init`: number of simulated drones; `coverage`: drones sharing the area |
| `--camera` | init | – | drones with a downward camera (`gz_x500_mono_cam_down`) |
| `--world W` | init | `baylands` | Gazebo world (`default` = flat and light) |
| `--headless` | init | – | no Gazebo GUI (saves CPU) |
| `--control-host IP` | init server | – | IP of the control computer (required on the VM) |
| `--build` | init local | – | rebuild the images before starting |

The `init` options are **saved in `docker/.env`**, so later restarts keep them. Edit or remove the lines there to undo
them (e.g. delete `PX4_SIM_MODEL` / `PX4_SYS_AUTOSTART` to drop the camera).

---

## 3. Start the simulator — `init`

```bash
aquarius init                               # local test: everything on this computer
aquarius init --drones 2 --camera           # 2 drones with cameras
aquarius init --world default --headless    # lightest setup (weak PC)
```

It creates `docker/.env` from `.env.example` if missing, runs `docker compose up -d` and waits (up to 6 min) until
the last drone is connected. Then it prints the links:

```
drone0 (sysid 1) -> 172.19.0.2:14540 [MAVSDK], :14550 [QGC]
>>> ready — 1 drone(s)
    Gazebo      http://localhost:8080/vnc.html
    QGC         http://localhost:8082/vnc.html
    camera      http://localhost:8083/
    Grafana     http://localhost:3000/d/aquarius-fleet?var-cam=http://localhost:8083
```

**Split setup** (simulator on the VM, scripts on your computer):

```bash
# VM — needs only compose.server.yml (+ .env) next to the script, or the repo
aquarius init server --control-host 192.168.1.50 --drones 2

# control computer
aquarius init control
aquarius status
```

On the VM the links use `http://<VM_IP>:8081/vnc.html` (Gazebo), `:8082` (QGC), `:8083` (camera), `http://<VM_IP>:3000` (Grafana, login `admin` / `GRAFANA_ADMIN_PASSWORD`).
Firewall ports are listed in `docker/README.md`.

---

## 4. Fly waypoints — `start mission`

```bash
aquarius start mission 37.4125,-121.9985                         # one point
aquarius start mission 37.4125,-121.9985 37.4128,-121.9992       # several, flown in order
aquarius start mission "{37.4125,-121.9985}" --alt 30 --speed 3  # braces / spaces are fine (use quotes)
aquarius start mission 37.4125,-121.9985 --drone 1               # second drone
```

Points are **latitude,longitude** in decimal degrees (`37.4125,-121.9985`, not `-121.9985,37.4125`).

What happens:
1. Connects to the drone and reads its home position.
2. Prints each point's distance from home and **refuses** points further than `--max-dist` (1000 m).
3. Uploads the points as a PX4 mission (you see it on the QGroundControl map).
4. Arms, takes off to `--alt`, flies the points, **returns to launch** and lands.

```
[drone0] connected, home 37.412173, -121.998879
[drone0] waypoint 37.412450,-121.998880: 31 m from home (+31 N, -0 E)
[drone0] waypoint 37.412450,-121.998500: 45 m from home (+31 N, +33 E)
[drone0] mission uploaded (2 waypoints, alt 15 m) — visible in QGroundControl
[drone0] arming + takeoff ...
[drone0] mission started
[drone0] waypoint 1/2
[drone0] waypoint 2/2
[drone0] mission done, returning to launch ...
[drone0] landed
=== MISSION DONE ===
```

### Finding coordinates
- Baylands (default world) home is about **`37.4122,-121.9989`**.
- **0.0001° latitude ≈ 11 m** north/south; 0.0001° longitude ≈ 9 m east/west (at this latitude).
  For example, `37.4131,-121.9989` is about 100 m north of home.
- Easier: click on the QGroundControl map (or Google Maps) and copy the coordinates.

---

## 5. Cover an area — `start coverage`

```bash
aquarius start coverage                                   # 80 x 60 m around drone 0's home
aquarius start coverage 37.4126,-121.9990 --size 40x30    # area centred on a point
aquarius start coverage 37.4126,-121.9990 --drones 2      # two drones split the area (init --drones 2 first)
aquarius start coverage --size 60x40 --sweep 8 --alt 25   # wider line spacing, higher
```

The MCPP planner (`Drone-Control/mcpp_mission.py`) splits the area between the drones and builds a lawnmower path for
each. It uploads one mission per drone and flies them all at once. Each drone flies 3 m higher than the previous one,
then all return home. The log ends with `=== MCPP coverage flight PASSED ===`.

To see the plan without flying (no simulator needed):

```bash
python3 Drone-Control/mcpp_mission.py --plan-only -n 2 --area-w 40 --area-h 30
```

---

## 6. Watch, stop, check

```bash
aquarius status              # armed / mode / battery / position / GPS / ready to arm
aquarius logs mission        # follow the running (or last) mission again
aquarius rtl                 # come home now        (aquarius stop = same)
aquarius land                # land right here
aquarius status --drone 1    # another drone
```

- **Ctrl+C while following a mission only stops following.** The mission runs in the background, so the drone
  keeps flying. To stop the drone, use `aquarius rtl` or `aquarius land`.
- `rtl`, `land` and a new `start` first stop a running mission script for that drone. Only one mission per drone
  can run at a time.
- While a mission is running, `status` only shows that it is running (the mission uses the drone's link). Use `logs`.
- `--detach` starts a mission without following it, useful for starting several drones:
  ```bash
  aquarius start mission 37.4125,-121.9985 --drone 0 --detach
  aquarius start mission 37.4128,-121.9992 --drone 1 --detach --alt 23
  aquarius logs mission --drone 1
  ```

---

## 7. How it runs (Docker or native)

| | Docker (default) | Native |
| :--- | :--- | :--- |
| When | the `drone-control` container is running | no container, `Drone-Control/.venv` exists (`scripts/install_control_computer.sh`) |
| Scripts | `/drone-control/*.py` inside the image (rebuild after editing `Drone-Control/`) | `Drone-Control/*.py` from the repo |
| Logs / pid | `/tmp/aquarius-<job>-<drone>.{log,pid}` in the container | same paths on this computer |

Scripts used: `Drone-Control/mission_cli.py` (`goto`, `status`, `rtl`, `land`) and `Drone-Control/mcpp_mission.py`
(coverage). Both can also be run directly, e.g. `python3 mission_cli.py goto 37.4125,-121.9985`.

Env vars: `DRONE_ID` (default drone), `AQUARIUS_DOCKER_DIR` (folder with the compose files and `.env`, default
`<repo>/docker`, or the current folder on a VM without the repo).

Exit codes: `0` = done; `1` = bad input, refused waypoint, mission did not finish, or PX4 not ready.

---

## 8. Troubleshooting

| Problem | Fix |
| :--- | :--- |
| `aquarius: command not found` | `bash scripts/aquarius install`, add `~/.local/bin` to PATH, or use `bash scripts/aquarius` |
| `PX4 not ready after 6 min` | `aquarius logs px4`. Baylands download or low RAM: try `--world default --headless` |
| `waypoint … m from home > MAX_DIST` | wrong point or swapped lat/lon? If intended: `--max-dist 3000` |
| `invalid point … (LAT,LON order?)` | latitude first, -90..90 |
| `a mission is already running for drone N` | `aquarius rtl --drone N` (or wait), then start again |
| mission hangs at `connecting …` | sim not running or the drone has no GPS yet: `aquarius status`. Split mode: firewall / `CONTROL_HOST` |
| `server mode needs the control computer IP` | `aquarius init server --control-host <IP>` |
| changed `Drone-Control/*.py` but nothing changed | Docker uses the image: `cd docker && docker compose -f compose.local.yml up -d --build drone-control` |
| sim slow (RTF < 1) with `--camera` | keep the default camera size (640x480 @ 10 Hz); see `docker/telemetry_plan.md` |

**Safety (real drones later):** the same commands will work with `MAVLINK_URL`, but always check the points in
QGroundControl first. Keep `--max-dist` small, and set geofence / RTL / failsafe parameters in PX4.
