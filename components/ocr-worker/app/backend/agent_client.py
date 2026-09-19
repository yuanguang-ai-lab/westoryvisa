"""Small HTTP boundary between DocFlow and the standalone Agent Core."""

import base64
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AgentClientError(RuntimeError):
    """A redacted Agent failure that is safe to show in the DocFlow UI."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def agent_base_url(base_url=None):
    return str(
        base_url
        if base_url is not None
        else os.environ.get("DOCFLOW_AGENT_URL", "")
    ).strip().rstrip("/")


def _json_request(path, payload=None, timeout=660, base_url=None):
    selected_base_url = agent_base_url(base_url)
    if not selected_base_url:
        raise AgentClientError("Agent Core 未配置，请重新运行本地启动器")
    request = Request(
        f"{selected_base_url}{path}",
        data=(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None else None
        ),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error")
        except Exception:
            detail = ""
        raise AgentClientError(
            str(detail or f"Agent Core 请求失败（HTTP {error.code}）")[:500],
            status=error.code,
        ) from error
    except (OSError, URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
        raise AgentClientError(
            f"Agent Core 暂时不可用：{type(error).__name__}"
        ) from error
    if not isinstance(decoded, dict):
        raise AgentClientError("Agent Core 返回了无法识别的结果")
    return decoded


def agent_health(timeout=1.5, *, base_url=None):
    return _json_request("/health", timeout=timeout, base_url=base_url)


def recognize_document(path, *, filename, mime_type, document_type):
    content = Path(path).read_bytes()
    if not content:
        raise AgentClientError("上传文件为空")
    return _json_request(
        "/v1/recognize-document",
        {
            "fileBase64": base64.b64encode(content).decode("ascii"),
            "filename": filename,
            "mediaType": mime_type or "application/octet-stream",
            "documentType": document_type or "unknown",
        },
    )


def recognize_text(text, *, filename="consultant-notes.txt", document_type="other"):
    return _json_request(
        "/v1/recognize-text",
        {
            "ocrText": text,
            "filename": filename,
            "documentType": document_type,
        },
        timeout=200,
    )


def transform_text(
    text,
    *,
    mode="translate",
    source_language="auto",
    target_language="en",
    target_script="Latn",
):
    return _json_request(
        "/v1/transform-text",
        {
            "text": text,
            "mode": mode,
            "sourceLanguage": source_language,
            "targetLanguage": target_language,
            "targetScript": target_script,
        },
        timeout=200,
    )


def create_computer_use_job(
    *, start_url, fields, required_field_ids, auto_next=True,
    manual_review_page_plan_ids=None, base_url=None,
):
    return _json_request(
        "/v1/jobs",
        {
            "startUrl": str(start_url),
            "fields": list(fields),
            "requiredFieldIds": list(required_field_ids),
            "autoNext": bool(auto_next),
            "manualReviewPagePlanIds": list(
                manual_review_page_plan_ids or ()
            ),
        },
        timeout=30,
        base_url=base_url,
    )


def review_computer_use_job(job_id, *, actor, decisions, base_url=None):
    return _json_request(
        f"/v1/jobs/{job_id}/review",
        {
            "actor": str(actor),
            "decisions": list(decisions),
        },
        timeout=30,
        base_url=base_url,
    )


def sync_computer_use_job(
    job_id,
    *,
    actor,
    fields,
    required_field_ids,
    decisions,
    auto_next=True,
    manual_review_page_plan_ids=None,
    base_url=None,
):
    return _json_request(
        f"/v1/jobs/{job_id}/sync",
        {
            "actor": str(actor),
            "fields": list(fields),
            "requiredFieldIds": list(required_field_ids),
            "decisions": list(decisions),
            "autoNext": bool(auto_next),
            "manualReviewPagePlanIds": list(
                manual_review_page_plan_ids or ()
            ),
        },
        timeout=30,
        base_url=base_url,
    )


def open_computer_use_job(job_id, *, base_url=None):
    """Open the job's browser without invoking the computer-use model."""
    return _json_request(
        f"/v1/jobs/{job_id}/open",
        {},
        timeout=120,
        base_url=base_url,
    )


def get_computer_use_job(job_id, *, base_url=None):
    """Read the latest provider checkpoint while a long run is in progress."""
    return _json_request(
        f"/v1/jobs/{job_id}",
        timeout=10,
        base_url=base_url,
    )


def run_computer_use_job(job_id, *, resume=False, base_url=None):
    action = "resume" if resume else "start"
    # A complete DS-160 can legitimately span more than an hour.  The backend
    # monitors the provider checkpoint independently, so this request must not
    # manufacture a failure at an arbitrary wall-clock boundary while the
    # Agent Core is still filling the same browser session.
    return _json_request(
        f"/v1/jobs/{job_id}/{action}",
        {},
        timeout=None,
        base_url=base_url,
    )


def pause_computer_use_job(job_id, *, actor="docflow", base_url=None):
    """Fence the active generation without closing its browser session."""
    return _json_request(
        f"/v1/jobs/{job_id}/pause",
        {"actor": str(actor)},
        timeout=30,
        base_url=base_url,
    )


def cancel_computer_use_job(job_id, *, actor="docflow", base_url=None):
    return _json_request(
        f"/v1/jobs/{job_id}/cancel",
        {"actor": str(actor)},
        timeout=30,
        base_url=base_url,
    )
