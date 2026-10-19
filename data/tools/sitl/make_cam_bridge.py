#!/usr/bin/env python3
"""Find every drone camera in the running Gazebo world and bridge it into ROS 2.

    python3 tools/sitl/make_cam_bridge.py              # writes the config and starts the bridge
    python3 tools/sitl/make_cam_bridge.py --dry-run    # only print the mapping

Gazebo names a camera topic after the model instance, e.g.
    /world/default/model/x500_mono_cam_down_2/link/camera_link/sensor/imager/image
Model suffix _2 = PX4 instance 2 = drone uav2, so it is published in ROS 2 as
    /uav2/camera/image          (sensor_msgs/Image, stamped with sim time)
and /clock is bridged too, so nodes started with use_sim_time:=true share that clock.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

MODEL_RE = re.compile(r"/model/[^/]*?_(\d+)/")


def parse_gz_topics(lines):
    """Return ({instance: gz_image_topic}, gz_clock_topic or None)."""
    cams, clock = {}, None
    for raw in lines:
        t = raw.strip()
        if not t:
            continue
        if t == "/clock" or (clock is None and re.fullmatch(r"/world/[^/]+/clock", t)):
            clock = t
            continue
        if not t.endswith("/image"):
            continue
        m = MODEL_RE.search(t)
        if m:
            cams.setdefault(int(m.group(1)), t)   # first camera of each drone
    return cams, clock


def bridge_yaml(cams, clock):
    out = []
    if clock:
        out.append(
            f'- ros_topic_name: "/clock"\n  gz_topic_name: "{clock}"\n'
            f'  ros_type_name: "rosgraph_msgs/msg/Clock"\n  gz_type_name: "gz.msgs.Clock"\n'
            f'  direction: GZ_TO_ROS\n')
    for i in sorted(cams):
        out.append(
            f'- ros_topic_name: "/uav{i}/camera/image"\n  gz_topic_name: "{cams[i]}"\n'
            f'  ros_type_name: "sensor_msgs/msg/Image"\n  gz_type_name: "gz.msgs.Image"\n'
            f'  direction: GZ_TO_ROS\n')
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.expanduser("~/aquarius-data/results/cam_bridge_sitl.yaml"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    try:
        lines = subprocess.run(["gz", "topic", "-l"], capture_output=True, text=True, timeout=20).stdout.splitlines()
    except FileNotFoundError:
        sys.exit("`gz` not found: is Gazebo Harmonic installed (ros-jazzy-ros-gz)?")
    cams, clock = parse_gz_topics(lines)
    if not cams:
        sys.exit("No camera topics found. Is the swarm running with a camera model (MODEL=x500_mono_cam_down)?\n"
                 "Check with: gz topic -l | grep image")
    print(f"clock: {clock or 'NOT FOUND'}")
    for i in sorted(cams):
        print(f"uav{i}: {cams[i]}  ->  /uav{i}/camera/image")
    text = bridge_yaml(cams, clock)
    if a.dry_run:
        print("\n" + text)
        return
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        f.write(text)
    print(f"\nwrote {a.out}\nstarting ros_gz_bridge (Ctrl+C to stop)...")
    os.execvp("ros2", ["ros2", "run", "ros_gz_bridge", "parameter_bridge",
                       "--ros-args", "-p", f"config_file:={a.out}"])


if __name__ == "__main__":
    main()
