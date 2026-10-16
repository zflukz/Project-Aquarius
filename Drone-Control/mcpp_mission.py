"""
mcpp_mission.py — automatic coverage flight over the map with MCPP
(Multi-robot Coverage Path Planning), uploaded to PX4 as a normal mission so
QGroundControl shows every planned path on its map.

Algorithm (MSTC-style: partition + Spanning Tree Coverage per drone):
  1. The area (rectangle around drone 0's home) is split into coarse cells of
     2 x SWEEP metres; each coarse cell = 4 sub-cells of SWEEP metres (the
     distance between two neighbouring flight lines).
  2. Balanced partition: every drone grows a connected region from the cell
     nearest its start; the drone with the fewest cells always grows next, so
     each drone gets ~1/N of the area.
  3. Per region, a minimum spanning tree over the coarse cells (edges along
     the long side are cheaper -> long straight lines, few turns).
  4. Spanning Tree Coverage: walking around the tree visits every sub-cell
     exactly once (Hamiltonian cycle) -> lawnmower-like path that covers
     the whole region.
  5. Path -> lat/lon waypoints -> PX4 mission -> takeoff, mission, RTL.

Env / args (args win):
  NUM_DRONES   drones to fly together (drone i on udp 14540+i)   default 1
  DRONE_ID     first drone id (single drone: just this one)      default 0
  AREA_W/AREA_H  area size east / north in metres                 default 80 / 60
  AREA_N/AREA_E  area centre offset from drone 0 home (north/east) default 0 / 0
  SWEEP        line spacing in metres (sensor footprint)          default 5
  ALT          flight altitude (drone i flies ALT + i*ALT_STEP)   default 20
  SPEED        m/s                                                 default 5

  python3 mcpp_mission.py                       # drone 0, default area
  NUM_DRONES=2 python3 mcpp_mission.py          # both drones share the area
  python3 mcpp_mission.py --plan-only -n 3      # print the plan, no flight
"""
import argparse
import asyncio
import heapq
import math
import os

EARTH_R = 6378137.0
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


# ---------------------------------------------------------------- planning

def build_grid(area_w, area_h, sweep):
    """Coarse grid (row = north, col = east) of 2*sweep cells, all free."""
    cell = 2 * sweep
    rows, cols = max(1, int(area_h // cell)), max(1, int(area_w // cell))
    return rows, cols, {(r, c) for r in range(rows) for c in range(cols)}


def nearest_cell(cells, point, cell, origin, taken=()):
    n, e = point
    return min((c for c in cells if c not in taken),
               key=lambda rc: (origin[0] + (rc[0] + 0.5) * cell - n) ** 2
                            + (origin[1] + (rc[1] + 0.5) * cell - e) ** 2)


def partition(cells, seeds):
    """Balanced region growing. Returns {cell: drone index}."""
    owner = {s: i for i, s in enumerate(seeds)}
    regions = [[s] for s in seeds]
    # per drone: BFS frontier ordered by distance from its seed -> compact regions
    frontier = [[] for _ in seeds]
    dist = [{s: 0} for s in seeds]

    def push(i, rc):
        for dr, dc in DIRS:
            nb = (rc[0] + dr, rc[1] + dc)
            if nb in cells and nb not in owner and nb not in dist[i]:
                dist[i][nb] = dist[i][rc] + 1
                heapq.heappush(frontier[i], (dist[i][nb], nb))

    for i, s in enumerate(seeds):
        push(i, s)
    while len(owner) < len(cells):
        grew = False
        for i in sorted(range(len(seeds)), key=lambda k: len(regions[k])):
            while frontier[i] and frontier[i][0][1] in owner:
                heapq.heappop(frontier[i])
            if frontier[i]:
                _, rc = heapq.heappop(frontier[i])
                owner[rc] = i
                regions[i].append(rc)
                push(i, rc)
                grew = True
                break
        if not grew:   # cells not connected to any drone
            break
    return owner


def spanning_tree(region, prefer_rows):
    """Kruskal MST over 4-connected coarse cells; cheaper along the long axis."""
    region = set(region)
    edges = []
    for r, c in region:
        if (r, c + 1) in region:
            edges.append((1.0 if prefer_rows else 1.5, (r, c), (r, c + 1)))
        if (r + 1, c) in region:
            edges.append((1.5 if prefer_rows else 1.0, (r, c), (r + 1, c)))
    edges.sort()
    parent = {rc: rc for rc in region}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    tree = []
    for _, a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
            tree.append((a, b))
    return tree


def stc_cycle(region, tree):
    """Spanning Tree Coverage: Hamiltonian cycle over all sub-cells (R, C)."""
    edges = set()

    def toggle(a, b, on):
        e = frozenset((a, b))
        (edges.add if on else edges.discard)(e)

    # each coarse cell starts as a small loop around its 4 sub-cells
    for r, c in region:
        R, C = 2 * r, 2 * c
        loop = [(R, C), (R, C + 1), (R + 1, C + 1), (R + 1, C)]
        for k in range(4):
            toggle(loop[k], loop[(k + 1) % 4], True)
    # each tree edge merges the two loops it joins
    for a, b in tree:
        (r1, c1), (r2, c2) = sorted((a, b))
        R, C = 2 * r1, 2 * c1
        if r1 == r2:   # b is east of a
            toggle((R, C + 1), (R + 1, C + 1), False)
            toggle((R, C + 2), (R + 1, C + 2), False)
            toggle((R, C + 1), (R, C + 2), True)
            toggle((R + 1, C + 1), (R + 1, C + 2), True)
        else:          # b is north of a
            toggle((R + 1, C), (R + 1, C + 1), False)
            toggle((R + 2, C), (R + 2, C + 1), False)
            toggle((R + 1, C), (R + 2, C), True)
            toggle((R + 1, C + 1), (R + 2, C + 1), True)

    adj = {}
    for e in edges:
        a, b = tuple(e)
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    assert all(len(v) == 2 for v in adj.values()), "STC: not a cycle"
    start = min(adj)
    cycle, prev, cur = [start], None, start
    while True:
        nxt = adj[cur][0] if adj[cur][0] != prev else adj[cur][1]
        if nxt == start:
            break
        cycle.append(nxt)
        prev, cur = cur, nxt
    assert len(cycle) == 4 * len(region), "STC: cycle misses sub-cells"
    return cycle


def simplify(path):
    """Keep only the corners of a grid path."""
    if len(path) < 3:
        return path
    out = [path[0]]
    for a, b, c in zip(path, path[1:], path[2:]):
        if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
            out.append(b)
    out.append(path[-1])
    return out


def plan(starts, area_w, area_h, area_n, area_e, sweep):
    """starts: [(north, east)] of each drone relative to drone 0 home.
    Returns (paths [[(north, east), ...]] per drone, owner, rows, cols)."""
    rows, cols, cells = build_grid(area_w, area_h, sweep)
    cell = 2 * sweep
    origin = (area_n - rows * cell / 2, area_e - cols * cell / 2)   # SW corner
    if len(starts) > len(cells):
        raise ValueError(f"{len(starts)} drones but only {len(cells)} cells — bigger area or smaller SWEEP")
    seeds = []
    for p in starts:
        seeds.append(nearest_cell(cells, p, cell, origin, taken=seeds))
    owner = partition(cells, seeds)

    paths = []
    for i, p in enumerate(starts):
        region = [rc for rc, o in owner.items() if o == i]
        cycle = stc_cycle(region, spanning_tree(region, prefer_rows=cols >= rows))
        to_ne = lambda RC: (origin[0] + (RC[0] + 0.5) * sweep, origin[1] + (RC[1] + 0.5) * sweep)
        # start at the sub-cell nearest the drone, go once around the cycle
        k = min(range(len(cycle)),
                key=lambda j: (to_ne(cycle[j])[0] - p[0]) ** 2 + (to_ne(cycle[j])[1] - p[1]) ** 2)
        ordered = cycle[k:] + cycle[:k]
        paths.append([to_ne(RC) for RC in simplify(ordered)])
    return paths, owner, rows, cols


def path_length(path):
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def print_plan(paths, owner, rows, cols, sweep):
    print(f"MCPP grid: {rows} x {cols} cells of {2 * sweep:g} m  (north up, digit = drone)")
    for r in reversed(range(rows)):
        print("  " + " ".join(str(owner.get((r, c), "#")) for c in range(cols)))
    for i, p in enumerate(paths):
        cells = sum(1 for o in owner.values() if o == i)
        print(f"  drone{i}: {cells} cells, {len(p)} waypoints, {path_length(p):.0f} m")


def ne_to_latlon(home_lat, home_lon, n, e):
    lat = home_lat + math.degrees(n / EARTH_R)
    lon = home_lon + math.degrees(e / (EARTH_R * math.cos(math.radians(home_lat))))
    return lat, lon


def latlon_to_ne(home_lat, home_lon, lat, lon):
    n = math.radians(lat - home_lat) * EARTH_R
    e = math.radians(lon - home_lon) * EARTH_R * math.cos(math.radians(home_lat))
    return n, e


# ---------------------------------------------------------------- flight

async def connect(drone_id, address, timeout):
    from mavsdk_grpc import System
    # one mavsdk_server per drone -> own gRPC port
    drone = System(port=50051 + drone_id)
    print(f"[drone{drone_id}] connecting {address} ...")
    await drone.connect(system_address=address)

    async def ready():
        async for state in drone.core.connection_state():
            if state.is_connected:
                break
        async for health in drone.telemetry.health():
            if health.is_global_position_ok and health.is_home_position_ok:
                break
        async for home in drone.telemetry.home():
            return home

    home = await asyncio.wait_for(ready(), timeout)
    print(f"[drone{drone_id}] connected, home {home.latitude_deg:.6f}, {home.longitude_deg:.6f}")
    return drone, home


async def fly(drone_id, drone, waypoints, alt, speed):
    from mavsdk_grpc.mission import MissionItem, MissionPlan
    tag = f"[drone{drone_id}]"
    nan = float("nan")
    items = [MissionItem(lat, lon, alt, speed, True, nan, nan,
                         MissionItem.CameraAction.NONE, nan, nan, 1.0, nan, nan,
                         MissionItem.VehicleAction.NONE)
             for lat, lon in waypoints]

    await drone.mission.clear_mission()
    # must be set before upload: MAVSDK appends the RTL item while uploading
    await drone.mission.set_return_to_launch_after_mission(True)
    await drone.mission.upload_mission(MissionPlan(items))
    print(f"{tag} mission uploaded ({len(items)} waypoints, alt {alt:g} m) — visible in QGroundControl")

    async for in_air in drone.telemetry.in_air():
        break
    if in_air:
        print(f"{tag} already flying, skipping takeoff")
    else:
        await drone.action.set_takeoff_altitude(alt)
        print(f"{tag} arming + takeoff ...")
        await drone.action.arm()
        await drone.action.takeoff()
        # PX4 spools up ~5 s before climbing (see flight_test.py)
        end = asyncio.get_event_loop().time() + 40
        async for pos in drone.telemetry.position():
            if pos.relative_altitude_m > alt * 0.8 or asyncio.get_event_loop().time() > end:
                break

    await drone.mission.start_mission()
    print(f"{tag} coverage mission started")
    async for prog in drone.mission.mission_progress():
        print(f"{tag} waypoint {prog.current}/{prog.total}")
        if prog.total and prog.current == prog.total:
            break

    print(f"{tag} coverage done, returning to launch ...")
    await drone.action.return_to_launch()   # explicit too, in case PX4 just loiters
    async for in_air in drone.telemetry.in_air():
        if not in_air:
            break
    print(f"{tag} landed")


async def run(args):
    ids = list(range(args.drone_id, args.drone_id + args.num_drones))
    # PX4 sends drone i to <this computer>:14540+i; MAVLINK_URL only for a single drone
    url = os.environ.get("MAVLINK_URL") if len(ids) == 1 else None
    links = await asyncio.gather(*(connect(i, url or f"udpin://0.0.0.0:{14540 + i}", args.connect_timeout)
                                   for i in ids))
    home0 = links[0][1]
    starts = [latlon_to_ne(home0.latitude_deg, home0.longitude_deg, h.latitude_deg, h.longitude_deg)
              for _, h in links]

    paths, owner, rows, cols = plan(starts, args.area_w, args.area_h, args.area_n, args.area_e, args.sweep)
    print_plan(paths, owner, rows, cols, args.sweep)

    flights = []
    for k, ((drone, _), path) in enumerate(zip(links, paths)):
        wps = [ne_to_latlon(home0.latitude_deg, home0.longitude_deg, n, e) for n, e in path]
        # different altitude per drone: transit legs between regions never collide
        flights.append(fly(ids[k], drone, wps, args.alt + k * args.alt_step, args.speed))
    await asyncio.gather(*flights)
    print("\n=== MCPP coverage flight PASSED ===")


def main():
    env = os.environ.get
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--num-drones", type=int, default=int(env("NUM_DRONES", "1")))
    ap.add_argument("--drone-id", type=int, default=int(env("DRONE_ID", "0")))
    ap.add_argument("--area-w", type=float, default=float(env("AREA_W", "80")), help="east-west size, m")
    ap.add_argument("--area-h", type=float, default=float(env("AREA_H", "60")), help="north-south size, m")
    ap.add_argument("--area-n", type=float, default=float(env("AREA_N", "0")), help="centre north of home, m")
    ap.add_argument("--area-e", type=float, default=float(env("AREA_E", "0")), help="centre east of home, m")
    ap.add_argument("--sweep", type=float, default=float(env("SWEEP", "5")), help="line spacing, m")
    ap.add_argument("--alt", type=float, default=float(env("ALT", "20")))
    ap.add_argument("--alt-step", type=float, default=float(env("ALT_STEP", "3")))
    ap.add_argument("--speed", type=float, default=float(env("SPEED", "5")))
    ap.add_argument("--spacing", type=float, default=float(env("DRONE_SPACING", "3")),
                    help="--plan-only: drones assumed DRONE_SPACING m apart to the north (Gazebo spawn)")
    ap.add_argument("--connect-timeout", type=float, default=float(env("CONNECT_TIMEOUT", "300")))
    ap.add_argument("--plan-only", action="store_true", help="print the plan, do not connect")
    args = ap.parse_args()

    if args.plan_only:
        starts = [(k * args.spacing, 0.0) for k in range(args.num_drones)]
        print_plan(*plan(starts, args.area_w, args.area_h, args.area_n, args.area_e, args.sweep), args.sweep)
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
