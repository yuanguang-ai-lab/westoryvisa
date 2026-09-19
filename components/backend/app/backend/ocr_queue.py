"""Durable, tenant-scoped OCR queue with lease fencing.

OCR containers are deliberately stateless between jobs.  A container owns at
most one job, renews a short database lease while an external parser runs, and
may only commit results while its worker id, lease owner and generation still
match.  This prevents a delayed process from writing after a retry was assigned
to another container.
"""

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone


ACTIVE_STATES = frozenset({"queued", "leased", "running", "applying"})
LEASED_STATES = frozenset({"leased", "running", "applying"})
TERMINAL_STATES = frozenset({
    "completed", "completed_with_errors", "failed", "cancelled",
})


class OcrConflict(ValueError):
    pass


def enabled():
    return os.environ.get("DOCFLOW_OCR_QUEUE_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def now_iso(now=None):
    return (now or datetime.now(timezone.utc)).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _row(row):
    return dict(row) if row is not None else None


def _append_event(connection, job, event_type, payload, stamped):
    connection.execute(
        """
        INSERT INTO ocr_job_events (
          job_id, organization_id, event_type, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (job["id"], job["organization_id"], event_type, _json(payload or {}), stamped),
    )


def register_worker(connect, *, worker_id, status="online", now=None):
    stamped = now_iso(now)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO ocr_workers (
              id, status, capacity, last_heartbeat_at, created_at, updated_at
            ) VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              status = excluded.status,
              capacity = 1,
              last_heartbeat_at = excluded.last_heartbeat_at,
              updated_at = excluded.updated_at
            """,
            (worker_id, status, stamped, stamped, stamped),
        )


def create_job(
    connect,
    *,
    organization_id,
    user_id,
    case_id,
    document_count,
    priority=100,
    max_attempts=3,
    now=None,
):
    stamped = now_iso(now)
    job_id = f"ocr-{secrets.token_hex(16)}"
    public_job_id = f"ocr-job-{secrets.token_hex(12)}"
    placeholders = ", ".join("?" for _ in ACTIVE_STATES)
    try:
        with connect() as connection:
            connection.execute(
                "BEGIN IMMEDIATE"
                if getattr(connection, "dialect", "sqlite") == "sqlite"
                else "BEGIN"
            )
            existing = connection.execute(
                f"""
                SELECT * FROM ocr_jobs
                WHERE organization_id = ? AND case_id = ?
                  AND state IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1
                """,
                (organization_id, case_id, *sorted(ACTIVE_STATES)),
            ).fetchone()
            if existing:
                return _row(existing), False
            owner = connection.execute(
                """
                SELECT 1 FROM users
                WHERE id = ? AND organization_id = ?
                """,
                (user_id, organization_id),
            ).fetchone()
            case = connection.execute(
                """
                SELECT 1 FROM ds160_cases
                WHERE id = ? AND organization_id = ?
                """,
                (case_id, organization_id),
            ).fetchone()
            if not owner or not case:
                raise PermissionError("客户档案不存在或无权创建 OCR 任务")
            summary = {
                "total": int(document_count),
                "processed": 0,
                "completed": 0,
                "failed": 0,
                "message": "等待独立 OCR Worker",
            }
            connection.execute(
                """
                INSERT INTO ocr_jobs (
                  id, public_job_id, organization_id, user_id, case_id,
                  state, priority, result_json, generation, attempt,
                  max_attempts, queued_at, available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, 0, 0, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, public_job_id, organization_id, user_id, case_id,
                    int(priority), _json(summary), max(1, int(max_attempts)),
                    stamped, stamped, stamped, stamped,
                ),
            )
            job = connection.execute(
                "SELECT * FROM ocr_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            _append_event(connection, job, "queued", summary, stamped)
            return _row(job), True
    except sqlite3.IntegrityError:
        # A second API process may have won the partial unique index.
        with connect() as connection:
            existing = connection.execute(
                f"""
                SELECT * FROM ocr_jobs
                WHERE organization_id = ? AND case_id = ?
                  AND state IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1
                """,
                (organization_id, case_id, *sorted(ACTIVE_STATES)),
            ).fetchone()
        if existing:
            return _row(existing), False
        raise


def get_case_job(connect, *, organization_id, case_id):
    with connect() as connection:
        job = connection.execute(
            """
            SELECT * FROM ocr_jobs
            WHERE organization_id = ? AND case_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (organization_id, case_id),
        ).fetchone()
    return _row(job)


def case_has_active_job(connect, *, organization_id, case_id):
    placeholders = ", ".join("?" for _ in ACTIVE_STATES)
    with connect() as connection:
        row = connection.execute(
            f"""
            SELECT 1 FROM ocr_jobs
            WHERE organization_id = ? AND case_id = ?
              AND state IN ({placeholders})
            LIMIT 1
            """,
            (organization_id, case_id, *sorted(ACTIVE_STATES)),
        ).fetchone()
    return bool(row)


def claim_next_job(
    connect,
    *,
    worker_id,
    lease_owner,
    lease_seconds=180,
    now=None,
):
    current = now or datetime.now(timezone.utc)
    stamped = now_iso(current)
    expires = now_iso(current + timedelta(seconds=max(30, int(lease_seconds))))
    with connect() as connection:
        connection.execute(
            "BEGIN IMMEDIATE"
            if getattr(connection, "dialect", "sqlite") == "sqlite"
            else "BEGIN"
        )
        worker_suffix = (
            " FOR UPDATE"
            if getattr(connection, "dialect", "sqlite") == "postgresql"
            else ""
        )
        worker = connection.execute(
            "SELECT * FROM ocr_workers WHERE id = ? AND status = 'online'"
            + worker_suffix,
            (worker_id,),
        ).fetchone()
        if not worker:
            return None
        busy_placeholders = ", ".join("?" for _ in LEASED_STATES)
        busy = connection.execute(
            f"""
            SELECT 1 FROM ocr_jobs
            WHERE worker_id = ? AND state IN ({busy_placeholders}) LIMIT 1
            """,
            (worker_id, *sorted(LEASED_STATES)),
        ).fetchone()
        if busy:
            return None
        lock_suffix = (
            " FOR UPDATE SKIP LOCKED"
            if getattr(connection, "dialect", "sqlite") == "postgresql"
            else ""
        )
        candidate = connection.execute(
            """
            SELECT * FROM ocr_jobs
            WHERE state = 'queued' AND available_at <= ?
            ORDER BY priority ASC, queued_at ASC, id ASC
            LIMIT 1
            """ + lock_suffix,
            (stamped,),
        ).fetchone()
        if not candidate:
            return None
        cursor = connection.execute(
            """
            UPDATE ocr_jobs SET
              state = 'leased', worker_id = ?, lease_owner = ?,
              lease_expires_at = ?, generation = generation + 1,
              attempt = attempt + 1, error_message = NULL, updated_at = ?
            WHERE id = ? AND state = 'queued'
            """,
            (worker_id, lease_owner, expires, stamped, candidate["id"]),
        )
        if cursor.rowcount != 1:
            return None
        job = connection.execute(
            "SELECT * FROM ocr_jobs WHERE id = ?", (candidate["id"],)
        ).fetchone()
        _append_event(connection, job, "leased", {
            "workerId": worker_id,
            "generation": int(job["generation"] or 0),
            "attempt": int(job["attempt"] or 0),
        }, stamped)
        return _row(job)


def start_running(
    connect,
    *,
    job_id,
    worker_id,
    generation,
    lease_owner,
    lease_seconds=180,
    now=None,
):
    current = now or datetime.now(timezone.utc)
    stamped = now_iso(current)
    expires = now_iso(current + timedelta(seconds=max(30, int(lease_seconds))))
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE ocr_jobs SET state = 'running', started_at = COALESCE(started_at, ?),
              lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND worker_id = ? AND generation = ?
              AND lease_owner = ? AND lease_expires_at > ? AND state = 'leased'
            """,
            (
                stamped, expires, stamped, job_id, worker_id,
                int(generation), lease_owner, stamped,
            ),
        )
        if cursor.rowcount != 1:
            raise OcrConflict("OCR 任务租约已变化，旧 Worker 不能开始处理")
        job = connection.execute(
            "SELECT * FROM ocr_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        connection.execute(
            """
            UPDATE documents
            SET scan_status = 'running', scan_message = '独立 OCR Worker 正在处理',
                updated_at = ?
            WHERE case_id = ? AND stored_path IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM ds160_cases
                WHERE ds160_cases.id = documents.case_id
                  AND ds160_cases.organization_id = ?
              )
            """,
            (stamped, job["case_id"], job["organization_id"]),
        )
        _append_event(connection, job, "running", {
            "workerId": worker_id,
            "generation": int(generation),
        }, stamped)
        return _row(job)


def renew_lease(
    connect,
    *,
    job_id,
    worker_id,
    generation,
    lease_owner,
    lease_seconds=180,
    now=None,
):
    current = now or datetime.now(timezone.utc)
    stamped = now_iso(current)
    expires = now_iso(current + timedelta(seconds=max(30, int(lease_seconds))))
    placeholders = ", ".join("?" for _ in LEASED_STATES)
    with connect() as connection:
        cursor = connection.execute(
            f"""
            UPDATE ocr_jobs SET lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND worker_id = ? AND generation = ?
              AND lease_owner = ? AND lease_expires_at > ?
              AND state IN ({placeholders})
            """,
            (
                expires, stamped, job_id, worker_id, int(generation),
                lease_owner, stamped, *sorted(LEASED_STATES),
            ),
        )
        return cursor.rowcount == 1


def update_progress(
    connect,
    *,
    job_id,
    worker_id,
    generation,
    lease_owner,
    summary,
    now=None,
):
    stamped = now_iso(now)
    placeholders = ", ".join("?" for _ in LEASED_STATES)
    with connect() as connection:
        cursor = connection.execute(
            f"""
            UPDATE ocr_jobs SET result_json = ?, updated_at = ?
            WHERE id = ? AND worker_id = ? AND generation = ?
              AND lease_owner = ? AND lease_expires_at > ?
              AND state IN ({placeholders})
            """,
            (
                _json(summary), stamped, job_id, worker_id, int(generation),
                lease_owner, stamped, *sorted(LEASED_STATES),
            ),
        )
        if cursor.rowcount != 1:
            raise OcrConflict("OCR 任务租约已变化，旧 Worker 不能更新进度")


def complete_in_transaction(
    connection,
    *,
    job_id,
    worker_id,
    generation,
    lease_owner,
    document_results,
    summary,
    actor,
    now=None,
):
    """Fence and commit document rows plus queue completion atomically.

    This function is called from the same transaction that saves the merged
    case payload, so a lease loss or case-version race rolls back both sides.
    """
    stamped = now_iso(now)
    lock_suffix = (
        " FOR UPDATE"
        if getattr(connection, "dialect", "sqlite") == "postgresql"
        else ""
    )
    job = connection.execute(
        """
        SELECT * FROM ocr_jobs
        WHERE id = ? AND worker_id = ? AND generation = ?
          AND lease_owner = ? AND lease_expires_at > ?
          AND state IN ('leased', 'running', 'applying')
        """ + lock_suffix,
        (job_id, worker_id, int(generation), lease_owner, stamped),
    ).fetchone()
    if not job:
        raise OcrConflict("OCR 任务租约已变化，旧 Worker 不能提交结果")
    connection.execute(
        "UPDATE ocr_jobs SET state = 'applying', updated_at = ? WHERE id = ?",
        (stamped, job_id),
    )
    for result in document_results:
        cursor = connection.execute(
            """
            UPDATE documents SET
              scan_status = ?, scan_message = ?, ocr_text = ?, ocr_json = ?,
              parser_name = ?, parser_version = ?, processed_at = ?, updated_at = ?
            WHERE id = ? AND case_id = ?
              AND EXISTS (
                SELECT 1 FROM ds160_cases
                WHERE ds160_cases.id = documents.case_id
                  AND ds160_cases.organization_id = ?
              )
            """,
            (
                result["status"], result["message"], result.get("ocrText"),
                result.get("ocrJson"), result.get("parserName"),
                result.get("parserVersion"), result.get("processedAt"), stamped,
                result["documentId"], job["case_id"], job["organization_id"],
            ),
        )
        if cursor.rowcount != 1:
            raise OcrConflict("OCR 结果包含不属于当前机构任务的文档")
    completed = int(summary.get("completed") or 0)
    failed = int(summary.get("failed") or 0)
    if completed and failed:
        terminal = "completed_with_errors"
    elif completed:
        terminal = "completed"
    else:
        terminal = "failed"
    cursor = connection.execute(
        """
        UPDATE ocr_jobs SET state = ?, result_json = ?, error_message = ?,
          finished_at = ?, lease_owner = NULL, lease_expires_at = NULL,
          updated_at = ?
        WHERE id = ? AND worker_id = ? AND generation = ?
          AND lease_owner = ? AND state = 'applying'
        """,
        (
            terminal, _json(summary),
            "全部文档处理失败" if terminal == "failed" else None,
            stamped, stamped, job_id, worker_id, int(generation), lease_owner,
        ),
    )
    if cursor.rowcount != 1:
        raise OcrConflict("OCR 任务租约已变化，旧 Worker 不能完成任务")
    final_job = dict(job)
    final_job["state"] = terminal
    _append_event(connection, final_job, terminal, summary, stamped)
    connection.execute(
        """
        INSERT INTO audit_logs (case_id, actor, action, payload_json, created_at)
        VALUES (?, ?, 'documents_scanned', ?, ?)
        """,
        (
            job["case_id"], actor,
            _json({
                "documents": int(summary.get("total") or 0),
                "completed": completed,
                "failed": failed,
                "ocrJobId": job["public_job_id"],
            }),
            stamped,
        ),
    )


def fail_or_requeue(
    connect,
    *,
    job_id,
    worker_id,
    generation,
    lease_owner,
    message,
    now=None,
):
    stamped = now_iso(now)
    with connect() as connection:
        connection.execute(
            "BEGIN IMMEDIATE"
            if getattr(connection, "dialect", "sqlite") == "sqlite"
            else "BEGIN"
        )
        lock_suffix = (
            " FOR UPDATE"
            if getattr(connection, "dialect", "sqlite") == "postgresql"
            else ""
        )
        job = connection.execute(
            """
            SELECT * FROM ocr_jobs
            WHERE id = ? AND worker_id = ? AND generation = ?
              AND lease_owner = ? AND state IN ('leased', 'running', 'applying')
            """ + lock_suffix,
            (job_id, worker_id, int(generation), lease_owner),
        ).fetchone()
        if not job:
            return None
        retry = int(job["attempt"] or 0) < int(job["max_attempts"] or 1)
        target = "queued" if retry else "failed"
        available_at = now_iso(
            (now or datetime.now(timezone.utc))
            + timedelta(seconds=min(300, 15 * (2 ** max(0, int(job["attempt"] or 1) - 1))))
        )
        connection.execute(
            """
            UPDATE ocr_jobs SET state = ?, worker_id = NULL,
              lease_owner = NULL, lease_expires_at = NULL,
              error_message = ?, queued_at = CASE WHEN ? = 'queued' THEN ? ELSE queued_at END,
              available_at = CASE WHEN ? = 'queued' THEN ? ELSE available_at END,
              finished_at = CASE WHEN ? = 'failed' THEN ? ELSE finished_at END,
              updated_at = ?
            WHERE id = ? AND worker_id = ? AND generation = ? AND lease_owner = ?
            """,
            (
                target, str(message)[:500], target, stamped,
                target, available_at, target, stamped,
                stamped, job_id, worker_id, int(generation), lease_owner,
            ),
        )
        connection.execute(
            """
            UPDATE documents SET scan_status = ?, scan_message = ?, updated_at = ?
            WHERE case_id = ? AND stored_path IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM ds160_cases
                WHERE ds160_cases.id = documents.case_id
                  AND ds160_cases.organization_id = ?
              )
            """,
            (
                "queued" if retry else "failed",
                "OCR Worker 异常，等待安全重试" if retry else str(message)[:500],
                stamped, job["case_id"], job["organization_id"],
            ),
        )
        final_job = dict(job)
        final_job["state"] = target
        _append_event(connection, final_job, target, {
            "retry": retry,
            "attempt": int(job["attempt"] or 0),
            "message": type(message).__name__ if isinstance(message, Exception) else "worker_error",
        }, stamped)
        return target


def reap_expired_leases(connect, *, now=None, worker_stale_seconds=90):
    current = now or datetime.now(timezone.utc)
    stamped = now_iso(current)
    worker_cutoff = now_iso(
        current - timedelta(seconds=max(30, int(worker_stale_seconds)))
    )
    recovered = 0
    failed = 0
    with connect() as connection:
        connection.execute(
            """
            UPDATE ocr_workers SET status = 'offline', updated_at = ?
            WHERE status = 'online' AND last_heartbeat_at < ?
            """,
            (stamped, worker_cutoff),
        )
        expired = connection.execute(
            """
            SELECT * FROM ocr_jobs
            WHERE lease_expires_at IS NOT NULL AND lease_expires_at <= ?
              AND state IN ('leased', 'running', 'applying')
            ORDER BY lease_expires_at
            """,
            (stamped,),
        ).fetchall()
        for job in expired:
            retry = int(job["attempt"] or 0) < int(job["max_attempts"] or 1)
            target = "queued" if retry else "failed"
            available_at = now_iso(current + timedelta(seconds=15))
            cursor = connection.execute(
                """
                UPDATE ocr_jobs SET state = ?, worker_id = NULL,
                  lease_owner = NULL, lease_expires_at = NULL,
                  error_message = 'worker_lease_expired',
                  queued_at = CASE WHEN ? = 'queued' THEN ? ELSE queued_at END,
                  available_at = CASE WHEN ? = 'queued' THEN ? ELSE available_at END,
                  finished_at = CASE WHEN ? = 'failed' THEN ? ELSE finished_at END,
                  updated_at = ?
                WHERE id = ? AND generation = ? AND state = ?
                  AND lease_expires_at = ? AND lease_expires_at <= ?
                """,
                (
                    target, target, stamped, target, available_at,
                    target, stamped, stamped,
                    job["id"], int(job["generation"] or 0), job["state"],
                    job["lease_expires_at"], stamped,
                ),
            )
            if cursor.rowcount != 1:
                continue
            connection.execute(
                """
                UPDATE documents SET scan_status = ?, scan_message = ?, updated_at = ?
                WHERE case_id = ? AND stored_path IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM ds160_cases
                    WHERE ds160_cases.id = documents.case_id
                      AND ds160_cases.organization_id = ?
                  )
                """,
                (
                    "queued" if retry else "failed",
                    "OCR Worker 租约中断，等待安全重试"
                    if retry else "OCR Worker 多次中断，任务已停止",
                    stamped, job["case_id"], job["organization_id"],
                ),
            )
            final_job = dict(job)
            final_job["state"] = target
            _append_event(connection, final_job, "lease_expired", {
                "requeued": retry,
                "generation": int(job["generation"] or 0),
            }, stamped)
            recovered += 1 if retry else 0
            failed += 0 if retry else 1
    return {"requeued": recovered, "failed": failed}
