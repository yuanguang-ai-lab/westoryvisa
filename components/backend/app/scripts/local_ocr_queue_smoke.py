#!/usr/bin/env python3
"""Deterministic two-tenant OCR queue smoke test for the local stack only."""

import json
import threading
import uuid

from backend import application, ocr_queue, ocr_worker


PASSPORT_MRZ = """P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<
L898902C36UTO7408122F1204159ZE184226B<<<<<10"""


def _user(suffix):
    return application.register_user({
        "email": f"ocr-smoke-{suffix}@example.invalid",
        "password": "local-smoke-password",
        "organizationName": f"OCR Smoke {suffix}",
        "name": f"OCR Owner {suffix}",
        "phone": "+1 202 555 0101",
    })


def _case(user, suffix):
    case_id = f"case-ocr-smoke-{suffix}"
    document_id = f"document-ocr-smoke-{suffix}"
    application.upsert_case({
        "id": case_id,
        "applicantName": f"Synthetic {suffix}",
        "visaType": "B1/B2",
        "currentStep": 1,
        "caseMeta": {"owner": user["name"]},
        "documents": [{
            "id": document_id,
            "slot": "护照",
            "fileName": "",
        }],
        "extractedFields": [],
        "branchQuestionnaire": [],
        "validationResults": [],
    }, user, require_version=True)
    application.save_uploaded_document(
        case_id,
        document_id,
        user,
        f"synthetic-{suffix}.pdf",
        "application/pdf",
        b"%PDF-1.4 local OCR smoke only",
    )
    application.start_case_scan(case_id, user)
    return case_id


def main():
    if not ocr_queue.enabled():
        raise SystemExit("DOCFLOW_OCR_QUEUE_ENABLED=true is required")
    application.init_db()
    run_id = uuid.uuid4().hex[:10]
    first = _user(f"{run_id}-a")
    second = _user(f"{run_id}-b")
    first_case = _case(first, f"{run_id}-a")
    second_case = _case(second, f"{run_id}-b")
    workers = [f"ocr-smoke-{run_id}-a", f"ocr-smoke-{run_id}-b"]
    owners = [f"owner-{run_id}-a", f"owner-{run_id}-b"]
    for worker_id in workers:
        ocr_queue.register_worker(application.connect, worker_id=worker_id)
    jobs = [
        ocr_queue.claim_next_job(
            application.connect,
            worker_id=workers[index],
            lease_owner=owners[index],
        )
        for index in range(2)
    ]
    if not all(jobs):
        raise RuntimeError("two OCR jobs were not claimed")
    if jobs[0]["organization_id"] == jobs[1]["organization_id"]:
        raise RuntimeError("OCR smoke jobs unexpectedly share a tenant")

    converted = {
        "text": PASSPORT_MRZ,
        "json": {"texts": [{"text": PASSPORT_MRZ}]},
        "pages": [{"page": 1, "text": PASSPORT_MRZ}],
        "parser": "local-smoke",
        "ocrEngine": "local-smoke",
        "textQuality": 1,
    }
    original_status = application.ocr_service_status
    original_convert = application.convert_file
    application.ocr_service_status = lambda: {"available": True}
    application.convert_file = lambda *_args, **_kwargs: converted
    errors = []

    def run(index):
        try:
            ocr_worker.process_job(
                jobs[index],
                worker_id=workers[index],
                lease_owner=owners[index],
            )
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    finally:
        application.ocr_service_status = original_status
        application.convert_file = original_convert
    if errors:
        raise RuntimeError("; ".join(errors))
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError("OCR smoke worker did not finish")

    first_status = application.scan_status(first_case, first)
    second_status = application.scan_status(second_case, second)
    if first_status["status"] != "completed" or second_status["status"] != "completed":
        raise RuntimeError("OCR jobs did not complete")
    try:
        application.get_case_payload(first_case, second)
        raise RuntimeError("cross-tenant OCR case read was unexpectedly allowed")
    except PermissionError:
        pass
    first_payload = application.get_case_payload(first_case, first)
    second_payload = application.get_case_payload(second_case, second)
    passport_numbers = []
    for payload in (first_payload, second_payload):
        values = {
            field["id"]: field.get("value")
            for field in payload.get("extractedFields") or []
        }
        passport_numbers.append(values.get("passport.number"))
    if passport_numbers != ["L898902C3", "L898902C3"]:
        raise RuntimeError("synthetic OCR fields were not committed")
    print(json.dumps({
        "ok": True,
        "workers": [job["worker_id"] for job in jobs],
        "tenantIsolation": True,
        "states": [first_status["status"], second_status["status"]],
        "passportFields": passport_numbers,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
