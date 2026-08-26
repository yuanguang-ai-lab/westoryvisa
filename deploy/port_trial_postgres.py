#!/usr/bin/env python3
"""Apply the trial-case changes to the PostgreSQL production application.

The production host keeps PostgreSQL-specific concurrency and transaction
extensions that are intentionally not replaced by the SQLite reference app.
Run this helper only after applying the bdc9c00 application patch to a copy of
the production application.py.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} match, found {count}")
    return source.replace(old, new, 1)


def port_application(path: Path) -> None:
    source = path.read_text()

    source = replace_once(
        source,
        "    *,\n    require_version=False,\n",
        "    *,\n    enforce_trial_limit=False,\n    require_version=False,\n",
        "upsert signature",
    )
    source = replace_once(
        source,
        '"SELECT organization_id, client_id, version FROM ds160_cases WHERE id = ?",',
        '"SELECT organization_id, client_id, visa_type, payload_json, version '
        'FROM ds160_cases WHERE id = ?",',
        "case lookup",
    )

    current_version = (
        '        current_version = int(existing_case["version"] or 1) '
        "if existing_case else 0\n"
    )
    identity_and_trial = '''        if existing_case:
            trial_use = conn.execute(
                "SELECT case_id FROM trial_case_uses WHERE case_id = ?", (case_id,)
            ).fetchone()
            if trial_use:
                try:
                    original_payload = json.loads(existing_case["payload_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    original_payload = {}
                original_name = str(original_payload.get("applicantName") or "").strip()
                updated_name = str(payload.get("applicantName") or "").strip()
                original_visa = str(
                    original_payload.get("visaType") or existing_case["visa_type"] or ""
                ).strip()
                updated_visa = str(payload.get("visaType") or "").strip()
                if updated_name != original_name or updated_visa != original_visa:
                    raise PermissionError(
                        "免费试用档案已锁定申请人姓名和签证类型；可继续补充材料并完成流程。"
                    )
        if enforce_trial_limit and not existing_case and not paid_membership:
            consume_trial_case_use(conn, user, case_id)
'''
    source = replace_once(
        source,
        current_version,
        identity_and_trial + current_version,
        "trial identity insertion point",
    )

    handler_call = '''                saved = upsert_case(
                    payload.get("case") or payload,
                    user,
                    require_version=True,
                )
'''
    enforced_call = '''                saved = upsert_case(
                    payload.get("case") or payload,
                    user,
                    enforce_trial_limit=True,
                    require_version=True,
                )
'''
    source = replace_once(
        source,
        handler_call,
        enforced_call,
        "case API handler call",
    )

    status_pattern = (
        r'(def status_for_step\(step\):\n)'
        r'    if step >= 7:\n        return [^\n]+\n'
        r'    if step >= 6:\n        return [^\n]+\n'
    )
    source, status_count = re.subn(
        status_pattern,
        r'\1    if step >= 6:\n        return "已完成"\n',
        source,
        count=1,
    )
    if status_count != 1:
        raise RuntimeError(f"expected one status mapping, found {status_count}")

    source = re.sub(
        r'API_VERSION = "[^"]+"',
        'API_VERSION = "2026-08-26-trial-seven-step-v1"',
        source,
        count=1,
    )
    source = re.sub(r"API_REVISION = \d+", "API_REVISION = 23", source, count=1)
    path.write_text(source)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("application", type=Path)
    args = parser.parse_args()
    port_application(args.application)


if __name__ == "__main__":
    main()
