import socket
import time
import threading
import cv2

TELLO_IP = "192.168.10.1"
TELLO_PORT = 8889
LOCAL_PORT = 9000

# --------------------------------------------------
# UDP command socket
# --------------------------------------------------

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("", LOCAL_PORT))
sock.settimeout(5)


def send_command(command):
    print(f">>> {command}")

    sock.sendto(
        command.encode("utf-8"),
        (TELLO_IP, TELLO_PORT)
    )

    try:
        response, _ = sock.recvfrom(1024)
        response = response.decode("utf-8")

        print(f"<<< {response}")

        return response

    except socket.timeout:
        print("<<< No response")
        return None


# --------------------------------------------------
# Video
# --------------------------------------------------

def video_stream():

    cap = cv2.VideoCapture(
        "udp://@0.0.0.0:11111"
    )

    if not cap.isOpened():
        print("Cannot open video stream")
        return

    while True:

        ret, frame = cap.read()

        if not ret:
            continue

        cv2.imshow("RoboMaster TT Camera", frame)

        # Press Q to close video window
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


# --------------------------------------------------
# Main
# --------------------------------------------------

try:

    # Enter SDK mode
    response = send_command("command")

    if response != "ok":
        raise Exception("Cannot enter SDK mode")

    time.sleep(1)

    # Start camera
    send_command("streamon")

    time.sleep(2)

    # Start video thread
    video_thread = threading.Thread(
        target=video_stream,
        daemon=True
    )

    video_thread.start()

    # Take off
    response = send_command("takeoff")

    # if response != "ok":
    #     raise Exception("Takeoff failed")

    print("Drone flying")
    time.sleep(2)
    send_command("up 10")
    time.sleep(20)

    # ==============================================
    # SQUARE
    # ==============================================


    # ==============================================
    # Land
    # ==============================================

    send_command("land")

    print("Landing...")

    time.sleep(3)

    # Stop video
    send_command("streamoff")

finally:

    sock.close()