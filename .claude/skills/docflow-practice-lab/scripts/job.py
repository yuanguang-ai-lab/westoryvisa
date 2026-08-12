#!/usr/bin/env python3
"""Validate and redact one-time DocFlow OpenCowork practice tasks."""

import argparse
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[4]
JOB_DIRECTORY = ROOT / "data" / "open_cowork_jobs"
JOB_ID_PATTERN = re.compile(r"open-cowork-[0-9a-f]{24}\Z")
TARGET_MARKER = "VISA FORM PRACTICE LAB"
TARGET_PATH = "/screen-agent-import.html"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

ALLOWED_FIELDS = {
    "personal.surname": ("Surname", "Example"),
    "personal.givenNames": ("Given Names", "Alex"),
    "personal.dateOfBirth": ("Date of Birth", "1990-01-15"),
    "personal.placeOfBirth": ("Place of Birth", "Sample City"),
    "passport.number": ("Passport Number", "DEMO123456"),
    "passport.issueDate": ("Passport Issue Date", "2024-01-15"),
    "passport.expiration": ("Passport Expiration Date", "2034-01-14"),
    "travel.visaType": ("Purpose of Trip", {"B1/B2", "F1", "J1", "OTHER"}),
    "travel.arrivalDate": ("Intended Date of Arrival", "2027-05-10"),
    "contact.usAddress": (
        "Address Where You Will Stay",
        "100 Example Avenue, Sample City",
    ),
    "contact.organizationName": (
        "U.S. Contact Organization",
        "Sample Training Center",
    ),
    "contact.phone": ("U.S. Contact Phone", "+1 202-555-0142"),
    "work.employerName": (
        "Present Employer or School",
        "Example Learning Studio",
    ),
    "education.schoolName": ("School Name", "Example Learning University"),
    "education.sevisId": ("SEVIS ID", "DEMO-SEVIS-001"),
    "education.programNumber": ("Program Number", "DEMO-PROGRAM-01"),
}

REQUIRED_SAFETY = {
    "localhostOnly": True,
    "practiceLabOnly": True,
    "sanitizedDemoOnly": True,
    "visibleComputerUse": True,
    "perFieldVisualAcknowledgement": True,
    "sensitiveQuestions": "manual_only",
    "captcha": "never",
    "credentials": "never",
    "accountCreation": "never",
    "legalDeclaration": "never",
    "payment": "never",
    "finalSubmission": "never",
}


class TaskError(RuntimeError):
    """Raised when a local practice task does not satisfy the contract."""


def utc_now():
    return datetime.now(timezone.utc)


def parse_timestamp(value, label):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise TaskError(f"{label} is invalid") from error
    if parsed.tzinfo is None:
        raise TaskError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def paths_for(job_id):
    if not JOB_ID_PATTERN.fullmatch(str(job_id or "")):
        raise TaskError("Task ID is invalid")
    return {
        "job": JOB_DIRECTORY / f"{job_id}.json",
        "status": JOB_DIRECTORY / f"{job_id}.status.json",
    }


def read_json(path, label):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise TaskError(f"{label} was not found") from error
    except (OSError, json.JSONDecodeError) as error:
        raise TaskError(f"{label} cannot be read") from error
    if not isinstance(payload, dict):
        raise TaskError(f"{label} must be a JSON object")
    return payload


def write_private_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def validate_target(payload, job_id, field_ids):
    target = urlparse(str(payload.get("targetUrl") or ""))
    if (
        target.scheme != "http"
        or target.hostname not in LOCAL_HOSTS
        or target.path != TARGET_PATH
        or target.username
        or target.password
        or target.fragment
    ):
        raise TaskError("Target must be the local Visa Form Practice Lab")
    query = parse_qs(target.query, keep_blank_values=True)
    if query.get("job") != [job_id]:
        raise TaskError("Target task ID does not match")
    if set(query) != {"job", "fields"}:
        raise TaskError("Target query contains unexpected parameters")
    manifest_ids = [item for item in query.get("fields", [""])[0].split(",") if item]
    if manifest_ids != field_ids:
        raise TaskError("Target field manifest does not match")
    return target.geturl()


def validate_fields(raw_fields):
    if not isinstance(raw_fields, list) or not raw_fields:
        raise TaskError("Task has no fields")
    validated = []
    seen = set()
    for raw_field in raw_fields:
        if not isinstance(raw_field, dict):
            raise TaskError("Task field is invalid")
        field_id = str(raw_field.get("id") or "")
        if field_id in seen or field_id not in ALLOWED_FIELDS:
            raise TaskError("Task contains an unknown or duplicate field")
        expected_label, expected_value = ALLOWED_FIELDS[field_id]
        value = raw_field.get("value")
        value_matches = (
            value in expected_value
            if isinstance(expected_value, set)
            else value == expected_value
        )
        if raw_field.get("label") != expected_label or not value_matches:
            raise TaskError(f"Task field {field_id} is not a fixed demo mapping")
        if raw_field.get("source") != "DocFlow 固定脱敏演示映射":
            raise TaskError(f"Task field {field_id} has an invalid source")
        seen.add(field_id)
        validated.append({"id": field_id, "label": expected_label, "value": value})
    return validated


def validate_job(job_id, allow_expired=False):
    paths = paths_for(job_id)
    payload = read_json(paths["job"], "Task")
    if payload.get("version") != 1 or payload.get("executor") != "open-cowork":
        raise TaskError("Task executor contract is invalid")
    if payload.get("jobId") != job_id:
        raise TaskError("Task ID does not match its file")
    if payload.get("targetMarker") != TARGET_MARKER:
        raise TaskError("Target marker is invalid")
    if payload.get("operatorAuthorized") is not True:
        raise TaskError("Visible operator authorization is missing")
    safety = payload.get("safety")
    if not isinstance(safety, dict) or any(
        safety.get(key) != value for key, value in REQUIRED_SAFETY.items()
    ):
        raise TaskError("Task safety boundaries are incomplete")

    created_at = parse_timestamp(payload.get("createdAt"), "createdAt")
    expires_at = parse_timestamp(payload.get("expiresAt"), "expiresAt")
    lifetime = (expires_at - created_at).total_seconds()
    if lifetime <= 0 or lifetime > 15 * 60 + 5:
        raise TaskError("Task lifetime exceeds the 15-minute limit")
    if not allow_expired and expires_at <= utc_now():
        raise TaskError("Task has expired; prepare a new task in DocFlow")
    if payload.get("redactedAt"):
        raise TaskError("Task has already been redacted")

    fields = validate_fields(payload.get("fields"))
    target_url = validate_target(payload, job_id, [field["id"] for field in fields])
    return paths, payload, {
        "taskId": job_id,
        "targetUrl": target_url,
        "targetMarker": TARGET_MARKER,
        "fields": fields,
        "fieldCount": len(fields),
        "expiresAt": expires_at.isoformat(),
        "stopBefore": "Security and Background",
        "boundaries": REQUIRED_SAFETY,
    }


def inspect_task(job_id):
    _paths, _payload, safe_view = validate_job(job_id)
    print(json.dumps(safe_view, ensure_ascii=False, indent=2))


def complete_task(job_id):
    paths, payload, safe_view = validate_job(job_id, allow_expired=True)
    for field in payload.get("fields") or []:
        field["value"] = ""
        field.pop("source", None)
    stamped = utc_now().isoformat()
    payload["redactedAt"] = stamped
    write_private_json(paths["job"], payload)

    status = {
        "jobId": job_id,
        "state": "completed_local_demo",
        "message": "OpenCowork local practice task completed and redacted",
        "completedFields": safe_view["fieldCount"],
        "totalFields": safe_view["fieldCount"],
        "updatedAt": stamped,
    }
    write_private_json(paths["status"], status)
    print(json.dumps({"taskId": job_id, "state": status["state"], "redacted": True}))


def main():
    parser = argparse.ArgumentParser(
        description="Inspect or redact a DocFlow OpenCowork practice task."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "complete"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--job-id", required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "inspect":
            inspect_task(arguments.job_id)
        else:
            complete_task(arguments.job_id)
    except TaskError as error:
        print(f"OpenCowork task rejected: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
