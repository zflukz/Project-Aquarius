# Fix: `aquarius start coverage` timed out on drone 0 (2026-11-28)

## Symptom

```
aquarius start coverage 13.6509,100.4939 --size 40x30 --drones 2 --alt 40
[drone0] connecting udpin://0.0.0.0:14540 ...
[drone1] connecting udpin://0.0.0.0:14541 ...
[drone1] connected, home 13.650848, 100.493799
Traceback ... asyncio.exceptions.CancelledError ... TimeoutError
aquarius: coverage did not finish
```

Drone 1 connected, drone 0 never did, and after `CONNECT_TIMEOUT` (300 s default) the script
crashed with a long traceback. `aquarius status` showed `home NO` for **both** drones.

## What was checked

| Check | Result |
|---|---|
| Tailscale / network: UDP 14540 + 14541 on the control PC (`scripts/mavlink_sniff.py`) | OK. Packets from the server `100.96.241.4`: sysid 1 on 14540, sysid 2 on 14541. Both send `GLOBAL_POSITION_INT` and `HOME_POSITION`. |
| PX4 log (px4-sim container) | OK. `[drone0] home set` and `[drone1] home set`, both `Ready for takeoff!` |
| MAVSDK connect to drone 0, watching `telemetry.home()` and `telemetry.health()` | **Problem found.** Home arrived at t=0 s (`13.650821,100.493799`), but `health.is_home_position_ok` stayed **False** for the whole 40 s |

## Cause

`connect()` in `Drone-Control/mcpp_mission.py` waited for
`health.is_global_position_ok and health.is_home_position_ok`. MAVSDK can leave the
`is_home_position_ok` flag False even when the home position has already been received,
so the wait never ended for drone 0. Drone 1 passed because its flag happened to turn on.
The network, Tailscale, the px4 container and the IP settings were **not** the problem.

## Code changes

Backups of the old files were taken before editing (temporary scratchpad, not kept).

### `Drone-Control/mcpp_mission.py`: `connect()`

- Don't wait for `is_home_position_ok`. Wait for a good global position, then for the real home
  position from `telemetry.home()`, which only returns once `HOME_POSITION` actually arrives.
- On timeout, exit with one clear line instead of a traceback.

```diff
         async for health in drone.telemetry.health():
-            if health.is_global_position_ok and health.is_home_position_ok:
+            # not is_home_position_ok: MAVSDK can leave it False although HOME_POSITION arrives
+            if health.is_global_position_ok:
                 break
-        async for home in drone.telemetry.home():
+        async for home in drone.telemetry.home():   # waits for the real home position
             return home

-    home = await asyncio.wait_for(ready(), timeout)
+    try:
+        home = await asyncio.wait_for(ready(), timeout)
+    except asyncio.TimeoutError:
+        raise SystemExit(f"[drone{drone_id}] not ready after {timeout:g} s (no connection, global position or home)")
```

`mission_cli.py` imports this same `connect()`, so `aquarius start mission` is fixed too.

### `Drone-Control/flight_test.py`

```diff
     async for health in drone.telemetry.health():
-        if health.is_global_position_ok and health.is_home_position_ok:
+        if health.is_global_position_ok:
             print("GPS lock OK.")
             break
```

## Test

`scripts/connect_test.py` connects to both drones with the mission's own `connect()`. It doesn't
arm or take off. It ran 3 times, and the full coverage flight afterwards worked.

| Run | Result |
|---|---|
| 1 | both ready in 6.8 s, PASSED |
| 2 | both ready in 4.8 s, PASSED |
| 3 | both ready in 6.6 s, PASSED |

No `mavsdk_server` was left running afterwards (ports 14540/14541 free).

## Tools in this folder

| File | What it does | Run |
|---|---|---|
| `connect_test.py` | Connects to drone 0 + 1 like the coverage mission does, prints their home positions, exits. No flying. | `Drone-Control/.venv/bin/python scripts/connect_test.py` |
| `mavlink_sniff.py` | Listens passively for 15 s on UDP 14540/14541 and shows which sysids arrive and how many heartbeat / position / home messages. Sends nothing. Run it only while no mission is running (it needs the ports). | `python3 scripts/mavlink_sniff.py` |

## Still open (not fixed)

- **`aquarius status` still shows `home NO`.** `mission_cli.py:97` prints the same unreliable
  flag. It's only the display and doesn't block flying. It could show the real
  `telemetry.home()` value instead.
- **Telemetry container sends commands to the wrong drone.** The PX4 log shows many
  `[drone0] Ignore command 511 from 246/191 to 2/1` (and drone 1 gets ones for `1/1`).
  Sysid 246 is the telemetry service on the server: it sends `SET_MESSAGE_INTERVAL` meant
  for drone 1 to drone 0 and the reverse. PX4 ignores them, so flying isn't affected, but
  the telemetry/Grafana side may be mixing up which drone is on which port. Check this on
  the server (telemetry container / gz relay).

## Notes: camera size vs coverage

- The coverage mission doesn't use the camera (no camera settings read, waypoints use
  `CameraAction.NONE`). Changing the camera resolution (now 640x480 @ 10 Hz, set in the
  px4-sim container on the server) won't break the flight.
- Coverage is decided by `--sweep` (line spacing, default 5 m). The camera's ground footprint
  must be at least `--sweep` wide. The footprint depends on **altitude and FOV**, not
  resolution: more pixels means more detail, same area.
- A bigger image means more load on Gazebo and the camera stream over Tailscale. Raise it in
  small steps and watch for `vehicle_imu ... timestamp error` in the px4 log.
