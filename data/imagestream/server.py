"""Live camera view: the newest JPEG of every drone from Kafka `image-frames`, as MJPEG.

    http://localhost:8090/                     grid of every drone (embedded in Grafana)
    http://localhost:8090/?drones=uav1,uav3    only these drones
    http://localhost:8090/stream/uav1          MJPEG stream of one drone (<img src=...>)
    http://localhost:8090/latest/uav1.jpg      newest frame as one JPEG
    http://localhost:8090/drones.json          drones seen, frame age, size

The images are the same bytes AI Detection reads (no re-encoding), so the view
runs at the camera rate the bridge sends (2 fps, A2). Reads from the newest
offset with a private consumer group: it never takes messages from anyone else.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("imagestream")
DRONE_RE = re.compile(r"^uav[0-9]+$")


class Frames:
    """Newest frame per drone + a condition so stream clients wake up on a new one."""

    def __init__(self):
        self.cond = threading.Condition()
        self.latest = {}          # drone_id -> dict(jpeg, ts_us, frame_id, width, height, at)
        self.seq = 0

    def put(self, drone_id, jpeg, headers):
        with self.cond:
            self.latest[drone_id] = {
                "jpeg": jpeg, "ts_us": int(headers.get("ts_us", 0) or 0),
                "frame_id": headers.get("frame_id"), "width": headers.get("width"),
                "height": headers.get("height"), "at": time.time()}
            self.seq += 1
            self.cond.notify_all()

    def get(self, drone_id):
        with self.cond:
            return self.latest.get(drone_id)

    def wait_newer(self, drone_id, after_at, timeout):
        """Block until drone_id has a frame newer than after_at (or timeout). Returns the frame or None."""
        end = time.time() + timeout
        with self.cond:
            while True:
                f = self.latest.get(drone_id)
                if f and f["at"] > after_at:
                    return f
                left = end - time.time()
                if left <= 0:
                    return f
                self.cond.wait(left)

    def summary(self):
        now = time.time()
        with self.cond:
            items = [(d, f) for d, f in self.latest.items()]
        rows = [{"drone_id": d, "frame_id": f["frame_id"], "ts_us": f["ts_us"],
                 "width": f["width"], "height": f["height"], "bytes": len(f["jpeg"]),
                 "age_s": round(now - f["at"], 1)} for d, f in items]
        return sorted(rows, key=lambda r: (len(r["drone_id"]), r["drone_id"]))


def consume(frames: Frames, bootstrap: str, stop: threading.Event):
    from confluent_kafka import Consumer
    c = Consumer({"bootstrap.servers": bootstrap, "group.id": f"imagestream-{uuid.uuid4()}",
                  "auto.offset.reset": "latest", "enable.auto.commit": False,
                  "max.partition.fetch.bytes": 2097152, "fetch.max.bytes": 52428800})
    c.subscribe(["image-frames"])
    log.info("reading image-frames from %s", bootstrap)
    try:
        while not stop.is_set():
            m = c.poll(0.5)
            if m is None or m.error():
                continue
            h = {k: v.decode("utf-8", "replace") for k, v in (m.headers() or [])}
            did = h.get("drone_id") or (m.key() or b"").decode("utf-8", "replace")
            if DRONE_RE.match(did) and m.value():
                frames.put(did, m.value(), h)
    finally:
        c.close()


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Aquarius cameras</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;background:#111217;color:#ccccdc;font:13px system-ui,sans-serif}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(%(minw)spx,1fr));gap:8px;padding:8px}
 .cam{background:#181b1f;border:1px solid #2c3235;border-radius:6px;overflow:hidden}
 .bar{display:flex;justify-content:space-between;padding:4px 8px;font-weight:600}
 .bar span:last-child{font-weight:400;color:#8e8e8e}
 .stale .bar span:last-child{color:#f2495c}
 img{display:block;width:100%%;aspect-ratio:4/3;object-fit:contain;background:#000}
 .empty{padding:24px;color:#8e8e8e}
</style></head><body>
<div class="grid" id="grid"><div class="empty">waiting for images on Kafka topic image-frames ...</div></div>
<script>
const only = %(only)s;
const grid = document.getElementById('grid');
const cards = {};
async function tick(){
  let rows = [];
  try { rows = await (await fetch('drones.json', {cache:'no-store'})).json(); } catch(e) { return; }
  if (only.length) rows = rows.filter(r => only.includes(r.drone_id));
  if (rows.length && grid.querySelector('.empty')) grid.innerHTML = '';
  for (const r of rows) {
    let c = cards[r.drone_id];
    if (!c) {
      c = document.createElement('div'); c.className = 'cam';
      c.innerHTML = '<div class="bar"><span></span><span></span></div><img alt="">';
      c.querySelector('.bar span').textContent = r.drone_id;
      c.querySelector('img').src = 'stream/' + r.drone_id;
      grid.appendChild(c); cards[r.drone_id] = c;
    }
    c.classList.toggle('stale', r.age_s > 5);
    c.querySelector('.bar span:last-child').textContent =
      (r.age_s > 5 ? 'no new image ' + Math.round(r.age_s) + ' s' : 'frame ' + r.frame_id) +
      ' \\u00b7 ' + r.width + '\\u00d7' + r.height + ' \\u00b7 ' + Math.round(r.bytes / 1024) + ' KB';
  }
}
tick(); setInterval(tick, 1000);
</script></body></html>"""


def make_handler(frames: Frames):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet: one line per stream, not per request
            pass

        def _send(self, code, ctype, body: bytes, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            path = u.path.rstrip("/") or "/"
            if path == "/":
                qs = parse_qs(u.query)
                only = [d for d in ",".join(qs.get("drones", [])).split(",") if DRONE_RE.match(d)]
                minw = max(160, min(1200, int((qs.get("minw", ["320"])[0] or 320))))
                page = PAGE % {"only": json.dumps(only), "minw": minw}
                return self._send(200, "text/html; charset=utf-8", page.encode())
            if path == "/drones.json":
                return self._send(200, "application/json", json.dumps(frames.summary()).encode())
            if path == "/health":
                return self._send(200, "text/plain", b"ok")
            m = re.fullmatch(r"/latest/(uav[0-9]+)\.jpg", path)
            if m:
                f = frames.get(m.group(1))
                if not f:
                    return self._send(404, "text/plain", b"no image yet for this drone")
                return self._send(200, "image/jpeg", f["jpeg"])
            m = re.fullmatch(r"/stream/(uav[0-9]+)", path)
            if m:
                return self._stream(m.group(1))
            self._send(404, "text/plain", f"not found: {html.escape(path)}".encode())

        def _stream(self, drone_id):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            last_at = 0.0
            try:
                while True:
                    # a new frame, or the old one again every 5 s so proxies keep the connection
                    f = frames.wait_newer(drone_id, last_at, timeout=5.0)
                    if not f:
                        continue
                    last_at = max(last_at, f["at"])
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(f["jpeg"])).encode() + b"\r\n\r\n" + f["jpeg"] + b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

    return Handler


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    frames, stop = Frames(), threading.Event()
    threading.Thread(target=consume, args=(frames, os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"), stop),
                     daemon=True).start()
    port = int(os.environ.get("PORT", "8090"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(frames))
    srv.daemon_threads = True
    log.info("serving on :%d", port)
    try:
        srv.serve_forever()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
