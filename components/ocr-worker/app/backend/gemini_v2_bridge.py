"""Narrow production bridge from DocFlow plans to the isolated V2 Agent."""

import hashlib
import json
import re

from .agent_client import (
    AgentClientError,
    agent_health,
    cancel_computer_use_job,
    create_computer_use_job,
    get_computer_use_job,
    open_computer_use_job,
    pause_computer_use_job,
    review_computer_use_job,
    run_computer_use_job,
)


def _worker_target(base_url):
    return {"base_url": base_url} if base_url is not None else {}


def v2_runtime_readiness(base_url=None):
    """Return a redacted readiness result for production/UI health checks."""
    try:
        payload = agent_health(timeout=2.0, **_worker_target(base_url))
    except AgentClientError as error:
        return {
            "ready": False,
            "connected": False,
            "message": f"Gemini V2 Agent 暂时不可达：{error}",
        }
    native_ready = bool(
        payload.get("selectInputReady")
        or payload.get("nativeInputReady")
    )
    ready = bool(
        payload.get("ok")
        and payload.get("modelConfigured")
        and payload.get("browserConfigured")
        and payload.get("checkpointStoreReady")
        and native_ready
        and payload.get("runtime") == "computer-use-v2"
    )
    return {
        "ready": ready,
        "connected": True,
        "service": str(payload.get("service") or "")[:100],
        "version": str(payload.get("version") or "")[:80],
        "nativeInputBackend": str(
            payload.get("selectInputBackend")
            or (payload.get("nativeInput") or {}).get("backend")
            or ""
        )[:80],
        "message": (
            "Gemini V2 与 Linux 虚拟显示器已就绪"
            if ready else
            str(
                payload.get("selectInputReason")
                or (payload.get("nativeInput") or {}).get("reason")
                or "Gemini、Chromium、加密检查点或 X11 输入尚未就绪"
            )[:300]
        ),
    }


def _clean_token(value, limit=240):
    return re.sub(
        r"[^a-z0-9_.-]",
        "-",
        re.sub(r"\s+", " ", str(value or "")).strip().casefold(),
    ).strip(".-")[:limit]


def _descriptor_terms(name, values):
    cleaned = []
    for raw_term in values or []:
        term = re.sub(
            r"[\x00-\x1f;|\[\]]",
            " ",
            str(raw_term or ""),
        ).strip()
        if term and term not in cleaned:
            cleaned.append(term[:80])
        if len(cleaned) >= 6:
            break
    return f"{name}=" + "|".join(cleaned) if cleaned else ""


def _action_identity(page_key, action):
    semantic = _clean_token(
        action.get("semanticId")
        or action.get("semantic_id")
        or action.get("id")
        or action.get("label")
        or action.get("kind")
        or "field",
        120,
    ) or "field"
    structure = {
        "kind": str(action.get("kind") or ""),
        "occurrence": action.get("occurrence"),
        "recordKey": (
            action.get("recordKey")
            or action.get("record_key")
            or action.get("recordId")
            or action.get("record_id")
            or ""
        ),
        "labelTerms": sorted(str(item) for item in action.get("labelTerms") or []),
        "controlHints": sorted(str(item) for item in action.get("controlHints") or []),
    }
    digest = hashlib.sha256(
        json.dumps(
            structure,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"ceac.{page_key}.{semantic}.{digest}"


def gemini_v2_manifest(plan):
    """Translate the reviewed DocFlow page plan to V1/V2 provider fields."""
    fields = []
    decisions = []
    seen = set()
    for page in plan.get("pages") or []:
        page_key = re.sub(
            r"[^a-z0-9_]",
            "",
            str(page.get("key") or "").lower(),
        )
        if not page_key:
            continue
        actions = list(page.get("actions") or [])
        if page_key == "us_contact" and not any(
            str(item.get("id") or "").casefold() == "us_contact.email"
            for item in actions
            if isinstance(item, dict)
        ):
            actions.append({
                "id": "us_contact.email",
                "label": "Email Address",
                "kind": "does_not_apply",
                "value": "true",
                "labelTerms": ["Email Address"],
                "controlHints": ["US_POC_EMAIL_ADDR"],
            })
        for action in actions:
            if not isinstance(action, dict):
                continue
            value = re.sub(
                r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
                " ",
                str(action.get("value") or ""),
            ).strip()[:500]
            if not value:
                continue
            field_id = _action_identity(page_key, action)
            if field_id in seen:
                raise ValueError(
                    "CEAC 字段结构重复，无法安全建立 Gemini V2 任务"
                )
            seen.add(field_id)
            label = re.sub(
                r"[\x00-\x1f\x7f]",
                " ",
                str(action.get("label") or action.get("id") or "CEAC field"),
            ).strip()[:180]
            kind = re.sub(
                r"[^a-z_]",
                "",
                str(action.get("kind") or "text").lower(),
            )[:40]
            structural = []
            if action.get("causesRefresh") is True:
                structural.append("refresh_after_change=true")
            if action.get("repairMissingBranch") is True:
                structural.append("repair_missing_branch=aspnet-reset-reload-v7")
            raw_occurrence = action.get("occurrence")
            if raw_occurrence is not None:
                try:
                    occurrence = max(1, min(20, int(raw_occurrence) + 1))
                except (TypeError, ValueError):
                    occurrence = None
                if occurrence is not None:
                    structural.append(f"occurrence={occurrence}")
            for descriptor in (
                _descriptor_terms("label_terms", action.get("labelTerms")),
                _descriptor_terms("control_hints", action.get("controlHints")),
            ):
                if descriptor:
                    structural.append(descriptor)
            if kind == "ensure_repeater":
                try:
                    expected_count = max(
                        1,
                        min(20, int(action.get("expectedCount") or value or 1)),
                    )
                except (TypeError, ValueError):
                    expected_count = 1
                structural.append(f"expected_count={expected_count}")
                record_labels = _descriptor_terms(
                    "record_labels",
                    action.get("recordLabelTerms"),
                )
                if record_labels:
                    structural.append(record_labels)
            descriptor = "; ".join([
                f"control={kind}",
                *structural,
                f"human-approved value={value}",
            ])
            fields.append({
                "id": field_id,
                "value": value,
                "label": f"{label} [{descriptor}]"[:500],
                "confidence": 1.0,
                "risk_level": "high",
            })
            decisions.append({
                "fieldId": field_id,
                "approved": True,
                "value": value,
                "source": "docflow-human-reviewed-plan",
                "reason": "Approved in DocFlow before CEAC handoff",
            })
    return fields, decisions


def prepare_gemini_v2(plan, actor, auto_next=True, base_url=None):
    fields, decisions = gemini_v2_manifest(plan)
    if not fields:
        raise ValueError("没有可交给 Gemini V2 的已确认字段")
    provider_job_id = ""
    try:
        created = create_computer_use_job(
            start_url=plan.get("targetUrl"),
            fields=fields,
            required_field_ids=[item["id"] for item in fields],
            auto_next=auto_next,
            manual_review_page_plan_ids=[
                f"ceac-plan-{page.get('key')}"
                for page in (plan.get("pages") or ())
                if page.get("key")
                and (
                    page.get("manualReview") is True
                    or page.get("allowNext") is False
                )
            ],
            **_worker_target(base_url),
        )
        provider_job_id = str(created.get("id") or "")
        if not provider_job_id:
            raise AgentClientError("Gemini V2 Agent 未返回任务编号")
        reviewed = review_computer_use_job(
            provider_job_id,
            actor=str(actor or "docflow-user"),
            decisions=decisions,
            **_worker_target(base_url),
        )
        if reviewed.get("state") != "ready_for_form":
            raise AgentClientError("Gemini V2 字段复核未达到可填写状态")
        opened = open_computer_use_job(
            provider_job_id, **_worker_target(base_url)
        )
        return provider_job_id, opened, len(fields)
    except (AgentClientError, ValueError):
        if provider_job_id:
            try:
                cancel_computer_use_job(
                    provider_job_id,
                    actor="docflow-prepare-rollback",
                    **_worker_target(base_url),
                )
            except AgentClientError:
                pass
        raise


def provider_snapshot(provider_job_id, base_url=None):
    return get_computer_use_job(
        provider_job_id, **_worker_target(base_url)
    )


def run_provider(provider_job_id, base_url=None):
    return run_computer_use_job(
        provider_job_id, resume=True, **_worker_target(base_url)
    )


def pause_provider(provider_job_id, actor="docflow-user", base_url=None):
    return pause_computer_use_job(
        provider_job_id, actor=actor, **_worker_target(base_url)
    )


def cancel_provider(provider_job_id, actor="docflow-user", base_url=None):
    return cancel_computer_use_job(
        provider_job_id, actor=actor, **_worker_target(base_url)
    )


def provider_status(provider):
    """Map a provider checkpoint to the existing production UI contract."""
    provider = provider if isinstance(provider, dict) else {}
    state = str(provider.get("state") or "")
    completed = len(provider.get("completed_field_ids") or [])
    total = len(provider.get("required_field_ids") or [])
    final_boundary = provider.get("final_submission_boundary_reached") is True
    wait_kind = str(provider.get("wait_kind") or "")
    execution_active = provider.get("execution_active") is True
    if state in {"completed", "review_required"} or final_boundary:
        public_state = "review_required"
        message = "已到达 Review/Sign 人工核对边界，不会签名或提交"
    elif state in {"cancelled"}:
        public_state = "revoked"
        message = "Gemini V2 任务已停止"
    elif state in {"failed"}:
        public_state = "failed"
        message = str(provider.get("human_checkpoint") or "Gemini V2 执行失败")[:400]
    elif wait_kind == "user_paused":
        public_state = "paused"
        message = str(
            provider.get("human_checkpoint")
            or "Gemini 已暂停，可人工修改当前页面"
        )[:400]
    elif state in {"blocked"} or wait_kind == "manual_hard_boundary":
        public_state = "blocked"
        message = str(provider.get("human_checkpoint") or "需要人工处理当前页面")[:400]
    elif state == "waiting_human" and not execution_active:
        public_state = "waiting_for_entry"
        message = str(
            provider.get("human_checkpoint")
            or "请在虚拟 Chrome 中完成验证码并进入正式表格"
        )[:400]
    else:
        public_state = "running"
        message = "Gemini V2 正在 Linux 可视 Chrome 中填写"
    return {
        "state": public_state,
        "message": message,
        "completedFields": completed,
        "totalFields": total,
        "pageLabel": str(provider.get("current_page_plan_id") or "")[:100],
        "statusCode": wait_kind[:64],
        "providerState": state,
        "runtimeOpen": provider.get("runtime_open") is True,
        "executionActive": execution_active,
        "userPaused": wait_kind == "user_paused",
    }
