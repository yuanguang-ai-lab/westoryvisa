#!/usr/bin/env python3
"""Copy a complete legacy DocFlow SQLite database into empty PostgreSQL.

The command never truncates PostgreSQL and refuses a target that already owns
users or cases.  It prints row-count and content-hash verification without
including customer values in the report.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TABLES = (
    "organizations",
    "users",
    "email_verifications",
    "clients",
    "ds160_cases",
    "documents",
    "ds160_fields",
    "field_evidence",
    "ds160_answers",
    "review_issues",
    "audit_logs",
    "app_session",
    "auth_sessions",
    "intake_links",
    "site_settings",
    "product_visitor_sessions",
    "product_analytics_events",
    "billing_products",
    "billing_orders",
    "payment_transactions",
    "billing_refunds",
    "billing_subscriptions",
    "billing_webhook_events",
)

TARGET_EMPTY_TABLES = tuple(
    table for table in TABLES if table != "billing_products"
) + (
    "automation_workers",
    "automation_jobs",
    "automation_job_events",
    "ocr_workers",
    "ocr_jobs",
    "ocr_job_events",
)


def sqlite_tables(connection):
    return {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def sqlite_columns(connection, table):
    return [
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def canonical_hash(rows, columns):
    digest = hashlib.sha256()
    encoded_rows = []
    for row in rows:
        payload = {column: row[column] for column in columns}
        encoded_rows.append(json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ))
    for encoded in sorted(encoded_rows):
        digest.update(encoded.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def source_inventory(connection):
    available = sqlite_tables(connection)
    inventory = {}
    for table in TABLES:
        if table not in available:
            continue
        rows = connection.execute(f"SELECT * FROM {table}").fetchall()
        columns = sqlite_columns(connection, table)
        inventory[table] = {
            "count": len(rows),
            "sha256": canonical_hash(rows, columns),
        }
    return inventory


def validate_source_for_personal_organizations(connection):
    available = sqlite_tables(connection)
    if "users" not in available:
        return
    missing_user_org = connection.execute(
        "SELECT 1 FROM users WHERE organization_id IS NULL LIMIT 1"
    ).fetchone()
    if missing_user_org:
        raise RuntimeError(
            "SQLite source contains a user without an organization. Assign a "
            "verified private organization before migration."
        )
    duplicates = connection.execute(
        """
        SELECT organization_id, COUNT(*) AS count
        FROM users
        WHERE organization_id IS NOT NULL
        GROUP BY organization_id HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    if duplicates:
        raise RuntimeError(
            "SQLite source contains an organization with multiple users. "
            "The current personal-organization model requires one user per "
            "organization; split that organization before migration."
        )
    if "ds160_cases" not in available:
        return
    orphan_case = connection.execute(
        """
        SELECT 1
        FROM ds160_cases
        LEFT JOIN users ON users.organization_id = ds160_cases.organization_id
        WHERE ds160_cases.organization_id IS NULL OR users.id IS NULL
        LIMIT 1
        """
    ).fetchone()
    if orphan_case:
        raise RuntimeError(
            "SQLite source contains a case without exactly one verified "
            "organization owner. Public registration will not claim legacy "
            "data by organization name; assign the owner explicitly before "
            "migration."
        )


def migrate(sqlite_path, database_url):
    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    available = sqlite_tables(source)
    validate_source_for_personal_organizations(source)

    os.environ["DOCFLOW_DATABASE_URL"] = database_url
    os.environ["DOCFLOW_REQUIRE_POSTGRES"] = "true"
    from backend import application

    # Repository source may be mounted read-only into a migration container;
    # runtime scratch and uploads stay beside the explicitly selected source.
    application.DATA_DIR = Path(sqlite_path).parent
    application.UPLOAD_DIR = application.DATA_DIR / "uploads"
    application.init_db()
    migrated = {}
    verification = {}
    # All business rows and verification share one transaction. A failed
    # foreign key, uniqueness check or content hash leaves no partial import.
    with application.connect() as target:
        nonempty = []
        for table in TARGET_EMPTY_TABLES:
            count = target.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()["count"]
            if int(count or 0):
                nonempty.append(table)
        if nonempty:
            raise RuntimeError(
                "PostgreSQL target business tables are not empty; migration "
                "refused without modifying them: " + ", ".join(nonempty)
            )
        for table in TABLES:
            if table not in available:
                continue
            source_columns = sqlite_columns(source, table)
            target_columns = target.table_columns(table)
            columns = [name for name in source_columns if name in target_columns]
            if not columns:
                continue
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            placeholders = ", ".join("?" for _ in columns)
            column_sql = ", ".join(columns)
            for row in rows:
                target.execute(
                    f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) "
                    "ON CONFLICT DO NOTHING",
                    tuple(row[column] for column in columns),
                )
            migrated[table] = len(rows)

        for table in (
            "audit_logs", "product_analytics_events", "automation_job_events",
            "ocr_job_events",
        ):
            target.execute(
                "SELECT setval(pg_get_serial_sequence(?, 'id'), "
                "COALESCE((SELECT MAX(id) FROM " + table + "), 1), "
                "(SELECT MAX(id) IS NOT NULL FROM " + table + "))",
                (table,),
            )
        for table, source_count in migrated.items():
            columns = [
                name for name in sqlite_columns(source, table)
                if name in target.table_columns(table)
            ]
            rows = target.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            source_rows = source.execute(
                f"SELECT {', '.join(columns)} FROM {table}"
            ).fetchall()
            source_hash = canonical_hash(source_rows, columns)
            target_hash = canonical_hash(rows, columns)
            # Seeded billing products can pre-exist by design; every other
            # table must match exactly.
            exact = (
                table == "billing_products"
                or (len(rows) == source_count and target_hash == source_hash)
            )
            verification[table] = {
                "sourceRows": source_count,
                "targetRows": len(rows),
                "contentMatch": bool(exact),
            }
            if not exact:
                raise RuntimeError(f"Verification failed for table: {table}")
    source.close()
    return verification


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sqlite",
        default=str(ROOT / "data" / "docflow_ds160.sqlite3"),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DOCFLOW_DATABASE_URL", ""),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sqlite_path = Path(args.sqlite).expanduser().resolve()
    if not sqlite_path.is_file():
        raise SystemExit(f"SQLite database not found: {sqlite_path}")
    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    validate_source_for_personal_organizations(source)
    inventory = source_inventory(source)
    source.close()
    if args.dry_run:
        print(json.dumps({"source": str(sqlite_path), "tables": inventory}, indent=2))
        return
    if not args.database_url:
        raise SystemExit("--database-url or DOCFLOW_DATABASE_URL is required")
    verification = migrate(sqlite_path, args.database_url)
    print(json.dumps({
        "source": str(sqlite_path),
        "verified": True,
        "tables": verification,
    }, indent=2))


if __name__ == "__main__":
    main()
