"""Container readiness checks for PostgreSQL-backed background processes."""

import socket
import sys
from datetime import datetime, timezone

from .application import connect
from .automation_config import configured_workers


MAX_HEARTBEAT_AGE_SECONDS = 90


def _age_seconds(value):
    stamped = datetime.fromisoformat(str(value or ""))
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamped).total_seconds()


def scheduler_ready():
    expected = {worker.worker_id for worker in configured_workers()}
    if not expected:
        return False
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, status, last_heartbeat_at FROM automation_workers"
        ).fetchall()
    workers = {row["id"]: row for row in rows}
    return all(
        worker_id in workers
        and workers[worker_id]["status"] == "online"
        and 0 <= _age_seconds(workers[worker_id]["last_heartbeat_at"])
        <= MAX_HEARTBEAT_AGE_SECONDS
        for worker_id in expected
    )


def ocr_worker_ready():
    worker_id = f"ocr-{socket.gethostname()}"
    with connect() as connection:
        row = connection.execute(
            "SELECT status, last_heartbeat_at FROM ocr_workers WHERE id = ?",
            (worker_id,),
        ).fetchone()
    return bool(
        row
        and row["status"] == "online"
        and 0 <= _age_seconds(row["last_heartbeat_at"])
        <= MAX_HEARTBEAT_AGE_SECONDS
    )


def main():
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        ready = {
            "scheduler": scheduler_ready,
            "ocr-worker": ocr_worker_ready,
        }[role]()
    except Exception:
        ready = False
    raise SystemExit(0 if ready else 1)


if __name__ == "__main__":
    main()
