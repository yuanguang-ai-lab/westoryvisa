"""Persistence and event/notification repository for appointment monitoring."""

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from .appointment_monitor import AppointmentMonitorError, MonitorConfig, MonitorEvent


def now_iso(now=None):
    if isinstance(now, str):
        return now
    return (now or datetime.now(timezone.utc)).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _row(row):
    return dict(row) if row is not None else None


def _config_json(config):
    return {
        "monitorId": config.monitor_id,
        "applicantId": config.applicant_id,
        "location": config.location,
        "intervalSeconds": config.interval_seconds,
        "earliestDate": config.earliest_date.isoformat() if config.earliest_date else None,
        "latestDate": config.latest_date.isoformat() if config.latest_date else None,
        "baselineDate": config.baseline_date.isoformat() if config.baseline_date else None,
        "enabled": config.enabled,
        "bookingMode": config.booking_mode,
    }


def create_task(connect, *, organization_id, user_id, case_id, config, provider_kind="mock", now=None):
    """Persist one monitor task; credentials and browser sessions are excluded."""
    if not isinstance(config, MonitorConfig):
        raise AppointmentMonitorError("config 必须是 MonitorConfig")
    stamped = now_iso(now)
    task_id = f"appointment-monitor-task-{secrets.token_hex(12)}"
    next_check = (
        datetime.fromisoformat(stamped) + timedelta(seconds=config.interval_seconds)
    ).isoformat()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO appointment_monitor_tasks (
              id, organization_id, user_id, case_id, applicant_id, location,
              state, interval_seconds, earliest_date, latest_date, baseline_date,
              booking_mode, provider_kind, worker_id, config_json,
              next_check_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'watching', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id, organization_id, user_id, case_id, config.applicant_id,
                config.location, config.interval_seconds,
                config.earliest_date.isoformat() if config.earliest_date else None,
                config.latest_date.isoformat() if config.latest_date else None,
                config.baseline_date.isoformat() if config.baseline_date else None,
                config.booking_mode, str(provider_kind or "mock"),
                config.monitor_id, _json(_config_json(config)), next_check,
                stamped, stamped,
            ),
        )
        return get_task(connection, task_id, organization_id=organization_id)


def get_task(connection_or_connect, task_id, *, organization_id=None):
    """Read one task from a connection or a connection factory."""
    if not hasattr(connection_or_connect, "execute"):
        with connection_or_connect() as connection:
            return get_task(connection, task_id, organization_id=organization_id)
    where = "id = ?"
    parameters = [task_id]
    if organization_id is not None:
        where += " AND organization_id = ?"
        parameters.append(organization_id)
    return _row(connection_or_connect.execute(
        f"SELECT * FROM appointment_monitor_tasks WHERE {where}", parameters
    ).fetchone())


def list_tasks(connect, *, organization_id, states=None):
    where = ["organization_id = ?"]
    parameters = [organization_id]
    if states:
        values = list(states)
        where.append("state IN (" + ",".join("?" for _ in values) + ")")
        parameters.extend(values)
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM appointment_monitor_tasks WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at",
            parameters,
        ).fetchall()
        return [_row(row) for row in rows]


def update_runtime(connect, *, task_id, organization_id, state, checked_at=None,
                   next_check_at=None, last_error=None, now=None):
    stamped = now_iso(now)
    assignments = ["state = ?", "updated_at = ?"]
    parameters = [state, stamped]
    if checked_at is not None:
        assignments.append("last_checked_at = ?")
        parameters.append(checked_at)
    if next_check_at is not None:
        assignments.append("next_check_at = ?")
        parameters.append(next_check_at)
    if last_error is not None:
        assignments.append("last_error = ?")
        parameters.append(last_error)
    parameters.extend([task_id, organization_id])
    with connect() as connection:
        connection.execute(
            "UPDATE appointment_monitor_tasks SET " + ", ".join(assignments)
            + " WHERE id = ? AND organization_id = ?",
            parameters,
        )
        return get_task(connection, task_id, organization_id=organization_id)


def record_snapshot(connect, *, task_id, organization_id, slots, observed_at=None):
    observed_at = observed_at or now_iso()
    payload = [slot.as_dict() for slot in slots]
    snapshot_hash = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
    with connect() as connection:
        row = connection.execute(
            """
            INSERT INTO appointment_monitor_snapshots (
              task_id, organization_id, snapshot_hash, slots_json, observed_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(task_id, snapshot_hash) DO NOTHING
            RETURNING id
            """,
            (task_id, organization_id, snapshot_hash, _json(payload), observed_at),
        ).fetchone()
        return {"created": bool(row), "snapshotHash": snapshot_hash}


def record_event(connect, *, task_id, organization_id, event):
    if not isinstance(event, MonitorEvent):
        raise AppointmentMonitorError("event 必须是 MonitorEvent")
    with connect() as connection:
        row = connection.execute(
            """
            INSERT INTO appointment_monitor_events (
              task_id, organization_id, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            RETURNING id
            """,
            (task_id, organization_id, event.event_type, _json(event.as_dict()), event.created_at),
        ).fetchone()
        if not row:
            raise AppointmentMonitorError("预约事件写入失败")
        event_id = row["id"]
        connection.execute(
            """
            INSERT INTO appointment_monitor_notifications (
              event_id, organization_id, channel, status, created_at
            ) VALUES (?, ?, 'internal', 'pending', ?)
            ON CONFLICT(event_id, channel) DO NOTHING
            """,
            (event_id, organization_id, now_iso()),
        )
        return {"id": event_id, "event": event.as_dict(), "notificationQueued": True}


def list_events(connect, *, task_id, organization_id, limit=50, unacknowledged_only=False):
    where = ["task_id = ?", "organization_id = ?"]
    parameters = [task_id, organization_id]
    if unacknowledged_only:
        where.append("acknowledged_at IS NULL")
    parameters.append(max(1, min(int(limit), 200)))
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM appointment_monitor_events WHERE "
            + " AND ".join(where)
            + " ORDER BY id DESC LIMIT ?",
            parameters,
        ).fetchall()
        result = []
        for row in rows:
            item = _row(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result


def acknowledge_event(connect, *, event_id, organization_id, actor, now=None):
    stamped = now_iso(now)
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE appointment_monitor_events
            SET acknowledged_at = ?, acknowledged_by = ?
            WHERE id = ? AND organization_id = ? AND acknowledged_at IS NULL
            """,
            (stamped, str(actor or "operator"), int(event_id), organization_id),
        )
        return cursor.rowcount == 1


def claim_pending_notifications(connect, *, organization_id, limit=20):
    """Return pending internal notifications for a later delivery adapter."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM appointment_monitor_notifications
            WHERE organization_id = ? AND status = 'pending'
            ORDER BY id LIMIT ?
            """,
            (organization_id, max(1, min(int(limit), 100))),
        ).fetchall()
        return [_row(row) for row in rows]


def mark_notification(connect, *, notification_id, organization_id, status,
                      error="", sent_at=None, now=None):
    if status not in {"pending", "sent", "failed"}:
        raise AppointmentMonitorError("通知状态无效")
    stamped = now_iso(now)
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE appointment_monitor_notifications
            SET status = ?, attempts = attempts + 1, last_error = ?,
                sent_at = ?, created_at = created_at
            WHERE id = ? AND organization_id = ?
            """,
            (status, str(error or ""), sent_at, int(notification_id), organization_id),
        )
        return cursor.rowcount == 1
