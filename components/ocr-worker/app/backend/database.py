"""Database compatibility boundary for SQLite development and PostgreSQL.

PostgreSQL is the production/concurrent database.  SQLite remains available
only for the existing unit tests and for importing old local installations.
The adapter intentionally keeps the application's current qmark SQL surface
small while the business module is split into repositories over time.
"""

import os
import re
import sqlite3
import threading


class DatabaseConfigurationError(RuntimeError):
    pass


_POOL = None
_POOL_URL = ""
_POOL_LOCK = threading.Lock()


def configured_database_url():
    return (
        os.environ.get("DOCFLOW_DATABASE_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )


def postgres_enabled():
    return configured_database_url().lower().startswith(
        ("postgres://", "postgresql://")
    )


def postgres_required():
    return os.environ.get("DOCFLOW_REQUIRE_POSTGRES", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


class ClosingSQLiteConnection(sqlite3.Connection):
    dialect = "sqlite"

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _replace_qmark_placeholders(statement):
    """Translate qmark parameters without touching quoted SQL literals."""
    output = []
    quote = ""
    index = 0
    while index < len(statement):
        character = statement[index]
        if quote:
            output.append(character)
            if character == quote:
                if index + 1 < len(statement) and statement[index + 1] == quote:
                    output.append(statement[index + 1])
                    index += 1
                else:
                    quote = ""
        elif character in {"'", '"'}:
            quote = character
            output.append(character)
        elif character == "?":
            output.append("%s")
        else:
            output.append(character)
        index += 1
    return "".join(output)


def postgres_sql(statement):
    sql = str(statement)
    sql = re.sub(
        r"^\s*BEGIN\s+IMMEDIATE\s*$",
        "BEGIN",
        sql,
        flags=re.IGNORECASE,
    )
    ignored_insert = bool(re.match(
        r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b",
        sql,
        flags=re.IGNORECASE,
    ))
    if ignored_insert:
        sql = re.sub(
            r"^(\s*)INSERT\s+OR\s+IGNORE\s+INTO\b",
            r"\1INSERT INTO",
            sql,
            count=1,
            flags=re.IGNORECASE,
        )
    sql = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGSERIAL PRIMARY KEY",
        sql,
        flags=re.IGNORECASE,
    )
    # SQLite exposes MAX(a, b) as a scalar; PostgreSQL names it GREATEST.
    sql = re.sub(
        r"\bMAX\s*\(\s*converted_wjx\s*,",
        "GREATEST(converted_wjx,",
        sql,
        flags=re.IGNORECASE,
    )
    sql = _replace_qmark_placeholders(sql)
    if ignored_insert and "ON CONFLICT" not in sql.upper():
        suffix = ";" if sql.rstrip().endswith(";") else ""
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING" + suffix
    return sql


class PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class NoopCursor:
    """Cursor-shaped result for transaction markers handled by psycopg."""

    rowcount = -1

    @staticmethod
    def fetchone():
        return None

    @staticmethod
    def fetchall():
        return []


class PostgresConnection:
    dialect = "postgresql"

    def __init__(self, connection, integrity_error, pool_context):
        self._connection = connection
        self._integrity_error = integrity_error
        self._pool_context = pool_context

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._pool_context.__exit__(exc_type, exc_value, traceback)

    def execute(self, statement, parameters=None):
        sql = postgres_sql(statement)
        # With autocommit disabled psycopg opens a transaction before the first
        # real statement. Sending an explicit BEGIN would therefore emit one
        # PostgreSQL warning per queue poll while adding no isolation guarantee.
        if sql.strip().rstrip(";").upper() == "BEGIN":
            return NoopCursor()
        try:
            cursor = self._connection.execute(
                sql,
                tuple(parameters or ()),
            )
        except self._integrity_error as error:
            # Preserve the legacy application's narrow conflict handling while
            # keeping psycopg an optional import for SQLite-only test runs.
            raise sqlite3.IntegrityError(str(error)) from error
        return PostgresCursor(cursor)

    def executescript(self, script):
        # Application schemas contain no procedural blocks; semicolon splitting
        # keeps each DDL statement independently diagnosable.
        for statement in str(script).split(";"):
            if statement.strip():
                self.execute(statement)

    def table_columns(self, table):
        rows = self.execute(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = ?
            ORDER BY ordinal_position
            """,
            (table,),
        ).fetchall()
        return {row["name"] for row in rows}


def connect(sqlite_path):
    url = configured_database_url()
    if url:
        if not postgres_enabled():
            raise DatabaseConfigurationError(
                "DOCFLOW_DATABASE_URL must be a PostgreSQL URL"
            )
        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as error:
            raise DatabaseConfigurationError(
                "PostgreSQL requires psycopg[binary,pool]"
            ) from error
        global _POOL, _POOL_URL
        with _POOL_LOCK:
            if _POOL is None or _POOL_URL != url:
                if _POOL is not None:
                    _POOL.close()
                try:
                    maximum = max(2, int(os.environ.get(
                        "DOCFLOW_DB_POOL_MAX", "8"
                    )))
                except ValueError:
                    maximum = 8
                _POOL = ConnectionPool(
                    conninfo=url,
                    min_size=1,
                    max_size=maximum,
                    kwargs={"row_factory": dict_row},
                    open=True,
                )
                _POOL_URL = url
        pool_context = _POOL.connection(timeout=15)
        connection = pool_context.__enter__()
        return PostgresConnection(
            connection,
            psycopg.IntegrityError,
            pool_context,
        )
    if postgres_required():
        raise DatabaseConfigurationError(
            "DOCFLOW_REQUIRE_POSTGRES is enabled but DOCFLOW_DATABASE_URL is empty"
        )
    connection = sqlite3.connect(
        sqlite_path,
        factory=ClosingSQLiteConnection,
        timeout=15,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA secure_delete = ON")
    # WAL improves legacy local usability, but it is not a substitute for the
    # PostgreSQL deployment path used by concurrent installations.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection
