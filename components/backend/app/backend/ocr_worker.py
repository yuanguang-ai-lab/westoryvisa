"""Single-slot OCR worker process for the durable PostgreSQL queue."""

import os
import logging
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

from . import application, ocr_queue
from .mineru_client import MinerUError


LEASE_SECONDS = max(60, int(os.environ.get("DOCFLOW_OCR_LEASE_SECONDS", "300")))
POLL_SECONDS = max(0.25, float(os.environ.get("DOCFLOW_OCR_POLL_SECONDS", "1")))
LOGGER = logging.getLogger("docflow.ocr_worker")


def _worker_id():
    configured = os.environ.get("DOCFLOW_OCR_WORKER_ID", "").strip()
    return configured or f"ocr-{socket.gethostname()}"


def _lease_owner(worker_id):
    return f"{socket.gethostname()}:{os.getpid()}:{worker_id}"


def _load_context(job):
    with application.connect() as connection:
        user_row = connection.execute(
            """
            SELECT users.*, organizations.name AS organization_name
            FROM users
            JOIN organizations ON organizations.id = users.organization_id
            WHERE users.id = ? AND users.organization_id = ?
            """,
            (job["user_id"], job["organization_id"]),
        ).fetchone()
        case = connection.execute(
            """
            SELECT id FROM ds160_cases
            WHERE id = ? AND organization_id = ?
            """,
            (job["case_id"], job["organization_id"]),
        ).fetchone()
        documents = connection.execute(
            """
            SELECT documents.*
            FROM documents
            JOIN ds160_cases ON ds160_cases.id = documents.case_id
            WHERE documents.case_id = ? AND ds160_cases.organization_id = ?
              AND documents.stored_path IS NOT NULL
            ORDER BY documents.created_at, documents.id
            """,
            (job["case_id"], job["organization_id"]),
        ).fetchall()
    if not user_row or not case:
        raise PermissionError("OCR 任务的用户或客户档案已不存在")
    if not documents:
        raise ValueError("OCR 任务没有可处理的真实文件")
    return application.public_user(user_row), [dict(row) for row in documents]


def _isolated_input(job, document, scratch):
    source = Path(document["stored_path"]).resolve()
    tenant_root = (
        application.UPLOAD_DIR
        / application.safe_path_component(job["organization_id"])
        / application.safe_path_component(job["case_id"])
    ).resolve()
    if tenant_root not in source.parents or not source.is_file():
        raise PermissionError("OCR 输入文件不在当前机构客户档案的专属目录中")
    suffix = source.suffix.lower()
    copied = Path(scratch) / (
        application.safe_path_component(document["id"]) + suffix
    )
    shutil.copy2(source, copied)
    return copied


def _document_result(job, document, visa_type, scratch):
    copied = _isolated_input(job, document, scratch)
    result = application.convert_file(
        str(copied),
        filename=document["file_name"],
        mime_type=document["mime_type"],
        document_type=document["slot"],
    )
    fields = application.map_document(
        document["slot"], document["file_name"], result["text"],
        document["id"], visa_type, page_texts=result.get("pages"),
    )
    quality_percent = round(float(result.get("textQuality") or 0) * 100)
    if result.get("parser") == "mineru":
        processing_mode = "MinerU 精准解析"
    else:
        processing_mode = "增强 OCR" if result.get("forcedOcr") else "版面解析 + OCR"
    if result.get("fallbackReason"):
        processing_mode += "（MinerU 异常，已回退本地解析）"
    rotation = int(result.get("rotationApplied") or 0)
    rotation_note = f" · 自动旋转 {rotation}°" if rotation else ""
    parser_name = f"{result['parser']} / {result['ocrEngine']}"
    if rotation:
        parser_name += f" / auto-rotate-{rotation}"
    stamped = application.now_iso()
    return {
        "document": {
            "status": "completed",
            "message": (
                f"扫描完成，映射出 {len(fields)} 个 DS-160 字段 · "
                f"{processing_mode}{rotation_note} · 文字质量 {quality_percent}%"
            ),
            "ocrText": result["text"][:5_000_000],
            "ocrJson": application.bounded_json(result["json"]),
            "parserName": parser_name,
            "parserVersion": "v3-queued",
            "processedAt": stamped,
            "documentId": document["id"],
        },
        "fields": fields,
        "evidence": {
            "documentId": document["id"],
            "fileName": document["file_name"],
            "slot": document["slot"],
            "text": result["text"],
        },
    }


def _payload_with_results(job, user, successful_fields, successful_documents, total):
    payload = application.get_case_payload(job["case_id"], user)
    visa_type = payload.get("visaType") or ""
    merged_fields, conflicts = application.merge_extracted_fields(
        payload.get("extractedFields") or [], successful_fields, visa_type,
    )
    payload["extractedFields"] = merged_fields
    questionnaire = application.build_questionnaire(
        visa_type,
        payload.get("branchQuestionnaire"),
        merged_fields,
    )
    questionnaire, answer_issues = application.infer_questionnaire_answers(
        questionnaire,
        successful_documents,
        merged_fields,
    )
    payload["branchQuestionnaire"] = application.build_questionnaire(
        visa_type,
        questionnaire,
        merged_fields,
    )
    payload["validationResults"] = [
        item for item in (payload.get("validationResults") or [])
        if not str(item.get("id", "")).startswith("ocr.")
    ] + conflicts + answer_issues
    completed = len(successful_documents)
    payload["currentStep"] = 3 if completed else 2
    payload["agentTimeline"] = application.build_scan_agent_timeline(
        payload.get("agentTimeline") or [], completed, total,
        completed == total and total > 0,
    )
    return payload


def _heartbeat(job, worker_id, lease_owner, stop_event, lease_lost):
    interval = min(30.0, max(10.0, LEASE_SECONDS / 3))
    while not stop_event.wait(interval):
        if not ocr_queue.renew_lease(
            application.connect,
            job_id=job["id"],
            worker_id=worker_id,
            generation=job["generation"],
            lease_owner=lease_owner,
            lease_seconds=LEASE_SECONDS,
        ):
            lease_lost.set()
            return
        ocr_queue.register_worker(
            application.connect, worker_id=worker_id, status="online"
        )


def process_job(job, *, worker_id, lease_owner):
    running = ocr_queue.start_running(
        application.connect,
        job_id=job["id"],
        worker_id=worker_id,
        generation=job["generation"],
        lease_owner=lease_owner,
        lease_seconds=LEASE_SECONDS,
    )
    stop_event = threading.Event()
    lease_lost = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat,
        args=(running, worker_id, lease_owner, stop_event, lease_lost),
        name=f"ocr-lease-{application.safe_path_component(job['id'])}",
        daemon=True,
    )
    heartbeat.start()
    try:
        service = application.ocr_service_status()
        if not service.get("available"):
            raise application.DoclingError(service.get("message") or "OCR 服务不可用")
        user, documents = _load_context(running)
        initial = application.get_case_payload(job["case_id"], user)
        visa_type = initial.get("visaType") or ""
        successful_fields = []
        successful_documents = []
        document_results = []
        document_errors = []
        with tempfile.TemporaryDirectory(
            prefix=f"docflow-{application.safe_path_component(job['id'])}-"
        ) as scratch:
            for index, document in enumerate(documents, start=1):
                if lease_lost.is_set():
                    raise ocr_queue.OcrConflict("OCR Worker 已失去任务租约")
                try:
                    result = _document_result(running, document, visa_type, scratch)
                    document_results.append(result["document"])
                    successful_fields.extend(result["fields"])
                    successful_documents.append(result["evidence"])
                except Exception as error:
                    document_errors.append(error)
                    document_results.append({
                        "documentId": document["id"],
                        "status": "failed",
                        "message": str(error)[:500],
                        "ocrText": None,
                        "ocrJson": None,
                        "parserName": None,
                        "parserVersion": "v3-queued",
                        "processedAt": application.now_iso(),
                    })
                summary = {
                    "total": len(documents),
                    "processed": index,
                    "completed": len(successful_documents),
                    "failed": index - len(successful_documents),
                    "message": f"已处理 {index} / {len(documents)} 份材料",
                }
                ocr_queue.update_progress(
                    application.connect,
                    job_id=running["id"],
                    worker_id=worker_id,
                    generation=running["generation"],
                    lease_owner=lease_owner,
                    summary=summary,
                )

        if (
            not successful_documents
            and document_errors
            and all(isinstance(error, (application.DoclingError, MinerUError))
                    for error in document_errors)
        ):
            # A provider-wide outage is retryable; a genuinely unreadable file
            # remains a terminal per-document failure.
            raise document_errors[0]

        summary = {
            "total": len(documents),
            "processed": len(documents),
            "completed": len(successful_documents),
            "failed": len(documents) - len(successful_documents),
            "message": (
                "文档扫描与字段映射完成"
                if successful_documents else "全部文档处理失败"
            ),
        }
        for attempt in range(4):
            if lease_lost.is_set():
                raise ocr_queue.OcrConflict("OCR Worker 已失去任务租约")
            payload = _payload_with_results(
                running, user, successful_fields, successful_documents,
                len(documents),
            )

            def commit(connection, _saved_payload):
                ocr_queue.complete_in_transaction(
                    connection,
                    job_id=running["id"],
                    worker_id=worker_id,
                    generation=running["generation"],
                    lease_owner=lease_owner,
                    document_results=document_results,
                    summary=summary,
                    actor=user.get("name") or user.get("id") or "ocr-worker",
                )

            try:
                return application.upsert_case(
                    payload,
                    user,
                    require_version=True,
                    transaction_callback=commit,
                )
            except application.CaseVersionConflict:
                if attempt >= 3:
                    raise
                continue
    finally:
        stop_event.set()
        heartbeat.join(timeout=2)


def run_forever():
    if not ocr_queue.enabled():
        raise SystemExit("DOCFLOW_OCR_QUEUE_ENABLED=true is required")
    application.init_db()
    worker_id = _worker_id()
    lease_owner = _lease_owner(worker_id)
    while True:
        try:
            ocr_queue.register_worker(
                application.connect, worker_id=worker_id, status="online"
            )
            ocr_queue.reap_expired_leases(application.connect)
            job = ocr_queue.claim_next_job(
                application.connect,
                worker_id=worker_id,
                lease_owner=lease_owner,
                lease_seconds=LEASE_SECONDS,
            )
            if job:
                try:
                    process_job(job, worker_id=worker_id, lease_owner=lease_owner)
                except ocr_queue.OcrConflict:
                    # Another generation owns the job; the stale process must
                    # exit silently without touching tenant data.
                    pass
                except Exception as error:
                    ocr_queue.fail_or_requeue(
                        application.connect,
                        job_id=job["id"],
                        worker_id=worker_id,
                        generation=job["generation"],
                        lease_owner=lease_owner,
                        message=error,
                    )
        except KeyboardInterrupt:
            ocr_queue.register_worker(
                application.connect, worker_id=worker_id, status="offline"
            )
            return
        except Exception as error:
            LOGGER.error("ocr worker loop error=%s", type(error).__name__)
            time.sleep(2)
        time.sleep(POLL_SECONDS)


def main():
    run_forever()


if __name__ == "__main__":
    main()
