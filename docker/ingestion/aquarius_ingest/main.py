"""Database Ingestion consumer (Phase B3).

Consumer group `db-ingestion` reads telemetry, detections, survivors and
replanning-events, and writes them to TimescaleDB in batches.

Delivery guarantee
------------------
1. Read up to BATCH_MAX messages (or wait at most BATCH_MAX_WAIT_S).
2. Insert the whole batch in ONE database transaction (ON CONFLICT DO NOTHING).
3. Only after the transaction commits, commit the Kafka offsets.
If the process dies between 2 and 3, the batch is read again after restart and
the duplicate rows are ignored by the primary keys -> no loss, no duplicates.

Phase C: in the same transaction, every mission / drone in the batch gets (or
updates) its row in missions / drones, and new survivors are merged into
persons (aq_merge_survivors, radius MERGE_RADIUS_M). A failed merge does not
lose data: the survivors stay stored and are merged the next time survivors of
that mission arrive (or by hand: SELECT aq_merge_survivors(<mission_id>)).
On a database error the process exits; Docker restarts it and it resumes from
the last committed offset.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from collections import Counter, defaultdict

import psycopg
from confluent_kafka import Consumer, KafkaError

from .rows import (DRONE_UPSERT_SQL, JSON_TOPICS, MERGE_SQL, MISSION_UPSERT_SQL, REJECT_SQL, SQL,
                   Rejected, Seen, decode, load_validators, to_row)

log = logging.getLogger("ingestion")


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def run() -> int:
    logging.basicConfig(level=env("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    batch_max = int(env("BATCH_MAX", "500"))
    wait_s = float(env("BATCH_MAX_WAIT_S", "1.0"))
    merge_radius_m = float(env("MERGE_RADIUS_M", "3.0"))
    validators = load_validators()

    consumer = Consumer({
        "bootstrap.servers": env("KAFKA_BOOTSTRAP", "localhost:9092"),
        "group.id": env("GROUP_ID", "db-ingestion"),
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
        "session.timeout.ms": 15000,
    })
    consumer.subscribe(list(JSON_TOPICS))

    stop = {"flag": False}
    def _stop(*_):  # noqa: E306
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    stats, last_log = Counter(), time.monotonic()
    log.info("ingestion started: topics=%s batch_max=%d", ",".join(JSON_TOPICS), batch_max)

    # autocommit=True so that each `with conn.transaction()` block is exactly one
    # BEGIN ... COMMIT (psycopg 3 recommendation)
    with psycopg.connect(env("PG_DSN", "postgresql://aquarius:aquarius@localhost:5432/aquarius"),
                         autocommit=True) as conn:
        while not stop["flag"]:
            msgs = consumer.consume(num_messages=batch_max, timeout=wait_s)
            if msgs:
                rows, rejects, seen = defaultdict(list), [], Seen()
                for m in msgs:
                    if m.error():
                        if m.error().code() != KafkaError._PARTITION_EOF:
                            log.warning("kafka error: %s", m.error())
                        continue
                    topic = m.topic()
                    try:
                        msg = decode(topic, m.value(), validators)
                        rows[topic].append(to_row(topic, msg))
                        seen.add(topic, msg)
                    except Rejected as e:
                        payload = (m.value() or b"")[:4000].decode("utf-8", "replace")
                        rejects.append((topic, m.partition(), m.offset(), str(e), payload))

                try:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            # parents first: survivors / replanning_events reference missions
                            if seen.missions:
                                cur.executemany(MISSION_UPSERT_SQL, seen.mission_rows())
                            if seen.drones:
                                cur.executemany(DRONE_UPSERT_SQL, seen.drone_rows())
                            for topic, batch in rows.items():
                                cur.executemany(SQL[topic], batch)
                                stats[topic] += len(batch)
                            if rejects:
                                cur.executemany(REJECT_SQL, rejects)
                                stats["rejected"] += len(rejects)
                            for mission in sorted(seen.survivor_missions):
                                try:
                                    with conn.transaction():      # savepoint: a merge error keeps the data
                                        cur.execute(MERGE_SQL, (mission, merge_radius_m))
                                        stats["merged"] += cur.fetchone()[0]
                                except psycopg.Error as e:
                                    log.warning("merge of survivors for mission %s failed (%s); "
                                                "retried when more survivors arrive", mission, e)
                except psycopg.Error:
                    log.exception("database write failed; exiting so the batch is replayed after restart")
                    consumer.close()
                    return 1
                consumer.commit(asynchronous=False)

            if time.monotonic() - last_log >= 10:
                log.info("[stats 10s] %s", " ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "idle")
                stats.clear()
                last_log = time.monotonic()

    consumer.close()
    log.info("ingestion stopped")
    return 0


if __name__ == "__main__":
    sys.exit(run())
