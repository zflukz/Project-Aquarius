"""
mission_cli.py — one-shot drone commands for the `aquarius` CLI (scripts/aquarius).

  python3 mission_cli.py goto 37.4125,-121.9985 37.4128,-121.9992   # waypoints -> PX4 mission -> RTL
  python3 mission_cli.py status                                     # armed, mode, battery, position
  python3 mission_cli.py rtl | land                                 # return to launch / land now

Points: "LAT,LON" (also "{LAT,LON}" or "LAT, LON"), flown in order at --alt, then RTL.
The mission is uploaded to PX4, so QGroundControl shows it. Takeoff / mission / RTL: mcpp_mission.fly().

Env / args (args win):
  DRONE_ID     drone to command (udp 14540 + DRONE_ID)       default 0
  MAVLINK_URL  full address instead (e.g. serial:///dev/ttyUSB0:57600)
  ALT / SPEED  mission altitude (m, relative to home) / m/s   default 20 / 5
  MAX_DIST     refuse points further than this from home (m) default 1000
"""
import argparse
import asyncio
import math
import os
import re

from mcpp_mission import SYSTEMS, connect, exit_clean, fly, latlon_to_ne

POINT = re.compile(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")


def parse_points(tokens):
    points = [(float(a), float(b)) for a, b in POINT.findall(" ".join(tokens))]
    if not points:
        raise SystemExit("no waypoint given — use LAT,LON e.g. 37.4125,-121.9985")
    for lat, lon in points:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise SystemExit(f"invalid point {lat},{lon} (LAT,LON order?)")
    return points


def address(drone_id):
    return os.environ.get("MAVLINK_URL") or f"udpin://0.0.0.0:{14540 + drone_id}"


async def goto(args):
    points = parse_points(args.points)
    drone, home = await connect(args.drone_id, address(args.drone_id), args.connect_timeout)
    for lat, lon in points:
        n, e = latlon_to_ne(home.latitude_deg, home.longitude_deg, lat, lon)
        dist = math.hypot(n, e)
        print(f"[drone{args.drone_id}] waypoint {lat:.6f},{lon:.6f}: {dist:.0f} m from home "
              f"({n:+.0f} N, {e:+.0f} E)")
        if dist > args.max_dist:
            raise SystemExit(f"waypoint {dist:.0f} m from home > MAX_DIST {args.max_dist:g} m — "
                             "wrong point? raise --max-dist if intended")
    await fly(args.drone_id, drone, points, args.alt, args.speed)
    print("=== MISSION DONE ===")


async def simple_connect(drone_id, timeout):
    from mavsdk_grpc import System
    drone = System(port=50051 + drone_id)
    SYSTEMS.append(drone)
    await drone.connect(system_address=address(drone_id))

    async def ready():
        async for state in drone.core.connection_state():
            if state.is_connected:
                return
    await asyncio.wait_for(ready(), timeout)
    return drone


async def first(stream, timeout=5):
    async def get():
        async for value in stream:
            return value
    try:
        return await asyncio.wait_for(get(), timeout)
    except asyncio.TimeoutError:
        return None


async def status(args):
    drone = await simple_connect(args.drone_id, args.connect_timeout)
    t = drone.telemetry
    armed, mode, in_air = await first(t.armed()), await first(t.flight_mode()), await first(t.in_air())
    bat, pos, gps = await first(t.battery()), await first(t.position()), await first(t.gps_info())
    health = await first(t.health())
    print(f"drone{args.drone_id}: {'ARMED' if armed else 'disarmed'}, mode {mode}, "
          f"{'in air' if in_air else 'on ground'}")
    if bat:
        print(f"  battery  {bat.remaining_percent:.0f} %  {bat.voltage_v:.1f} V")
    if pos:
        print(f"  position {pos.latitude_deg:.6f},{pos.longitude_deg:.6f}  alt {pos.relative_altitude_m:.1f} m")
    if gps:
        print(f"  gps      {gps.fix_type}, {gps.num_satellites} satellites")
    if health:
        print(f"  ready    global position {'ok' if health.is_global_position_ok else 'NO'}, "
              f"home {'ok' if health.is_home_position_ok else 'NO'}, "
              f"armable {'yes' if health.is_armable else 'no'}")


async def action(args):
    drone = await simple_connect(args.drone_id, args.connect_timeout)
    if args.cmd == "rtl":
        await drone.action.return_to_launch()
        print(f"drone{args.drone_id}: return to launch")
    else:
        await drone.action.land()
        print(f"drone{args.drone_id}: landing")


def main():
    env = os.environ.get
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["goto", "status", "rtl", "land"])
    ap.add_argument("points", nargs="*", help="goto: LAT,LON ...")
    ap.add_argument("--drone-id", type=int, default=int(env("DRONE_ID", "0")))
    ap.add_argument("--alt", type=float, default=float(env("ALT", "20")))
    ap.add_argument("--speed", type=float, default=float(env("SPEED", "5")))
    ap.add_argument("--max-dist", type=float, default=float(env("MAX_DIST", "1000")))
    ap.add_argument("--connect-timeout", type=float, default=float(env("CONNECT_TIMEOUT", "60")))
    args = ap.parse_args()
    run = {"goto": goto, "status": status}.get(args.cmd, action)
    exit_clean(run(args))


if __name__ == "__main__":
    main()
