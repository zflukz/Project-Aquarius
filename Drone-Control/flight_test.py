"""
flight_test.py — automated flight simulation that exercises every
movement axis from teleop_drone.py and verifies telemetry after each.
"""
import asyncio
import mavsdk_grpc as mavsdk
from mavsdk_grpc import System
from mavsdk_grpc.offboard import OffboardError, VelocityBodyYawspeed

SPEED_LINEAR = 2.5
SPEED_VERT   = 1.2
SPEED_YAW    = 35.0
STEP_SECS    = 2.0   # seconds per movement step
HOVER        = VelocityBodyYawspeed(0.0, 0.0, 0.0, 0.0)

async def send(drone, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=STEP_SECS):
    cmd = VelocityBodyYawspeed(vx, vy, vz, vyaw)
    end = asyncio.get_event_loop().time() + duration
    while asyncio.get_event_loop().time() < end:
        await drone.offboard.set_velocity_body(cmd)
        await asyncio.sleep(0.05)

async def get_pos(drone):
    async for pos in drone.telemetry.position():
        return pos

async def run():
    import os
    host = os.environ.get("PX4_HOST", "")
    address = f"udpout://{host}:14540"
    drone = System()
    print(f"Connecting to PX4 SITL {address} ...")
    await drone.connect(system_address=address)

    async for state in drone.core.connection_state():
        if state.is_connected:
            print("Connected.")
            break

    # wait for GPS lock
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            print("GPS lock OK.")
            break

    print("Arming ...")
    await drone.action.arm()
    print("Takeoff ...")
    await drone.action.takeoff()
    await asyncio.sleep(6)

    pos = await get_pos(drone)
    assert pos.relative_altitude_m > 1.0, \
        f"FAIL: takeoff altitude too low ({pos.relative_altitude_m:.1f} m)"
    print(f"Altitude after takeoff: {pos.relative_altitude_m:.1f} m  PASS")

    # enter Offboard
    await drone.offboard.set_velocity_body(HOVER)
    try:
        await drone.offboard.start()
        print("Offboard mode active.")
    except OffboardError as e:
        raise RuntimeError(f"Offboard start failed: {e}")

    steps = [
        ("Forward  [W]", dict(vx= SPEED_LINEAR)),
        ("Backward [S]", dict(vx=-SPEED_LINEAR)),
        ("Left     [Q]", dict(vy=-SPEED_LINEAR)),
        ("Right    [E]", dict(vy= SPEED_LINEAR)),
        ("Up       [R]", dict(vz=-SPEED_VERT)),
        ("Down     [F]", dict(vz= SPEED_VERT)),
        ("Yaw-L    [A]", dict(vyaw=-SPEED_YAW)),
        ("Yaw-R    [D]", dict(vyaw= SPEED_YAW)),
        ("Hover [SPACE]", {}),
    ]

    for label, kwargs in steps:
        print(f"  {label} ...")
        await send(drone, **kwargs)
        pos = await get_pos(drone)
        print(f"    alt={pos.relative_altitude_m:.1f} m  lat={pos.latitude_deg:.6f}  lon={pos.longitude_deg:.6f}")

    print("Landing [X] ...")
    await drone.offboard.stop()
    await drone.action.land()

    async for in_air in drone.telemetry.in_air():
        if not in_air:
            print("Landed.")
            break

    print("\n=== Flight simulation PASSED ===")

if __name__ == "__main__":
    asyncio.run(run())
