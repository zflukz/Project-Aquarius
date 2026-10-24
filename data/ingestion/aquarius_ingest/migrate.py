"""Apply database migrations in order (Phase C1).

    python -m aquarius_ingest.migrate            # apply every pending file
    python -m aquarius_ingest.migrate --status   # list applied / pending, change nothing

Files live in db/migrations/ and are named NNNN_short_name.sql. Each pending
file runs in ONE transaction together with its row in schema_migrations, so a
file is either fully applied or not at all. Applied files are never run again:
to change the schema, add a new file instead of editing an old one (an edited
file is reported as "changed" and left alone).

docker compose runs this as the one-shot service `db-migrate` before `ingestion`
starts. An advisory lock makes two runs at the same time wait for each other.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
LOCK_ID = 7270_0001          # any constant; shared by every migrate run

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)"""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: str
    checksum: str


def default_dir() -> str:
    env = os.environ.get("MIGRATIONS_DIR")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "..", "db", "migrations"), "/app/migrations"):
        if os.path.isdir(cand):
            return os.path.abspath(cand)
    raise FileNotFoundError("db/migrations not found; set MIGRATIONS_DIR")


def discover(directory: str) -> List[Migration]:
    """Every NNNN_name.sql file, sorted by version. Other files are an error, not ignored."""
    out, seen = [], {}
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".sql"):
            continue
        m = FILE_RE.match(fn)
        if not m:
            raise ValueError(f"bad migration file name {fn!r}: use NNNN_lower_case_name.sql")
        version, name = m.groups()
        if version in seen:
            raise ValueError(f"two migrations share version {version}: {seen[version]} and {fn}")
        seen[version] = fn
        path = os.path.join(directory, fn)
        with open(path, "rb") as f:
            checksum = hashlib.sha256(f.read()).hexdigest()
        out.append(Migration(version, name, path, checksum))
    return sorted(out, key=lambda x: x.version)


def plan(found: List[Migration], applied: Dict[str, str]) -> Tuple[List[Migration], List[Migration]]:
    """(pending, changed): pending = not applied yet; changed = applied but the file differs now."""
    pending = [m for m in found if m.version not in applied]
    changed = [m for m in found if m.version in applied and applied[m.version] != m.checksum]
    return pending, changed


def connect(dsn: str, tries: int = 30):
    import psycopg
    for i in range(tries):
        try:
            return psycopg.connect(dsn, autocommit=True)
        except psycopg.OperationalError as e:
            if i == tries - 1:
                raise
            print(f"database not ready ({e.__class__.__name__}), retrying...", file=sys.stderr)
            time.sleep(2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN", "postgresql://aquarius:aquarius@localhost:5433/aquarius"))
    ap.add_argument("--dir", default=None, help="migrations directory (default: db/migrations)")
    ap.add_argument("--status", action="store_true", help="only print what is applied / pending")
    a = ap.parse_args(argv)

    found = discover(a.dir or default_dir())
    conn = connect(a.dsn)
    with conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        conn.execute(CREATE_TABLE)
        # from here on, show RAISE NOTICE / WARNING from the migration files (e.g. compression on/off)
        conn.add_notice_handler(lambda d: print(f"  {d.severity.lower()}: {d.message_primary}"))
        applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())
        pending, changed = plan(found, applied)

        for m in changed:
            print(f"WARNING {m.version}_{m.name}.sql changed after it was applied; "
                  f"the change is NOT applied (put it in a new file)")
        if a.status:
            for m in found:
                state = "pending" if m in pending else ("changed" if m in changed else "applied")
                print(f"{m.version}  {state:8}  {m.name}")
            return 0

        for m in pending:
            with open(m.path, encoding="utf-8") as f:
                sql = f.read()
            t0 = time.monotonic()
            try:
                with conn.transaction():
                    conn.execute(sql)          # no parameters -> several statements allowed
                    conn.execute("INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                                 (m.version, m.name, m.checksum))
            except Exception as e:  # noqa: BLE001 - report which file failed, then stop
                print(f"FAILED  {m.version}_{m.name}.sql: {e}", file=sys.stderr)
                return 1
            print(f"applied {m.version}_{m.name}.sql ({time.monotonic() - t0:.1f} s)")
        if not pending:
            print(f"database is up to date ({len(found)} migrations)")
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return 0


if __name__ == "__main__":
    sys.exit(main())
