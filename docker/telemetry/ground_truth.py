#!/usr/bin/env python3
"""ground_truth.py — true pose of every drone from Gazebo -> ROS 2, one topic per drone (T5).

Subscribes /world/<PX4_GZ_WORLD>/dynamic_pose/info (gz.msgs.Pose_V, gz-transport) and publishes
/uav_<i>/gz/ground_truth (geometry_msgs/PoseStamped, frame "world", ENU, sim time) for every model
named <MODEL>_<i>. ros_gz_bridge can't do this: its Pose_V -> TFMessage conversion drops the model
names, and the drones have no PosePublisher / OdometryPublisher plugin.

Env: PX4_GZ_WORLD, PX4_SIM_MODEL, PX4_NUM_DRONES, GZ_PARTITION (as px4-sim), GT_RATE (Hz, default 30).
Needs gz.transport13 / gz.msgs10 on PYTHONPATH (ROS Jazzy vendor packages, see entrypoint.sh).
"""
import os
import time

import rclpy
from geometry_msgs.msg import PoseStamped
from gz.msgs10.pose_v_pb2 import Pose_V
from gz.transport13 import Node as GzNode
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

WORLD = os.environ.get('PX4_GZ_WORLD', 'baylands')
MODEL = os.environ.get('PX4_SIM_MODEL', 'gz_x500').split('gz_', 1)[-1]
NUM = int(os.environ.get('PX4_NUM_DRONES', '1'))
RATE = float(os.environ.get('GT_RATE', '30'))


class GroundTruth(Node):
    def __init__(self):
        super().__init__('ground_truth')
        self.pubs = {f'{MODEL}_{i}': self.create_publisher(
            PoseStamped, f'/uav_{i}/gz/ground_truth', qos_profile_sensor_data) for i in range(NUM)}
        self.last = 0.0
        self.gz = GzNode()
        self.topic = f'/world/{WORLD}/dynamic_pose/info'
        if not self.gz.subscribe(Pose_V, self.topic, self.on_poses):
            raise RuntimeError(f'cannot subscribe {self.topic}')
        self.get_logger().info(f'{self.topic} -> /uav_<0..{NUM - 1}>/gz/ground_truth ({MODEL}_<i>, {RATE} Hz)')

    def on_poses(self, msg):   # gz-transport thread
        if not rclpy.ok():     # shutting down
            return
        now = time.monotonic()
        if RATE > 0 and now - self.last < 1.0 / RATE:
            return
        self.last = now
        for p in msg.pose:
            pub = self.pubs.get(p.name)
            if pub is None:
                continue
            out = PoseStamped()
            out.header.stamp.sec = msg.header.stamp.sec
            out.header.stamp.nanosec = msg.header.stamp.nsec
            out.header.frame_id = 'world'
            out.pose.position.x, out.pose.position.y, out.pose.position.z = \
                p.position.x, p.position.y, p.position.z
            o = out.pose.orientation
            o.x, o.y, o.z, o.w = p.orientation.x, p.orientation.y, p.orientation.z, p.orientation.w
            pub.publish(out)


def main():
    rclpy.init()
    node = GroundTruth()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.gz.unsubscribe(node.topic)
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
