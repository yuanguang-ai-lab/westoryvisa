#!/usr/bin/env python3
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SQLITE_PATH = ROOT / "data" / "docflow_ds160.sqlite3"
SCHEMA_PATH = ROOT / "postgres_schema.sql"
OUTPUT_PATH = ROOT / "outputs" / "postgres_import.sql"

TABLES = [
    "organizations",
    "users",
    "clients",
    "ds160_cases",
    "intake_links",
    "documents",
    "ds160_fields",
    "field_evidence",
    "ds160_answers",
    "review_issues",
    "audit_logs",
]

JSON_COLUMNS = {
    "ds160_cases": {"payload_json"},
    "documents": {"ocr_json"},
    "ds160_answers": {"details_json"},
    "audit_logs": {"payload_json"},
}

BOOLEAN_COLUMNS = {
    "ds160_fields": {"requires_user_confirmation", "confirmed", "edited_by_user"},
    "review_issues": {"requires_user_resolution", "resolved"},
    "ds160_answers": {"sensitive", "confirmed_by_user"},
}


def sql_literal(value, *, is_json=False, is_bool=False):
    if value is None:
        return "NULL"
    if is_bool:
        return "TRUE" if bool(value) else "FALSE"
    if isinstance(value, (int, float)) and not is_json:
        return str(value)
    text = str(value).replace("'", "''")
    if is_json:
        return f"'{text}'::jsonb"
    return f"'{text}'"


def export():
    if not SQLITE_PATH.exists():
        raise SystemExit(f"SQLite database not found: {SQLITE_PATH}")

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row

    lines = [
        "-- Generated from data/docflow_ds160.sqlite3",
        "-- Run this whole file in pgAdmin Query Tool.",
        "",
        SCHEMA_PATH.read_text(encoding="utf-8"),
        "",
        "BEGIN;",
    ]

    for table in TABLES:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            continue
        columns = rows[0].keys()
        quoted_columns = ", ".join(columns)
        for row in rows:
            values = []
            for column in columns:
                values.append(
                    sql_literal(
                        row[column],
                        is_json=column in JSON_COLUMNS.get(table, set()),
                        is_bool=column in BOOLEAN_COLUMNS.get(table, set()),
                    )
                )
            lines.append(
                f"INSERT INTO {table} ({quoted_columns}) VALUES ({', '.join(values)}) "
                f"ON CONFLICT DO NOTHING;"
            )

    lines.append("COMMIT;")
    lines.append("")
    conn.close()
    OUTPUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Exported PostgreSQL import SQL: {OUTPUT_PATH}")


if __name__ == "__main__":
    export()
