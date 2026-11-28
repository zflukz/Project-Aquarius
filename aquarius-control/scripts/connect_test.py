import asyncio, sys, time
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Drone-Control"))
from mcpp_mission import connect, exit_clean
async def main():
    t = time.time()
    links = await asyncio.gather(*(connect(i, f"udpin://0.0.0.0:{14540+i}", 60) for i in (0, 1)))
    print(f"both drones ready in {time.time()-t:.1f} s")
    print("\n=== connect test PASSED ===")
exit_clean(main())
