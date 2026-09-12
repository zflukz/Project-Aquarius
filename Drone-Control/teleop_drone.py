import asyncio
import sys
import termios
import tty
import select
from mavsdk import System
from mavsdk.offboard import OffboardError, VelocityBodyYawspeed

# ตารางการกดปุ่ม
HELP_MSG = """
==================================================
          PX4 Drone Keyboard Teleop
==================================================
       [R] บินขึ้น             [W] เดินหน้า
       [F] บินลง              [S] ถอยหลัง
  [Q] บินเบี่ยงซ้าย           [E] บินเบี่ยงขวา
  [A] หมุนซ้าย (Yaw)          [D] หมุนขวา (Yaw)

  [SPACE] : หยุดนิ่งลอยตัว (Hover)
  [X]     : สั่งลงจอด (Land) และปิดสคริปต์
==================================================
"""

def get_key(settings):
    tty.setraw(sys.stdin.fileno())
    rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
    if rlist:
        key = sys.stdin.read(1)
    else:
        key = ''
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
    return key

async def run():
    drone = System()
    print("กำลังเชื่อมต่อกับ PX4 SITL ผ่าน UDP :14540...")
    await drone.connect(system_address="udp://:14540")

    async for state in drone.core.connection_state():
        if state.is_connected:
            print(">>> เชื่อมต่อ PX4 สำเร็จ! <<<")
            break

    # เช็คสถานะการบิน หากยังอยู่บนพื้นดินให้บินขึ้นก่อน
    async for in_air in drone.telemetry.in_air():
        if not in_air:
            print("โดรนอยู่บนพื้น กำลังสั่ง Arm และ Takeoff สู่ระดับปลอดภัย...")
            await drone.action.arm()
            await drone.action.takeoff()
            await asyncio.sleep(6)
        break

    # ส่ง Setpoint ความเร็วเริ่มต้น (0,0,0,0) ก่อนเปิดโหมด Offboard
    await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0.0, 0.0, 0.0, 0.0))
    try:
        await drone.offboard.start()
        print(">>> เข้าสู่โหมด Offboard สำเร็จ พร้อมรับคำสั่งปุ่ม <<<")
    except OffboardError as e:
        print(f"เข้าโหมด Offboard ไม่สำเร็จ: {e}")
        return

    print(HELP_MSG)
    settings = termios.tcgetattr(sys.stdin)

    # ค่าความเร็วเริ่มต้น (เมตร/วินาที และ องศา/วินาที)
    SPEED_LINEAR = 2.5
    SPEED_VERT = 1.2
    SPEED_YAW = 35.0

    vx, vy, vz, vyaw = 0.0, 0.0, 0.0, 0.0

    try:
        while True:
            key = get_key(settings)

            if key in ['w', 'W']:
                vx = SPEED_LINEAR
            elif key in ['s', 'S']:
                vx = -SPEED_LINEAR
            elif key in ['q', 'Q']:
                vy = -SPEED_LINEAR
            elif key in ['e', 'E']:
                vy = SPEED_LINEAR
            elif key in ['a', 'A']:
                vyaw = -SPEED_YAW
            elif key in ['d', 'D']:
                vyaw = SPEED_YAW
            elif key in ['r', 'R']:
                vz = -SPEED_VERT   # ในระบบ NED แกน Z ชี้ลงพื้น (ค่าลบ = บินขึ้น)
            elif key in ['f', 'F']:
                vz = SPEED_VERT    # ค่าบวก = บินลง
            elif key == ' ':       # Spacebar = หยุดนิ่ง
                vx, vy, vz, vyaw = 0.0, 0.0, 0.0, 0.0
            elif key in ['x', 'X']:
                print("\nกำลังสั่ง Land ลงจอด...")
                await drone.action.land()
                break
            elif key == '\x03':    # Ctrl+C
                break

            # ส่งคำสั่งความเร็วไปยังตัวโดรนต่อเนื่อง
            await drone.offboard.set_velocity_body(
                VelocityBodyYawspeed(vx, vy, vz, vyaw)
            )
            await asyncio.sleep(0.05)

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
        try:
            await drone.offboard.stop()
        except Exception:
            pass

if __name__ == '__main__':
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nปิดโปรแกรมควบคุม")