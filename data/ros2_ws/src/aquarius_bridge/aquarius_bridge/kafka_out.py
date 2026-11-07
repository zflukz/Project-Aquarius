"""Kafka producer settings for the bridge (A3 producer table).

telemetry and image-frames: acks=1, no idempotence, linger.ms=5
(frequent data, a lost sample is acceptable, speed matters most).
"""
from __future__ import annotations

from confluent_kafka import KafkaException, Producer

TOPIC_TELEMETRY = "telemetry"
TOPIC_IMAGES = "image-frames"


class KafkaOut:
    def __init__(self, bootstrap: str, logger=None):
        self._log = logger
        self._errors = 0
        self._p = Producer({
            "bootstrap.servers": bootstrap,
            "client.id": "aquarius-bridge",
            "acks": "1",
            "enable.idempotence": False,
            "linger.ms": 5,
            "compression.type": "none",          # JPEG is already compressed
            "message.max.bytes": 2097152,        # = max.request.size in A3
            "queue.buffering.max.messages": 20000,
            "queue.buffering.max.kbytes": 65536,
            # Drop rather than pile up stale data if the link to Kafka is down
            "message.timeout.ms": 5000,
        })

    def _on_delivery(self, err, _msg):
        if err is not None:
            self._errors += 1
            if self._log is not None and self._errors % 100 == 1:
                self._log.warn(f"kafka delivery failed: {err}")

    def _send(self, topic: str, key: str, value: bytes, headers=None) -> bool:
        try:
            self._p.produce(topic, key=key.encode(), value=value, headers=headers,
                            on_delivery=self._on_delivery)
            return True
        except BufferError:
            # Local queue full (Kafka unreachable): drop this sample, keep running
            self._p.poll(0)
            return False
        except KafkaException as e:
            if self._log is not None:
                self._log.error(f"kafka produce error: {e}")
            return False

    def send_telemetry(self, drone_id: str, value: bytes) -> bool:
        return self._send(TOPIC_TELEMETRY, drone_id, value)

    def send_image(self, drone_id: str, jpeg: bytes, headers) -> bool:
        ok = self._send(TOPIC_IMAGES, drone_id, jpeg, headers)
        self._p.poll(0)
        return ok

    def poll(self) -> None:
        self._p.poll(0)

    def take_error_count(self) -> int:
        n, self._errors = self._errors, 0
        return n

    def flush(self, timeout_s: float) -> None:
        self._p.flush(timeout_s)
