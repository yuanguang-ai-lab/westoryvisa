"""Durable, tenant-scoped automation queue and worker leases."""

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone


ACTIVE_STATES = frozenset({
    "queued",
    "leased",
    "preparing",
    "prepared",
    "start_requested",
    "running",
    "waiting_human",
    "paused",
    "review_required",
})
WORKER_OWNED_STATES = frozenset(ACTIVE_STATES - {"queued"})
TERMINAL_STATES = frozenset({
    "completed", "failed", "cancelled", "expired",
    "recovery_required",
})


class AutomationConflict(ValueError):
    pass


class AutomationNotFound(FileNotFoundError):
    pass


def now_iso(now=None):
    return (now or datetime.now(timezone.utc)).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _row(row):
    return dict(row) if row is not None else None


def _append_event(connection, job_id, organization_id, event_type, payload, stamped):
    connection.execute(
        """
        INSERT INTO automation_job_events (
          job_id, organization_id, event_type, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (job_id, organization_id, event_type, _json(payload or {}), stamped),
    )


def register_worker(
    connect,
    *,
    worker_id,
    api_url,
    view_url="",
    control_url="",
    capacity=1,
    status="online",
    now=None,
):
    stamped = now_iso(now)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO automation_workers (
              id, api_url, view_url, control_url, status, capacity,
              last_heartbeat_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              api_url = excluded.api_url,
              view_url = excluded.view_url,
              control_url = excluded.control_url,
              status = CASE
                WHEN automation_workers.status = 'quarantined'
                THEN 'quarantined' ELSE excluded.status END,
              capacity = excluded.capacity,
              last_heartbeat_at = excluded.last_heartbeat_at,
              updated_at = excluded.updated_at
            """,
            (
                worker_id, api_url.rstrip("/"), view_url.rstrip("/"),
                control_url.rstrip("/"), status, max(1, int(capacity)),
                stamped, stamped, stamped,
            ),
        )


def quarantine_worker(connect, *, worker_id, now=None):
    stamped = now_iso(now)
    with connect() as connection:
        connection.execute(
            """
            UPDATE automation_workers
            SET status = 'quarantined', updated_at = ?
            WHERE id = ?
            """,
            (stamped, worker_id),
        )


def clear_worker_quarantine(connect, *, worker_id, now=None):
    """Explicit ops action after the worker container/browser was reset."""
    stamped = now_iso(now)
    with connect() as connection:
        active = connection.execute(
            """
            SELECT 1 FROM automation_jobs
            WHERE worker_id = ? AND state IN (
              'leased', 'preparing', 'prepared', 'start_requested', 'running',
              'waiting_human', 'paused', 'review_required'
            ) LIMIT 1
            """,
            (worker_id,),
        ).fetchone()
        if active:
            raise AutomationConflict(
                "Worker 仍有活跃任务，不能解除隔离"
            )
        connection.execute(
            """
            UPDATE automation_workers
            SET status = 'online', updated_at = ?
            WHERE id = ? AND status = 'quarantined'
            """,
            (stamped, worker_id),
        )


def create_job(
    connect,
    *,
    organization_id,
    user_id,
    case_id,
    workflow_type,
    payload,
    status_payload,
    idempotency_key,
    public_job_id=None,
    priority=100,
    viewer_token_hash="",
    expires_at=None,
    now=None,
):
    stamped = now_iso(now)
    job_id = f"automation-{secrets.token_hex(16)}"
    public_job_id = public_job_id or f"codex-agent-{secrets.token_hex(12)}"
    active_placeholders = ", ".join("?" for _ in ACTIVE_STATES)
    try:
        with connect() as connection:
            connection.execute(
                "BEGIN IMMEDIATE"
                if getattr(connection, "dialect", "sqlite") == "sqlite"
                else "BEGIN"
            )
            existing = connection.execute(
                f"""
                SELECT * FROM automation_jobs
                WHERE organization_id = ? AND case_id = ?
                  AND state IN ({active_placeholders})
                ORDER BY created_at DESC LIMIT 1
                """,
                (organization_id, case_id, *sorted(ACTIVE_STATES)),
            ).fetchone()
            if existing:
                return _row(existing), False
            tenant_active = connection.execute(
                f"""
                SELECT * FROM automation_jobs
                WHERE organization_id = ?
                  AND state IN ({active_placeholders})
                ORDER BY created_at DESC LIMIT 1
                """,
                (organization_id, *sorted(ACTIVE_STATES)),
            ).fetchone()
            if tenant_active:
                raise AutomationConflict(
                    "当前账号已有一个排队中或运行中的自动填写任务"
                )
            connection.execute(
                """
                INSERT INTO automation_jobs (
                  id, public_job_id, organization_id, user_id, case_id,
                  workflow_type, state, priority, payload_json, status_json,
                  idempotency_key, viewer_token_hash, expires_at,
                  queued_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, public_job_id, organization_id, user_id, case_id,
                    workflow_type, int(priority), _json(payload),
                    _json(status_payload), idempotency_key,
                    str(viewer_token_hash or ""), expires_at,
                    stamped, stamped, stamped,
                ),
            )
            _append_event(
                connection,
                job_id,
                organization_id,
                "queued",
                {"publicJobId": public_job_id},
                stamped,
            )
            created = connection.execute(
                "SELECT * FROM automation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return _row(created), True
    except sqlite3.IntegrityError:
        # A second API process may win either idempotency or active-case
        # uniqueness. Read the authoritative winner in a fresh transaction.
        with connect() as connection:
            existing = connection.execute(
                f"""
                SELECT * FROM automation_jobs
                WHERE organization_id = ?
                  AND (idempotency_key = ? OR state IN ({active_placeholders}))
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    organization_id, idempotency_key,
                    *sorted(ACTIVE_STATES),
                ),
            ).fetchone()
        if existing:
            existing = _row(existing)
            if (
                existing["case_id"] == case_id
                or existing["idempotency_key"] == idempotency_key
            ):
                return existing, False
            raise AutomationConflict(
                "当前账号已有一个排队中或运行中的自动填写任务"
            )
        raise


def get_job(connect, public_job_id, organization_id):
    with connect() as connection:
        job = connection.execute(
            """
            SELECT automation_jobs.*, automation_workers.api_url,
                   automation_workers.view_url, automation_workers.control_url
            FROM automation_jobs
            LEFT JOIN automation_workers ON automation_workers.id = automation_jobs.worker_id
            WHERE automation_jobs.public_job_id = ?
              AND automation_jobs.organization_id = ?
            """,
            (public_job_id, organization_id),
        ).fetchone()
    if not job:
        raise AutomationNotFound("自动填写任务不存在或无权访问")
    return _row(job)


def get_internal_job(connect, job_id):
    with connect() as connection:
        job = connection.execute(
            """
            SELECT automation_jobs.*, automation_workers.api_url,
                   automation_workers.view_url, automation_workers.control_url
            FROM automation_jobs
            LEFT JOIN automation_workers ON automation_workers.id = automation_jobs.worker_id
            WHERE automation_jobs.id = ?
            """,
            (job_id,),
        ).fetchone()
    return _row(job)


def get_worker_job(connect, worker_id):
    placeholders = ", ".join("?" for _ in WORKER_OWNED_STATES)
    with connect() as connection:
        job = connection.execute(
            f"""
            SELECT automation_jobs.*, automation_workers.api_url,
                   automation_workers.view_url, automation_workers.control_url
            FROM automation_jobs
            JOIN automation_workers ON automation_workers.id = automation_jobs.worker_id
            WHERE automation_jobs.worker_id = ?
              AND automation_jobs.state IN ({placeholders})
            ORDER BY automation_jobs.updated_at DESC LIMIT 1
            """,
            (worker_id, *sorted(WORKER_OWNED_STATES)),
        ).fetchone()
    return _row(job)


def get_job_for_viewer(connect, *, token_hash, worker_id, organization_id):
    placeholders = ", ".join("?" for _ in ACTIVE_STATES)
    with connect() as connection:
        job = connection.execute(
            f"""
            SELECT * FROM automation_jobs
            WHERE viewer_token_hash = ? AND worker_id = ?
              AND organization_id = ?
              AND state IN ({placeholders})
            LIMIT 1
            """,
            (
                token_hash, worker_id, organization_id,
                *sorted(ACTIVE_STATES),
            ),
        ).fetchone()
    if not job:
        raise AutomationNotFound("虚拟 Chrome 会话已失效")
    return _row(job)


def list_workers(connect):
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT automation_workers.*,
              (SELECT COUNT(*) FROM automation_jobs
               WHERE automation_jobs.worker_id = automation_workers.id
                 AND automation_jobs.state IN (
                   'leased', 'preparing', 'prepared', 'start_requested',
                   'running', 'waiting_human', 'paused', 'review_required'
                 )) AS active_jobs
            FROM automation_workers ORDER BY automation_workers.id
            """
        ).fetchall()
    return [_row(row) for row in rows]


def claim_next_job(connect, *, worker_id, lease_owner, lease_seconds=45, now=None):
    current = now or datetime.now(timezone.utc)
    stamped = now_iso(current)
    expires = now_iso(current + timedelta(seconds=max(10, int(lease_seconds))))
    with connect() as connection:
        connection.execute(
            "BEGIN IMMEDIATE"
            if getattr(connection, "dialect", "sqlite") == "sqlite"
            else "BEGIN"
        )
        worker_lock_suffix = (
            " FOR UPDATE"
            if getattr(connection, "dialect", "sqlite") == "postgresql"
            else ""
        )
        worker = connection.execute(
            "SELECT * FROM automation_workers "
            "WHERE id = ? AND status = 'online'" + worker_lock_suffix,
            (worker_id,),
        ).fetchone()
        if not worker:
            return None
        owned_placeholders = ", ".join("?" for _ in WORKER_OWNED_STATES)
        owned = connection.execute(
            f"""
            SELECT COUNT(*) AS count FROM automation_jobs
            WHERE worker_id = ? AND state IN ({owned_placeholders})
            """,
            (worker_id, *sorted(WORKER_OWNED_STATES)),
        ).fetchone()["count"]
        if int(owned or 0) >= int(worker["capacity"] or 1):
            return None
        lock_suffix = (
            " FOR UPDATE SKIP LOCKED"
            if getattr(connection, "dialect", "sqlite") == "postgresql"
            else ""
        )
        candidate = connection.execute(
            """
            SELECT * FROM automation_jobs
            WHERE state = 'queued'
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY priority ASC, queued_at ASC, id ASC
            LIMIT 1
            """ + lock_suffix,
            (stamped,),
        ).fetchone()
        if not candidate:
            return None
        cursor = connection.execute(
            """
            UPDATE automation_jobs SET
              state = 'leased', worker_id = ?, lease_owner = ?,
              lease_expires_at = ?, generation = generation + 1,
              attempt = attempt + 1, updated_at = ?
            WHERE id = ? AND state = 'queued'
            """,
            (worker_id, lease_owner, expires, stamped, candidate["id"]),
        )
        if cursor.rowcount != 1:
            return None
        leased = connection.execute(
            "SELECT * FROM automation_jobs WHERE id = ?", (candidate["id"],)
        ).fetchone()
        _append_event(
            connection,
            leased["id"],
            leased["organization_id"],
            "leased",
            {
                "workerId": worker_id,
                "generation": int(leased["generation"] or 0),
            },
            stamped,
        )
        return _row(leased)


def transition_job(
    connect,
    *,
    job_id,
    worker_id,
    generation,
    from_states,
    to_state,
    status_payload=None,
    provider_job_id=None,
    lease_owner,
    lease_seconds=45,
    now=None,
):
    if to_state not in ACTIVE_STATES | TERMINAL_STATES:
        raise ValueError("Unknown automation state")
    current = now or datetime.now(timezone.utc)
    stamped = now_iso(current)
    expires = now_iso(current + timedelta(seconds=max(10, int(lease_seconds))))
    states = tuple(sorted(set(from_states)))
    if not states:
        raise ValueError("from_states is required")
    placeholders = ", ".join("?" for _ in states)
    assignments = ["state = ?", "updated_at = ?"]
    parameters = [to_state, stamped]
    if status_payload is not None:
        assignments.append("status_json = ?")
        parameters.append(_json(status_payload))
    if provider_job_id is not None:
        assignments.append("provider_job_id = ?")
        parameters.append(str(provider_job_id))
    if to_state != "start_requested":
        assignments.append("desired_action = NULL")
    if to_state in WORKER_OWNED_STATES:
        assignments.append("lease_expires_at = ?")
        parameters.append(expires)
        assignments.append("lease_owner = ?")
        parameters.append(str(lease_owner))
    if to_state == "running":
        assignments.append("started_at = COALESCE(started_at, ?)")
        parameters.append(stamped)
    if to_state in TERMINAL_STATES:
        assignments.extend([
            "finished_at = ?",
            "lease_owner = NULL",
            "lease_expires_at = NULL",
            "payload_json = '{\"redacted\":true}'",
            "viewer_token_hash = NULL",
        ])
        parameters.append(stamped)
    parameters.extend([
        job_id, worker_id, int(generation), str(lease_owner), *states,
    ])
    with connect() as connection:
        cursor = connection.execute(
            f"""
            UPDATE automation_jobs SET {', '.join(assignments)}
            WHERE id = ? AND worker_id = ? AND generation = ?
              AND lease_owner = ?
              AND state IN ({placeholders})
            """,
            tuple(parameters),
        )
        if cursor.rowcount != 1:
            raise AutomationConflict(
                "自动填写任务租约已变化，旧 worker 不能继续写入"
            )
        updated = connection.execute(
            "SELECT * FROM automation_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        _append_event(
            connection,
            updated["id"],
            updated["organization_id"],
            to_state,
            {"workerId": worker_id, "generation": int(generation)},
            stamped,
        )
        return _row(updated)


def update_job_status(
    connect,
    *,
    job_id,
    worker_id,
    generation,
    status_payload,
    state=None,
    clear_action=False,
    expected_states=None,
    lease_owner,
    now=None,
):
    """Persist a provider checkpoint without manufacturing queue events."""
    if state is not None and state not in ACTIVE_STATES | TERMINAL_STATES:
        raise ValueError("Unknown automation state")
    stamped = now_iso(now)
    assignments = ["status_json = ?", "updated_at = ?"]
    parameters = [_json(status_payload), stamped]
    if state is not None:
        assignments.append("state = ?")
        parameters.append(state)
    if clear_action:
        assignments.append("desired_action = NULL")
    if state in TERMINAL_STATES:
        assignments.extend([
            "finished_at = ?",
            "lease_owner = NULL",
            "lease_expires_at = NULL",
            "payload_json = '{\"redacted\":true}'",
            "viewer_token_hash = NULL",
        ])
        parameters.append(stamped)
    states = tuple(sorted(set(expected_states or ())))
    state_guard = ""
    if states:
        state_guard = " AND state IN (" + ", ".join("?" for _ in states) + ")"
    parameters.extend([
        job_id, worker_id, int(generation), str(lease_owner), *states,
    ])
    with connect() as connection:
        cursor = connection.execute(
            f"""
            UPDATE automation_jobs SET {', '.join(assignments)}
            WHERE id = ? AND worker_id = ? AND generation = ?
              AND lease_owner = ?
              {state_guard}
            """,
            tuple(parameters),
        )
        if cursor.rowcount != 1:
            raise AutomationConflict(
                "自动填写任务租约已变化，旧 worker 不能继续写入"
            )
        return _row(connection.execute(
            "SELECT * FROM automation_jobs WHERE id = ?", (job_id,)
        ).fetchone())


def request_action(
    connect,
    *,
    public_job_id,
    organization_id,
    action,
    now=None,
):
    """Record a tenant-authorized command for the lease-owning scheduler."""
    if action not in {"start", "pause", "cancel"}:
        raise ValueError("自动填写任务操作无效")
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
            SELECT * FROM automation_jobs
            WHERE public_job_id = ? AND organization_id = ?
            """ + lock_suffix,
            (public_job_id, organization_id),
        ).fetchone()
        if not job:
            raise AutomationNotFound("自动填写任务不存在或无权访问")
        current = str(job["state"] or "")
        if current in TERMINAL_STATES:
            return _row(job)
        if action == "start":
            if current not in {"prepared", "waiting_human", "paused"}:
                raise AutomationConflict("当前任务尚未准备好，不能启动")
            next_state = "start_requested"
        elif action == "pause":
            if current != "running":
                raise AutomationConflict("只有正在填写的任务可以暂停")
            next_state = current
        else:
            if current == "queued":
                cursor = connection.execute(
                    """
                    UPDATE automation_jobs SET state = 'cancelled',
                      finished_at = ?, updated_at = ?,
                      payload_json = '{"redacted":true}',
                      viewer_token_hash = NULL
                    WHERE id = ? AND state = 'queued'
                    """,
                    (stamped, stamped, job["id"]),
                )
                if cursor.rowcount != 1:
                    raise AutomationConflict("自动填写任务状态已变化，请重试")
                _append_event(
                    connection, job["id"], organization_id,
                    "cancelled", {"beforeLease": True}, stamped,
                )
                return _row(connection.execute(
                    "SELECT * FROM automation_jobs WHERE id = ?", (job["id"],)
                ).fetchone())
            next_state = current
        cursor = connection.execute(
            """
            UPDATE automation_jobs SET state = ?, desired_action = ?,
              command_requested_at = ?, updated_at = ?
            WHERE id = ? AND state = ?
            """,
            (next_state, action, stamped, stamped, job["id"], current),
        )
        if cursor.rowcount != 1:
            raise AutomationConflict("自动填写任务状态已变化，请重试")
        _append_event(
            connection, job["id"], organization_id,
            f"{action}_requested", {}, stamped,
        )
        return _row(connection.execute(
            "SELECT * FROM automation_jobs WHERE id = ?", (job["id"],)
        ).fetchone())


def renew_lease(connect, *, job_id, worker_id, generation, lease_owner, lease_seconds=45, now=None):
    current = now or datetime.now(timezone.utc)
    stamped = now_iso(current)
    expires = now_iso(current + timedelta(seconds=max(10, int(lease_seconds))))
    placeholders = ", ".join("?" for _ in WORKER_OWNED_STATES)
    with connect() as connection:
        cursor = connection.execute(
            f"""
            UPDATE automation_jobs
            SET lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND worker_id = ? AND generation = ?
              AND lease_owner = ? AND state IN ({placeholders})
            """,
            (
                expires, stamped, job_id, worker_id, int(generation),
                lease_owner, *sorted(WORKER_OWNED_STATES),
            ),
        )
        return cursor.rowcount == 1


def cancel_job(connect, *, public_job_id, organization_id, now=None):
    """Compatibility wrapper: never free an opened browser before shutdown."""
    return request_action(
        connect,
        public_job_id=public_job_id,
        organization_id=organization_id,
        action="cancel",
        now=now,
    )


def reap_expired_leases(connect, now=None):
    stamped = now_iso(now)
    results = {"requeued": 0, "recoveryRequired": 0}
    with connect() as connection:
        expired_queued = connection.execute(
            """
            SELECT * FROM automation_jobs
            WHERE state = 'queued' AND expires_at IS NOT NULL
              AND expires_at <= ?
            ORDER BY expires_at
            """,
            (stamped,),
        ).fetchall()
        for job in expired_queued:
            cursor = connection.execute(
                """
                UPDATE automation_jobs SET state = 'expired',
                  finished_at = ?, updated_at = ?,
                  payload_json = '{"redacted":true}',
                  viewer_token_hash = NULL
                WHERE id = ? AND state = 'queued' AND expires_at <= ?
                """,
                (stamped, stamped, job["id"], stamped),
            )
            if cursor.rowcount != 1:
                continue
            _append_event(
                connection,
                job["id"],
                job["organization_id"],
                "expired",
                {"beforeLease": True},
                stamped,
            )
        expired = connection.execute(
            """
            SELECT * FROM automation_jobs
            WHERE lease_expires_at IS NOT NULL AND lease_expires_at < ?
              AND state IN (
                'leased', 'preparing', 'prepared', 'start_requested',
                'running', 'waiting_human', 'paused'
                , 'review_required'
              )
            ORDER BY lease_expires_at
            """,
            (stamped,),
        ).fetchall()
        for job in expired:
            # Once preparation starts, the provider may have opened Chromium
            # even if its job id never made it back to PostgreSQL (for example
            # a response was lost). Only a lease that never entered prepare is
            # provably safe to requeue.
            safe_to_requeue = (
                job["state"] == "leased" and not job["provider_job_id"]
            )
            target = "queued" if safe_to_requeue else "recovery_required"
            cursor = connection.execute(
                """
                UPDATE automation_jobs SET state = ?,
                  worker_id = CASE WHEN ? = 'queued' THEN NULL ELSE worker_id END,
                  lease_owner = NULL, lease_expires_at = NULL,
                  finished_at = CASE WHEN ? = 'recovery_required' THEN ? ELSE finished_at END,
                  payload_json = CASE WHEN ? = 'recovery_required'
                    THEN '{"redacted":true}' ELSE payload_json END,
                  viewer_token_hash = CASE WHEN ? = 'recovery_required'
                    THEN NULL ELSE viewer_token_hash END,
                  updated_at = ?
                WHERE id = ? AND generation = ? AND state = ?
                  AND lease_expires_at = ? AND lease_expires_at < ?
                """,
                (
                    target, target, target, stamped, target, target, stamped,
                    job["id"], int(job["generation"] or 0), job["state"],
                    job["lease_expires_at"], stamped,
                ),
            )
            if cursor.rowcount != 1:
                continue
            if not safe_to_requeue and job["worker_id"]:
                connection.execute(
                    """
                    UPDATE automation_workers
                    SET status = 'quarantined', updated_at = ?
                    WHERE id = ?
                    """,
                    (stamped, job["worker_id"]),
                )
            _append_event(
                connection,
                job["id"],
                job["organization_id"],
                "lease_expired",
                {"previousState": job["state"], "nextState": target},
                stamped,
            )
            if safe_to_requeue:
                results["requeued"] += 1
            else:
                results["recoveryRequired"] += 1
    return results
