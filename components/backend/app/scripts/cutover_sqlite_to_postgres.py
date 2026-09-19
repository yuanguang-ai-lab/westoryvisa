#!/usr/bin/env python3
"""One-command, stop-the-world SQLite to PostgreSQL database cutover.

This command does not start PostgreSQL or deploy containers.  It requires an
already reachable, empty PostgreSQL target and an explicit acknowledgement
that all SQLite writers have stopped.  It takes a consistent SQLite backup,
then delegates to the transactional row-count/content-hash migrator.
"""

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts.migrate_sqlite_to_postgres import migrate


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def consistent_snapshot(source_path, backup_directory):
    source_path = Path(source_path).expanduser().resolve()
    backup_directory = Path(backup_directory).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")
    backup_directory.mkdir(parents=True, exist_ok=True)
    stamped = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = backup_directory / (
        f"docflow-before-postgres-{stamped}-{secrets.token_hex(4)}.sqlite3"
    )
    source = sqlite3.connect(
        f"file:{source_path}?mode=ro", uri=True, timeout=30
    )
    target = sqlite3.connect(snapshot)
    try:
        source.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite snapshot integrity check failed: {integrity}")
    finally:
        target.close()
        source.close()
    os.chmod(snapshot, 0o600)
    return snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", required=True)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DOCFLOW_DATABASE_URL", ""),
    )
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--confirm-writers-stopped", action="store_true")
    args = parser.parse_args()
    if not args.confirm_writers_stopped:
        raise SystemExit(
            "Refusing cutover: stop all SQLite-writing app containers, then "
            "pass --confirm-writers-stopped"
        )
    if not args.database_url:
        raise SystemExit("--database-url or DOCFLOW_DATABASE_URL is required")
    source_path = Path(args.sqlite).expanduser().resolve()
    snapshot = consistent_snapshot(source_path, args.backup_dir)
    snapshot_hash = file_sha256(snapshot)
    verification = migrate(snapshot, args.database_url)
    print(json.dumps({
        "ok": True,
        "source": str(source_path),
        "snapshot": str(snapshot),
        "snapshotSha256": snapshot_hash,
        "postgresVerified": True,
        "tables": verification,
        "nextStep": (
            "Start the PostgreSQL-configured application only after preserving "
            "the separate uploads/data volume backup."
        ),
    }, indent=2))


if __name__ == "__main__":
    main()
