#!/usr/bin/env python3
"""End-to-end local smoke for PostgreSQL, scheduler, workers and nginx auth."""

import json
import secrets
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend import automation_queue
from backend.application import (
    connect,
    create_auth_session,
    init_db,
    register_user,
    upsert_case,
)
from backend.central_automation import (
    enqueue_ds160_job,
    public_job_status,
    request_job_action,
)


FRONTEND_URL = "http://frontend"


def plan(label):
    return {
        "targetUrl": "https://ceac.state.gov/GenNIV/Default.aspx",
        "totalFields": 1,
        "pages": [{
            "key": "personal",
            "actions": [{
                "id": "surname",
                "label": "Surname",
                "kind": "text",
                "value": label,
            }],
        }],
    }


def make_user_and_case(suffix):
    unique = secrets.token_hex(6)
    user = register_user({
        "email": f"smoke-{suffix}-{unique}@example.invalid",
        "password": "local-smoke-password",
        "organizationName": f"Smoke {suffix} {unique}",
        "name": f"Smoke {suffix}",
        "phone": "+12025550123",
    })
    case_id = f"smoke-case-{suffix}-{unique}"
    upsert_case({
        "id": case_id,
        "applicantName": f"Smoke {suffix}",
        "visaType": "B1/B2",
        "currentStep": 0,
        "caseMeta": {},
        "documents": [],
        "extractedFields": [],
        "branchQuestionnaire": [],
        "validationResults": [],
    }, user, require_version=True)
    return user, case_id, create_auth_session(user["id"])


def wait_status(case_id, public_job_id, user, wanted, timeout=30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = public_job_status(
            connect,
            case_id=case_id,
            public_job_id=public_job_id,
            user=user,
        )
        if last["state"] in wanted:
            return last
        time.sleep(0.5)
    raise RuntimeError(
        f"timed out waiting for {sorted(wanted)}; last={last and last['state']}"
    )


def viewer_body(url, session_token):
    request = Request(
        FRONTEND_URL + url,
        headers={"Cookie": f"docflow_session={session_token}"},
    )
    with urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


def api_cases(session_token):
    request = Request(
        FRONTEND_URL + "/api/cases",
        headers={"Cookie": f"docflow_session={session_token}"},
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))["cases"]


def main():
    init_db()
    first_user, first_case, first_session = make_user_and_case("a")
    second_user, second_case, second_session = make_user_and_case("b")
    first_visible_cases = {item["id"] for item in api_cases(first_session)}
    second_visible_cases = {item["id"] for item in api_cases(second_session)}
    if first_case not in first_visible_cases or second_case in first_visible_cases:
        raise AssertionError("first tenant API case boundary failed")
    if second_case not in second_visible_cases or first_case in second_visible_cases:
        raise AssertionError("second tenant API case boundary failed")
    first = enqueue_ds160_job(
        connect,
        case_id=first_case,
        user=first_user,
        plan=plan("FIRST-SYNTHETIC"),
        auto_next=True,
    )
    second = enqueue_ds160_job(
        connect,
        case_id=second_case,
        user=second_user,
        plan=plan("SECOND-SYNTHETIC"),
        auto_next=True,
    )
    first_ready = wait_status(
        first_case, first["jobId"], first_user, {"waiting_for_entry"}
    )
    second_ready = wait_status(
        second_case, second["jobId"], second_user, {"waiting_for_entry"}
    )
    if {first_ready["workerId"], second_ready["workerId"]} != {
        "agent-v2-a", "agent-v2-b"
    }:
        raise AssertionError("two jobs were not assigned to distinct workers")
    first_view = viewer_body(first_ready["viewerUrl"], first_session)
    second_view = viewer_body(second_ready["viewerUrl"], second_session)
    if "agent-v2-a:6081" not in first_view and "agent-v2-b:6081" not in first_view:
        raise AssertionError("first viewer did not reach its leased worker")
    if "agent-v2-a:6081" not in second_view and "agent-v2-b:6081" not in second_view:
        raise AssertionError("second viewer did not reach its leased worker")
    if first_ready["workerId"] in second_view or second_ready["workerId"] in first_view:
        raise AssertionError("viewer routes crossed worker boundaries")
    try:
        viewer_body(first_ready["viewerUrl"], second_session)
    except HTTPError as error:
        if error.code != 403:
            raise
    else:
        raise AssertionError("cross-tenant viewer access was accepted")

    for case_id, queued, user in (
        (first_case, first, first_user),
        (second_case, second, second_user),
    ):
        request_job_action(
            connect,
            case_id=case_id,
            public_job_id=queued["jobId"],
            user=user,
            action="start",
        )
    first_done = wait_status(
        first_case, first["jobId"], first_user, {"review_required"}
    )
    second_done = wait_status(
        second_case, second["jobId"], second_user, {"review_required"}
    )
    # Review keeps each browser reserved for the same tenant. Explicit stop is
    # what closes the provider session and releases the slot.
    for case_id, queued, user in (
        (first_case, first, first_user),
        (second_case, second, second_user),
    ):
        request_job_action(
            connect,
            case_id=case_id,
            public_job_id=queued["jobId"],
            user=user,
            action="cancel",
        )
        wait_status(case_id, queued["jobId"], user, {"revoked"})
    with connect() as connection:
        job_rows = connection.execute(
            """
            SELECT public_job_id, worker_id, state, generation,
                   payload_json, viewer_token_hash
            FROM automation_jobs WHERE public_job_id IN (?, ?)
            ORDER BY public_job_id
            """,
            (first["jobId"], second["jobId"]),
        ).fetchall()
        event_count = connection.execute(
            """
            SELECT COUNT(*) AS count FROM automation_job_events
            WHERE job_id IN (
              SELECT id FROM automation_jobs WHERE public_job_id IN (?, ?)
            )
            """,
            (first["jobId"], second["jobId"]),
        ).fetchone()["count"]
    if any(row["payload_json"] != '{"redacted":true}' for row in job_rows):
        raise AssertionError("terminal jobs retained sensitive form payloads")
    if any(row["viewer_token_hash"] is not None for row in job_rows):
        raise AssertionError("terminal jobs retained viewer capabilities")
    print(json.dumps({
        "ok": True,
        "jobs": [
            {
                "workerId": row["worker_id"],
                "state": row["state"],
                "generation": int(row["generation"]),
            }
            for row in job_rows
        ],
        "viewerIsolation": True,
        "caseIsolation": True,
        "crossTenantDenied": True,
        "eventCount": int(event_count),
        "publicStates": [first_done["state"], second_done["state"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
