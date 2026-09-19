"""Lease-owning scheduler for isolated Gemini browser workers."""

import json
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone

from . import automation_queue
from .agent_client import AgentClientError
from .application import connect, init_db
from .automation_config import configured_workers
from .gemini_v2_bridge import (
    cancel_provider,
    pause_provider,
    prepare_gemini_v2,
    provider_snapshot,
    provider_status,
    run_provider,
    v2_runtime_readiness,
)


LEASE_SECONDS = max(30, int(os.environ.get("DOCFLOW_AUTOMATION_LEASE_SECONDS", "60")))
PREPARE_LEASE_SECONDS = max(
    LEASE_SECONDS,
    int(os.environ.get("DOCFLOW_AUTOMATION_PREPARE_LEASE_SECONDS", "240")),
)
POLL_SECONDS = max(0.5, float(os.environ.get("DOCFLOW_AUTOMATION_POLL_SECONDS", "1")))
LOGGER = logging.getLogger("docflow.automation_scheduler")


def _decode(value):
    try:
        payload = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _expired(job):
    try:
        expires_at = datetime.fromisoformat(str(job.get("expires_at") or ""))
    except ValueError:
        return False
    return expires_at <= datetime.now(timezone.utc)


def _internal_state(mapped, current, run_active=False):
    public = str(mapped.get("state") or "")
    if public == "review_required":
        return "review_required"
    if public == "revoked":
        return "cancelled"
    if public == "failed":
        return "failed"
    if public == "paused":
        return "paused"
    if public == "blocked":
        return "waiting_human"
    if public == "waiting_for_entry":
        if run_active or current in {"running", "start_requested"}:
            return "running"
        return "prepared"
    return "running" if public == "running" else current


class WorkerLoop:
    def __init__(self, config):
        self.config = config
        self.lease_owner = (
            f"{socket.gethostname()}:{os.getpid()}:{config.worker_id}"
        )
        self.run_threads = {}
        self.lock = threading.Lock()
        self.last_health = 0.0
        self.healthy = False

    def _register(self, force=False):
        current = time.monotonic()
        if not force and current - self.last_health < 5:
            return self.healthy
        readiness = v2_runtime_readiness(base_url=self.config.api_url)
        self.healthy = bool(readiness.get("ready"))
        self.last_health = current
        automation_queue.register_worker(
            connect,
            worker_id=self.config.worker_id,
            api_url=self.config.api_url,
            capacity=1,
            status="online" if self.healthy else "offline",
        )
        return self.healthy

    def _run_active(self, job_id):
        with self.lock:
            thread = self.run_threads.get(job_id)
            if thread and not thread.is_alive():
                self.run_threads.pop(job_id, None)
                return False
            return bool(thread)

    def _persist_snapshot(self, job, mapped, target=None, clear_action=False):
        current = automation_queue.get_internal_job(connect, job["id"])
        if not current or current["state"] in automation_queue.TERMINAL_STATES:
            return current
        current_state = str(current["state"])
        target = target or _internal_state(
            mapped,
            current_state,
            self._run_active(job["id"]),
        )
        mapped = {
            **mapped,
            "totalFields": int(
                mapped.get("totalFields")
                or _decode(current.get("payload_json")).get("totalFields")
                or 0
            ),
        }
        if target != current_state:
            return automation_queue.transition_job(
                connect,
                job_id=current["id"],
                worker_id=self.config.worker_id,
                generation=current["generation"],
                from_states={current_state},
                to_state=target,
                status_payload=mapped,
                lease_owner=self.lease_owner,
                lease_seconds=LEASE_SECONDS,
            )
        return automation_queue.update_job_status(
            connect,
            job_id=current["id"],
            worker_id=self.config.worker_id,
            generation=current["generation"],
            status_payload=mapped,
            state=target,
            clear_action=clear_action,
            expected_states={current_state},
            lease_owner=self.lease_owner,
        )

    def _prepare(self, job):
        preparing = automation_queue.transition_job(
            connect,
            job_id=job["id"],
            worker_id=self.config.worker_id,
            generation=job["generation"],
            from_states={"leased"},
            to_state="preparing",
            status_payload={
                "state": "queued",
                "message": "正在启动专属 Linux Chrome",
                "completedFields": 0,
                "totalFields": int(_decode(job["payload_json"]).get("totalFields") or 0),
            },
            lease_owner=self.lease_owner,
            lease_seconds=PREPARE_LEASE_SECONDS,
        )
        payload = _decode(preparing["payload_json"])
        provider_job_id = ""
        try:
            provider_job_id, opened, provider_total = prepare_gemini_v2(
                payload.get("plan") or {},
                actor=payload.get("actor") or "docflow-user",
                auto_next=payload.get("autoNext") is True,
                base_url=self.config.api_url,
            )
            latest = automation_queue.get_internal_job(connect, preparing["id"])
            if latest and latest.get("desired_action") == "cancel":
                cancel_provider(
                    provider_job_id,
                    actor="docflow-cancel-during-prepare",
                    base_url=self.config.api_url,
                )
                automation_queue.transition_job(
                    connect,
                    job_id=latest["id"],
                    worker_id=self.config.worker_id,
                    generation=latest["generation"],
                    from_states={"preparing"},
                    to_state="cancelled",
                    status_payload={
                        "state": "revoked",
                        "message": "自动填写任务已停止",
                    },
                    provider_job_id=provider_job_id,
                    lease_owner=self.lease_owner,
                )
                return
            mapped = {
                **provider_status(opened),
                "totalFields": provider_total,
            }
            automation_queue.transition_job(
                connect,
                job_id=preparing["id"],
                worker_id=self.config.worker_id,
                generation=preparing["generation"],
                from_states={"preparing"},
                to_state="prepared",
                status_payload=mapped,
                provider_job_id=provider_job_id,
                lease_owner=self.lease_owner,
                lease_seconds=LEASE_SECONDS,
            )
        except Exception as error:
            # Manifest validation fails before any provider request. Every
            # other failure is conservatively uncertain: a timed-out create or
            # open response may have left Chromium running even though no
            # provider id reached this process.
            provider_cancelled = (
                isinstance(error, ValueError) and not provider_job_id
            )
            if provider_job_id:
                try:
                    cancel_provider(
                        provider_job_id,
                        actor="docflow-prepare-rollback",
                        base_url=self.config.api_url,
                    )
                    provider_cancelled = True
                except AgentClientError:
                    pass
            latest = automation_queue.get_internal_job(connect, preparing["id"])
            if latest and latest["state"] not in automation_queue.TERMINAL_STATES:
                terminal_state = "failed" if provider_cancelled else "recovery_required"
                if not provider_cancelled:
                    automation_queue.quarantine_worker(
                        connect, worker_id=self.config.worker_id
                    )
                automation_queue.transition_job(
                    connect,
                    job_id=latest["id"],
                    worker_id=self.config.worker_id,
                    generation=latest["generation"],
                    from_states={latest["state"]},
                    to_state=terminal_state,
                    status_payload={
                        "state": "failed",
                        "message": (
                            f"专属浏览器准备失败：{type(error).__name__}"
                            if provider_cancelled else
                            "浏览器状态无法确认，Worker 已隔离，不会分配给其他用户"
                        ),
                    },
                    lease_owner=self.lease_owner,
                )

    def _run_provider(self, job):
        try:
            result = run_provider(
                job["provider_job_id"], base_url=self.config.api_url
            )
            self._persist_snapshot(job, provider_status(result))
        except AgentClientError as error:
            current = automation_queue.get_internal_job(connect, job["id"])
            if current and current["state"] not in automation_queue.TERMINAL_STATES:
                automation_queue.quarantine_worker(
                    connect, worker_id=self.config.worker_id
                )
                self._persist_snapshot(current, {
                    "state": "failed",
                    "message": (
                        "Gemini V2 连接中断，无法确认旧动作是否结束；"
                        "Worker 已隔离，不会分配给其他用户"
                    ),
                }, target="recovery_required")
        finally:
            with self.lock:
                self.run_threads.pop(job["id"], None)

    def _start(self, job):
        if self._run_active(job["id"]):
            return
        running = automation_queue.transition_job(
            connect,
            job_id=job["id"],
            worker_id=self.config.worker_id,
            generation=job["generation"],
            from_states={"start_requested"},
            to_state="running",
            status_payload={
                **_decode(job["status_json"]),
                "state": "running",
                "message": "Gemini V2 正在专属 Linux Chrome 中填写",
            },
            lease_owner=self.lease_owner,
            lease_seconds=LEASE_SECONDS,
        )
        thread = threading.Thread(
            target=self._run_provider,
            args=(running,),
            name=f"automation-run-{self.config.worker_id}",
            daemon=True,
        )
        with self.lock:
            self.run_threads[job["id"]] = thread
        thread.start()

    def _command(self, job):
        action = str(job.get("desired_action") or "")
        if action == "start" and job["state"] == "start_requested":
            self._start(job)
            return True
        if action == "pause" and job["state"] == "running":
            paused = pause_provider(
                job["provider_job_id"],
                actor="docflow-central-scheduler",
                base_url=self.config.api_url,
            )
            self._persist_snapshot(
                job, provider_status(paused), target="paused", clear_action=True
            )
            return True
        if action == "cancel":
            if job.get("provider_job_id"):
                cancel_provider(
                    job["provider_job_id"],
                    actor="docflow-central-scheduler",
                    base_url=self.config.api_url,
                )
            self._persist_snapshot(job, {
                "state": "revoked",
                "message": "自动填写任务已停止",
            }, target="cancelled", clear_action=True)
            return True
        return False

    def _reconcile(self, job):
        if _expired(job):
            provider_closed = not job.get("provider_job_id")
            if job.get("provider_job_id"):
                try:
                    cancel_provider(
                        job["provider_job_id"],
                        actor="docflow-expiry",
                        base_url=self.config.api_url,
                    )
                    provider_closed = True
                except AgentClientError:
                    pass
            target = "expired" if provider_closed else "recovery_required"
            if not provider_closed:
                automation_queue.quarantine_worker(
                    connect, worker_id=self.config.worker_id
                )
            self._persist_snapshot(job, {
                "state": "expired" if provider_closed else "failed",
                "message": (
                    "自动填写任务已过期"
                    if provider_closed else
                    "任务过期时无法确认浏览器已关闭；Worker 已隔离"
                ),
            }, target=target)
            return
        if self._command(job):
            return
        automation_queue.renew_lease(
            connect,
            job_id=job["id"],
            worker_id=self.config.worker_id,
            generation=job["generation"],
            lease_owner=self.lease_owner,
            lease_seconds=LEASE_SECONDS,
        )
        if job.get("provider_job_id"):
            snapshot = provider_snapshot(
                job["provider_job_id"], base_url=self.config.api_url
            )
            self._persist_snapshot(job, provider_status(snapshot))

    def run(self):
        self._register(force=True)
        while True:
            try:
                healthy = self._register()
                job = automation_queue.get_worker_job(
                    connect, self.config.worker_id
                )
                if job:
                    # Never let a fresh/duplicate scheduler process adopt an
                    # opened browser that is still fenced by another lease
                    # owner. The old owner may renew; otherwise the lease
                    # reaper safely requeues an unopened job or quarantines an
                    # opened browser for explicit recovery.
                    if job.get("lease_owner") == self.lease_owner:
                        self._reconcile(job)
                elif healthy:
                    claimed = automation_queue.claim_next_job(
                        connect,
                        worker_id=self.config.worker_id,
                        lease_owner=self.lease_owner,
                        lease_seconds=PREPARE_LEASE_SECONDS,
                    )
                    if claimed:
                        self._prepare(claimed)
            except (AgentClientError, automation_queue.AutomationConflict) as error:
                LOGGER.warning(
                    "scheduler worker=%s transient=%s",
                    self.config.worker_id,
                    type(error).__name__,
                )
            except Exception as error:
                # The scheduler stays alive; durable leases and redacted health
                # endpoints expose a stuck slot without logging tenant payloads.
                LOGGER.error(
                    "scheduler worker=%s unexpected=%s",
                    self.config.worker_id,
                    type(error).__name__,
                )
                time.sleep(2)
            time.sleep(POLL_SECONDS)


def main():
    init_db()
    workers = configured_workers()
    if not workers:
        raise SystemExit("DOCFLOW_AGENT_WORKERS is required")
    threads = []
    for worker in workers:
        thread = threading.Thread(
            target=WorkerLoop(worker).run,
            name=f"automation-worker-{worker.worker_id}",
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    try:
        while True:
            automation_queue.reap_expired_leases(connect)
            time.sleep(10)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
