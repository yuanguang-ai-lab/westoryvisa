"""Validated configuration for the fixed, isolated browser-worker pool."""

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerConfig:
    worker_id: str
    api_url: str


def configured_workers(raw=None):
    value = (
        os.environ.get("DOCFLOW_AGENT_WORKERS", "")
        if raw is None else str(raw or "")
    ).strip()
    if not value:
        return []
    workers = []
    seen = set()
    for item in value.split(","):
        worker_id, separator, api_url = item.strip().partition("=")
        worker_id = worker_id.strip()
        api_url = api_url.strip().rstrip("/")
        if (
            not separator
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", worker_id)
            or not re.fullmatch(r"https?://[A-Za-z0-9._:-]+", api_url)
        ):
            raise ValueError(
                "DOCFLOW_AGENT_WORKERS 必须为 worker-id=http://host:port 列表"
            )
        if worker_id in seen:
            raise ValueError(f"浏览器 Worker 编号重复：{worker_id}")
        seen.add(worker_id)
        workers.append(WorkerConfig(worker_id=worker_id, api_url=api_url))
    return workers


def central_automation_enabled():
    return bool(os.environ.get("DOCFLOW_AGENT_WORKERS", "").strip())
