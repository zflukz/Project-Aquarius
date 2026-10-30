"""HTTP side of the image stream, with frames put in by hand (no Kafka needed)."""
import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from server import Frames, make_handler  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0fakejpeg\xff\xd9"


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = Frames()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.frames))
        cls.srv.daemon_threads = True
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.frames.put("uav2", JPEG, {"ts_us": "1", "frame_id": "7", "width": "1280", "height": "960"})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers, r.read()

    def test_page_and_list(self):
        st, h, body = self.get("/?drones=uav2,bad;x")
        self.assertEqual(st, 200)
        self.assertIn(b'["uav2"]', body)          # only valid drone ids reach the page
        st, _, body = self.get("/drones.json")
        (row,) = json.loads(body)
        self.assertEqual((row["drone_id"], row["frame_id"], row["bytes"]), ("uav2", "7", len(JPEG)))

    def test_latest(self):
        st, h, body = self.get("/latest/uav2.jpg")
        self.assertEqual((st, h["Content-Type"], body), (200, "image/jpeg", JPEG))
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/latest/uav9.jpg")

    def test_stream_sends_new_frames(self):
        r = urllib.request.urlopen(self.base + "/stream/uav2", timeout=5)
        self.assertIn("multipart/x-mixed-replace", r.headers["Content-Type"])
        first = r.read(len(b"--frame\r\n"))
        self.assertEqual(first, b"--frame\r\n")
        threading.Timer(0.2, lambda: self.frames.put("uav2", JPEG + b"2", {"frame_id": "8"})).start()
        buf = b""
        t0 = time.time()
        while JPEG + b"2" not in buf and time.time() - t0 < 3:
            buf += r.read1(65536)
        r.close()
        self.assertIn(JPEG + b"2", buf)


if __name__ == "__main__":
    unittest.main()
