# Project Aquarius — Workflow

## Overview

```
Environment Setup
      │
      ▼
PX4 SITL + Gazebo (simulated drone)
      │
      ├──► Keyboard Teleop (teleop_drone.py)
      │
      ├──► Automated Flight Test (flight_test.py)
      │
      └──► Camera Stream (view_cam.py)
            │
            ▼
      Real Drone Deployment
```

---

## 1. Environment Setup

### Split Setup (VM Server + Computer)

```
VM Server                          Your Computer (WSL2)
─────────────────────────────      ─────────────────────────────
PX4 SITL + Gazebo Garden           ROS 2 Humble + MAVSDK
                                   teleop_drone.py
                                   flight_test.py
                                   view_cam.py
```

**On the VM server** (Ubuntu 22.04):
```bash
bash scripts/install_sim_server.sh <YOUR_COMPUTER_IP>
```

**On your computer** (Ubuntu 22.04 / WSL2):
```bash
bash scripts/install_control_computer.sh <VM_SERVER_IP>
```

The computer script automatically patches `udp://:14540` → `udp://<VM_IP>:14540` in `teleop_drone.py` and `flight_test.py`.

---

## 2. Start the Simulated Drone

Open **Terminal 1** (WSL2):

```bash
cd ~/PX4-Autopilot
make px4_sitl gz_x500               # basic quadrotor + Gazebo window
# or with depth camera:
make px4_sitl gz_x500_depth
```

PX4 SITL starts and Gazebo opens. The drone sits on the ground waiting for commands on UDP `:14540`.

---

## 3. Flight Control

### 3a. Automated Flight Test

Verifies every movement axis without touching a keyboard.

```bash
cd Drone-Control
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install mavsdk
python3 flight_test.py
```

Sequence executed automatically:

```
Connect → GPS lock → Arm → Takeoff
  → Forward → Backward → Left → Right
  → Up → Down → Yaw-Left → Yaw-Right
  → Hover → Land
```

Each step prints live telemetry (altitude, lat, lon). Test passes when the drone lands and `in_air == False`.

### 3b. Keyboard Teleop (manual)

Open **Terminal 2** (WSL2):

```bash
cd Drone-Control
source .venv/bin/activate
python3 teleop_drone.py
```

| Key | Action |
|-----|--------|
| `W` / `S` | Forward / Backward |
| `Q` / `E` | Strafe Left / Right |
| `R` / `F` | Up / Down |
| `A` / `D` | Yaw Left / Right |
| `Space` | Hover (stop all movement) |
| `X` | Land and exit |

> `teleop_drone.py` uses `termios` / `tty` — **must run in a Linux terminal (WSL2), not PowerShell.**

---

## 4. Camera Stream

Requires `gz_x500_depth` vehicle (step 2).

**Terminal 3** — find the Gazebo image topic and bridge it to ROS 2:

```bash
gz topic -l                         # find the camera topic, e.g. /camera

source /opt/ros/humble/setup.bash
ros2 run ros_gz_bridge parameter_bridge \
  '/camera@sensor_msgs/msg/Image@gz.msgs.Image' \
  --ros-args -r '/camera:=/camera'
```

**Terminal 4** — view the stream:

```bash
cd Drone-Control
source /opt/ros/humble/setup.bash
python3 view_cam.py
```

> If `view_cam.py` waits forever, change QoS in the file from `ReliabilityPolicy.RELIABLE` → `ReliabilityPolicy.BEST_EFFORT`.

---

## 5. File Reference

```
Project-Aquarius/
├── scripts/
│   ├── install_sim_server.sh       VM server: PX4 + Gazebo, broadcasts MAVLink to computer
│   └── install_control_computer.sh Computer: ROS 2 + MAVSDK, patches VM IP into scripts
└── Drone-Control/
    ├── teleop_drone.py             keyboard controller → PX4 via MAVSDK
    ├── flight_test.py              automated flight simulation (arm/takeoff/move/land)
    ├── view_cam.py                 ROS 2 subscriber → OpenCV camera window

```

---

## 6. Component Dependency Map

```
teleop_drone.py ──► MAVSDK ──► PX4 SITL ──► Gazebo (gz_x500)
flight_test.py  ──► MAVSDK ──► PX4 SITL ──► Gazebo (gz_x500)

view_cam.py ──► rclpy ──► ros_gz_bridge ──► Gazebo (gz_x500_depth)
                                                    │
                                             /camera topic
```

---

## 7. Real Drone Deployment (future)

1. Replace `udp://:14540` in `teleop_drone.py` / `flight_test.py` with the real drone's MAVLink address (e.g. `serial:///dev/ttyUSB0:57600`)
2. Remove `await drone.action.arm()` auto-arm — arm manually for safety
3. Test in an open area with a spotter
