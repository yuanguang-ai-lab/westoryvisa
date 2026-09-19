"""Reject jobs routed to the wrong country worker before opening Chromium."""

import os


SUPPORTED_COUNTRIES = frozenset({"CN", "MX", "BR", "IN"})
DEFAULT_RELEASES = {
    "CN": "cn-legacy",
    "MX": "mx-v1",
    "BR": "br-v1",
    "IN": "in-v1",
}


def configured_worker_target():
    country = os.environ.get("DOCFLOW_WORKER_COUNTRY_CODE", "CN").strip().upper()
    if country not in SUPPORTED_COUNTRIES:
        raise ValueError("Worker 国家配置无效")
    version = os.environ.get(
        "DOCFLOW_WORKER_EXECUTOR_VERSION", DEFAULT_RELEASES[country]
    ).strip()
    if not version:
        raise ValueError("Worker 执行版本不能为空")
    return country, version


def country_guard_issues(payload):
    payload = payload if isinstance(payload, dict) else {}
    configured_country, configured_version = configured_worker_target()
    requested_country = str(payload.get("countryCode") or "CN").strip().upper()
    requested_version = str(
        payload.get("executorVersion")
        or ("cn-legacy" if requested_country == "CN" else "")
    ).strip()
    issues = []
    if requested_country != configured_country:
        issues.append(
            f"任务国家 {requested_country or '未指定'} 与 Worker {configured_country} 不匹配"
        )
    if requested_version != configured_version:
        issues.append(
            f"任务执行版本 {requested_version or '未指定'} 与 Worker {configured_version} 不匹配"
        )
    expected_protocol = 1 if configured_country == "CN" else 2
    try:
        requested_protocol = int(payload.get("protocolVersion") or 1)
    except (TypeError, ValueError):
        requested_protocol = 0
    if requested_protocol != expected_protocol:
        issues.append(
            f"任务协议版本 {requested_protocol} 与 Worker {expected_protocol} 不匹配"
        )
    return issues


def require_country_guard(payload):
    issues = country_guard_issues(payload)
    if issues:
        raise ValueError("国家执行隔离检查未通过：" + "；".join(issues))
