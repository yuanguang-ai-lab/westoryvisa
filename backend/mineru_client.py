#!/usr/bin/env python3
"""MinerU precision API adapter for DocFlow's separated backend."""

import io
import json
import os
import time
import uuid
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://mineru.net/api/v4"
PROCESSING_STATES = {"waiting-file", "pending", "running", "converting"}
MAX_RESULT_BYTES = 150 * 1024 * 1024


class MinerUError(RuntimeError):
    pass


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def token():
    return os.environ.get("MINERU_API_TOKEN", "").strip()


def base_url():
    return os.environ.get("MINERU_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def model_version():
    value = os.environ.get("MINERU_MODEL_VERSION", "vlm").strip().lower()
    return value if value in {"vlm", "pipeline"} else "vlm"


def status():
    configured = bool(token())
    model = model_version()
    return {
        "available": configured,
        "configured": configured,
        "installed": True,
        "starting": False,
        "remote": True,
        "provider": "mineru",
        "providerLabel": f"MinerU {model.upper()} 精准解析",
        "service": "MinerU Precision API",
        "ocrEngine": model,
        "message": (
            f"MinerU {model.upper()} 精准解析已配置；材料会上传至 MinerU 进行版面与文字识别"
            if configured
            else "尚未配置 MINERU_API_TOKEN，请先在后端环境变量中填写 MinerU Token"
        ),
    }


def convert_file(file_path, filename=None, mime_type=None, timeout=None, document_type=""):
    """Submit one local file and normalize MinerU's archive for DS-160 mapping."""
    del mime_type, document_type
    if not token():
        raise MinerUError("尚未配置 MINERU_API_TOKEN")

    path = Path(file_path)
    safe_name = _safe_filename(filename or path.name)
    data_id = f"docflow-{uuid.uuid4().hex}"
    timeout = float(timeout or os.environ.get("MINERU_TIMEOUT_SECONDS", "600"))
    poll_interval = max(
        0.2, float(os.environ.get("MINERU_POLL_INTERVAL_SECONDS", "3"))
    )
    force_ocr = env_flag("MINERU_FORCE_OCR", True)
    payload = {
        "files": [{
            "name": safe_name,
            "data_id": data_id,
            "is_ocr": force_ocr,
        }],
        "model_version": model_version(),
        "language": os.environ.get("MINERU_LANGUAGE", "ch").strip() or "ch",
        "enable_table": env_flag("MINERU_ENABLE_TABLE", True),
        "enable_formula": env_flag("MINERU_ENABLE_FORMULA", True),
    }

    created = _api_data(
        _request_json("/file-urls/batch", method="POST", payload=payload),
        "MinerU 获取上传地址失败",
    )
    batch_id = str(created.get("batch_id") or "").strip()
    upload_urls = created.get("file_urls") or []
    if not batch_id or len(upload_urls) != 1:
        raise MinerUError("MinerU 未返回有效的 batch_id 或上传地址")
    _upload_file(upload_urls[0], path)

    started = time.monotonic()
    result_item = None
    while time.monotonic() - started <= timeout:
        batch = _api_data(
            _request_json(f"/extract-results/batch/{batch_id}"),
            "MinerU 查询解析结果失败",
        )
        result_item = _matching_result(
            batch.get("extract_result"), data_id, safe_name
        )
        state = str((result_item or {}).get("state") or "").lower()
        if state in {"done", "failed"}:
            break
        if state and state not in PROCESSING_STATES:
            raise MinerUError(f"MinerU 返回未知任务状态：{state}")
        time.sleep(poll_interval)
    else:
        raise MinerUError(f"MinerU 解析超时（超过 {round(timeout)} 秒）")

    if not result_item:
        raise MinerUError("MinerU 查询结果中没有当前文件")
    if result_item.get("state") == "failed":
        raise MinerUError(
            f"MinerU 解析失败：{result_item.get('err_msg') or '未提供原因'}"
        )
    archive_url = str(result_item.get("full_zip_url") or "").strip()
    if not archive_url:
        raise MinerUError("MinerU 已完成解析，但没有返回结果下载地址")

    markdown, content_list = parse_result_archive(_download_archive(archive_url))
    text = markdown.strip() or flatten_content_list(content_list)
    if not text:
        raise MinerUError("MinerU 结果中没有可读取的文字")
    return {
        "status": "success",
        "text": text,
        "json": {
            "provider": "mineru",
            "modelVersion": model_version(),
            "batchId": batch_id,
            "dataId": data_id,
            "contentList": content_list,
        },
        "pages": content_list_pages(content_list),
        "markdown": markdown,
        "processingTime": round(time.monotonic() - started, 3),
        "timings": {},
        "parser": "mineru",
        "ocrEngine": model_version(),
        "forcedOcr": force_ocr,
        "textQuality": round(text_quality_score(text), 3),
    }


def _safe_filename(filename):
    value = Path(str(filename or "document.pdf")).name
    value = value.replace("\r", "").replace("\n", "").strip()
    return value[:240] or "document.pdf"


def _request_json(path, method="GET", payload=None):
    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token()}",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(
        f"{base_url()}{path}", data=body, method=method, headers=headers
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise MinerUError(
            f"MinerU API 请求失败（HTTP {error.code}）：{detail}"
        ) from error
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise MinerUError(f"无法连接 MinerU API：{error}") from error


def _api_data(response, prefix):
    if not isinstance(response, dict) or response.get("code") != 0:
        message = response.get("msg") if isinstance(response, dict) else ""
        raise MinerUError(f"{prefix}：{message or '响应格式无效'}")
    data = response.get("data")
    if not isinstance(data, dict):
        raise MinerUError(f"{prefix}：响应缺少 data")
    return data


def _require_https(value, label):
    parsed = urlparse(str(value or ""))
    if parsed.scheme != "https" or not parsed.hostname:
        raise MinerUError(f"{label}不是有效的 HTTPS 地址")


def _upload_file(upload_url, path):
    _require_https(upload_url, "MinerU 上传地址")
    request = Request(upload_url, data=Path(path).read_bytes(), method="PUT")
    try:
        with urlopen(request, timeout=180) as response:
            if response.status not in {200, 201, 204}:
                raise MinerUError(f"MinerU 文件上传失败（HTTP {response.status}）")
    except HTTPError as error:
        raise MinerUError(f"MinerU 文件上传失败（HTTP {error.code}）") from error
    except (URLError, TimeoutError, OSError) as error:
        raise MinerUError(f"无法上传文件到 MinerU：{error}") from error


def _matching_result(results, data_id, filename):
    if not isinstance(results, list):
        return None
    for item in results:
        if isinstance(item, dict) and item.get("data_id") == data_id:
            return item
    for item in results:
        if isinstance(item, dict) and item.get("file_name") == filename:
            return item
    return results[0] if len(results) == 1 and isinstance(results[0], dict) else None


def _download_archive(url):
    _require_https(url, "MinerU 结果地址")
    try:
        with urlopen(Request(url, headers={"Accept": "application/zip"}), timeout=120) as response:
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > MAX_RESULT_BYTES:
                raise MinerUError("MinerU 结果压缩包超过安全大小限制")
            content = response.read(MAX_RESULT_BYTES + 1)
    except HTTPError as error:
        raise MinerUError(f"MinerU 结果下载失败（HTTP {error.code}）") from error
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise MinerUError(f"无法下载 MinerU 结果：{error}") from error
    if len(content) > MAX_RESULT_BYTES:
        raise MinerUError("MinerU 结果压缩包超过安全大小限制")
    return content


def parse_result_archive(archive):
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as opened:
            names = [name for name in opened.namelist() if not name.endswith("/")]
            markdown_name = _member(names, "full.md", ".md")
            content_name = _member(names, "content_list.json", "_content_list.json")
            markdown = (
                opened.read(markdown_name).decode("utf-8", errors="replace")
                if markdown_name else ""
            )
            decoded = (
                json.loads(opened.read(content_name).decode("utf-8"))
                if content_name else []
            )
            return markdown, decoded if isinstance(decoded, list) else []
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as error:
        raise MinerUError(f"MinerU 结果压缩包无法读取：{error}") from error


def _member(names, exact_name, fallback_suffix):
    exact = next((name for name in names if Path(name).name == exact_name), None)
    return exact or next(
        (name for name in names if name.endswith(fallback_suffix)), None
    )


def _item_text(item):
    if not isinstance(item, dict):
        return ""
    for key in ("text", "content", "table_body", "html"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def flatten_content_list(content_list):
    return "\n\n".join(
        text for text in (_item_text(item) for item in content_list) if text
    )


def content_list_pages(content_list):
    pages = {}
    for item in content_list if isinstance(content_list, list) else []:
        text = _item_text(item)
        if not text:
            continue
        try:
            page_number = int(item.get("page_idx")) + 1
        except (TypeError, ValueError):
            page_number = 1
        pages.setdefault(max(1, page_number), []).append(text)
    return [
        {"page": page, "text": "\n\n".join(parts)}
        for page, parts in sorted(pages.items())
    ]


def text_quality_score(text):
    compact = "".join(str(text or "").split())
    if not compact:
        return 0.0
    useful = sum(
        1 for char in compact
        if char.isalnum() or "\u4e00" <= char <= "\u9fff" or char in "<@/:-_.|"
    )
    useful_ratio = useful / len(compact)
    length_score = min(1.0, len(compact) / 180)
    corrupted_ratio = (compact.count("�") + compact.count("□")) / len(compact)
    return max(
        0.0,
        min(1.0, useful_ratio * 0.68 + length_score * 0.32 - corrupted_ratio),
    )
