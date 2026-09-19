"""Public, tenant-scoped boundary for the durable automation scheduler."""

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import automation_queue
from .automation_config import configured_workers


def _decode(value, fallback=None):
    try:
        decoded = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        decoded = fallback
    return decoded if isinstance(decoded, dict) else (fallback or {})


def _token_hash(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def enqueue_ds160_job(
    connect,
    *,
    case_id,
    user,
    plan,
    auto_next,
    ttl_minutes=60,
):
    pages = list(plan.get("pages") or [])
    total_fields = int(plan.get("totalFields") or 0)
    if not pages or not total_fields:
        raise ValueError("当前客户档案没有可交给 Gemini V2 的已确认字段")
    viewer_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    plan_digest = hashlib.sha256(
        json.dumps(
            plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "version": 1,
        "plan": plan,
        "actor": str(user.get("id") or user.get("identity") or "docflow-user"),
        "autoNext": bool(auto_next),
        "viewerToken": viewer_token,
        "totalFields": total_fields,
        "totalPages": len(pages),
    }
    queued_status = {
        "state": "queued",
        "message": "任务已进入中央队列，正在分配独立浏览器",
        "completedFields": 0,
        "totalFields": total_fields,
    }
    job, created = automation_queue.create_job(
        connect,
        organization_id=user["organizationId"],
        user_id=user["id"],
        case_id=case_id,
        workflow_type="ds160",
        payload=payload,
        status_payload=queued_status,
        # Active-case and active-tenant indexes provide the concurrency fence.
        # A fresh suffix lets the same unchanged plan start again after its
        # previous job reached a terminal state; reusing the plan digest alone
        # would return the redacted cancelled job forever.
        idempotency_key=(
            f"ds160:{case_id}:{plan_digest}:{secrets.token_hex(8)}"
        ),
        viewer_token_hash=_token_hash(viewer_token),
        expires_at=expires_at.isoformat(),
    )
    if not created:
        payload = _decode(job.get("payload_json"), {})
        expires_at = _parse_time(job.get("expires_at")) or expires_at
    return {
        "jobId": job["public_job_id"],
        "caseId": case_id,
        "state": str(job.get("state") or "queued"),
        "executionMode": "gemini-v2-linux",
        "viewerUrl": _viewer_url(job, payload, "control"),
        "totalFields": int(payload.get("totalFields") or total_fields),
        "totalPages": int(payload.get("totalPages") or len(pages)),
        "expiresAt": expires_at.isoformat(),
        "browserOpened": bool(job.get("provider_job_id")),
        "message": (
            "任务已进入中央队列；分配完成后会在本页显示专属 Linux Chrome。"
            if str(job.get("state") or "") in {"queued", "leased", "preparing"}
            else "已恢复当前案件的独立 Linux Chrome 任务。"
        ),
    }


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _viewer_url(job, payload, mode):
    worker_id = str(job.get("worker_id") or "")
    token = str(payload.get("viewerToken") or "")
    if not worker_id or not token or not job.get("provider_job_id"):
        return ""
    selected_mode = "control" if mode == "control" else "view"
    prefix = f"computer-use/{worker_id}/{selected_mode}/{token}"
    view_only = "0" if selected_mode == "control" else "1"
    return (
        f"/{prefix}/vnc.html?autoconnect=1&reconnect=1"
        f"&reconnect_delay=1000&resize=scale&quality=3&compression=7"
        f"&view_only={view_only}&shared=1&show_dot=1"
        f"&path={quote(prefix + '/websockify', safe='/')}"
    )


def public_job_status(connect, *, case_id, public_job_id, user):
    job = automation_queue.get_job(
        connect, public_job_id, user["organizationId"]
    )
    if str(job.get("case_id") or "") != str(case_id):
        raise automation_queue.AutomationNotFound(
            "自动填写任务不存在或无权访问"
        )
    status = _decode(job.get("status_json"), {})
    payload = _decode(job.get("payload_json"), {})
    internal_state = str(job.get("state") or "queued")
    public_state = str(status.get("state") or internal_state)
    message = str(status.get("message") or "")
    if internal_state in {"queued", "leased", "preparing"}:
        public_state = "queued"
        message = {
            "queued": "正在排队，等待可用的独立浏览器",
            "leased": "已分配独立浏览器，正在建立安全会话",
            "preparing": "正在启动专属 Linux Chrome",
        }[internal_state]
    elif internal_state == "prepared":
        public_state = "waiting_for_entry"
        message = (
            "专属 Linux Chrome 已就绪，请完成验证码"
            "和初始步骤后再启动 Gemini"
        )
    elif internal_state == "start_requested":
        public_state = "starting"
        message = "中央调度器正在启动 Gemini"
    elif internal_state == "cancelled":
        public_state = "revoked"
        message = "自动填写任务已停止"
    elif internal_state == "recovery_required":
        public_state = "failed"
        message = "Worker 租约中断，为避免串台已禁止自动重试"
    desired_action = str(job.get("desired_action") or "")
    if desired_action == "pause":
        public_state = "pausing"
        message = "暂停指令已记入中央队列，正在废止旧动作"
    elif desired_action == "cancel":
        public_state = "stopping"
        message = "停止指令已记入中央队列，正在关闭专属会话"
    expires_at = str(job.get("expires_at") or "")
    return {
        "jobId": public_job_id,
        "workflowType": str(job.get("workflow_type") or "ds160"),
        "state": public_state,
        "queueState": internal_state,
        "message": message,
        "completedFields": int(status.get("completedFields") or 0),
        "totalFields": int(
            status.get("totalFields") or payload.get("totalFields") or 0
        ),
        "pageLabel": str(status.get("pageLabel") or ""),
        "failedActionIds": status.get("failedActionIds") or [],
        "missingFields": status.get("missingFields") or [],
        "statusCode": str(status.get("statusCode") or ""),
        "currentRoute": status.get("currentRoute"),
        "observedRoutes": status.get("observedRoutes") or [],
        "updatedAt": str(job.get("updated_at") or ""),
        "expiresAt": expires_at,
        "closed": internal_state in automation_queue.TERMINAL_STATES,
        "executionMode": "gemini-v2-linux",
        "viewerUrl": _viewer_url(job, payload, "control"),
        "workerId": str(job.get("worker_id") or ""),
        "resumeReady": bool(
            internal_state == "paused"
            and status.get("executionActive") is not True
        ),
    }


def request_job_action(
    connect, *, case_id, public_job_id, user, action
):
    job = automation_queue.get_job(
        connect, public_job_id, user["organizationId"]
    )
    if str(job.get("case_id") or "") != str(case_id):
        raise automation_queue.AutomationNotFound(
            "自动填写任务不存在或无权访问"
        )
    automation_queue.request_action(
        connect,
        public_job_id=public_job_id,
        organization_id=user["organizationId"],
        action=action,
    )
    return public_job_status(
        connect, case_id=case_id, public_job_id=public_job_id, user=user
    )


def authorize_viewer(
    connect, *, viewer_token, worker_id, viewer_mode, user
):
    token = str(viewer_token or "")
    if viewer_mode not in {"view", "control"} or not (40 <= len(token) <= 128):
        raise PermissionError("虚拟 Chrome 观看链接无效")
    try:
        job = automation_queue.get_job_for_viewer(
            connect,
            token_hash=_token_hash(token),
            worker_id=str(worker_id or ""),
            organization_id=user["organizationId"],
        )
    except automation_queue.AutomationNotFound as error:
        raise PermissionError(str(error)) from error
    payload = _decode(job.get("payload_json"), {})
    if not hmac.compare_digest(str(payload.get("viewerToken") or ""), token):
        raise PermissionError("虚拟 Chrome 会话已失效")
    expires_at = _parse_time(job.get("expires_at"))
    if expires_at and expires_at <= datetime.now(timezone.utc):
        raise PermissionError("虚拟 Chrome 会话已过期")
    return {"ok": True, "workerId": job["worker_id"]}


def runtime_readiness(connect):
    configured = configured_workers()
    configured_ids = {item.worker_id for item in configured}
    now = datetime.now(timezone.utc)
    registered = []
    for worker in automation_queue.list_workers(connect):
        heartbeat = _parse_time(worker.get("last_heartbeat_at"))
        if (
            worker.get("id") in configured_ids
            and worker.get("status") == "online"
            and heartbeat
            and now - heartbeat < timedelta(seconds=30)
        ):
            registered.append(worker)
    capacity = sum(int(item.get("capacity") or 1) for item in registered)
    active = sum(int(item.get("active_jobs") or 0) for item in registered)
    ready = bool(configured and registered)
    return {
        "ready": ready,
        "connected": ready,
        "service": "docflow-central-automation",
        "version": "lease-v1",
        "workerCount": len(registered),
        "configuredWorkers": len(configured),
        "capacity": capacity,
        "activeJobs": active,
        "message": (
            f"中央调度已就绪：{len(registered)} 个隔离 Worker，"
            f"{active}/{capacity} 个会话正在使用"
            if ready else
            "中央调度已配置，但尚无健康的浏览器 Worker"
        ),
    }
