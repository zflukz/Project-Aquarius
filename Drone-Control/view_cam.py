import time
import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from cv_bridge import CvBridge

class DroneCameraViewer(Node):
    def __init__(self):
        super().__init__('drone_camera_viewer')
        self.bridge = CvBridge()
        self.received_first_frame = False

        # กำหนด QoS ดั้งเดิมที่ทำงานเข้ากับ Bridge ได้เสถียรที่สุด
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        self.subscription = self.create_subscription(
            Image,
            '/camera',
            self.image_callback,
            qos
        )

        self.prev_time = time.time()
        self.fps = 0.0
        self.frame_count = 0
        self.get_logger().info("กำลังรอรับสัญญาณภาพจาก /camera...")

    def image_callback(self, msg):
        if not self.received_first_frame:
            self.get_logger().info(">>> รับเฟรมภาพสำเร็จ! กำลังเปิดหน้าต่างแสดงผล <<<")
            self.received_first_frame = True
            cv2.namedWindow("PX4 Drone Camera Stream", cv2.WINDOW_NORMAL)

        try:
            frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        except Exception as e:
            self.get_logger().error(f"แปลงภาพไม่สำเร็จ: {e}")
            return

        self.frame_count += 1
        current_time = time.time()
        time_diff = current_time - self.prev_time

        if time_diff >= 1.0:
            self.fps = self.frame_count / time_diff
            self.frame_count = 0
            self.prev_time = current_time
            self.get_logger().info(f"สตรีมสดปกติ... FPS: {self.fps:.1f}")

        cv2.putText(frame, f"PX4 Stream | FPS: {self.fps:.1f}", (15, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        cv2.imshow("PX4 Drone Camera Stream", frame)
        cv2.waitKey(1)

def main(args=None):
    rclpy.init(args=args)
    node = DroneCameraViewer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        cv2.destroyAllWindows()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()