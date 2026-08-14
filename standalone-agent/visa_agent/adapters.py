"""Built-in vendor adapters.

Vendor-specific request/response handling lives here so domain, safety, and
workflow modules remain provider-neutral.
"""

import base64
import http.client
import io
import json
import math
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .models import (
    ActionKind,
    BrowserObservation,
    ComputerAction,
    Evidence,
    ExtractedField,
    NextDispatchReceiptUnavailable,
)
from .providers import ProviderNotConfigured
from .page_plans import PagePlanRegistry, classify_ceac_page
from .profile_storage import (
    profile_path_is_broad,
    purge_private_profile_path,
)
from .recovery import normalize_security_question
from .validation import DEFAULT_FIELD_SCHEMAS


class ProviderRequestError(RuntimeError):
    """A redacted provider failure safe to expose in Agent error messages."""

    def __init__(
        self,
        message,
        *,
        retryable=None,
        status_code=None,
        reason_code="",
    ):
        super().__init__(str(message))
        self.status_code = (
            int(status_code) if status_code is not None else None
        )
        if retryable is None:
            normalized = str(message or "").casefold()
            retryable = (
                "connection failed" in normalized
                or "timed out" in normalized
                or "timeout" in normalized
                or "incomplete" in normalized
                or "invalid json" in normalized
                or self.status_code in {408, 425, 429}
                or (
                    self.status_code is not None
                    and self.status_code >= 500
                )
            )
        self.retryable = bool(retryable)
        self.reason_code = str(reason_code or "")


def _safe_provider_http_rejection(error):
    """Return a small allowlisted provider reason without leaking a body."""
    message = ""
    try:
        raw = error.read(16384)
        decoded = json.loads(raw.decode("utf-8"))
        if isinstance(decoded, dict):
            detail = decoded.get("error")
            if isinstance(detail, dict):
                message = str(detail.get("message") or "")
            elif isinstance(detail, str):
                message = detail
    except Exception:
        pass
    normalized = re.sub(r"\s+", " ", message).strip().casefold()
    if "user location is not supported" in normalized:
        return "user location is not supported", "unsupported_location"
    if any(pattern in normalized for pattern in (
        "api key not valid",
        "invalid api key",
        "api_key_invalid",
        "permission denied",
    )):
        return "provider credentials were rejected", "invalid_credentials"
    if "model" in normalized and any(pattern in normalized for pattern in (
        "not found",
        "not supported",
        "unavailable",
        "does not exist",
    )):
        return "configured model is unavailable", "model_unavailable"
    return "request rejected", "request_rejected"


class ControlBindingUnavailable(RuntimeError):
    """The previously verified DOM identity is no longer live.

    This is a transient browser-planning condition, not evidence that the
    approved value violates a CEAC control constraint.  The workflow may
    safely discard the stale selector and replan because no DOM mutation has
    occurred when this exception is raised.
    """


class ControlBindingCollision(ControlBindingUnavailable):
    """Two logical fields resolved to the same live DOM control."""


class ControlValueConstraintError(RuntimeError):
    """A live, semantically bound control cannot accept the approved value."""


class HTTPClient:
    def request(self, method, url, body=None, headers=None, timeout=120):
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers or {}),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return (
                    response.read(),
                    str(response.headers.get("Content-Type") or ""),
                )
        except urllib.error.HTTPError as error:
            safe_reason, reason_code = _safe_provider_http_rejection(error)
            raise ProviderRequestError(
                f"Provider HTTP {error.code}: {safe_reason}",
                status_code=error.code,
                retryable=(
                    error.code in {408, 425, 429}
                    or error.code >= 500
                ),
                reason_code=reason_code,
            ) from error
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.IncompleteRead,
        ) as error:
            raise ProviderRequestError(
                f"Provider connection failed: {type(error).__name__}",
                retryable=True,
            ) from error

    def json(self, method, url, payload, headers=None, timeout=120):
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **dict(headers or {}),
        }
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None else None
        )
        raw, _content_type = self.request(
            method,
            url,
            body,
            request_headers,
            timeout,
        )
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ProviderRequestError("Provider returned invalid JSON") from error
        if not isinstance(decoded, dict):
            raise ProviderRequestError("Provider returned an invalid JSON object")
        return decoded


def _endpoint(base_url, path):
    base = str(base_url or "").rstrip("/")
    normalized_path = "/" + str(path).lstrip("/")
    if base.endswith(normalized_path):
        return base
    return base + normalized_path


def _auth_headers(api_key, header="Authorization"):
    key = str(api_key or "").strip()
    if not key:
        return {}
    if header.lower() == "authorization":
        return {header: f"Bearer {key}"}
    return {header: key}


def _multipart(fields, file_field, filename, media_type, content):
    boundary = f"----docflow-{uuid4().hex}"
    chunks = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "document.bin"
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        (
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{safe_filename}"\r\n'
        ).encode(),
        f"Content-Type: {media_type or 'application/octet-stream'}\r\n\r\n".encode(),
        bytes(content),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class MinerUAdapter:
    """MinerU 3.x synchronous FastAPI adapter using POST /file_parse."""

    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport or HTTPClient()
        self.base_url = config.api_base_url or "http://127.0.0.1:8000"

    def parse(self, content, filename, media_type):
        if not content:
            return ""
        if "mineru.net" in self.base_url.lower():
            return self._parse_cloud(content, filename)
        body, content_type = _multipart(
            {
                "return_md": "true",
                "response_format_zip": "false",
                "return_images": "false",
                "return_middle_json": "false",
                "return_model_output": "false",
                "return_content_list": "false",
            },
            "files",
            filename,
            media_type,
            content,
        )
        raw, response_type = self.transport.request(
            "POST",
            _endpoint(self.base_url, "/file_parse"),
            body,
            {
                "Content-Type": content_type,
                "Accept": "application/json, application/zip",
                **_auth_headers(self.config.api_key),
            },
            timeout=600,
        )
        text = self._extract_response(raw, response_type)
        if not text.strip():
            raise ProviderRequestError("MinerU returned no Markdown text")
        return text

    def recognize(self, content, filename, media_type):
        return self.parse(content, filename, media_type)

    def _parse_cloud(self, content, filename):
        if not self.config.api_key:
            raise ProviderNotConfigured("MinerU cloud API token is not configured")
        root = self.base_url.split("/api/", 1)[0].rstrip("/")
        headers = _auth_headers(self.config.api_key)
        submission = self.transport.json(
            "POST",
            f"{root}/api/v4/file-urls/batch",
            {
                "files": [{"name": filename, "data_id": uuid4().hex}],
                "model_version": self.config.model or "vlm",
                "enable_table": True,
                "enable_formula": True,
                "is_ocr": True,
            },
            headers=headers,
            timeout=60,
        )
        if submission.get("code") != 0:
            raise ProviderRequestError("MinerU cloud upload request was rejected")
        data = submission.get("data") or {}
        batch_id = str(data.get("batch_id") or "")
        upload_urls = data.get("file_urls") or []
        if not batch_id or not upload_urls:
            raise ProviderRequestError("MinerU cloud did not return an upload URL")
        self.transport.request(
            "PUT",
            str(upload_urls[0]),
            bytes(content),
            {
                "Accept": "*/*",
                # urllib otherwise injects
                # application/x-www-form-urlencoded for any request body.
                # That changes the OSS V1 string-to-sign and makes MinerU's
                # presigned upload fail with SignatureDoesNotMatch.
                "Content-Type": "",
            },
            timeout=300,
        )
        deadline = time.monotonic() + 600
        result_url = (
            f"{root}/api/v4/extract-results/batch/{batch_id}"
        )
        while time.monotonic() < deadline:
            status = self.transport.json(
                "GET",
                result_url,
                None,
                headers=headers,
                timeout=60,
            )
            if status.get("code") != 0:
                raise ProviderRequestError("MinerU cloud status request failed")
            results = (status.get("data") or {}).get("extract_result") or []
            result = results[0] if results else {}
            state = str(result.get("state") or "")
            if state == "done":
                zip_url = str(result.get("full_zip_url") or "")
                if not zip_url:
                    raise ProviderRequestError(
                        "MinerU cloud result is missing the ZIP URL"
                    )
                raw, content_type = self.transport.request(
                    "GET", zip_url, None, {"Accept": "application/zip"}, 300
                )
                text = self._extract_response(raw, content_type)
                if not text.strip():
                    raise ProviderRequestError(
                        "MinerU cloud returned no Markdown text"
                    )
                return text
            if state == "failed":
                raise ProviderRequestError("MinerU cloud document parsing failed")
            time.sleep(2)
        raise ProviderRequestError("MinerU cloud document parsing timed out")

    @classmethod
    def _extract_response(cls, raw, content_type):
        if "zip" in str(content_type).lower() or raw[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    names = sorted(
                        name for name in archive.namelist()
                        if name.lower().endswith((".md", ".markdown", ".txt"))
                    )
                    return "\n\n".join(
                        archive.read(name).decode("utf-8", errors="replace")
                        for name in names
                    )
            except (zipfile.BadZipFile, OSError) as error:
                raise ProviderRequestError("MinerU returned an invalid ZIP") from error
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ProviderRequestError("MinerU returned invalid JSON") from error
        candidates = []
        cls._collect_markdown(payload, candidates)
        return "\n\n".join(item for item in candidates if item.strip())

    @classmethod
    def _collect_markdown(cls, value, output):
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    str(key).lower()
                    in {"md_content", "markdown", "md", "text", "content"}
                    and isinstance(item, str)
                ):
                    output.append(item)
                else:
                    cls._collect_markdown(item, output)
        elif isinstance(value, list):
            for item in value:
                cls._collect_markdown(item, output)


class PaddleOCRAdapter:
    """PaddleOCR official cloud API, with optional self-hosted compatibility."""

    def __init__(self, config, transport=None, client_factory=None):
        self.config = config
        self.transport = transport or HTTPClient()
        self.base_url = config.api_base_url or "official"
        self.client_factory = client_factory

    def recognize(self, content, filename, media_type):
        if not content:
            return ""
        if self.base_url.lower() in {"official", "paddleocr://official"}:
            return self._recognize_official(content, filename)
        normalized_type = str(media_type or "").split(";", 1)[0].lower()
        is_pdf = normalized_type == "application/pdf" or str(filename).lower().endswith(".pdf")
        payload = {
            "file": base64.b64encode(content).decode("ascii"),
            "fileType": 0 if is_pdf else 1,
            "useDocOrientationClassify": True,
            "useDocUnwarping": True,
            "useTextlineOrientation": True,
            "visualize": False,
        }
        response = self.transport.json(
            "POST",
            _endpoint(self.base_url, "/ocr"),
            payload,
            headers=_auth_headers(self.config.api_key),
            timeout=300,
        )
        if response.get("errorCode") not in (None, 0):
            raise ProviderRequestError(
                f"PaddleOCR rejected the request: {response.get('errorMsg') or 'unknown error'}"
            )
        pages = (response.get("result") or {}).get("ocrResults")
        if not isinstance(pages, list):
            raise ProviderRequestError("PaddleOCR response is missing ocrResults")
        lines = []
        for page in pages:
            source = page.get("prunedResult", page) if isinstance(page, dict) else page
            self._collect_text(source, lines, preferred=True)
        return "\n".join(self._deduplicate(lines))

    def _recognize_official(self, content, filename):
        if not self.config.api_key:
            raise ProviderNotConfigured(
                "PaddleOCR official API access token is not configured"
            )
        factory = self.client_factory
        if factory is None:
            try:
                from paddleocr import PaddleOCRClient
            except ImportError as error:
                raise ProviderNotConfigured(
                    "PaddleOCR SDK is not installed; install the providers extra"
                ) from error
            factory = PaddleOCRClient
        client = factory(
            token=self.config.api_key,
            request_timeout=300.0,
            poll_timeout=600.0,
        )
        suffix = Path(filename).suffix or ".bin"
        temporary_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                prefix="docflow-paddle-", suffix=suffix, delete=False
            ) as temporary:
                temporary.write(content)
                temporary_path = temporary.name
            result = client.ocr(
                file_path=temporary_path,
                model=self.config.model or "PP-OCRv6",
            )
            lines = []
            for page in getattr(result, "pages", []) or []:
                source = self._page_payload(page)
                if isinstance(source, dict):
                    source = source.get(
                        "prunedResult",
                        source.get("pruned_result", source),
                    )
                self._collect_text(source, lines, preferred=True)
            return "\n".join(self._deduplicate(lines))
        except ProviderNotConfigured:
            raise
        except Exception as error:
            raise ProviderRequestError(
                f"PaddleOCR official API failed: {type(error).__name__}"
            ) from error
        finally:
            try:
                client.close()
            except Exception:
                pass
            if temporary_path:
                try:
                    Path(temporary_path).unlink()
                except OSError:
                    pass

    @staticmethod
    def _page_payload(page):
        if isinstance(page, dict):
            return page
        for attribute in ("pruned_result", "prunedResult", "json"):
            value = getattr(page, attribute, None)
            if value is not None:
                return value
        for method_name in ("model_dump", "dict"):
            method = getattr(page, method_name, None)
            if callable(method):
                value = method()
                if isinstance(value, dict):
                    return value
        return {}

    @classmethod
    def _collect_text(cls, value, output, preferred=False):
        if isinstance(value, dict):
            preferred_keys = {
                "rec_texts", "rectexts", "rec_text", "text", "texts", "label"
            }
            for key, item in value.items():
                normalized = str(key).replace("-", "_").lower()
                if normalized in preferred_keys:
                    cls._collect_text(item, output, preferred=True)
                elif isinstance(item, (dict, list)):
                    cls._collect_text(item, output, preferred=False)
        elif isinstance(value, list):
            for item in value:
                cls._collect_text(item, output, preferred=preferred)
        elif preferred and isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text and len(text) <= 10000:
                output.append(text)

    @staticmethod
    def _deduplicate(lines):
        output = []
        for line in lines:
            if not output or output[-1] != line:
                output.append(line)
        return output


class DeepSeekAdapter:
    """DeepSeek V4 adapter for extraction, review, translation, and transliteration."""

    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport or HTTPClient()
        self.base_url = config.api_base_url or "https://api.deepseek.com"
        self.model = config.model or "deepseek-v4-flash"

    def extract(self, text, document_type, filename):
        schemas = {
            field_id: schema.label
            for field_id, schema in DEFAULT_FIELD_SCHEMAS.items()
        }
        payload = self._json_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Extract visa-document fields as strict JSON. Never infer missing "
                        "facts. Omit absent fields entirely; never return empty placeholder "
                        "fields. Normalize dates to YYYY-MM-DD and sex to MALE, FEMALE, X, "
                        "or UNSPECIFIED. contact.phone, contact.email, and contact.address "
                        "refer only to the U.S. contact or U.S. stay details, never the "
                        "applicant's own phone, email, or home address. Every field must "
                        "contain an exact source excerpt. Return "
                        '{"fields":[{"id":"...","value":"...","confidence":0.0,'
                        '"evidence":[{"excerpt":"...","page":1}]}]}.'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "documentType": document_type,
                        "filename": filename,
                        "allowedFields": schemas,
                        "sourceText": str(text)[:120000],
                    }, ensure_ascii=False),
                },
            ],
            max_tokens=12000,
        )
        fields = []
        for item in payload.get("fields") or []:
            if not isinstance(item, dict):
                continue
            evidence = []
            for source in item.get("evidence") or []:
                if not isinstance(source, dict):
                    continue
                evidence.append(Evidence(
                    document_id="untrusted-model-output",
                    filename=filename,
                    page=max(1, int(source.get("page") or 1)),
                    excerpt=str(source.get("excerpt") or "")[:500],
                    method="deepseek-extraction",
                ))
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                confidence = -1.0
            fields.append(ExtractedField(
                id=str(item.get("id") or ""),
                value=str(item.get("value") or ""),
                confidence=confidence,
                evidence=evidence,
                alternatives=[
                    str(value) for value in item.get("alternatives") or []
                ][:5],
            ))
        return fields

    def review(self, fields, document_type):
        response = self._json_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Review extracted visa fields for contradictions or suspicious "
                        "values. Do not approve, modify, or add facts. Return strict JSON "
                        'as {"warnings":["..."]}; return an empty list when clean.'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "documentType": document_type,
                        "fields": [
                            {
                                "id": item.id,
                                "value": item.value,
                                "confidence": item.confidence,
                                "evidence": [
                                    evidence.excerpt for evidence in item.evidence[:3]
                                ],
                            }
                            for item in fields
                        ],
                    }, ensure_ascii=False),
                },
            ],
            max_tokens=3000,
        )
        return [
            str(item)[:1000]
            for item in response.get("warnings") or []
            if str(item).strip()
        ][:50]

    def review_action(self, action, before, after):
        response = self._json_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Independently review whether deterministic browser evidence "
                        "supports the claimed action. Never rely on model confidence. "
                        'Return strict JSON as {"verified":true|false,"reason":"..."}.'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "action": {
                            "kind": action.kind.value,
                            "fieldId": action.field_id,
                            "target": action.target_hint,
                        },
                        "before": {
                            "url": before.url,
                            "title": before.title,
                        },
                        "after": {
                            "url": after.url,
                            "title": after.title,
                            "fieldValue": (
                                after.control_values.get(action.field_id)
                                if action.field_id else None
                            ),
                            "errors": after.errors,
                            "acknowledged": action.id in after.acknowledged_action_ids,
                        },
                    }, ensure_ascii=False),
                },
            ],
            max_tokens=1000,
        )
        return response.get("verified") is True

    def translate(self, text, source_language, target_language):
        response = self._json_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Translate faithfully without adding facts. Preserve names, dates, "
                        "identifiers, and formatting. Return strict JSON as {\"text\":\"...\"}."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "sourceLanguage": source_language,
                        "targetLanguage": target_language,
                        "text": text,
                    }, ensure_ascii=False),
                },
            ],
            max_tokens=12000,
        )
        return str(response.get("text") or "")

    def transliterate(self, text, source_language, target_script="Latn"):
        response = self._json_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Transliterate only; do not translate meaning or add facts. "
                        "Preserve identifiers and punctuation. Return strict JSON as "
                        '{"text":"..."}.'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "sourceLanguage": source_language,
                        "targetScript": target_script,
                        "text": text,
                    }, ensure_ascii=False),
                },
            ],
            max_tokens=12000,
        )
        return str(response.get("text") or "")

    def _json_chat(self, messages, max_tokens):
        if not self.config.api_key:
            raise ProviderNotConfigured("DeepSeek API key is not configured")
        response = self.transport.json(
            "POST",
            _endpoint(self.base_url, "/chat/completions"),
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "temperature": 0,
                "max_tokens": max_tokens,
                "thinking": {"type": "disabled"},
                "response_format": {"type": "json_object"},
            },
            headers=_auth_headers(self.config.api_key),
            timeout=180,
        )
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderRequestError("DeepSeek response is missing message content") from error
        if isinstance(content, dict):
            return content
        text = str(content).strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as error:
            raise ProviderRequestError("DeepSeek returned invalid JSON content") from error
        if not isinstance(decoded, dict):
            raise ProviderRequestError("DeepSeek returned a non-object JSON result")
        return decoded


class GeminiComputerUseAdapter:
    """Stateful Gemini Computer Use loop with system-owned field values."""

    # One interaction plans a whole visible page.  Computer-use generations
    # with a screenshot routinely finish just beyond the old 22-second read
    # timeout, so cancelling at 22 seconds and starting an 8-second request
    # from scratch guaranteed repeated failures for otherwise healthy calls.
    # Give the original generation one complete page-level window.  The short
    # second attempt remains useful for fast transport resets, but a fully
    # elapsed provider timeout is surfaced to the workflow instead of wasting
    # another request that cannot possibly finish in the recovery window.
    PRIMARY_PLANNING_TIMEOUT_SECONDS = 35
    RECOVERY_PLANNING_TIMEOUT_SECONDS = 6
    PLANNING_RETRY_BACKOFF_SECONDS = 0.5
    PLANNING_TOTAL_BUDGET_SECONDS = (
        PRIMARY_PLANNING_TIMEOUT_SECONDS
        + RECOVERY_PLANNING_TIMEOUT_SECONDS
        + PLANNING_RETRY_BACKOFF_SECONDS
    )

    SAFE_BROWSER_KEYS = {
        "tab": "Tab",
        "arrowup": "ArrowUp",
        "arrowdown": "ArrowDown",
        "arrowleft": "ArrowLeft",
        "arrowright": "ArrowRight",
        "escape": "Escape",
        "esc": "Escape",
        "home": "Home",
        "end": "End",
        "pageup": "PageUp",
        "pagedown": "PageDown",
    }

    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport or HTTPClient()
        self.base_url = config.api_base_url or "https://generativelanguage.googleapis.com"
        self.model = config.model or "gemini-3.6-flash"
        self.focused_field_id = ""
        self.interaction_count = 0
        self.request_count = 0
        self._previous_interaction_id = ""
        self._pending_function_call = None
        self._continuation_input = None
        self._page_context = {}
        self._correction_context = {}
        self._status_callback = None

    def set_status_callback(self, callback):
        """Publish provider wait/retry phases without exposing request data."""
        self._status_callback = callback if callable(callback) else None

    def _publish_status(self, state, message):
        if self._status_callback is None:
            return
        try:
            self._status_callback(state, message)
        except Exception:
            pass

    def _record_usage_event(self, event, **details):
        """Append privacy-safe Gemini request accounting when enabled."""
        log_path = os.environ.get("GEMINI_USAGE_LOG_PATH", "").strip()
        if not log_path:
            return
        record = {
            "timestamp": time.time(),
            "event": str(event),
            "model": self.model,
            "interaction_count": self.interaction_count,
            "request_count": self.request_count,
            **details,
        }
        try:
            with open(log_path, "a", encoding="utf-8") as usage_log:
                usage_log.write(
                    json.dumps(record, ensure_ascii=False, sort_keys=True)
                    + "\n"
                )
        except (OSError, TypeError, ValueError):
            # Accounting must never interrupt or alter a form-filling run.
            pass

    def set_page_context(self, context):
        self._page_context = {
            str(field_id): {
                "label": str((details or {}).get("label") or "")[:500],
            }
            for field_id, details in dict(context or {}).items()
        }

    def propose_action(self, observation, available_field_ids, completed_field_ids):
        if not self.config.api_key:
            raise ProviderNotConfigured("Gemini API key is not configured")
        if self.focused_field_id in completed_field_ids:
            self.focused_field_id = ""
        tools = [{
            "type": "computer_use",
            "environment": "browser",
            "enable_prompt_injection_detection": True,
        }]
        if self._continuation_input is not None and self._previous_interaction_id:
            request = {
                "model": self.model,
                "previous_interaction_id": self._previous_interaction_id,
                "input": self._continuation_input,
                "tools": tools,
                "store": True,
            }
            self._continuation_input = None
        else:
            instructions = (
                "Operate this browser visually to fill only the approved field IDs "
                "listed below. Inspect the screenshot, then return exactly one native "
                "computer-use action. Use mouse clicks and keyboard typing; do not "
                "request DOM selectors or batch field injection. Never invent a value. "
                "For keyboard navigation, use only Tab, Shift+Tab, arrow keys, "
                "Escape, Home, End, PageUp, or PageDown. Never press Enter or Space; "
                "click an approved visible field control instead. "
                "For every click or type involving a field, include the exact marker "
                "[field_id=THE.EXACT.ID] in intent. The client replaces any proposed "
                "typed text with the human-approved value. If Current focused field ID "
                "is non-empty and not completed, do not click it again; issue the type "
                "action for that field. Re-observe after every action and correct "
                "ordinary UI mistakes. Confirmed history/background questions "
                "listed in Approved field IDs are allowed; never infer or touch "
                "an unlisted answer. Stop for CAPTCHA, login, signature, payment, "
                "or final submission.\n"
                "Never start or create an application, retrieve an application, "
                "or operate a CEAC landing-page control. Work only inside the "
                "existing application already opened by the consultant.\n"
                f"Approved field IDs: {json.dumps(available_field_ids)}\n"
                "Pending fields on this page (labels include the exact "
                "human-approved choice for choice controls): "
                f"{json.dumps(self._page_context, ensure_ascii=False)}\n"
                f"Completed field IDs: {json.dumps(completed_field_ids)}\n"
                f"Current focused field ID: {self.focused_field_id}\n"
                f"Current URL: {observation.url}"
            )
            request = {
                "model": self.model,
                "input": [
                    {"type": "text", "text": instructions},
                    *self._observation_blocks(observation),
                ],
                "tools": tools,
                # Stateful continuation is required for the native function-result
                # screenshot loop documented by the Gemini Interactions API.
                "store": True,
            }
        response = self._request(request)
        self._previous_interaction_id = str(response.get("id") or "")
        return self._to_action(
            response,
            available_field_ids,
            completed_field_ids,
        )

    def record_action_result(
        self,
        action,
        before,
        after,
        verified=True,
        error="",
    ):
        """Queue the executed action and fresh screenshot for the next turn."""
        if not verified and action.field_id:
            self._correction_context = {
                "field_id": str(action.field_id),
                "error": str(error or "verification failed")[:300],
            }
        elif (
            verified
            and action.field_id
            and self._correction_context.get("field_id") == action.field_id
        ):
            self._correction_context = {}
        pending = self._pending_function_call
        self._pending_function_call = None
        if not pending or not self._previous_interaction_id:
            return
        screenshot, mime_type = self._screenshot_data(after.screenshot_ref)
        result = [{
            "type": "text",
            "text": json.dumps(
                {
                    "url": after.url,
                    "status": "success" if verified else "verification_failed",
                    "field_id": action.field_id,
                    "error": str(error or "")[:500],
                },
                ensure_ascii=False,
            ),
        }]
        if screenshot:
            result.append({
                "type": "image",
                "data": screenshot,
                "mime_type": mime_type,
            })
        self._continuation_input = [{
            "type": "function_result",
            "name": pending["name"],
            "call_id": pending["call_id"],
            "result": result,
        }]

    def propose_actions(
        self,
        observation,
        available_field_ids,
        completed_field_ids,
        page_field_ids=None,
    ):
        """Plan all visible pending fields in one Gemini interaction.

        The model returns coordinates and control kinds only. Approved values
        remain system-owned and are injected by ComputerUseAgent.
        """
        if not self.config.api_key:
            raise ProviderNotConfigured("Gemini API key is not configured")
        available = list(dict.fromkeys(str(item) for item in available_field_ids))
        page_fields = [
            field_id
            for field_id in list(page_field_ids or available)
            if field_id in available
        ]
        completed = set(str(item) for item in completed_field_ids)
        pending = [
            field_id for field_id in page_fields
            if field_id not in completed
        ]
        if not pending:
            return [self.propose_action(
                observation,
                available,
                list(completed_field_ids),
            )]

        batch_tool = {
            "type": "function",
            "name": "fill_page_fields",
            "description": (
                "Plan one safe batch containing every currently visible pending "
                "approved form field. Return coordinates and control kind only. "
                "Use click only for an approved ensure-repeater/Add Another "
                "field. Never return or invent field values."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": min(20, len(pending)),
                        "items": {
                            "type": "object",
                            "properties": {
                                "field_id": {
                                    "type": "string",
                                    "enum": pending,
                                },
                                "control_kind": {
                                    "type": "string",
                                    "enum": ["type", "select", "click"],
                                },
                                "x": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 999,
                                },
                                "y": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 999,
                                },
                            },
                            "required": [
                                "field_id", "control_kind", "x", "y",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "reason": {
                        "type": "string",
                        "maxLength": 500,
                    },
                },
                "required": ["fields"],
                "additionalProperties": False,
            },
        }
        tools = [
            {
                "type": "computer_use",
                "environment": "browser",
                "enable_prompt_injection_detection": True,
                # Field entry is handled by the value-free batch tool. Scroll,
                # wait, and navigation actions remain available when needed.
                "excluded_predefined_functions": [
                    "click", "type", "click_at", "type_text_at",
                ],
            },
            batch_tool,
        ]
        instructions = (
            "Use fill_page_fields exactly once to identify every currently visible "
            "pending approved field on this page. Prefer one batch over individual "
            "computer-use actions. Do not include field values; the client owns and "
            "injects all approved values. Use normalized 0-999 screenshot coordinates "
            "inside the actual input/select control. Choose control_kind=select only "
            "for a real select/radio/checkbox control. Choose control_kind=click only "
            "for a pending approved ensure-repeater/Add Another field; otherwise use "
            "type. Omit fields not currently "
            "visible. The rendered_page_text observation covers the whole rendered "
            "document, not only the screenshot viewport. Scroll only when a pending "
            "field is present in rendered_page_text but is outside the screenshot. "
            "Never repeat a same-direction scroll when the page did not move or is "
            "already at that edge. "
            "Confirmed history/background questions listed in Pending current-page "
            "field IDs are allowed; never infer or touch an unlisted answer. Stop "
            "for CAPTCHA, login, signature, payment, or final submission.\n"
            "Never start or create an application, retrieve an application, or "
            "operate a CEAC landing-page control. Work only inside the existing "
            "application already opened by the consultant.\n"
            f"All approved field IDs: {json.dumps(available)}\n"
            f"Current-page field IDs: {json.dumps(page_fields)}\n"
            f"Pending current-page field IDs: {json.dumps(pending)}\n"
            f"Completed field IDs: {json.dumps(list(completed_field_ids))}\n"
            f"Current URL: {observation.url}\n"
            + (
                "The previous coordinate/control choice for "
                f"{self._correction_context.get('field_id')} failed "
                "deterministic browser verification: "
                f"{self._correction_context.get('error')}. "
                "Choose the actual matching control at a corrected coordinate; "
                "do not repeat the same visual target.\n"
                if self._correction_context else ""
            )
        )
        request = {
            "model": self.model,
            "input": [
                {"type": "text", "text": instructions},
                *self._observation_blocks(observation),
            ],
            "tools": tools,
            "store": False,
        }
        response = self._request(request)
        return self._to_actions(
            response,
            available,
            list(completed_field_ids),
            pending,
        )

    def _request(self, request):
        self.interaction_count += 1
        response = None
        attempt_timeouts = (
            self.PRIMARY_PLANNING_TIMEOUT_SECONDS,
            self.RECOVERY_PLANNING_TIMEOUT_SECONDS,
        )
        max_attempts = len(attempt_timeouts)
        for attempt, request_timeout in enumerate(attempt_timeouts):
            total_budget_seconds = math.ceil(
                self.PLANNING_TOTAL_BUDGET_SECONDS
            )
            self._publish_status(
                "thinking",
                (
                    "正在等待 Gemini 返回本页批量规划"
                    f"（单页最多约 {total_budget_seconds} 秒）"
                    if attempt == 0
                    else (
                        "网络响应中断，正在进行一次短恢复请求 "
                        f"{attempt + 1}/{max_attempts}"
                        f"（最多 {request_timeout} 秒）"
                    )
                ),
            )
            try:
                self.request_count += 1
                self._record_usage_event(
                    "request_attempt",
                    attempt=attempt + 1,
                    timeout_seconds=request_timeout,
                )
                response = self.transport.json(
                    "POST",
                    _endpoint(self.base_url, "/v1beta/interactions"),
                    request,
                    headers=_auth_headers(self.config.api_key, "x-goog-api-key"),
                    # A page-level batch should normally return quickly.
                    # Bound the complete primary+recovery budget so a degraded
                    # provider cannot recreate a one-minute-per-field freeze.
                    timeout=request_timeout,
                )
                usage = response.get("usage")
                self._record_usage_event(
                    "request_success",
                    attempt=attempt + 1,
                    usage=usage if isinstance(usage, dict) else {},
                )
                break
            except ProviderRequestError as error:
                self._record_usage_event(
                    "request_error",
                    attempt=attempt + 1,
                    retryable=bool(error.retryable),
                    status_code=error.status_code,
                    error_type=type(error).__name__,
                )
                if not error.retryable:
                    error.provider_retry_exhausted = True
                    self._publish_status(
                        "error",
                        "Gemini 请求被拒绝，已停止无效重试",
                    )
                    raise
                normalized_error = str(error or "").casefold()
                primary_window_elapsed = (
                    attempt == 0
                    and (
                        "timeout" in normalized_error
                        or "timed out" in normalized_error
                    )
                )
                if primary_window_elapsed:
                    error.provider_retry_exhausted = True
                    self._publish_status(
                        "error",
                        "Gemini 本页规划超过时限，系统将自动重试；"
                        "网页保持不动",
                    )
                    raise
                if attempt == max_attempts - 1:
                    error.provider_retry_exhausted = True
                    self._publish_status(
                        "error",
                        "Gemini 连续两次未返回可用响应",
                    )
                    raise
                time.sleep(self.PLANNING_RETRY_BACKOFF_SECONDS)
        self._publish_status("working", "Gemini 已返回本页动作规划")
        return response

    def verify_action(self, action, before, after):
        # System deterministic verification is authoritative. Gemini does not
        # self-certify its own action when no independent reviewer is configured.
        return True

    def _to_actions(
        self,
        response,
        available_field_ids,
        completed_field_ids,
        pending_field_ids,
    ):
        safety = str(response.get("safety_decision") or "").lower()
        if "block" in safety:
            return [ComputerAction(
                kind=ActionKind.PAUSE,
                reason=f"Gemini safety decision: {safety}",
            )]
        steps = response.get("steps") or []
        calls = [
            step for step in steps
            if isinstance(step, dict) and step.get("type") == "function_call"
        ]
        batch_calls = [
            call for call in calls
            if str(call.get("name") or "") == "fill_page_fields"
        ]
        if batch_calls:
            if len(calls) != 1 or len(batch_calls) != 1:
                return [ComputerAction(
                    kind=ActionKind.PAUSE,
                    reason="Gemini mixed a page batch with other actions",
                )]
            return self._parse_page_batch(
                batch_calls[0],
                pending_field_ids,
            )
        return [self._to_action(
            response,
            available_field_ids,
            completed_field_ids,
        )]

    def _parse_page_batch(self, call, pending_field_ids):
        arguments = call.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        rows = arguments.get("fields")
        if not isinstance(rows, list) or not rows or len(rows) > 20:
            return [ComputerAction(
                kind=ActionKind.PAUSE,
                reason="Gemini returned an invalid or empty page field batch",
            )]
        pending = set(pending_field_ids)
        reason = str(arguments.get("reason") or "Gemini page batch")[:500]
        seen = set()
        actions = []
        for row in rows:
            if not isinstance(row, dict):
                return [ComputerAction(
                    kind=ActionKind.PAUSE,
                    reason="Gemini page batch contains an invalid field entry",
                )]
            field_id = str(row.get("field_id") or "")
            control_kind = str(row.get("control_kind") or "").lower()
            x = self._coordinate(row.get("x"))
            y = self._coordinate(row.get("y"))
            page_label = str(
                (self._page_context.get(field_id) or {}).get("label")
                or ""
            ).casefold()
            if (
                field_id not in pending
                or field_id in seen
                or control_kind not in {"type", "select", "click"}
                or (
                    control_kind == "click"
                    and "[control=ensure_repeater" not in page_label
                )
                or x is None
                or y is None
            ):
                return [ComputerAction(
                    kind=ActionKind.PAUSE,
                    reason="Gemini page batch failed field allowlist validation",
                )]
            seen.add(field_id)
            actions.append(ComputerAction(
                kind=(
                    ActionKind.CLICK
                    if control_kind == "click"
                    else ActionKind.SELECT
                    if control_kind == "select"
                    else ActionKind.TYPE
                ),
                field_id=field_id,
                target_hint=field_id,
                reason=f"{reason} [field_id={field_id}]",
                coordinate_x=x,
                coordinate_y=y,
            ))
        return actions

    def _to_action(
        self,
        response,
        available_field_ids,
        completed_field_ids=(),
    ):
        safety = str(response.get("safety_decision") or "").lower()
        if "block" in safety:
            return ComputerAction(
                kind=ActionKind.PAUSE,
                reason=f"Gemini safety decision: {safety}",
            )
        steps = response.get("steps") or []
        calls = [
            step for step in steps
            if isinstance(step, dict) and step.get("type") == "function_call"
        ]
        if len(calls) > 1:
            return ComputerAction(
                kind=ActionKind.PAUSE,
                reason="Gemini returned multiple actions in one interaction",
            )
        if not calls:
            text = self._model_text(steps)
            if re.search(r"\b(done|complete|completed|finished)\b", text, re.IGNORECASE):
                return ComputerAction(kind=ActionKind.COMPLETE, reason=text[:1000])
            return ComputerAction(
                kind=ActionKind.PAUSE,
                reason=text[:1000] or "Gemini returned no executable action",
            )
        call = calls[0]
        name = str(call.get("name") or "")
        arguments = call.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        call_safety = arguments.get("safety_decision") or {}
        if isinstance(call_safety, dict):
            decision = str(
                call_safety.get("decision")
                or call_safety.get("status")
                or ""
            ).lower()
            if "block" in decision:
                return ComputerAction(
                    kind=ActionKind.PAUSE,
                    reason=(
                        "Gemini safety decision: "
                        + str(
                            call_safety.get("explanation")
                            or decision
                        )[:900]
                    ),
                )
        call_id = str(call.get("id") or call.get("call_id") or "")
        self._pending_function_call = (
            {"name": name, "call_id": call_id}
            if name and call_id else None
        )
        intent = str(arguments.get("intent") or "")
        field_id = self._field_id(intent, available_field_ids)
        x = self._coordinate(arguments.get("x"))
        y = self._coordinate(arguments.get("y"))
        if name in {"click", "click_at", "double_click", "triple_click"}:
            if field_id:
                self.focused_field_id = field_id
                target = field_id
            else:
                target = self._button_target(intent)
            if not target or x is None or y is None:
                return ComputerAction(
                    kind=ActionKind.PAUSE,
                    reason="Gemini click lacked an approved field/button or coordinates",
                )
            return ComputerAction(
                kind=ActionKind.CLICK,
                field_id=field_id,
                target_hint=target,
                reason=intent,
                coordinate_x=x,
                coordinate_y=y,
            )
        if name in {"type", "type_text_at"}:
            field_id = field_id or self.focused_field_id
            if not field_id:
                return ComputerAction(
                    kind=ActionKind.PAUSE,
                    reason="Gemini type action did not identify an approved field",
                )
            return ComputerAction(
                kind=ActionKind.TYPE,
                field_id=field_id,
                target_hint=field_id,
                reason=intent,
                coordinate_x=x,
                coordinate_y=y,
            )
        if name == "scroll":
            direction = str(arguments.get("direction") or "down").lower()
            amount = int(arguments.get("magnitude_in_pixels") or 300)
            return ComputerAction(
                kind=ActionKind.SCROLL,
                reason=intent,
                coordinate_x=x,
                coordinate_y=y,
                scroll_direction=direction,
                scroll_amount=max(1, min(2000, amount)),
            )
        if name == "press_key":
            key = self._safe_browser_key(arguments.get("key"))
            if not key:
                return ComputerAction(
                    kind=ActionKind.PAUSE,
                    reason="Gemini requested a key outside the safe browser allowlist",
                )
            return ComputerAction(
                kind=ActionKind.PRESS_KEY,
                value=key,
                reason=intent or f"Press {key}",
            )
        if name in {"hotkey", "key_combination"}:
            keys = arguments.get("keys")
            if isinstance(keys, str):
                raw_keys = re.split(r"\s*\+\s*", keys)
            elif isinstance(keys, (list, tuple)):
                raw_keys = [str(item) for item in keys]
            else:
                raw_keys = []
            normalized = [str(item).strip().casefold() for item in raw_keys]
            if normalized not in (["shift", "tab"], ["tab", "shift"]):
                return ComputerAction(
                    kind=ActionKind.PAUSE,
                    reason="Gemini requested a hotkey outside the safe browser allowlist",
                )
            return ComputerAction(
                kind=ActionKind.PRESS_KEY,
                value="Shift+Tab",
                reason=intent or "Press Shift+Tab",
            )
        if name in {"wait", "wait_5_seconds"}:
            all_completed = (
                bool(available_field_ids)
                and set(available_field_ids).issubset(completed_field_ids)
            )
            if all_completed and re.search(
                r"\ball\b.{0,80}\b(?:complete|completed|filled|finished)\b",
                intent,
                flags=re.IGNORECASE,
            ):
                return ComputerAction(
                    kind=ActionKind.COMPLETE,
                    reason=intent,
                )
            return ComputerAction(kind=ActionKind.WAIT, reason=intent or name)
        if name in {"take_screenshot", "open_web_browser"}:
            return ComputerAction(kind=ActionKind.WAIT, reason=intent or name)
        if name in {"navigate", "open_url"}:
            return ComputerAction(
                kind=ActionKind.NAVIGATE,
                value=str(arguments.get("url") or ""),
                reason=intent,
            )
        return ComputerAction(
            kind=ActionKind.PAUSE,
            reason=f"Unsupported Gemini computer-use action: {name}",
        )

    @staticmethod
    def _coordinate(value):
        if isinstance(value, bool):
            return None
        try:
            converted = int(value)
        except (TypeError, ValueError):
            return None
        return converted if 0 <= converted <= 999 else None

    @classmethod
    def _safe_browser_key(cls, value):
        normalized = re.sub(r"[\s_-]+", "", str(value or "")).casefold()
        return cls.SAFE_BROWSER_KEYS.get(normalized, "")

    @staticmethod
    def _field_id(intent, available_field_ids):
        marker = re.search(r"\[field_id=([A-Za-z0-9_.-]+)\]", intent)
        if marker and marker.group(1) in available_field_ids:
            return marker.group(1)
        return next(
            (field_id for field_id in available_field_ids if field_id in intent),
            "",
        )

    @staticmethod
    def _button_target(intent):
        for label in ("Continue", "Next", "Save", "Back", "Previous"):
            if re.search(rf"\b{label}\b", intent, re.IGNORECASE):
                return label
        return ""

    @staticmethod
    def _model_text(steps):
        output = []
        for step in steps:
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            for block in step.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    output.append(str(block.get("text") or ""))
        return " ".join(output).strip()

    @staticmethod
    def _observation_blocks(observation):
        summary = {
            "url": observation.url,
            "title": observation.title,
            "errors": observation.errors,
            "result": "current browser state",
            # ``innerText`` is already bounded by the browser adapter and is
            # the same rendered document Gemini is authorized to inspect.
            # Supplying it avoids blind viewport-by-viewport searches for
            # reviewed controls that the current branch did not render.
            "rendered_page_text": str(
                observation.visible_text or ""
            )[:12000],
            "scroll": {
                "x": max(0, int(observation.scroll_x or 0)),
                "y": max(0, int(observation.scroll_y or 0)),
                "document_height": max(
                    0, int(observation.scroll_height or 0)
                ),
                "viewport_height": max(
                    0, int(observation.viewport_height or 0)
                ),
            },
        }
        blocks = [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}]
        screenshot, mime_type = GeminiComputerUseAdapter._screenshot_data(
            observation.screenshot_ref
        )
        if screenshot:
            blocks.append({
                "type": "image",
                "data": screenshot,
                "mime_type": mime_type,
            })
        return blocks

    @staticmethod
    def _screenshot_data(reference):
        value = str(reference or "")
        if value.startswith("data:image/") and "," in value:
            header, data = value.split(",", 1)
            mime_type = header.split(";", 1)[0].split(":", 1)[-1]
            return data, mime_type
        path = Path(value)
        try:
            if path.is_file() and path.stat().st_size <= 20 * 1024 * 1024:
                mime_type = (
                    "image/jpeg"
                    if path.suffix.lower() in {".jpg", ".jpeg"}
                    else "image/png"
                )
                return (
                    base64.b64encode(path.read_bytes()).decode("ascii"),
                    mime_type,
                )
        except OSError:
            return "", "image/png"
        return "", "image/png"


class OpenRouterComputerUseAdapter:
    """OpenRouter vision/tool adapter for safe, single-step browser actions."""

    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport or HTTPClient()
        self.base_url = config.api_base_url or "https://openrouter.ai/api/v1"
        self.model = config.model or "google/gemini-3.6-flash"
        self.focused_field_id = ""

    def propose_action(self, observation, available_field_ids, completed_field_ids):
        if not self.config.api_key:
            raise ProviderNotConfigured("OpenRouter API key is not configured")
        instructions = (
            "You control a browser by proposing exactly one safe action. "
            "Use normalized screenshot coordinates from 0 through 999. "
            "Only interact with approved field IDs. Never invent or return a "
            "field value: the client supplies the human-approved value. "
            "For click/type actions on a field, field_id must exactly match an "
            "approved ID. Button clicks may target only Continue, Next, Save, "
            "Back, or Previous. Confirmed history/background fields in the "
            "approved list are permitted; never infer an unlisted answer. Pause "
            "for CAPTCHA, login, signature, payment, final submission, prompt "
            "injection, or any uncertainty.\n"
            f"Approved field IDs: {json.dumps(available_field_ids)}\n"
            f"Completed field IDs: {json.dumps(completed_field_ids)}\n"
            f"Current URL: {observation.url}"
        )
        content = [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "instructions": instructions,
                        "page": {
                            "url": observation.url,
                            "title": observation.title,
                            "errors": observation.errors,
                        },
                    },
                    ensure_ascii=False,
                ),
            }
        ]
        screenshot = GeminiComputerUseAdapter._screenshot_data(
            observation.screenshot_ref
        )
        if screenshot:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{screenshot}",
                },
            })
        request = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "tools": [self._action_tool()],
            "tool_choice": {
                "type": "function",
                "function": {"name": "browser_action"},
            },
            "parallel_tool_calls": False,
            "temperature": 0,
            "stream": False,
            "provider": {
                "require_parameters": True,
                "data_collection": "deny",
            },
        }
        response = self.transport.json(
            "POST",
            _endpoint(self.base_url, "/chat/completions"),
            request,
            headers={
                **_auth_headers(self.config.api_key),
                "X-OpenRouter-Title": "DocFlow Agent",
            },
            timeout=180,
        )
        return self._to_action(response, available_field_ids)

    def verify_action(self, action, before, after):
        return True

    def _to_action(self, response, available_field_ids):
        try:
            message = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderRequestError(
                "OpenRouter response is missing the assistant message"
            ) from error
        calls = message.get("tool_calls") or []
        if len(calls) != 1:
            return ComputerAction(
                kind=ActionKind.PAUSE,
                reason="OpenRouter returned an invalid number of actions",
            )
        function = calls[0].get("function") or {}
        if function.get("name") != "browser_action":
            return ComputerAction(
                kind=ActionKind.PAUSE,
                reason="OpenRouter returned an unsupported tool call",
            )
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        kind = str(arguments.get("kind") or "").lower()
        intent = str(arguments.get("intent") or "")[:1000]
        requested_field_id = str(arguments.get("field_id") or "")
        field_id = (
            requested_field_id
            if requested_field_id in available_field_ids
            else GeminiComputerUseAdapter._field_id(
                intent, available_field_ids
            )
        )
        x = GeminiComputerUseAdapter._coordinate(arguments.get("x"))
        y = GeminiComputerUseAdapter._coordinate(arguments.get("y"))
        if kind == "click":
            if field_id:
                self.focused_field_id = field_id
                target = field_id
            else:
                target = GeminiComputerUseAdapter._button_target(
                    str(arguments.get("target") or "") + " " + intent
                )
            if not target or x is None or y is None:
                return ComputerAction(
                    kind=ActionKind.PAUSE,
                    reason="OpenRouter click lacked an approved target or coordinates",
                )
            return ComputerAction(
                kind=ActionKind.CLICK,
                field_id=field_id,
                target_hint=target,
                reason=intent,
                coordinate_x=x,
                coordinate_y=y,
            )
        if kind == "type":
            field_id = field_id or self.focused_field_id
            if not field_id:
                return ComputerAction(
                    kind=ActionKind.PAUSE,
                    reason="OpenRouter type action lacked an approved field",
                )
            return ComputerAction(
                kind=ActionKind.TYPE,
                field_id=field_id,
                target_hint=field_id,
                reason=intent,
                coordinate_x=x,
                coordinate_y=y,
            )
        if kind == "scroll":
            direction = str(arguments.get("direction") or "down").lower()
            try:
                amount = int(arguments.get("amount") or 300)
            except (TypeError, ValueError):
                amount = 300
            return ComputerAction(
                kind=ActionKind.SCROLL,
                reason=intent,
                coordinate_x=x,
                coordinate_y=y,
                scroll_direction=direction,
                scroll_amount=max(1, min(2000, amount)),
            )
        if kind == "navigate":
            return ComputerAction(
                kind=ActionKind.NAVIGATE,
                value=str(arguments.get("url") or ""),
                reason=intent,
            )
        if kind == "wait":
            return ComputerAction(kind=ActionKind.WAIT, reason=intent)
        if kind == "complete":
            return ComputerAction(kind=ActionKind.COMPLETE, reason=intent)
        return ComputerAction(
            kind=ActionKind.PAUSE,
            reason=intent or "OpenRouter requested a pause",
        )

    @staticmethod
    def _action_tool():
        return {
            "type": "function",
            "function": {
                "name": "browser_action",
                "description": "Propose exactly one safe browser action.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "click",
                                "type",
                                "scroll",
                                "navigate",
                                "wait",
                                "complete",
                                "pause",
                            ],
                        },
                        "field_id": {"type": "string"},
                        "target": {"type": "string"},
                        "x": {"type": "integer", "minimum": 0, "maximum": 999},
                        "y": {"type": "integer", "minimum": 0, "maximum": 999},
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down", "left", "right"],
                        },
                        "amount": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 2000,
                        },
                        "url": {"type": "string"},
                        "intent": {"type": "string"},
                    },
                    "required": ["kind", "intent"],
                    "additionalProperties": False,
                },
            },
        }


class PlaywrightBrowserDriver:
    """Isolated Chromium driver with screenshot and deterministic control evidence."""

    ACTION_TIMEOUT_MS = 4000
    NAVIGATION_TIMEOUT_MS = 18000
    VISIBLE_TEXT_LIMIT = 60000
    DISPATCH_LEDGER_KEY = "__docflowAgentDispatchLedgerV1"
    DISPATCH_LEDGER_LIMIT = 128
    RECOVERY_LANDING_URL = "https://ceac.state.gov/GenNIV/Default.aspx"
    RECOVERY_RETRIEVE_LABELS = frozenset({
        "retrieve an application",
        "retrieve application",
    })
    requires_next_dispatch_receipt = True

    def __init__(self, config):
        self.config = config
        self.engine_name = (config.model or "chromium").lower()
        self.headless = "headless" in self.engine_name
        self.width = 1440
        self.height = 900
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._temporary = tempfile.TemporaryDirectory(
            prefix="docflow-agent-browser-"
        )
        self._observation_index = 0
        self._field_selectors = {}
        self._target_selectors = {}
        # Dynamic-section descriptors are code-owned and page-scoped.  Keeping
        # them here lets ordinary post-action observations expose the live
        # record count; restart recovery additionally passes the persisted
        # action to ``observe_action`` so it does not depend on this memory.
        self._repeater_record_labels = {}
        self._semantic_field_bindings = set()
        self._verified_field_values = {}
        self._acknowledged = []
        self.execution_mode = "hybrid"
        self._visual_status_state = "observing"
        self._visual_status_message = "正在读取页面"
        self._cursor_x = 24.0
        self._cursor_y = 24.0
        self.browser_launch_source = ""
        self._action_watch_active = False
        self._action_dom_generation_before = ""
        self._action_field_tokens_before = set()
        self._last_dynamic_refresh_evidence = {}
        self.navigation_outcome_timeout_seconds = 20
        self._profile_dir = None
        self._profile_dir_validated = False
        self._purge_profile_after_close = False

    def set_execution_mode(self, mode):
        self.execution_mode = str(mode or "hybrid").strip().lower()

    def set_profile_dir(self, path):
        """Use one private persistent browser profile on the next start."""
        if self._context is not None or self._browser is not None:
            raise RuntimeError("Browser profile must be configured before start")
        requested = Path(path).expanduser()
        if requested.is_symlink():
            raise ValueError("Browser profile path must not be a symlink")
        target = requested.resolve()
        if self._profile_path_is_broad(target):
            raise ValueError("Browser profile path is too broad")
        if target.exists() and not target.is_dir():
            raise ValueError("Browser profile path must be a directory")
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(target, 0o700)
        self._profile_dir = target
        self._profile_dir_validated = True
        return target

    def purge_profile_on_close(self):
        """Delete after close, or immediately if teardown already finished."""
        self._purge_profile_after_close = True
        if all(
            target is None
            for target in (
                self._page,
                self._context,
                self._browser,
                self._playwright,
            )
        ):
            return self._purge_private_profile()
        return False

    @staticmethod
    def _profile_path_is_broad(path, *, follow_symlinks=True):
        """Reject filesystem roots, shared roots, and process-owned roots."""
        return profile_path_is_broad(
            path,
            follow_symlinks=follow_symlinks,
        )

    def _purge_private_profile(self):
        """Remove only this driver's validated private profile.

        The stored path is resolved when configured.  If the leaf is replaced
        with a symlink before teardown, unlink the owned directory entry but
        never follow it.  A changed ancestor or any broad path is refused.
        Failed deletion keeps both the path and intent so a later close/purge
        call can retry instead of silently leaking the profile.
        """
        profile = self._profile_dir
        if profile is None:
            self._profile_dir_validated = False
            self._purge_profile_after_close = False
            return True
        if not self._profile_dir_validated:
            return False
        if not purge_private_profile_path(profile):
            return False
        self._profile_dir = None
        self._profile_dir_validated = False
        self._purge_profile_after_close = False
        return True

    @classmethod
    def _configure_timeout_target(cls, target):
        set_action = getattr(target, "set_default_timeout", None)
        if callable(set_action):
            set_action(cls.ACTION_TIMEOUT_MS)
        set_navigation = getattr(
            target, "set_default_navigation_timeout", None
        )
        if callable(set_navigation):
            set_navigation(cls.NAVIGATION_TIMEOUT_MS)

    def _persistent_launch_options(self):
        return {
            "headless": self.headless,
            "viewport": {"width": self.width, "height": self.height},
            "accept_downloads": False,
            "args": ["--restore-last-session"],
        }

    def start(self, url):
        if self._page is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as error:
                raise ProviderNotConfigured(
                    "Playwright is not installed; install the providers extra"
                ) from error
            self._playwright = sync_playwright().start()
            engine = (
                self._playwright.firefox
                if "firefox" in self.engine_name
                else self._playwright.webkit
                if "webkit" in self.engine_name
                else self._playwright.chromium
            )
            if self._profile_dir is not None:
                try:
                    self._context = engine.launch_persistent_context(
                        str(self._profile_dir),
                        **self._persistent_launch_options(),
                    )
                    self.browser_launch_source = (
                        f"playwright-persistent-{engine.name}"
                    )
                except Exception as bundled_error:
                    if engine is not self._playwright.chromium:
                        raise
                    self._context = self._launch_local_persistent_chromium(
                        engine,
                        self._profile_dir,
                        bundled_error,
                    )
            else:
                try:
                    self._browser = engine.launch(headless=self.headless)
                    self.browser_launch_source = f"playwright-{engine.name}"
                except Exception as bundled_error:
                    if engine is not self._playwright.chromium:
                        raise
                    self._browser = self._launch_local_chromium(
                        engine,
                        bundled_error,
                    )
                self._context = self._browser.new_context(
                    viewport={"width": self.width, "height": self.height},
                    accept_downloads=False,
                )
            self._configure_timeout_target(self._context)
            if self._visual_execution:
                self._context.add_init_script(
                    script=self._visual_document_init_script()
                )
            self._reuse_restored_page_or_navigate(url)
            if self._visual_execution:
                self._install_visual_document_guard()
        else:
            self._configure_timeout_target(self._page)
            if not self._preserve_restored_classification(
                self._classify_live_page(self._page)
            ):
                self._page.goto(
                    str(url),
                    wait_until="domcontentloaded",
                    timeout=self.NAVIGATION_TIMEOUT_MS,
                )
        self.focus()
        if self._visual_execution:
            self._ensure_visible_cursor()

    @staticmethod
    def _preserve_restored_classification(classification):
        """Keep known CEAC state and a still-loading formal-route shell."""
        kind = str(getattr(classification, "kind", "") or "")
        return kind in {
            "formal",
            "recovery",
            "default",
            "captcha",
            "sign",
            "final_submit",
            "session_timeout",
        } or bool(
            kind == "unsupported"
            and int(getattr(classification, "stage_score", 0) or 0) > 0
        )

    def _reuse_restored_page_or_navigate(self, url):
        """Keep a restored formal CEAC tab; navigate only a first/blank tab."""
        pages = []
        try:
            pages = [
                page for page in self._context.pages
                if not page.is_closed()
            ]
        except Exception:
            pages = []
        self._page = pages[0] if pages else self._context.new_page()
        self._select_best_page()
        self._configure_timeout_target(self._page)
        if self._preserve_restored_classification(
            self._classify_live_page(self._page)
        ):
            return
        self._page.goto(
            str(url),
            wait_until="domcontentloaded",
            timeout=self.NAVIGATION_TIMEOUT_MS,
        )

    @staticmethod
    def _local_chromium_candidates(environ=None):
        """Return deterministic local Chrome fallbacks in preference order."""
        environ = os.environ if environ is None else environ
        raw_candidates = [
            environ.get("BROWSER_EXECUTABLE_PATH"),
            environ.get("PLAYWRIGHT_CHROME_PATH"),
            environ.get("CHROME_EXECUTABLE"),
            (
                "/Applications/Google Chrome.app/Contents/MacOS/"
                "Google Chrome"
            ),
            (
                "/Applications/Google Chrome for Testing.app/Contents/"
                "MacOS/Google Chrome for Testing"
            ),
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            (
                "~/Applications/Google Chrome.app/Contents/MacOS/"
                "Google Chrome"
            ),
        ]
        candidates = []
        seen = set()
        for raw_path in raw_candidates:
            if not str(raw_path or "").strip():
                continue
            path = Path(str(raw_path)).expanduser()
            normalized = str(path)
            if normalized in seen or not path.is_file():
                continue
            seen.add(normalized)
            candidates.append(path)
        return candidates

    @staticmethod
    def _launch_error_summary(error):
        first_line = str(error or "").strip().splitlines()
        message = first_line[0] if first_line else "unknown launch error"
        return f"{type(error).__name__}: {message[:240]}"

    def _launch_local_chromium(
        self,
        engine,
        bundled_error,
        environ=None,
    ):
        """Launch an installed Chrome when Playwright's bundle is absent.

        Desktop distributions intentionally do not have to download a second
        Chromium.  Prefer an explicit/local executable and retain Playwright's
        named-channel lookup as the last portable fallback.  If every attempt
        fails, report each stage instead of hiding the actionable local-Chrome
        error behind the original missing-bundle exception.
        """
        failures = [
            "bundled="
            + self._launch_error_summary(bundled_error)
        ]
        last_error = bundled_error
        for executable in self._local_chromium_candidates(environ):
            try:
                browser = engine.launch(
                    headless=self.headless,
                    executable_path=str(executable),
                )
                self.browser_launch_source = (
                    f"local-executable:{executable}"
                )
                return browser
            except Exception as error:
                last_error = error
                failures.append(
                    f"executable={executable}:"
                    + self._launch_error_summary(error)
                )
        try:
            browser = engine.launch(
                headless=self.headless,
                channel="chrome",
            )
            self.browser_launch_source = "channel:chrome"
            return browser
        except Exception as error:
            last_error = error
            failures.append(
                "channel=chrome:" + self._launch_error_summary(error)
            )
        raise ProviderNotConfigured(
            "Playwright could not launch Chromium; "
            + "; ".join(failures)
        ) from last_error

    def _launch_local_persistent_chromium(
        self,
        engine,
        profile_dir,
        bundled_error,
        environ=None,
    ):
        """Launch a persistent installed Chrome when the bundle is absent."""
        failures = [
            "bundled="
            + self._launch_error_summary(bundled_error)
        ]
        last_error = bundled_error
        options = self._persistent_launch_options()
        for executable in self._local_chromium_candidates(environ):
            try:
                context = engine.launch_persistent_context(
                    str(profile_dir),
                    executable_path=str(executable),
                    **options,
                )
                self.browser_launch_source = (
                    f"local-persistent-executable:{executable}"
                )
                return context
            except Exception as error:
                last_error = error
                failures.append(
                    f"executable={executable}:"
                    + self._launch_error_summary(error)
                )
        try:
            context = engine.launch_persistent_context(
                str(profile_dir),
                channel="chrome",
                **options,
            )
            self.browser_launch_source = "persistent-channel:chrome"
            return context
        except Exception as error:
            last_error = error
            failures.append(
                "channel=chrome:" + self._launch_error_summary(error)
            )
        raise ProviderNotConfigured(
            "Playwright could not launch persistent Chromium; "
            + "; ".join(failures)
        ) from last_error

    def observe(self):
        self._require_page()
        self._select_best_page()
        if self._visual_execution:
            self._ensure_visible_cursor()
        self._observation_index += 1
        screenshot = (
            Path(self._temporary.name)
            / f"observation-{self._observation_index:04d}.jpg"
        )
        if self._visual_execution:
            self._set_visual_overlays_hidden(True)
        try:
            self._page.screenshot(
                path=str(screenshot),
                type="jpeg",
                quality=65,
            )
        finally:
            if self._visual_execution:
                self._set_visual_overlays_hidden(False)
        visible_text = self._visible_page_text()
        # Structured controls (radio groups and CEAC's three-part dates) are
        # verified when they are set. Preserve that exact, system-derived
        # value so the workflow can compare it with the approved record.
        self._prune_detached_field_bindings()
        control_values = {}
        for field_id, selector in list(self._field_selectors.items()):
            try:
                value = self._live_control_value(field_id, selector, 2000)
                if value is not None:
                    control_values[field_id] = value
            except Exception:
                continue
        errors = self._validation_errors()
        repeater_counts = self._tracked_repeater_counts()
        dispatch = self._dispatch_receipt_snapshot(create=False)
        scroll = self._scroll_metrics()
        return BrowserObservation(
            url=self._page.url,
            title=self._page.title(),
            visible_text=visible_text,
            screenshot_ref=str(screenshot),
            page_id=self._page_identity(),
            control_values=control_values,
            form_control_count=self._form_control_count(),
            repeater_counts=repeater_counts,
            errors=[str(item)[:500] for item in errors if str(item).strip()][:20],
            acknowledged_action_ids=list(self._acknowledged),
            dispatched_action_ids=list(dispatch.get("ids") or ()),
            dispatch_receipt_scope=str(dispatch.get("scope") or ""),
            dispatch_receipts_authoritative=bool(
                dispatch.get("authoritative")
            ),
            dispatch_receipt_conflict=bool(dispatch.get("conflict")),
            scroll_x=scroll["x"],
            scroll_y=scroll["y"],
            scroll_height=scroll["height"],
            viewport_height=scroll["viewport_height"],
        )

    def observe_lightweight(self):
        """Return exact post-action state without another screenshot/OCR cycle."""
        self._require_page()
        self._select_best_page()
        if self._visual_execution:
            self._ensure_visible_cursor()
        visible_text = self._visible_page_text()
        self._prune_detached_field_bindings()
        control_values = {}
        for field_id, selector in list(self._field_selectors.items()):
            try:
                value = self._live_control_value(field_id, selector, 500)
                if value is not None:
                    control_values[field_id] = value
            except Exception:
                continue
        errors = self._validation_errors()
        repeater_counts = self._tracked_repeater_counts()
        dispatch = self._dispatch_receipt_snapshot(create=False)
        scroll = self._scroll_metrics()
        return BrowserObservation(
            url=self._page.url,
            title=self._page.title(),
            visible_text=visible_text,
            screenshot_ref="",
            page_id=self._page_identity(),
            control_values=control_values,
            form_control_count=self._form_control_count(),
            repeater_counts=repeater_counts,
            errors=[str(item)[:500] for item in errors if str(item).strip()][:20],
            acknowledged_action_ids=list(self._acknowledged),
            dispatched_action_ids=list(dispatch.get("ids") or ()),
            dispatch_receipt_scope=str(dispatch.get("scope") or ""),
            dispatch_receipts_authoritative=bool(
                dispatch.get("authoritative")
            ),
            dispatch_receipt_conflict=bool(dispatch.get("conflict")),
            scroll_x=scroll["x"],
            scroll_y=scroll["y"],
            scroll_height=scroll["height"],
            viewport_height=scroll["viewport_height"],
        )

    def observe_route_lightweight(self):
        """Read only route ownership immediately before a browser mutation.

        The workflow uses this to reject a page-level action if a consultant
        manually advanced while Gemini was planning.  URL/title/page identity
        are sufficient for code-owned page-plan matching; avoiding visible
        text, validation, and control scans keeps the guard effectively free
        compared with an ordinary field write.
        """
        self._require_page()
        self._select_best_page()
        return BrowserObservation(
            url=self._page.url,
            title=self._page.title(),
            visible_text="",
            screenshot_ref="",
            page_id=self._page_identity(),
            form_control_count=0,
        )

    def _scroll_metrics(self):
        """Read document scroll geometry without changing the page."""
        try:
            value = self._page.evaluate(
                """() => {
                    const root = document.scrollingElement
                        || document.documentElement
                        || document.body;
                    const body = document.body;
                    const doc = document.documentElement;
                    const height = Math.max(
                        root?.scrollHeight || 0,
                        body?.scrollHeight || 0,
                        doc?.scrollHeight || 0
                    );
                    return {
                        x: Math.max(0, Math.round(
                            window.scrollX || root?.scrollLeft || 0
                        )),
                        y: Math.max(0, Math.round(
                            window.scrollY || root?.scrollTop || 0
                        )),
                        height: Math.max(0, Math.round(height)),
                        viewport_height: Math.max(
                            0, Math.round(window.innerHeight || 0)
                        ),
                    };
                }""",
            )
        except Exception:
            value = {}
        return {
            "x": max(0, int(dict(value or {}).get("x") or 0)),
            "y": max(0, int(dict(value or {}).get("y") or 0)),
            "height": max(
                0, int(dict(value or {}).get("height") or 0)
            ),
            "viewport_height": max(
                0, int(dict(value or {}).get("viewport_height") or 0)
            ),
        }

    def _visible_page_text(self):
        """Read bounded rendered text without leaking observer-owned overlays.

        ``innerText`` gives us the browser's rendered-text view, so hidden
        controls and inactive modal contents do not wake the continuous-run
        watcher.  DocFlow's cursor/status elements are part of ``body`` in
        visual mode, however, and their heartbeat changes every second.  Hide
        only those observer-owned nodes inside the same JavaScript task,
        restore their exact inline styles in ``finally``, and bound the value
        before it crosses the Playwright boundary.

        Browser-owned/error documents may deny script access.  Observation is
        intentionally best-effort in that case so a text read can never break
        action verification or recovery polling.
        """
        try:
            value = self._page.evaluate(
                """limit => {
                    const body = document.body;
                    if (!body) return "";
                    const overlays = Array.from(document.querySelectorAll(
                        "#docflow-agent-visible-cursor,"
                        + "#docflow-agent-visual-status"
                    ));
                    const priorDisplay = overlays.map(element => ({
                        element,
                        value: element.style.getPropertyValue("display"),
                        priority: element.style.getPropertyPriority("display")
                    }));
                    try {
                        for (const {element} of priorDisplay) {
                            element.style.setProperty(
                                "display",
                                "none",
                                "important"
                            );
                        }
                        return String(body.innerText || "").slice(
                            0,
                            Math.max(0, Number(limit) || 0)
                        );
                    } finally {
                        for (const {
                            element,
                            value,
                            priority
                        } of priorDisplay) {
                            if (value) {
                                element.style.setProperty(
                                    "display",
                                    value,
                                    priority
                                );
                            } else {
                                element.style.removeProperty("display");
                            }
                        }
                    }
                }""",
                self.VISIBLE_TEXT_LIMIT,
            )
        except Exception:
            return ""
        return str(value or "")[:self.VISIBLE_TEXT_LIMIT]

    def _form_control_count(self, page=None):
        """Return only a structural count, never control values or selectors."""
        target = page or self._page
        if target is None:
            return 0
        try:
            value = target.evaluate(
                """() => Array.from(document.querySelectorAll(
                    'form input:not([type="hidden"]), form select, '
                    + 'form textarea, form button'
                )).filter(item => {
                    const style = getComputedStyle(item);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden';
                }).length"""
            )
            return max(0, int(value or 0))
        except Exception:
            try:
                return max(
                    0,
                    int(getattr(target, "form_control_count", 0) or 0),
                )
            except (TypeError, ValueError):
                return 0

    @staticmethod
    def _page_text_attribute(page, name):
        value = getattr(page, name, "")
        try:
            value = value() if callable(value) else value
        except Exception:
            value = ""
        return str(value or "")

    def _classification_observation(self, page):
        """Read the minimum live evidence used by the shared CEAC classifier."""
        url = self._page_text_attribute(page, "url")
        title = self._page_text_attribute(page, "title")
        visible_text = self._page_text_attribute(page, "visible_text")
        if not visible_text:
            try:
                visible_text = str(page.evaluate(
                    """limit => String(
                        document.body ? document.body.innerText : ''
                    ).slice(0, limit)""",
                    min(self.VISIBLE_TEXT_LIMIT, 12000),
                ) or "")
            except Exception:
                visible_text = ""
        return BrowserObservation(
            url=url,
            title=title,
            visible_text=visible_text,
            form_control_count=self._form_control_count(page),
        )

    def _classify_live_page(self, page):
        return classify_ceac_page(
            self._classification_observation(page)
        )

    def observe_action(self, action_or_field_id):
        """Read only the control changed by the current action.

        Page batches can mark many controls before the first mutation. Reading
        every marker after every action makes verification quadratic and lets
        one unrelated stale locator consume the whole action budget.
        """
        self._require_page()
        self._select_best_page()
        if self._visual_execution:
            self._ensure_visible_cursor()
        action = (
            action_or_field_id
            if hasattr(action_or_field_id, "field_id")
            else None
        )
        if action is not None:
            requested = str(
                getattr(action, "field_id", "") or ""
            )
        else:
            requested = str(action_or_field_id or "")
        control_values = {}
        selector = self._field_selectors.get(requested)
        if selector:
            try:
                value = self._live_control_value(
                    requested,
                    selector,
                    min(500, self.ACTION_TIMEOUT_MS),
                )
                if value is not None:
                    control_values[requested] = value
            except Exception:
                pass
        repeater_counts = self._tracked_repeater_counts(
            action=action,
        )
        errors = []
        marker_pattern = re.compile(r"^\[field_id=([^\]]+)\]\s*")
        for raw_error in self._validation_errors():
            text = str(raw_error or "").strip()
            matched = marker_pattern.match(text)
            if (
                not requested
                or not matched
                or matched.group(1) == requested
            ):
                errors.append(text[:500])
        dispatch = self._dispatch_receipt_snapshot(create=False)
        return BrowserObservation(
            url=self._page.url,
            title=self._page.title(),
            visible_text="",
            screenshot_ref="",
            page_id=self._page_identity(),
            control_values=control_values,
            form_control_count=self._form_control_count(),
            repeater_counts=repeater_counts,
            errors=errors[:20],
            acknowledged_action_ids=list(self._acknowledged),
            dispatched_action_ids=list(dispatch.get("ids") or ()),
            dispatch_receipt_scope=str(dispatch.get("scope") or ""),
            dispatch_receipts_authoritative=bool(
                dispatch.get("authoritative")
            ),
            dispatch_receipt_conflict=bool(dispatch.get("conflict")),
        )

    def _validation_errors(self):
        """Return visible validation text tagged to one marked field when known."""
        try:
            values = self._page.locator(
                "[aria-invalid='true'], .error, .field-validation-error, "
                ".validation-summary-errors"
            ).evaluate_all(
                """items => {
                    const visible = (item) => {
                        const style = getComputedStyle(item);
                        const box = item.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0 && box.height > 0;
                    };
                    const fieldMarkers = (root) => {
                        const values = new Set();
                        if (root?.matches?.('[data-docflow-field]')) {
                            values.add(String(
                                root.getAttribute(
                                    'data-docflow-field'
                                ) || ''
                            ));
                        }
                        for (const field of Array.from(
                            root?.querySelectorAll?.(
                                '[data-docflow-field]'
                            ) || []
                        )) {
                            values.add(String(
                                field.getAttribute(
                                    'data-docflow-field'
                                ) || ''
                            ));
                        }
                        values.delete('');
                        return values;
                    };
                    return items.filter(visible).map((item) => {
                        const text = String(
                            item.innerText
                            || item.validationMessage
                            || item.getAttribute('aria-label')
                            || item.getAttribute('title')
                            || ''
                        ).trim();
                        if (!text) return '';
                        let markers = fieldMarkers(item);
                        const errorId = String(item.id || '');
                        if (!markers.size && errorId) {
                            const described = Array.from(
                                document.querySelectorAll(
                                    '[data-docflow-field][aria-describedby]'
                                )
                            ).filter((field) => String(
                                field.getAttribute('aria-describedby') || ''
                            ).split(/\\s+/).includes(errorId));
                            if (described.length === 1) {
                                markers = fieldMarkers(described[0]);
                            }
                        }
                        let current = item.parentElement;
                        for (
                            let depth = 0;
                            !markers.size && current && depth < 5;
                            depth += 1
                        ) {
                            const nearby = fieldMarkers(current);
                            if (nearby.size === 1) {
                                markers = nearby;
                                break;
                            }
                            if (nearby.size > 1) break;
                            current = current.parentElement;
                        }
                        if (markers.size === 1) {
                            return `[field_id=${Array.from(markers)[0]}] `
                                + text;
                        }
                        return text;
                    }).filter(Boolean);
                }"""
            )
        except Exception:
            values = []

        # CEAC's production ASP.NET validation summary is not consistent
        # across deployments: the red box visible to the user may have no
        # ``.validation-summary-errors``/ARIA class at all.  Detect the exact
        # server-owned heading in visible body text as a second, read-only
        # signal.  Without this fallback the workflow could regard a retained
        # error page as clean merely because its CSS class differed from the
        # synthetic site used by acceptance tests.
        try:
            ceac_summary = self._page.evaluate(
                """() => {
                    const body = document.body;
                    if (!body) return [];
                    const lines = String(body.innerText || '')
                        .split(/\\n+/)
                        .map(value => value.replace(/\\s+/g, ' ').trim())
                        .filter(Boolean);
                    const marker = lines.findIndex(line => (
                        /please correct all areas in error as indicated below/i
                            .test(line)
                    ));
                    if (marker < 0) return [];
                    const result = [lines[marker]];
                    for (const line of lines.slice(marker + 1, marker + 25)) {
                        if (
                            /\\b(?:has not been completed|has not been answered|is invalid|required)\\b/i
                                .test(line)
                        ) result.push(line);
                    }
                    return result.slice(0, 20);
                }"""
            )
        except Exception:
            ceac_summary = []
        merged = list(dict.fromkeys(
            str(item).strip()
            for item in [*(values or ()), *(ceac_summary or ())]
            if str(item).strip()
        ))
        return [item[:500] for item in merged[:20]]

    def focus(self):
        """Expose the exact Playwright-controlled window used by the job."""
        self._require_page()
        self._select_best_page()
        try:
            self._page.bring_to_front()
        except Exception:
            pass
        # Normalise a hidden/minimised Chrome window through CDP. This is
        # especially important on macOS when a regular Chrome and a testing
        # Chrome are both already open.
        if self._context is not None and "chrom" in self.engine_name:
            session = None
            try:
                session = self._context.new_cdp_session(self._page)
                window = session.send("Browser.getWindowForTarget")
                window_id = window.get("windowId")
                if window_id is not None:
                    session.send(
                        "Browser.setWindowBounds",
                        {
                            "windowId": window_id,
                            "bounds": {"windowState": "normal"},
                        },
                    )
            except Exception:
                pass
            finally:
                if session is not None:
                    try:
                        session.detach()
                    except Exception:
                        pass
        # A restored tab did not necessarily exist when the context-level
        # document-init script was registered.  Reinstall the observer-owned
        # visuals whenever the controlled window is explicitly focused.
        if self._visual_execution:
            self._ensure_visible_cursor()

    def _select_best_page(self):
        """Prefer the live CEAC formal-form tab over stale landing tabs."""
        if self._context is None:
            return
        try:
            pages = [
                page for page in self._context.pages
                if not page.is_closed()
            ]
        except Exception:
            return
        if not pages:
            return

        def score(page):
            classification = self._classify_live_page(page)
            kind = classification.kind
            if kind == "sign":
                value = max(600, classification.stage_score)
            elif kind == "final_submit":
                value = max(500, classification.stage_score)
            elif kind in {"formal", "captcha"}:
                value = max(100, classification.stage_score)
            elif kind == "recovery":
                value = 30
            elif kind == "default":
                value = 20
            elif kind == "session_timeout":
                value = 10
            else:
                value = 0
            # The currently focused tab is only a tie-breaker.  Adding its
            # bonus to the stage number let an immediately preceding DS-160
            # page tie the true later page, so a stale focused tab could win.
            return (value, int(page is self._page))

        selected = max(pages, key=score)
        if selected is self._page:
            return
        self._page = selected
        self.clear_page_state()
        self._configure_timeout_target(selected)
        if self._visual_execution:
            self._install_visual_document_guard()
        try:
            selected.bring_to_front()
        except Exception:
            pass

    def _install_visual_document_guard(self):
        """Install the init-script guard into an already restored live page."""
        if self._page is None:
            return
        try:
            self._page.evaluate(self._visual_document_init_script())
        except Exception:
            # A navigation in progress receives the same script through the
            # context-level init hook before its next document starts.
            pass

    def clear_page_state(self):
        """Drop selectors and canonical caches owned by the previous page."""
        self._field_selectors.clear()
        self._target_selectors.clear()
        self._repeater_record_labels.clear()
        self._semantic_field_bindings.clear()
        self._verified_field_values.clear()
        self._acknowledged.clear()
        self._action_watch_active = False
        self._action_dom_generation_before = ""
        self._action_field_tokens_before = set()
        self._last_dynamic_refresh_evidence = {}

    def _prune_detached_field_bindings(self):
        """Drop every selector whose marked control left the live document.

        ``plan_fields`` resolves and marks a whole page before execution.  A
        legacy ASP.NET UpdatePanel can replace that entire marked subtree after
        the first branch-changing select.  Keeping the remaining selectors made
        the next observation wait for each detached field independently, so a
        single postback could look like a minute-long freeze.  One DOM query
        prunes all invalid bindings in O(number of planned fields) local work.
        """
        if self._page is None or not self._field_selectors:
            return []
        try:
            live_tokens = set(self._page.locator(
                "[data-docflow-field]"
            ).evaluate_all(
                """items => items.map(item => String(
                    item.getAttribute('data-docflow-field') || ''
                )).filter(Boolean)"""
            ))
        except Exception:
            # Failure to inspect the document is not evidence that every
            # binding is stale. The bounded observation/recovery path remains
            # responsible for a temporarily unavailable page.
            return []
        detached = []
        for field_id in list(self._field_selectors):
            token = re.sub(r"[^A-Za-z0-9_.-]", "_", str(field_id))
            if token in live_tokens:
                continue
            detached.append(field_id)
            self._field_selectors.pop(field_id, None)
            self._semantic_field_bindings.discard(field_id)
            self._verified_field_values.pop(field_id, None)
        return detached

    def invalidate_field_binding(self, field_id):
        """Discard a failed visual target so deterministic repair cannot reuse it."""
        requested = str(field_id or "")
        selector = self._field_selectors.pop(requested, None)
        self._semantic_field_bindings.discard(requested)
        self._verified_field_values.pop(requested, None)
        self._target_selectors.pop(requested, None)
        if self._page is None:
            return
        token = re.sub(r"[^A-Za-z0-9_.-]", "_", requested)
        selectors = [
            selector,
            f'[data-docflow-field="{token}"]' if token else "",
        ]
        for candidate in dict.fromkeys(
            item for item in selectors if str(item or "").strip()
        ):
            try:
                self._page.locator(candidate).evaluate_all(
                    """items => items.forEach(item => {
                        item.removeAttribute('data-docflow-field');
                        item.removeAttribute('data-docflow-field-owner');
                        item.removeAttribute('data-docflow-mark-target');
                    })"""
                )
            except Exception:
                continue

    def set_visual_status(self, state, message=""):
        self._visual_status_state = str(state or "observing").strip().lower()
        self._visual_status_message = str(message or "").strip()[:180]
        if not self._visual_execution or self._page is None:
            return
        self._ensure_visible_cursor()
        lease_ms = self._visual_lease_ms(self._visual_status_state)
        try:
            self._page.evaluate(
                """([state, message, x, y, leaseMs]) => {
                    const now = Date.now();
                    let saved = {};
                    try {
                        saved = JSON.parse(
                            sessionStorage.getItem(
                                "__docflowAgentVisualState"
                            ) || "{}"
                        );
                    } catch (_error) {
                        saved = {};
                    }
                    const sameOperation = (
                        String(saved.state || "") === state
                        && String(saved.message || "") === (message || "")
                    );
                    const startedAt = sameOperation
                        ? Number(saved.startedAt || now)
                        : now;
                    const leaseUntil = leaseMs > 0
                        ? now + leaseMs
                        : 0;
                    try {
                        sessionStorage.setItem(
                            "__docflowAgentVisualState",
                            JSON.stringify({
                                state,
                                message: message || "",
                                startedAt,
                                x,
                                y,
                                leaseUntil
                            })
                        );
                    } catch (_error) {
                        // Some browser-owned/error documents deny storage.
                    }
                    const badge = document.getElementById(
                        "docflow-agent-visual-status"
                    );
                    if (!badge) return;
                    badge.dataset.state = state;
                    badge.dataset.baseMessage = message || "";
                    badge.dataset.startedAt = String(startedAt);
                    badge.dataset.leaseUntil = String(leaseUntil);
                    const cursor = document.getElementById(
                        "docflow-agent-visible-cursor"
                    );
                    if (cursor) {
                        cursor.dataset.state = state;
                        cursor.style.left = `${x}px`;
                        cursor.style.top = `${y}px`;
                    }
                    const label = badge.querySelector(
                        "[data-docflow-status-label]"
                    );
                    const labels = {
                        observing: "Gemini · 读取页面",
                        thinking: "Gemini · 规划本页",
                        working: "Gemini · 正在填写",
                        navigating: "Gemini · 正在进入下一页",
                        paused: "Gemini · 已暂停，需要处理",
                        blocked: "Gemini · 已停止，需要处理",
                        disconnected: "Gemini · 连接中断",
                        error: "Gemini · 运行失败",
                        completed: "Gemini · 本轮完成"
                    };
                    if (label) label.textContent = labels[state]
                        || "Gemini · 工作中";
                    if (window.__docflowAgentRenderHeartbeat) {
                        window.__docflowAgentRenderHeartbeat();
                    }
                }""",
                [
                    self._visual_status_state,
                    self._visual_status_message,
                    self._cursor_x,
                    self._cursor_y,
                    lease_ms,
                ],
            )
        except Exception:
            pass

    @staticmethod
    def _visual_lease_ms(state):
        """Return a bounded host-heartbeat lease for one visible phase."""
        normalized = str(state or "").strip().lower()
        if normalized == "thinking":
            # The complete page-level primary+recovery budget is about 42
            # seconds. Keep a small rendering/network margin so the visible
            # status never reports a false pause while Gemini is still inside
            # its legitimate request window. A recovery request also publishes
            # a fresh heartbeat before it starts.
            return 47000
        if normalized == "navigating":
            # Navigation itself is bounded at 18 seconds.
            return 25000
        if normalized in {"observing", "working"}:
            # Local screenshots and individual actions are each short.
            return 15000
        return 0

    def plan_fields(self, field_ids, field_labels=None, control_hints=None):
        """Resolve unambiguous known controls locally without a model call."""
        self._require_page()
        field_labels = dict(field_labels or {})
        control_hints = dict(control_hints or {})
        actions = []
        unresolved = []
        # Existing repeater rows must be populated before Add Another.  CEAC
        # rejects the repeater postback while the current row is blank.  Page
        # field IDs are intentionally sorted for determinism, which otherwise
        # puts ``ensure`` before ``record`` and creates an endless flashing
        # Add Another loop.  Preserve the incoming order within each group but
        # move every idempotent ensure-N action behind visible value fields.
        ordered_field_ids = list(field_ids or ())
        ordered_field_ids.sort(key=lambda raw_field_id: int(
            self._control_kind(
                field_labels.get(str(raw_field_id)) or ()
            ) == "ensure_repeater"
        ))
        for raw_field_id in ordered_field_ids:
            field_id = str(raw_field_id)
            labels = field_labels.get(field_id) or ()
            if self._control_kind(labels) == "ensure_repeater":
                try:
                    repeater = self._plan_repeater_field(field_id, labels)
                except ControlBindingUnavailable:
                    repeater = None
                if repeater is None:
                    unresolved.append(field_id)
                else:
                    actions.append(repeater)
                continue
            locator = self._deterministic_control(
                field_id,
                labels,
                control_hints.get(field_id) or (),
            )
            if locator is None:
                unresolved.append(field_id)
                continue
            try:
                metadata = locator.evaluate(
                    """el => ({
                        tag: el.tagName.toLowerCase(),
                        type: String(el.getAttribute('type') || '')
                            .toLowerCase()
                    })"""
                )
            except Exception:
                unresolved.append(field_id)
                continue
            tag_name = str(metadata.get("tag") or "")
            control_type = str(metadata.get("type") or "")
            action = ComputerAction(
                kind=(
                    ActionKind.SELECT
                    if (
                        tag_name == "select"
                        or (
                            tag_name == "input"
                            and control_type in {"radio", "checkbox"}
                        )
                    )
                    else ActionKind.TYPE
                ),
                field_id=field_id,
                target_hint=field_id,
                reason=(
                    "Deterministic DOM label/control match "
                    f"[field_id={field_id}]"
                ),
            )
            try:
                self._mark_field(locator, action)
            except ControlBindingUnavailable:
                # A deterministic page batch owns a one-to-one mapping from
                # logical fields to live controls.  If another field already
                # owns this element, keep its marker intact and send only the
                # colliding descriptor through fresh visual resolution.
                unresolved.append(field_id)
                continue
            actions.append(action)
        return actions, unresolved

    def classify_field_presence(
        self,
        field_ids,
        field_labels=None,
        control_hints=None,
    ):
        """Classify reviewed fields against the current rendered CEAC DOM.

        This is a read-only scope check, not a form-filling shortcut. Gemini
        remains responsible for choosing actions for present fields. A field is
        called absent only when neither a visible semantic control nor visible
        label/question/hint evidence exists; ambiguous visible evidence stays
        unresolved and is therefore still sent to Gemini.
        """
        self._require_page()
        field_labels = dict(field_labels or {})
        control_hints = dict(control_hints or {})
        present = []
        absent = []
        unresolved = []
        for raw_field_id in field_ids:
            field_id = str(raw_field_id or "")
            labels = tuple(field_labels.get(field_id) or ())
            hints = tuple(control_hints.get(field_id) or ())
            control_kind = self._control_kind(labels)
            if control_kind == "ensure_repeater":
                try:
                    repeater = self._plan_repeater_field(field_id, labels)
                except Exception:
                    repeater = None
                if repeater is not None:
                    present.append(field_id)
                    continue

            label_terms = []
            for raw in labels:
                text = str(raw or "").split("[control=", 1)[0].strip()
                if text and text not in label_terms:
                    label_terms.append(text)
            normalized_hints = list(dict.fromkeys(
                re.sub(r"[^A-Za-z0-9_-]", "", str(raw or ""))
                for raw in hints
                if re.sub(r"[^A-Za-z0-9_-]", "", str(raw or ""))
            ))
            try:
                evidence = dict(self._page.evaluate(
                    """args => {
                        const visible = element => {
                            if (!element) return false;
                            const style = getComputedStyle(element);
                            const box = element.getBoundingClientRect();
                            return style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && box.width > 0 && box.height > 0;
                        };
                        const norm = value => String(value || '')
                            .toLowerCase()
                            .replace(/[^a-z0-9\\u4e00-\\u9fff]+/g, ' ')
                            .trim();
                        const controls = Array.from(
                            document.querySelectorAll('input,select,textarea')
                        ).filter(visible);
                        const hintMatch = controls.some(control => {
                            const identity = norm(
                                `${control.id || ''} ${control.name || ''}`
                            ).replace(/ /g, '');
                            return args.hints.some(raw => {
                                const hint = norm(raw).replace(/ /g, '');
                                // Presence classification is deliberately
                                // stricter than the later field locator.
                                // Generic fragments such as PAYER, CITY, or
                                // ADDRESS can match an unrelated visible
                                // branch control and must never resurrect a
                                // hidden conditional field.
                                return hint.length >= 8
                                    && identity.includes(hint);
                            });
                        });
                        const lines = String(document.body?.innerText || '')
                            .split(/\\n+/)
                            .map(norm)
                            .filter(Boolean);
                        const labelMatch = args.labels.some(raw => {
                            const term = norm(raw);
                            if (!term) return false;
                            return lines.some(line => (
                                line === term
                                || (
                                    term.length >= 10
                                    && line.includes(term)
                                )
                            ));
                        });
                        return {hintMatch, labelMatch};
                    }""",
                    {
                        "labels": label_terms,
                        "hints": normalized_hints,
                    },
                ) or {})
            except Exception:
                # An unavailable read cannot prove that a reviewed field is
                # absent. Keep it pending for Gemini.
                unresolved.append(field_id)
                continue
            if bool(evidence.get("hintMatch")):
                present.append(field_id)
            elif bool(evidence.get("labelMatch")):
                # Visible text proves the field/question belongs to the
                # rendered branch, while the later semantic binder still owns
                # exact control identity.
                unresolved.append(field_id)
            else:
                absent.append(field_id)
        return {
            "present": present,
            "absent": absent,
            "unresolved": unresolved,
        }

    def settle_after_dynamic_refresh(self, field_id, labels=(), hints=()):
        """Wait for a CEAC branch postback and rebind only the changed field.

        Legacy ASP.NET controls can replace the document or an UpdatePanel while
        preserving the URL. This method waits for the replacement DOM, then
        restores an exact selector for the changed control so its approved value
        can be verified. The following observation prunes every other detached
        locator; remaining Gemini-approved batch actions must semantically bind
        again before they are allowed to mutate the replacement DOM.
        """
        self._require_page()
        self._wait_for_watched_dom_replacement()
        try:
            self._page.wait_for_load_state(
                "domcontentloaded", timeout=5000
            )
        except Exception:
            pass
        # A partial UpdatePanel postback does not produce a load-state event.
        # A short settle window lets its synchronous DOM replacement finish
        # without adding model latency to every ordinary text field.
        try:
            self._page.wait_for_timeout(350)
        except Exception:
            pass
        # A page-level deterministic plan may contain many locators captured
        # before this postback.  Remove every detached binding in one pass
        # before rebinding the changed field, otherwise the next observation
        # pays one locator timeout for every stale action in the old batch.
        self._prune_detached_field_bindings()
        if self._control_kind(labels) == "ensure_repeater":
            # A repeater's authoritative state is its live row count, not a
            # replacement binding for the Add Another link.  Rebinding this
            # action through the generic input locator can collide with the
            # first Language Name field after an ASP.NET postback.  Wait only
            # for the monotonic count and leave the replacement button for the
            # next ensure action, if one is still needed.
            expected = 1
            record_labels = []
            for raw_label in labels or ():
                label = str(raw_label or "")
                matched = re.search(
                    r"\bexpected_count=(\d{1,2})\b",
                    label,
                    flags=re.IGNORECASE,
                )
                if matched:
                    expected = max(1, min(20, int(matched.group(1))))
                records = re.search(
                    r"\brecord_labels=([^;\]]+)",
                    label,
                    flags=re.IGNORECASE,
                )
                if records:
                    record_labels = [
                        item.strip()
                        for item in records.group(1).split("|")
                        if item.strip()
                    ][:4]
            for delay_ms in (0, 150, 250, 400, 650, 1000, 1500):
                if self._count_repeater_records(record_labels) >= expected:
                    return True
                if delay_ms:
                    try:
                        self._page.wait_for_timeout(delay_ms)
                    except Exception:
                        break
            return False
        locator = self._deterministic_control(
            str(field_id or ""),
            tuple(labels or ()),
            tuple(hints or ()),
        )
        if locator is None:
            if self._control_kind(labels) != "yes_no":
                return False
            actions, unresolved = self.plan_choice_fields(
                [str(field_id or "")],
                {str(field_id or ""): tuple(labels or ())},
                {str(field_id or ""): tuple(hints or ())},
            )
            if not actions or unresolved:
                return False
            if self._descriptor_approved_value(labels):
                return self._cache_rebound_choice_value(
                    str(field_id or ""),
                    labels,
                )
            return True
        self._mark_field(
            locator,
            ComputerAction(
                kind=ActionKind.SELECT,
                field_id=str(field_id or ""),
                target_hint=str(field_id or ""),
                reason="Postback rebind for deterministic verification",
            ),
        )
        approved = self._descriptor_approved_value(labels)
        control_kind = self._control_kind(labels)
        if approved and control_kind in {
            "date", "duration", "text_segments",
        }:
            selector = self._field_selectors.get(str(field_id or ""))
            if not selector:
                return False
            try:
                actual = self._live_control_value(
                    str(field_id or ""),
                    selector,
                    800,
                )
            except Exception:
                return False
            # Structured helpers reconstruct the complete value from all live
            # subcontrols. Never downgrade that proof to the one select/input
            # returned by deterministic rebinding.
            return actual == approved
        # A select/radio/checkbox can dispatch an ASP.NET postback before the
        # write helper gets a chance to read the same locator back.  In that
        # case the old marker has disappeared and there is deliberately no
        # canonical value in ``_verified_field_values``.  Prove the approved
        # choice against the replacement DOM, then cache the approved spelling
        # so the normal exact verifier does not compare (for example)
        # ``CHINA`` with the browser's combined ``CHINA CHIN`` evidence.
        try:
            metadata = locator.evaluate(
                """el => ({
                    tag: el.tagName.toLowerCase(),
                    type: String(el.getAttribute('type') || '').toLowerCase()
                })""",
                timeout=800,
            )
        except Exception:
            return False
        if (
            str(metadata.get("tag") or "") == "select"
            or (
                str(metadata.get("tag") or "") == "input"
                and str(metadata.get("type") or "") in {
                    "radio", "checkbox",
                }
            )
        ) and approved:
            return self._cache_rebound_choice_value(
                str(field_id or ""),
                labels,
            )
        return True

    def _cache_rebound_choice_value(self, field_id, labels):
        """Canonicalise one postback-replaced choice from live DOM evidence."""
        approved = self._descriptor_approved_value(labels)
        selector = self._field_selectors.get(str(field_id or ""))
        if not approved or not selector:
            return False
        # Never let a value cached before the postback self-verify the
        # replacement control.  ``_live_control_value`` must first reconstruct
        # the currently selected/checked state from the new DOM.
        self._verified_field_values.pop(str(field_id or ""), None)
        try:
            actual = self._live_control_value(
                str(field_id or ""),
                selector,
                800,
            )
        except Exception:
            return False
        if actual is None or not self._choice_matches(approved, actual):
            return False
        self._verified_field_values[str(field_id or "")] = approved
        return True

    def _begin_action_dom_watch(self):
        """Capture page generation and marked controls before one mutation."""
        self._action_watch_active = True
        self._last_dynamic_refresh_evidence = {}
        token = f"document-{uuid4().hex}"
        try:
            state = self._page.evaluate(
                """token => {
                    if (!window.__docflowAgentDocumentGeneration) {
                        window.__docflowAgentDocumentGeneration = token;
                    }
                    window.__docflowAgentMarkedControlRemoved = false;
                    window.__docflowAgentPostbackStarted = false;
                    if (window.__docflowAgentRemovalObserver) {
                        window.__docflowAgentRemovalObserver.disconnect();
                    }
                    const observer = new MutationObserver((records) => {
                        for (const record of records) {
                            for (const node of record.removedNodes) {
                                if (
                                    node.nodeType === Node.ELEMENT_NODE
                                    && (
                                        node.matches?.(
                                            '[data-docflow-field]'
                                        )
                                        || node.querySelector?.(
                                            '[data-docflow-field]'
                                        )
                                    )
                                ) {
                                    window.__docflowAgentMarkedControlRemoved =
                                        true;
                                }
                            }
                        }
                    });
                    observer.observe(
                        document.documentElement,
                        {subtree: true, childList: true}
                    );
                    window.__docflowAgentRemovalObserver = observer;
                    if (
                        typeof window.__doPostBack === 'function'
                        && !window.__doPostBack.__docflowWrapped
                    ) {
                        const original = window.__doPostBack;
                        const wrapped = function(...args) {
                            window.__docflowAgentPostbackStarted = true;
                            return original.apply(this, args);
                        };
                        wrapped.__docflowWrapped = true;
                        window.__doPostBack = wrapped;
                    }
                    if (!window.__docflowAgentSubmitWatchInstalled) {
                        document.addEventListener(
                            'submit',
                            () => {
                                window.__docflowAgentPostbackStarted = true;
                            },
                            true
                        );
                        window.__docflowAgentSubmitWatchInstalled = true;
                    }
                    return {
                        generation:
                            window.__docflowAgentDocumentGeneration,
                        fields: Array.from(document.querySelectorAll(
                            '[data-docflow-field]'
                        )).map(item => String(
                            item.getAttribute('data-docflow-field') || ''
                        )).filter(Boolean)
                    };
                }""",
                token,
            )
        except Exception:
            state = {}
        self._action_dom_generation_before = str(
            (state or {}).get("generation") or ""
        )
        self._action_field_tokens_before = set(
            str(item)
            for item in list((state or {}).get("fields") or [])
            if str(item)
        )

    def dynamic_refresh_detected(self, _action=None):
        """Detect full-document or marked-control replacement after an action."""
        if not self._action_watch_active:
            return False
        # Let CEAC's setTimeout(__doPostBack, 0) wrapper run without imposing a
        # page-wide settle delay on ordinary field actions.
        try:
            self._page.wait_for_timeout(80)
        except Exception:
            pass
        token = f"document-{uuid4().hex}"
        inspection_available = True
        try:
            state = self._page.evaluate(
                """token => {
                    if (!window.__docflowAgentDocumentGeneration) {
                        window.__docflowAgentDocumentGeneration = token;
                    }
                    return {
                        generation:
                            window.__docflowAgentDocumentGeneration,
                        fields: Array.from(document.querySelectorAll(
                            '[data-docflow-field]'
                        )).map(item => String(
                            item.getAttribute('data-docflow-field') || ''
                        )).filter(Boolean),
                        removed: Boolean(
                            window.__docflowAgentMarkedControlRemoved
                        ),
                        postback: Boolean(
                            window.__docflowAgentPostbackStarted
                        )
                    };
                }""",
                token,
            )
        except Exception:
            inspection_available = False
            state = {}
        if not isinstance(state, dict):
            inspection_available = False
            state = {}
        after_generation = str((state or {}).get("generation") or "")
        after_fields = set(
            str(item)
            for item in list((state or {}).get("fields") or [])
            if str(item)
        )
        # A failed Playwright inspection is not evidence that CEAC removed a
        # control. Treating an unavailable read as an empty DOM used to turn
        # every transient adapter error into a fake ASP.NET postback, discard
        # the remaining page batch, and force one Gemini call per field.
        missing = (
            sorted(
                self._action_field_tokens_before.difference(after_fields)
            )
            if inspection_available
            else []
        )
        generation_changed = bool(
            self._action_dom_generation_before
            and after_generation
            and after_generation != self._action_dom_generation_before
        )
        evidence = {
            "generationChanged": generation_changed,
            "markedControlRemoved": bool((state or {}).get("removed")),
            "postbackStarted": bool((state or {}).get("postback")),
            "missingFieldTokens": missing,
            "inspectionUnavailable": not inspection_available,
        }
        detected = bool(
            generation_changed
            or evidence["markedControlRemoved"]
            or evidence["postbackStarted"]
            or missing
        )
        self._last_dynamic_refresh_evidence = evidence
        self._action_watch_active = False
        return detected

    def _wait_for_watched_dom_replacement(self):
        """Wait only when a postback was observed before its response arrived."""
        evidence = dict(self._last_dynamic_refresh_evidence or {})
        if not evidence.get("postbackStarted"):
            return
        if (
            evidence.get("generationChanged")
            or evidence.get("markedControlRemoved")
            or evidence.get("missingFieldTokens")
        ):
            return
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            token = f"document-{uuid4().hex}"
            try:
                state = self._page.evaluate(
                    """token => {
                        if (!window.__docflowAgentDocumentGeneration) {
                            window.__docflowAgentDocumentGeneration = token;
                        }
                        return {
                            generation:
                                window.__docflowAgentDocumentGeneration,
                            fields: Array.from(document.querySelectorAll(
                                '[data-docflow-field]'
                            )).map(item => String(
                                item.getAttribute(
                                    'data-docflow-field'
                                ) || ''
                            )).filter(Boolean)
                        };
                    }""",
                    token,
                )
            except Exception:
                state = None
            if not isinstance(state, dict):
                # An unavailable read cannot prove that the watched control
                # disappeared. Keep the bounded postback settle loop alive
                # instead of treating an empty fallback as replacement.
                try:
                    self._page.wait_for_timeout(120)
                except Exception:
                    pass
                continue
            after_generation = str(
                (state or {}).get("generation") or ""
            )
            after_fields = set(
                str(item)
                for item in list((state or {}).get("fields") or [])
                if str(item)
            )
            if (
                (
                    self._action_dom_generation_before
                    and after_generation
                    and after_generation
                    != self._action_dom_generation_before
                )
                or self._action_field_tokens_before.difference(
                    after_fields
                )
            ):
                return
            try:
                self._page.wait_for_timeout(100)
            except Exception:
                return

    def _plan_repeater_field(self, field_id, labels):
        expected = 1
        record_labels = []
        target_label = ""
        for raw_label in labels or ():
            label = str(raw_label or "")
            if not target_label:
                target_label = label.split("[control=", 1)[0].strip()
            matched = re.search(
                r"\bexpected_count=(\d{1,2})\b",
                label,
                flags=re.IGNORECASE,
            )
            if matched:
                expected = max(1, min(20, int(matched.group(1))))
            records = re.search(
                r"\brecord_labels=([^;\]]+)",
                label,
                flags=re.IGNORECASE,
            )
            if records:
                record_labels = [
                    item.strip()
                    for item in records.group(1).split("|")
                    if item.strip()
                ][:4]
        if not target_label:
            target_label = "Add Another"
        current = self._count_repeater_records(record_labels)
        action = ComputerAction(
            kind=ActionKind.CLICK,
            field_id=field_id,
            target_hint=target_label,
            reason=(
                "Deterministic repeater ensure "
                f"[expected_count={expected}; current_count={current}; "
                f"record_labels={'|'.join(record_labels)}]"
            ),
        )
        self._repeater_record_labels[str(field_id)] = tuple(record_labels)

        # Count the existing records before resolving Add Another.  CEAC can
        # render one identically named LinkButton below every existing row.
        # Once the approved target count is already present, requiring a
        # unique button incorrectly sends the ensure field to visual planning
        # and creates an ambiguous-click retry loop.  The repeater executor
        # treats current >= expected as an acknowledged no-op, and the
        # verifier independently checks the live record count before marking
        # the field complete.
        if current >= expected:
            return action

        locator = self._find_repeater_button(target_label)
        if locator is None:
            return None
        self._mark_field(locator, action)
        return action

    def _find_repeater_button(self, label):
        pattern = re.compile(
            rf"^\s*{re.escape(str(label or 'Add Another'))}\s*$",
            flags=re.IGNORECASE,
        )
        candidates = (
            self._page.get_by_role("button", name=pattern)
            .or_(self._page.get_by_role("link", name=pattern))
        )
        try:
            count = min(candidates.count(), 10)
        except Exception:
            count = 0
        visible = []
        for index in range(count):
            item = candidates.nth(index)
            try:
                if item.is_visible() and not item.is_disabled():
                    visible.append(item)
            except Exception:
                continue
        if len(visible) == 1:
            return visible[0]
        inputs = self._page.locator(
            "input[type='button'], input[type='submit']"
        )
        visible = []
        try:
            count = min(inputs.count(), 50)
        except Exception:
            count = 0
        for index in range(count):
            item = inputs.nth(index)
            try:
                value = str(item.get_attribute("value") or "").strip()
                if (
                    pattern.fullmatch(value)
                    and item.is_visible()
                    and not item.is_disabled()
                ):
                    visible.append(item)
            except Exception:
                continue
        return visible[0] if len(visible) == 1 else None

    def _count_repeater_records(self, record_labels):
        if not record_labels:
            return 1
        try:
            return max(1, int(self._page.evaluate(
                """(terms) => {
                    const normalize = (value) => String(value || '')
                        .replace(/\\s+/g, ' ').trim().toLowerCase();
                    const wanted = terms.map(normalize).filter(Boolean);
                    const visible = (element) => {
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0 && box.height > 0;
                    };
                    const matched = Array.from(document.querySelectorAll(
                        'label, legend, span, td, th'
                    )).filter((item) => {
                        if (!visible(item)) return false;
                        const text = normalize(item.innerText);
                        return wanted.some(
                            (term) => text === term
                                || text.startsWith(`${term} `)
                        );
                    });
                    const controls = new Set();
                    for (const item of matched) {
                        const associated = item.tagName.toLowerCase()
                            === 'label' ? item.control : null;
                        if (associated) {
                            controls.add(associated);
                            continue;
                        }
                        let current = item.parentElement;
                        for (
                            let depth = 0;
                            depth < 5 && current;
                            depth += 1
                        ) {
                            const found = Array.from(
                                current.querySelectorAll(
                                    'input:not([type="hidden"]), '
                                    + 'textarea, select'
                                )
                            ).filter(visible);
                            if (found.length === 1) {
                                controls.add(found[0]);
                                break;
                            }
                            current = current.parentElement;
                        }
                    }
                    return controls.size;
                }""",
                list(record_labels),
            )))
        except Exception:
            return 1

    @staticmethod
    def _repeater_labels_from_action(action):
        reason = str(getattr(action, "reason", "") or "")
        if not reason.startswith("Deterministic repeater ensure "):
            return ()
        matched = re.search(
            r"\brecord_labels=([^\]]*)",
            reason,
            flags=re.IGNORECASE,
        )
        if not matched:
            return ()
        return tuple(
            item.strip()
            for item in matched.group(1).split("|")
            if item.strip()
        )[:4]

    def _tracked_repeater_counts(self, action=None):
        """Read monotonic dynamic-section counts from the live document."""
        tracked = {
            str(field_id): tuple(labels or ())
            for field_id, labels in dict(
                self._repeater_record_labels or {}
            ).items()
            if str(field_id)
        }
        if action is not None:
            reason = str(getattr(action, "reason", "") or "")
            if reason.startswith("Deterministic repeater ensure "):
                field_id = str(
                    getattr(action, "field_id", "")
                    or getattr(action, "id", "")
                    or ""
                )
                if field_id:
                    tracked[field_id] = self._repeater_labels_from_action(
                        action
                    )
                    if getattr(action, "field_id", ""):
                        self._repeater_record_labels[field_id] = tracked[
                            field_id
                        ]
        counts = {}
        for field_id, labels in tracked.items():
            # A descriptor without record labels defines the already-present
            # first row.  This is the same conservative base used by the
            # deterministic planner and never claims an unobserved extra row.
            counts[field_id] = (
                self._count_repeater_records(labels)
                if labels
                else 1
            )
        return counts

    def plan_choice_fields(
        self,
        field_ids,
        field_labels=None,
        control_hints=None,
    ):
        """Resolve pending Yes/No fields against their own radio groups.

        Completed CEAC radio groups remain visible after each ASP.NET postback,
        so mapping ``pending fields == all visible groups`` only works for the
        first action on a page.  Match each pending field independently using
        its system-owned control suffixes and question text.  Geometry is used
        only to associate a matching question with a nearby group; any tied or
        conflicting result stays unresolved for Gemini.
        """
        self._require_page()
        approved = [str(field_id) for field_id in field_ids]
        if not approved:
            return [], []
        field_labels = dict(field_labels or {})
        control_hints = dict(control_hints or {})
        specs = []
        invalid = set()
        for field_id in approved:
            labels = tuple(field_labels.get(field_id) or ())
            occurrence, occurrence_valid = self._control_occurrence(labels)
            if not occurrence_valid:
                invalid.add(field_id)
                continue
            terms = []
            for raw_label in labels:
                term = str(raw_label or "").split(
                    "[control=", 1
                )[0].strip()
                if term and term not in terms:
                    terms.append(term)
            hints = []
            for raw_hint in tuple(control_hints.get(field_id) or ()):
                hint = str(raw_hint or "").strip()
                if hint and hint not in hints:
                    hints.append(hint)
            specs.append({
                "fieldId": field_id,
                "terms": terms[:12],
                "hints": hints[:8],
                "occurrence": occurrence,
            })
        try:
            matches = self._page.evaluate(
                """(specs) => {
                    const normalize = (value) => String(value || '')
                        .toLowerCase()
                        .replace(/[^a-z0-9\\u4e00-\\u9fff]+/g, ' ')
                        .replace(/\\s+/g, ' ').trim();
                    const compact = (value) => normalize(value)
                        .replace(/\\s+/g, '');
                    const visible = (item) => {
                        const style = getComputedStyle(item);
                        const box = item.getBoundingClientRect();
                        return !item.disabled
                            && style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0
                            && box.height > 0;
                    };
                    const radios = Array.from(document.querySelectorAll(
                        'input[type="radio"]'
                    )).filter(visible);
                    const byName = new Map();
                    for (const radio of radios) {
                        const name = String(radio.name || '');
                        if (!name) continue;
                        if (!byName.has(name)) byName.set(name, []);
                        byName.get(name).push(radio);
                    }
                    const groups = Array.from(byName.entries()).map(
                        ([name, items], order) => {
                            const boxes = items.map(
                                item => item.getBoundingClientRect()
                            );
                            const left = Math.min(...boxes.map(
                                box => box.left
                            ));
                            const top = Math.min(...boxes.map(
                                box => box.top
                            ));
                            const right = Math.max(...boxes.map(
                                box => box.right
                            ));
                            const bottom = Math.max(...boxes.map(
                                box => box.bottom
                            ));
                            return {
                                key: name,
                                name,
                                id: String(items[0].id || ''),
                                items,
                                order,
                                box: {
                                    left, top, right, bottom,
                                    width: right - left,
                                    height: bottom - top
                                },
                                identity: compact(
                                    `${name} ${items.map(
                                        item => item.id || ''
                                    ).join(' ')}`
                                ),
                                identities: [
                                    compact(name),
                                    ...items.map(item => compact(
                                        item.id || ''
                                    ))
                                ].filter(Boolean)
                            };
                        }
                    ).sort((left, right) => (
                        left.box.top - right.box.top
                        || left.box.left - right.box.left
                        || left.order - right.order
                    ));
                    if (!groups.length) return [];

                    const groupNamesWithin = (element) => new Set(
                        Array.from(element.querySelectorAll(
                            'input[type="radio"][name]'
                        )).filter(visible).map(item => item.name)
                    );
                    const anchors = Array.from(document.querySelectorAll(
                        'label, legend, span, div, td, th, p, strong, b'
                    )).filter((element) => {
                        if (!visible(element)) return false;
                        const text = normalize(element.innerText);
                        return text.length >= 2 && text.length <= 1200;
                    }).map((element) => ({
                        element,
                        text: normalize(element.innerText),
                        box: element.getBoundingClientRect()
                    }));

                    const textQuality = (text, term) => {
                        if (!term || term.length < 2) return 0;
                        if (text === term) return 430;
                        if (text.startsWith(`${term} `)) return 370;
                        if (
                            term.length >= 8
                            && term.startsWith(`${text} `)
                        ) return 330;
                        if (
                            term.length >= 8
                            && text.includes(term)
                        ) return 285;
                        if (
                            text.length >= 12
                            && term.includes(text)
                        ) return 235;
                        return 0;
                    };
                    const structuralScore = (anchor, group) => {
                        const element = anchor.element;
                        const first = group.items[0];
                        if (
                            element.tagName.toLowerCase() === 'label'
                            && element.control
                            && group.items.includes(element.control)
                        ) return 850;
                        if (
                            element.contains(first)
                            && groupNamesWithin(element).size === 1
                        ) return 720;
                        if (
                            element.parentElement?.contains(first)
                            && groupNamesWithin(
                                element.parentElement
                            ).size === 1
                        ) return 630;
                        let current = element.parentElement;
                        for (
                            let depth = 1;
                            depth <= 5 && current;
                            depth += 1
                        ) {
                            if (!current.contains(first)) {
                                current = current.parentElement;
                                continue;
                            }
                            if (groupNamesWithin(current).size === 1) {
                                return Math.max(360, 585 - depth * 42);
                            }
                            break;
                        }
                        const a = anchor.box;
                        const g = group.box;
                        const verticalCenter = Math.abs(
                            (g.top + g.height / 2)
                            - (a.top + a.height / 2)
                        );
                        const right = g.left - a.right;
                        if (
                            right >= -30
                            && right <= 820
                            && verticalCenter <= 75
                        ) {
                            return Math.max(
                                0,
                                310 - verticalCenter * 1.8
                                    - Math.max(0, right) * 0.035
                            );
                        }
                        const below = g.top - a.bottom;
                        const horizontal = Math.abs(
                            (g.left + g.width / 2)
                            - (a.left + a.width / 2)
                        );
                        if (
                            below >= -20
                            && below <= 360
                            && horizontal <= 760
                        ) {
                            return Math.max(
                                0,
                                300 - Math.max(0, below) * 0.65
                                    - horizontal * 0.035
                            );
                        }
                        return 0;
                    };

                    const scoreGroup = (spec, group) => {
                        let bestHint = 0;
                        for (const rawHint of spec.hints || []) {
                            const hint = compact(rawHint);
                            if (hint.length < 3) continue;
                            const exactSuffix = (
                                group.identities || []
                            ).some(identity => (
                                identity === hint
                                || identity.endsWith(hint)
                            ));
                            if (exactSuffix) {
                                // CEAC control names are ASP.NET paths whose
                                // stable semantic key is the final suffix.
                                // Prefer that exact suffix over a broad
                                // substring shared by adjacent questions.
                                // For example HUMAN_TRAFFICKING also appears
                                // inside the Assistance and Family controls.
                                bestHint = Math.max(
                                    bestHint,
                                    5200 + Math.min(hint.length, 120) * 8
                                );
                            } else if (group.identity.includes(hint)) {
                                bestHint = Math.max(
                                    bestHint,
                                    4000 + Math.min(hint.length, 120) * 8
                                );
                            }
                        }
                        let bestLabel = 0;
                        for (const rawTerm of spec.terms || []) {
                            const term = normalize(rawTerm);
                            if (!term) continue;
                            for (const anchor of anchors) {
                                const quality = textQuality(
                                    anchor.text, term
                                );
                                if (!quality) continue;
                                const structure = structuralScore(
                                    anchor, group
                                );
                                if (!structure) continue;
                                bestLabel = Math.max(
                                    bestLabel, quality + structure
                                );
                            }
                        }
                        if (bestHint >= bestLabel) {
                            return {
                                score: bestHint,
                                method: bestHint ? 'control-hint' : ''
                            };
                        }
                        return {
                            score: bestLabel,
                            method: bestLabel ? 'label-geometry' : ''
                        };
                    };

                    const proposals = [];
                    for (const spec of specs) {
                        const ranked = groups.map(group => ({
                            group,
                            ...scoreGroup(spec, group)
                        })).filter(item => item.score > 0).sort(
                            (left, right) => (
                                right.score - left.score
                                || left.group.box.top
                                    - right.group.box.top
                                || left.group.box.left
                                    - right.group.box.left
                            )
                        );
                        if (!ranked.length) continue;
                        let best = ranked[0];
                        if (Number.isInteger(spec.occurrence)) {
                            const sameMethod = ranked.filter(item => (
                                item.method === best.method
                                && (
                                    best.method === 'control-hint'
                                        ? item.score >= 4000
                                        : item.score >= 650
                                )
                            )).sort((left, right) => (
                                left.group.box.top
                                    - right.group.box.top
                                || left.group.box.left
                                    - right.group.box.left
                            ));
                            const index = spec.occurrence - 1;
                            if (
                                index < 0
                                || index >= sameMethod.length
                            ) continue;
                            best = sameMethod[index];
                        } else {
                            const minimum = (
                                best.method === 'control-hint'
                                    ? 4000 : 650
                            );
                            if (best.score < minimum) continue;
                            const runner = ranked.find(
                                item => item.group.key !== best.group.key
                            );
                            if (
                                runner
                                && runner.score >= minimum
                                && best.score - runner.score < 45
                            ) continue;
                        }
                        proposals.push({
                            fieldId: spec.fieldId,
                            key: best.group.key,
                            name: best.group.name,
                            id: best.group.id,
                            score: best.score,
                            method: best.method
                        });
                    }
                    const collisions = new Map();
                    for (const proposal of proposals) {
                        if (!collisions.has(proposal.key)) {
                            collisions.set(proposal.key, []);
                        }
                        collisions.get(proposal.key).push(proposal);
                    }
                    const resolved = [];
                    for (const items of collisions.values()) {
                        items.sort((left, right) => (
                            right.score - left.score
                        ));
                        if (
                            items.length > 1
                            && items[0].score - items[1].score < 45
                        ) continue;
                        resolved.push(items[0]);
                    }
                    return resolved;
                }""",
                specs,
            )
        except Exception:
            return [], approved
        if not isinstance(matches, list):
            return [], approved
        matched = {
            str((item or {}).get("fieldId") or ""): item
            for item in matches
            if isinstance(item, dict)
        }
        actions = []
        unresolved = []
        for field_id in approved:
            group = matched.get(field_id)
            name = str((group or {}).get("name") or "")
            if (
                field_id in invalid
                or not re.fullmatch(r"[A-Za-z0-9_.$:-]{1,200}", name)
            ):
                unresolved.append(field_id)
                continue
            selector = f'input[type="radio"][name="{name}"]'
            locator = self._page.locator(selector).first
            try:
                if not locator.is_visible():
                    unresolved.append(field_id)
                    continue
            except Exception:
                unresolved.append(field_id)
                continue
            action = ComputerAction(
                kind=ActionKind.SELECT,
                field_id=field_id,
                target_hint=field_id,
                reason=(
                    "Deterministic descriptor-matched CEAC radio group "
                    f"[field_id={field_id}; "
                    f"match={(group or {}).get('method') or 'unknown'}]"
                ),
            )
            try:
                self._mark_field(locator, action)
            except ControlBindingUnavailable:
                unresolved.append(field_id)
                continue
            actions.append(action)
        return actions, unresolved

    def _dispatch_receipt_snapshot(self, create=False):
        """Read/create the isolated browser's durable Next-dispatch ledger.

        The ledger contains only random action identifiers and one random scope;
        it never contains applicant data.  It is mirrored to sessionStorage so
        the actual click event can synchronously record dispatch before an
        ASP.NET navigation, and to localStorage so the same private profile can
        retain the receipt across a browser-runtime reconstruction.
        """
        if self._page is None:
            return {"authoritative": False, "scope": "", "ids": []}
        try:
            result = self._page.evaluate(
                """([key, limit, create]) => {
                    const safeLedger = (value) => {
                        if (!value || typeof value !== 'object') return null;
                        const scope = String(value.scope || '').slice(0, 200);
                        if (!scope) return null;
                        const ids = Array.isArray(value.ids)
                            ? Array.from(new Set(value.ids.map(
                                item => String(item || '').slice(0, 240)
                            ).filter(Boolean))).slice(-limit)
                            : [];
                        return {scope, ids};
                    };
                    const storage = (name) => {
                        try {
                            return window[name];
                        } catch (_error) {
                            return null;
                        }
                    };
                    const load = (target) => {
                        if (!target) return null;
                        try {
                            return safeLedger(JSON.parse(
                                target.getItem(key) || 'null'
                            ));
                        } catch (_error) {
                            return null;
                        }
                    };
                    const durable = storage('localStorage');
                    const session = storage('sessionStorage');
                    const durableLedger = load(durable);
                    const sessionLedger = load(session);
                    if (durableLedger && sessionLedger) {
                        const leftIds = [...durableLedger.ids].sort();
                        const rightIds = [...sessionLedger.ids].sort();
                        const conflict = (
                            durableLedger.scope !== sessionLedger.scope
                            || JSON.stringify(leftIds)
                                !== JSON.stringify(rightIds)
                        );
                        if (conflict) {
                            return {
                                authoritative: false,
                                sessionAuthoritative: false,
                                durableAuthoritative: false,
                                conflict: true,
                                scope: '',
                                ids: []
                            };
                        }
                    }
                    let ledger = durableLedger || sessionLedger;
                    if (!ledger && !create) {
                        return {
                            authoritative: false,
                            sessionAuthoritative: false,
                            durableAuthoritative: false,
                            conflict: false,
                            scope: '',
                            ids: []
                        };
                    }
                    if (!ledger) {
                        const random = (
                            globalThis.crypto?.randomUUID?.()
                            || `${Date.now()}-${Math.random()}`
                        );
                        ledger = {
                            scope: `dispatch-${random}`.slice(0, 200),
                            ids: []
                        };
                    }
                    let sessionAuthoritative = false;
                    let durableAuthoritative = false;
                    for (const target of [session, durable]) {
                        if (!target) continue;
                        try {
                            target.setItem(key, JSON.stringify(ledger));
                            const confirmed = load(target);
                            if (
                                confirmed
                                && confirmed.scope === ledger.scope
                            ) {
                                if (target === session) {
                                    sessionAuthoritative = true;
                                }
                                if (target === durable) {
                                    durableAuthoritative = true;
                                }
                            }
                        } catch (_error) {
                            // The other browser-owned storage can still prove
                            // the same isolated receipt scope.
                        }
                    }
                    const authoritative = (
                        sessionAuthoritative || durableAuthoritative
                    );
                    return {
                        authoritative,
                        sessionAuthoritative,
                        durableAuthoritative,
                        conflict: false,
                        scope: authoritative ? ledger.scope : '',
                        ids: authoritative ? ledger.ids : []
                    };
                }""",
                [
                    self.DISPATCH_LEDGER_KEY,
                    self.DISPATCH_LEDGER_LIMIT,
                    bool(create),
                ],
            )
        except Exception:
            return {
                "authoritative": False,
                "sessionAuthoritative": False,
                "durableAuthoritative": False,
                "conflict": False,
                "scope": "",
                "ids": [],
            }
        if not isinstance(result, dict):
            return {
                "authoritative": False,
                "conflict": False,
                "scope": "",
                "ids": [],
            }
        return {
            "authoritative": bool(result.get("authoritative")),
            "sessionAuthoritative": bool(
                result.get("sessionAuthoritative")
            ),
            "durableAuthoritative": bool(
                result.get("durableAuthoritative")
            ),
            "conflict": bool(result.get("conflict")),
            "scope": str(result.get("scope") or "")[:200],
            "ids": [
                str(item)[:240]
                for item in list(result.get("ids") or [])
                if str(item or "")
            ][-self.DISPATCH_LEDGER_LIMIT:],
        }

    def _arm_next_dispatch_receipt(self, locator, action):
        """Install a capture listener that records dispatch before navigation."""
        if (
            not action.dispatch_receipt_required
            or not action.dispatch_receipt_scope
        ):
            return False
        try:
            return bool(locator.evaluate(
                """(element, payload) => {
                    const {key, limit, scope, actionId} = payload;
                    const storage = (name) => {
                        try {
                            return window[name];
                        } catch (_error) {
                            return null;
                        }
                    };
                    const write = (target) => {
                        if (!target) return false;
                        try {
                            const current = JSON.parse(
                                target.getItem(key) || 'null'
                            );
                            if (
                                !current
                                || String(current.scope || '') !== scope
                            ) {
                                return false;
                            }
                            const ids = Array.isArray(current.ids)
                                ? current.ids.map(item => String(item || ''))
                                : [];
                            if (!ids.includes(actionId)) ids.push(actionId);
                            const ledger = {
                                scope,
                                ids: Array.from(new Set(ids.filter(Boolean)))
                                    .slice(-limit)
                            };
                            target.setItem(key, JSON.stringify(ledger));
                            const confirmed = JSON.parse(
                                target.getItem(key) || 'null'
                            );
                            return Boolean(
                                confirmed
                                && String(confirmed.scope || '') === scope
                                && Array.isArray(confirmed.ids)
                                && confirmed.ids.map(String).includes(actionId)
                            );
                        } catch (_error) {
                            return false;
                        }
                    };
                    const rollback = (target) => {
                        if (!target) return;
                        try {
                            const current = JSON.parse(
                                target.getItem(key) || 'null'
                            );
                            if (
                                !current
                                || String(current.scope || '') !== scope
                                || !Array.isArray(current.ids)
                            ) {
                                return;
                            }
                            current.ids = current.ids.map(String).filter(
                                item => item && item !== actionId
                            ).slice(-limit);
                            target.setItem(key, JSON.stringify(current));
                        } catch (_error) {
                            // A surviving receipt is conservative: recovery will
                            // observe rather than risk a duplicate click.
                        }
                    };
                    element.addEventListener('click', (event) => {
                        // Do not short-circuit: write both ledgers so a browser
                        // runtime reconstruction retains the same dispatch fact.
                        const session = storage('sessionStorage');
                        const durable = storage('localStorage');
                        const sessionRecorded = write(session);
                        const durableRecorded = write(durable);
                        const recorded = (
                            sessionRecorded && durableRecorded
                        );
                        if (!recorded) {
                            if (sessionRecorded) rollback(session);
                            if (durableRecorded) rollback(durable);
                            // Never dispatch a non-idempotent Next click unless
                            // its browser-side receipt was synchronously stored.
                            event.preventDefault();
                            event.stopImmediatePropagation();
                        }
                    }, {capture: true, once: true});
                    return true;
                }""",
                {
                    "key": self.DISPATCH_LEDGER_KEY,
                    "limit": self.DISPATCH_LEDGER_LIMIT,
                    "scope": str(action.dispatch_receipt_scope)[:200],
                    "actionId": str(action.id)[:240],
                },
            ))
        except Exception:
            return False

    @staticmethod
    def _safe_next_control_text(text, current_url):
        """Allow ordinary Next and the one non-final Photo confirmation.

        ``Next: Confirm Photo`` advances only to CEAC's photo confirmation
        stage; it is not application confirmation, signing, or submission.
        Every other Confirm/Sign/Submit control remains outside automatic
        authority.
        """
        candidate = str(text or "").strip()
        if not re.match(r"^next(?:\s*:|\s*$)", candidate, re.IGNORECASE):
            return False
        if not re.search(
            r"\b(sign|submit|confirm)\b",
            candidate,
            re.IGNORECASE,
        ):
            return True
        try:
            parsed = urlsplit(str(current_url or ""))
        except ValueError:
            return False
        return bool(
            parsed.scheme.casefold() == "https"
            and str(parsed.hostname or "").casefold() == "ceac.state.gov"
            and str(parsed.path or "").casefold().startswith(
                "/genniv/general/photo/"
            )
            and re.fullmatch(
                r"next\s*:\s*confirm\s+photo",
                candidate,
                re.IGNORECASE,
            )
        )

    def plan_next(self):
        """Resolve CEAC's fixed Next control without another model request."""
        self._require_page()
        candidates = self._page.locator(
            "input[type='submit'], input[type='button'], button, a"
        )
        matches = []
        try:
            count = min(candidates.count(), 100)
        except Exception:
            count = 0
        for index in range(count):
            item = candidates.nth(index)
            try:
                if not item.is_visible() or item.is_disabled():
                    continue
                text = str(item.inner_text(timeout=300) or "").strip()
                if not text:
                    text = str(item.get_attribute("value") or "").strip()
                if not self._safe_next_control_text(text, self._page.url):
                    continue
                matches.append((item, text))
            except Exception:
                continue
        if len(matches) != 1:
            return None
        locator, text = matches[0]
        token = f"next-{uuid4().hex}"
        locator.evaluate(
            "(el, token) => el.setAttribute('data-docflow-next', token)",
            token,
        )
        dispatch = self._dispatch_receipt_snapshot(create=True)
        if dispatch.get("conflict"):
            raise NextDispatchReceiptUnavailable(
                "Next dispatch ledger conflict: browser session and durable "
                "receipts disagree"
            )
        if not (
            dispatch.get("sessionAuthoritative")
            and dispatch.get("durableAuthoritative")
            and dispatch.get("scope")
        ):
            raise NextDispatchReceiptUnavailable(
                "Next dispatch receipt unavailable: both browser-owned ledgers "
                "must be writable before navigation"
            )
        action = ComputerAction(
            kind=ActionKind.CLICK,
            target_hint=text,
            reason="Deterministic fixed CEAC Next control",
            dispatch_receipt_required=True,
            dispatch_receipt_scope=str(dispatch.get("scope") or ""),
        )
        # Reuse the exact resolved element; role/name matching is needlessly
        # fragile on CEAC's legacy input buttons.
        self._target_selectors[text] = (
            f'[data-docflow-next="{token}"]'
        )
        return action

    def recover_existing_application(self, credentials):
        """Advance exactly one approved existing-application recovery stage.

        This path is deterministic and intentionally separate from Gemini's
        ordinary form-page tools.  Credential values never enter a model
        prompt, and the only page-control click accepted here is an exact
        ``Retrieve (an) Application`` control.  Start/Create/Continue/Submit
        are not aliases and can never pass the allowlist.
        """
        self._require_page()
        self._select_best_page()
        observation = self.observe_lightweight()
        classification = classify_ceac_page(observation)
        kind = str(classification.kind or "")

        if kind == "formal":
            return {"status": "formal", "stage": "formal"}
        if kind == "captcha" or self._recovery_captcha_visible():
            return {"status": "captcha", "stage": "captcha"}
        if kind == "session_timeout":
            # CEAC's timeout document has no safe form action.  Moving to the
            # fixed landing URL is the only navigation allowed; the next loop
            # can then choose only Retrieve, never Start/Create.
            self._page.goto(
                self.RECOVERY_LANDING_URL,
                wait_until="domcontentloaded",
                timeout=self.NAVIGATION_TIMEOUT_MS,
            )
            if self._visual_execution:
                self._ensure_visible_cursor()
            return {
                "status": "advanced",
                "stage": "timeout_to_landing",
            }
        if kind == "default":
            button = self._unique_recovery_retrieve_control()
            if button is None:
                return {
                    "status": "boundary",
                    "stage": "landing",
                    "reasonCode": "retrieve_control_unavailable",
                }
            return self._click_recovery_retrieve(
                button,
                stage="landing_retrieve",
            )
        if kind != "recovery":
            return {
                "status": "boundary",
                "stage": "unsupported",
                "reasonCode": "unsupported_recovery_page",
            }

        controls = self._recovery_controls()
        application_control = controls.get("application_id")
        surname_control = controls.get("surname_prefix")
        birth_year_control = controls.get("birth_year")
        question_control = controls.get("security_question")
        answer_control = controls.get("security_answer")
        security_stage = any((
            surname_control,
            birth_year_control,
            question_control,
            answer_control,
        ))
        retrieve = self._unique_recovery_retrieve_control()

        # Resolve the whole stage before the first keystroke.  Ambiguous or
        # mismatched controls are a boundary, not permission for partial fill.
        if security_stage:
            missing = [
                name
                for name, control in (
                    ("surname_prefix", surname_control),
                    ("birth_year", birth_year_control),
                    ("security_answer", answer_control),
                )
                if control is None
            ]
            if missing:
                return {
                    "status": "boundary",
                    "stage": "security",
                    "reasonCode": "recovery_control_unavailable",
                    "missingControls": missing,
                }
            question_choice = self._approved_recovery_question_choice(
                question_control,
                credentials.security_question,
            )
            if question_choice is None:
                return {
                    "status": "boundary",
                    "stage": "security",
                    "reasonCode": "security_question_mismatch",
                }
            if retrieve is None:
                return {
                    "status": "boundary",
                    "stage": "security",
                    "reasonCode": "retrieve_control_unavailable",
                }
            writes = []
            if application_control is not None:
                writes.append((
                    application_control,
                    credentials.application_id,
                ))
            writes.extend((
                (surname_control, credentials.surname_prefix),
                (birth_year_control, credentials.birth_year),
            ))
            for locator, value in writes:
                if not self._fill_recovery_text(locator, value):
                    return {
                        "status": "boundary",
                        "stage": "security",
                        "reasonCode": "recovery_value_verification_failed",
                    }
            if question_control is not None:
                if not self._select_recovery_question(
                    question_control,
                    question_choice,
                    credentials.security_question,
                ):
                    return {
                        "status": "boundary",
                        "stage": "security",
                        "reasonCode": "security_question_verification_failed",
                    }
            if not self._fill_recovery_text(
                answer_control,
                credentials.security_answer,
            ):
                return {
                    "status": "boundary",
                    "stage": "security",
                    "reasonCode": "recovery_value_verification_failed",
                }
            return self._click_recovery_retrieve(
                retrieve,
                stage="security_retrieve",
            )

        if application_control is not None:
            if retrieve is None:
                return {
                    "status": "boundary",
                    "stage": "application_id",
                    "reasonCode": "retrieve_control_unavailable",
                }
            if not self._fill_recovery_text(
                application_control,
                credentials.application_id,
            ):
                return {
                    "status": "boundary",
                    "stage": "application_id",
                    "reasonCode": "recovery_value_verification_failed",
                }
            return self._click_recovery_retrieve(
                retrieve,
                stage="application_id_retrieve",
            )

        return {
            "status": "boundary",
            "stage": "recovery",
            "reasonCode": "recovery_stage_unrecognized",
        }

    def _recovery_controls(self):
        descriptors = {
            "application_id": (
                (
                    "Application ID [control=text]",
                    "DS-160 Application ID [control=text]",
                ),
                (
                    "ApplicationID", "ApplicationId", "txtApplication",
                ),
            ),
            "surname_prefix": (
                (
                    "First 5 letters of Surname [control=text]",
                    "First 5 letters of applicant's surname [control=text]",
                    "First 5 letters of applicant’s surname [control=text]",
                ),
                (
                    "Surname", "SName", "LastName", "FamilyName",
                ),
            ),
            "birth_year": (
                (
                    "Year of Birth [control=text]",
                    "Applicant's year of birth [control=text]",
                    "Applicant’s year of birth [control=text]",
                ),
                ("BirthYear", "YearOfBirth", "DOBYear"),
            ),
            "security_question": (
                ("Security Question [control=select]",),
                ("SecurityQuestion", "SecQuestion", "Question"),
            ),
            "security_answer": (
                (
                    "Answer [control=text]",
                    "Security Answer [control=text]",
                ),
                ("SecurityAnswer", "SecAnswer", "Answer"),
            ),
        }
        controls = {}
        for field_id, (labels, hints) in descriptors.items():
            controls[field_id] = self._deterministic_control(
                f"docflow_recovery.{field_id}",
                labels,
                hints,
            )
        return controls

    def _recovery_captcha_visible(self):
        try:
            text = str(self._visible_page_text() or "")
        except Exception:
            text = ""
        if re.search(
            r"\bcaptcha\b|\benter\s+the\s+code\s+as\s+shown\b"
            r"|\bcode\s+shown\s+in\s+the\s+image\b",
            text,
            flags=re.IGNORECASE,
        ):
            return True
        try:
            candidates = self._page.locator(
                "img[alt*='captcha' i], img[id*='captcha' i], "
                "input[id*='captcha' i], input[name*='captcha' i]"
            )
            return any(
                candidates.nth(index).is_visible()
                for index in range(min(candidates.count(), 20))
            )
        except Exception:
            return False

    def _unique_recovery_retrieve_control(self):
        candidates = self._page.locator(
            "button, input[type='submit'], input[type='button'], a"
        )
        matches = []
        try:
            count = min(candidates.count(), 100)
        except Exception:
            count = 0
        for index in range(count):
            item = candidates.nth(index)
            try:
                if not item.is_visible() or item.is_disabled():
                    continue
                text = str(item.inner_text(timeout=300) or "").strip()
                if not text:
                    text = str(item.get_attribute("value") or "").strip()
                normalized = re.sub(
                    r"\s+", " ", text.casefold()
                ).strip()
                if normalized not in self.RECOVERY_RETRIEVE_LABELS:
                    continue
                # Defense in depth: an element with conflicting identity is
                # never treated as Retrieve merely because its text changed.
                identity = " ".join(filter(None, (
                    str(item.get_attribute("id") or ""),
                    str(item.get_attribute("name") or ""),
                    str(item.get_attribute("title") or ""),
                    str(item.get_attribute("aria-label") or ""),
                ))).casefold()
                if re.search(r"\b(?:start|create|submit|sign)\b", identity):
                    continue
                matches.append(item)
            except Exception:
                continue
        return matches[0] if len(matches) == 1 else None

    def _approved_recovery_question_choice(self, locator, approved_question):
        approved_identity = normalize_security_question(approved_question)
        if not approved_identity:
            return None
        if locator is None:
            try:
                visible_identity = normalize_security_question(
                    self._visible_page_text()
                )
            except Exception:
                visible_identity = ""
            # Exact token sequence is accepted only when the page renders the
            # approved question as text instead of a select.  This is still
            # deterministic identity matching, never semantic/fuzzy matching.
            padded = f" {visible_identity} "
            return (
                {"kind": "rendered", "identity": approved_identity}
                if f" {approved_identity} " in padded
                else None
            )
        try:
            options = locator.locator("option").evaluate_all(
                """items => items.map(item => ({
                    label: String(item.textContent || '').trim(),
                    value: String(item.value || '')
                }))"""
            )
        except Exception:
            return None
        matches = [
            item for item in options
            if normalize_security_question(item.get("label"))
            == approved_identity
        ]
        if len(matches) != 1:
            return None
        return {
            "kind": "select",
            "label": str(matches[0].get("label") or ""),
            "value": str(matches[0].get("value") or ""),
        }

    def _select_recovery_question(self, locator, choice, approved_question):
        if choice.get("kind") == "rendered":
            return True
        try:
            if not self._activate_select_option(locator, choice):
                return False
            selected = locator.locator("option:checked").first.inner_text(
                timeout=1000
            )
            return normalize_security_question(selected) == (
                normalize_security_question(approved_question)
            )
        except Exception:
            return False

    def _fill_recovery_text(self, locator, value):
        if locator is None:
            return False
        approved = str(value)
        try:
            if self._visual_execution:
                self._move_pointer_to_locator(locator, clicking=True)
                locator.focus()
                self._page.keyboard.press("ControlOrMeta+A")
                self._page.keyboard.press("Backspace")
                self._page.keyboard.type(approved, delay=16)
            else:
                locator.fill(approved)
            return str(locator.input_value(timeout=1500)) == approved
        except Exception:
            return False

    def _click_recovery_retrieve(self, locator, *, stage):
        before_identity = self._page_identity()
        try:
            if self._visual_execution:
                self._move_pointer_to_locator(locator, clicking=True)
            locator.click()
            self._wait_for_page_transition(before_identity)
            self._select_best_page()
            if self._visual_execution:
                self._ensure_visible_cursor()
            after = self.observe_lightweight()
        except Exception:
            return {
                "status": "boundary",
                "stage": stage,
                "reasonCode": "retrieve_dispatch_failed",
            }
        if self._validation_errors():
            return {
                "status": "boundary",
                "stage": stage,
                "reasonCode": "retrieve_validation_error",
            }
        after_kind = classify_ceac_page(after).kind
        after_identity = self._page_identity()
        if after_kind in {"formal", "recovery", "captcha"} and (
            after_identity != before_identity or after_kind != "recovery"
        ):
            return {
                "status": "advanced",
                "stage": stage,
                "nextKind": after_kind,
            }
        return {
            "status": "boundary",
            "stage": stage,
            "reasonCode": "retrieve_transition_unverified",
        }

    def execute(self, action):
        self._require_page()
        self._begin_action_dom_watch()
        acknowledged = True
        if action.kind == ActionKind.NAVIGATE:
            self._page.goto(
                action.value or action.target_hint,
                wait_until="domcontentloaded",
                timeout=self.NAVIGATION_TIMEOUT_MS,
            )
        elif action.kind == ActionKind.TYPE:
            locator = self._action_locator(action)
            self._mark_field(locator, action)
            self._enable_guarded_control(locator)
            if self._visual_execution:
                self._move_pointer_to_locator(locator, clicking=True)
            if self._apply_structured_field_value(locator, action):
                pass
            elif self._visual_execution:
                # The model coordinate is a visual hint, never the authority
                # for field identity. ``bind_visual_field`` has already tied
                # this action to a code-verified locator; focus that locator
                # after moving the visible pointer to its real centre.
                locator.focus()
                self._page.keyboard.press("ControlOrMeta+A")
                self._page.keyboard.press("Backspace")
                # Keep typing visibly observable without restoring the old
                # multi-second-per-field pace. A fixed delay is reproducible
                # and fast, while avoiding an instantaneous text injection.
                self._page.keyboard.type(action.value, delay=16)
            else:
                try:
                    locator.fill(action.value)
                except Exception:
                    locator.click()
                    self._page.keyboard.press("ControlOrMeta+A")
                    self._page.keyboard.insert_text(action.value)
        elif action.kind == ActionKind.SELECT:
            locator = self._action_locator(action)
            self._mark_field(locator, action)
            if self._visual_execution:
                self._move_pointer_to_locator(locator, clicking=True)
            if not self._apply_structured_field_value(locator, action):
                try:
                    locator.select_option(label=action.value)
                except Exception:
                    locator.select_option(value=action.value)
        elif action.kind == ActionKind.CLICK:
            if str(action.reason or "").startswith(
                "Deterministic repeater ensure "
            ):
                acknowledged = self._execute_repeater(action)
            elif action.field_id:
                locator = self._action_locator(action)
                self._mark_field(locator, action)
                if self._visual_execution:
                    self._move_pointer_to_locator(locator, clicking=True)
                locator.click()
                # Playwright's successful actionable click is sufficient
                # acknowledgement for an intermediate field-focus action.
                # Native date/select controls may move DOM focus into their
                # browser-owned popup even though the click succeeded. The
                # field is not considered complete here; TYPE/SELECT must still
                # pass an exact control-value verification afterward.
                acknowledged = True
            else:
                selector = self._target_selectors.get(action.target_hint)
                locator = (
                    self._page.locator(selector).first
                    if selector
                    else self._page.get_by_role(
                        "button", name=re.compile(
                            rf"^{re.escape(action.target_hint)}$",
                            re.IGNORECASE,
                        )
                    ).first
                )
                if (
                    self._is_deterministic_next(action)
                    and action.dispatch_receipt_required
                    and not self._arm_next_dispatch_receipt(locator, action)
                ):
                    raise RuntimeError(
                        "Could not arm the authoritative Next dispatch receipt"
                    )
                if self._visual_execution:
                    self._move_pointer_to_locator(locator, clicking=True)
                locator.click()
                # Do not add a second page-transition wait here. Playwright
                # already waits for a navigation directly caused by the click,
                # and the workflow owns the single bounded 20-second outcome
                # loop for delayed ASP.NET postbacks. The former extra
                # ten-second identity wait was pure latency on retained pages.
                acknowledged = False
        elif action.kind == ActionKind.SCROLL:
            if action.coordinate_x is not None:
                x = self._pixel_x(action.coordinate_x)
                y = self._pixel_y(action.coordinate_y)
                if self._visual_execution:
                    self._move_visible_pointer(x, y)
                else:
                    self._page.mouse.move(x, y)
            horizontal = (
                action.scroll_amount
                if action.scroll_direction == "right"
                else -action.scroll_amount
                if action.scroll_direction == "left"
                else 0
            )
            vertical = (
                action.scroll_amount
                if action.scroll_direction == "down"
                else -action.scroll_amount
                if action.scroll_direction == "up"
                else 0
            )
            self._page.mouse.wheel(horizontal, vertical)
        elif action.kind == ActionKind.PRESS_KEY:
            self._page.keyboard.press(action.value)
        elif action.kind == ActionKind.WAIT:
            self._page.wait_for_timeout(1000)
        else:
            raise ValueError(f"Browser cannot execute action kind: {action.kind.value}")
        # A click can synchronously replace CEAC's document or open a new tab.
        # Select the live controlled page first, then recreate the visuals
        # immediately instead of waiting for the next screenshot/model turn.
        if self._visual_execution:
            self._select_best_page()
            self._configure_timeout_target(self._page)
            self._ensure_visible_cursor()
        if acknowledged:
            self._acknowledged.append(action.id)

    def _execute_repeater(self, action):
        expected = self._reason_integer(
            action.reason, "expected_count", default=1
        )
        current = self._reason_integer(
            action.reason, "current_count", default=1
        )
        matched = re.search(
            r"\brecord_labels=([^\]]*)",
            str(action.reason or ""),
            flags=re.IGNORECASE,
        )
        record_labels = [
            item.strip()
            for item in (
                matched.group(1).split("|")
                if matched and matched.group(1)
                else []
            )
            if item.strip()
        ][:4]
        if current >= expected:
            return True
        observed = (
            self._count_repeater_records(record_labels)
            if record_labels
            else current
        )
        observed = max(int(current), int(observed or 0))
        for index in range(min(20, expected - current)):
            locator = (
                self._action_locator(action)
                if index == 0
                else self._find_repeater_button(action.target_hint)
            )
            if locator is None:
                return False
            self._mark_field(locator, action)
            if self._visual_execution:
                self._move_pointer_to_locator(locator, clicking=True)
            # Use a real browser input activation first.  CEAC's legacy
            # LinkButton has validation and WebForms event wiring around its
            # ``javascript:__doPostBack`` href; calling that function directly
            # first can report success even though no trusted activation or
            # server round-trip occurred.  That was the source of the visible
            # Add Another flash loop: the executor proved only that a timer
            # was scheduled, not that CEAC added a row.
            diagnostic = {
                "activation": "trusted_click",
                "beforeCount": observed,
                "expectedCount": expected,
                "clicked": False,
            }
            click_error = None
            try:
                try:
                    locator.click(timeout=8000)
                except TypeError:
                    # Keep lightweight adapter fakes and older Playwright
                    # shims compatible without weakening the real timeout.
                    locator.click()
                diagnostic["clicked"] = True
            except Exception as error:
                # A WebForms navigation may destroy the old execution context
                # after the click has already been delivered.  Observe the
                # live page before deciding whether a fallback is safe.
                click_error = error
                diagnostic["clickErrorType"] = type(error).__name__

            if not record_labels:
                self._last_repeater_dispatch_diagnostic = diagnostic
                if not diagnostic["clicked"] and click_error is not None:
                    raise click_error
                return True

            grew, after_click = self._wait_for_repeater_growth(
                record_labels,
                previous_count=observed,
                expected_count=min(expected, observed + 1),
                timeout_ms=9000,
            )
            diagnostic["afterTrustedClickCount"] = after_click
            if grew:
                observed = after_click
                diagnostic["result"] = "trusted_click_grew"
                self._last_repeater_dispatch_diagnostic = diagnostic
                if self._visual_execution:
                    self._ensure_visible_cursor()
                continue

            # Only after the trusted click has returned and a bounded live-DOM
            # observation proves no growth may we try CEAC's exact postback.
            # Re-resolve the link because a retained WebForms response can
            # replace the document while keeping the same URL.
            fallback = self._find_repeater_button(action.target_hint)
            if fallback is None:
                diagnostic["result"] = "link_missing_after_click"
                self._last_repeater_dispatch_diagnostic = diagnostic
                return False
            dispatched = self._dispatch_repeater_postback(fallback)
            fallback_diagnostic = dict(
                getattr(self, "_last_repeater_dispatch_diagnostic", {}) or {}
            )
            diagnostic["fallbackDispatch"] = fallback_diagnostic
            if dispatched:
                grew, after_fallback = self._wait_for_repeater_growth(
                    record_labels,
                    previous_count=observed,
                    expected_count=min(expected, observed + 1),
                    timeout_ms=9000,
                )
                diagnostic["afterFallbackCount"] = after_fallback
                if grew:
                    observed = after_fallback
                    diagnostic["result"] = "fallback_postback_grew"
                    self._last_repeater_dispatch_diagnostic = diagnostic
                    if self._visual_execution:
                        self._ensure_visible_cursor()
                    continue
            diagnostic["result"] = "no_growth_after_both_activations"
            self._last_repeater_dispatch_diagnostic = diagnostic
            return False
        if not record_labels:
            return True
        return observed >= expected

    def _wait_for_repeater_growth(
        self,
        record_labels,
        *,
        previous_count,
        expected_count,
        timeout_ms,
    ):
        """Poll the live CEAC document until one repeater row is proven."""
        deadline = time.monotonic() + max(0.1, int(timeout_ms) / 1000)
        latest = int(previous_count or 0)
        while time.monotonic() < deadline:
            try:
                self._select_best_page()
                self._configure_timeout_target(self._page)
                latest = int(
                    self._count_repeater_records(record_labels) or 0
                )
            except Exception:
                latest = int(latest or 0)
            if latest >= int(expected_count):
                return True, latest
            try:
                self._page.wait_for_timeout(180)
            except Exception:
                time.sleep(0.18)
        try:
            latest = int(
                self._count_repeater_records(record_labels) or 0
            )
        except Exception:
            pass
        return latest >= int(expected_count), latest

    def _dispatch_repeater_postback(self, locator):
        """Invoke one exact ``__doPostBack`` repeater link when available."""
        try:
            diagnostic = dict(locator.evaluate(
                """el => {
                    const href = String(
                        el.getAttribute('href') || ''
                    ).trim();
                    const matched = href.match(
                        /^javascript:\s*__doPostBack\(\s*'([^']+)'\s*,\s*'([^']*)'\s*\)\s*;?$/i
                    );
                    if (
                        !matched
                        || typeof window.__doPostBack !== 'function'
                    ) return {
                        dispatched: false,
                        href: href.slice(0, 240),
                        matched: Boolean(matched),
                        postbackType: typeof window.__doPostBack
                    };
                    const target = matched[1];
                    const argument = matched[2];
                    window.setTimeout(
                        () => window.__doPostBack(target, argument),
                        0,
                    );
                    return {
                        dispatched: true,
                        href: href.slice(0, 240),
                        matched: true,
                        postbackType: typeof window.__doPostBack,
                        target: target.slice(0, 200)
                    };
                }"""
            ) or {})
            self._last_repeater_dispatch_diagnostic = diagnostic
            return bool(diagnostic.get("dispatched"))
        except Exception as error:
            self._last_repeater_dispatch_diagnostic = {
                "dispatched": False,
                "errorType": type(error).__name__,
            }
            return False

    def repeater_dispatch_diagnostic(self):
        return dict(
            getattr(self, "_last_repeater_dispatch_diagnostic", {}) or {}
        )

    @staticmethod
    def _reason_integer(reason, name, default=0):
        matched = re.search(
            rf"\b{re.escape(str(name))}=(\d{{1,3}})\b",
            str(reason or ""),
            flags=re.IGNORECASE,
        )
        return int(matched.group(1)) if matched else int(default)

    @staticmethod
    def _is_deterministic_next(action):
        return bool(
            str(action.reason or "")
            == "Deterministic fixed CEAC Next control"
            or str(action.target_hint or "").strip().lower().startswith("next")
        )

    def _page_identity(self):
        """Return a stable identity that changes across CEAC postbacks/pages."""
        try:
            return str(self._page.evaluate(
                """() => {
                    const heading = Array.from(document.querySelectorAll(
                        'h1, h2, h3, legend'
                    )).map((item) => String(item.innerText || '').trim())
                      .find(Boolean) || '';
                    return [
                        location.pathname + location.search,
                        document.title || '',
                        heading
                    ].join('\\n');
                }"""
            ))
        except Exception:
            return f"{self._page.url}\n{self._page.title()}"

    def _wait_for_page_transition(self, before_identity):
        """Wait briefly for CEAC's legacy ASP.NET postback after Next."""
        try:
            self._page.wait_for_function(
                """before => {
                    const heading = Array.from(document.querySelectorAll(
                        'h1, h2, h3, legend'
                    )).map((item) => String(item.innerText || '').trim())
                      .find(Boolean) || '';
                    const current = [
                        location.pathname + location.search,
                        document.title || '',
                        heading
                    ].join('\\n');
                    return current !== before;
                }""",
                before_identity,
                timeout=10000,
            )
            try:
                self._page.wait_for_load_state(
                    "domcontentloaded", timeout=5000
                )
            except Exception:
                pass
        except Exception:
            # A retained page is handled by deterministic workflow
            # verification, which also reports CEAC validation errors.
            pass

    def close(self):
        for target in (self._context, self._browser):
            if target is not None:
                try:
                    target.close()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        try:
            self._temporary.cleanup()
        except Exception:
            pass
        if self._purge_profile_after_close:
            self._purge_private_profile()

    def emergency_close(self):
        """Terminate only this driver's profile-owned Chrome after a hard hang.

        Normal shutdown always goes through Playwright. This fallback is used
        only after the runtime thread exceeded its close deadline and therefore
        cannot service another Playwright command. Chromium's persistent
        profile lock identifies the owning browser PID; the live command line
        must also contain this exact private profile before a signal is sent.
        """
        profile = self._profile_dir
        if profile is None:
            return False
        try:
            resolved_profile = profile.resolve()
            lock = resolved_profile / "SingletonLock"
            if not lock.is_symlink():
                return False
            lock_target = os.readlink(lock)
            matched = re.search(r"-(\d+)$", str(lock_target or ""))
            if not matched:
                return False
            pid = int(matched.group(1))
            if pid <= 1 or pid == os.getpid():
                return False
            result = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            command = str(result.stdout or "")
            normalized = command.casefold()
            try:
                command_tokens = shlex.split(command)
            except ValueError:
                return False
            owned_profile = False
            for index, token in enumerate(command_tokens):
                candidate = ""
                if token.startswith("--user-data-dir="):
                    candidate = token.split("=", 1)[1]
                elif (
                    token == "--user-data-dir"
                    and index + 1 < len(command_tokens)
                ):
                    candidate = command_tokens[index + 1]
                if not candidate:
                    continue
                try:
                    owned_profile = (
                        Path(candidate).expanduser().resolve()
                        == resolved_profile
                    )
                except OSError:
                    owned_profile = False
                if owned_profile:
                    break
            if (
                result.returncode != 0
                or not owned_profile
                or (
                    "chrome" not in normalized
                    and "chromium" not in normalized
                )
            ):
                return False
            os.kill(pid, signal.SIGTERM)
            return True
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def bind_visual_field(self, action, labels=(), hints=()):
        """Bind a model coordinate to one code-owned semantic field.

        Coordinates may guide perception, but may not create field identity.
        Prefer a deterministic label/hint locator.  If that resolver is
        inconclusive, accept the coordinate only when the hit control itself
        has a unique direct label/ARIA/placeholder or id/name hint matching the
        system-owned descriptor (and the declared occurrence when present).
        """
        field_id = str(getattr(action, "field_id", "") or "")
        if not field_id:
            return False
        self._prune_detached_field_bindings()
        selector = self._field_selectors.get(field_id)
        if selector and field_id in self._semantic_field_bindings:
            try:
                if self._page.locator(selector).count() == 1:
                    return True
            except Exception:
                pass
        elif selector:
            # Never let a marker created by an older/aborted visual binding
            # become the first "deterministic" match on the next attempt.
            self.invalidate_field_binding(field_id)

        # Repeater rows are the one approved field action represented by a
        # button rather than an input/select. Gemini decides from the
        # screenshot that this reviewed repeater is visible; the DOM still
        # owns its identity, current row count, and bounded target count. Turn
        # the visual hint into the same idempotent ensure-N action used by the
        # deterministic verifier, without allowing an arbitrary model click.
        if self._control_kind(labels) == "ensure_repeater":
            if action.kind != ActionKind.CLICK:
                return False
            try:
                repeater = self._plan_repeater_field(field_id, labels)
            except ControlBindingUnavailable:
                repeater = None
            if repeater is None:
                return False
            action.target_hint = repeater.target_hint
            action.reason = repeater.reason
            return True

        # A Yes/No field is one logical field backed by a pair of radio
        # controls.  The generic coordinate binder deliberately requires one
        # unique control, so it cannot safely bind a visual click whose direct
        # label is only "Yes" or "No" when several questions are visible.
        # Reuse the descriptor-aware radio-group resolver here: Gemini still
        # chooses the reviewed field from the screenshot, while system-owned
        # question text/control hints prove the exact radio group and the
        # approved record continues to own the selected value.
        if self._control_kind(labels) == "yes_no":
            if action.kind != ActionKind.SELECT:
                return False
            try:
                choice_actions, unresolved = self.plan_choice_fields(
                    [field_id],
                    {field_id: tuple(labels or ())},
                    {field_id: tuple(hints or ())},
                )
            except Exception:
                return False
            return bool(choice_actions and not unresolved)

        locator = self._deterministic_control(
            field_id,
            tuple(labels or ()),
            tuple(hints or ()),
        )
        if locator is not None:
            self._mark_field(locator, action)
            return True
        if (
            action.coordinate_x is None
            or action.coordinate_y is None
        ):
            return False
        occurrence, occurrence_valid = self._control_occurrence(labels)
        if not occurrence_valid:
            return False
        terms = []
        for raw in labels or ():
            term = str(raw or "").split("[control=", 1)[0].strip()
            if term and term not in terms:
                terms.append(term)
        schema = DEFAULT_FIELD_SCHEMAS.get(field_id)
        if schema is not None and schema.label not in terms:
            terms.append(schema.label)
        normalized_hints = list(dict.fromkeys(
            item
            for item in (
                re.sub(r"[^A-Za-z0-9_-]", "", str(raw or ""))
                for raw in hints or ()
            )
            if len(item) >= 3
        ))
        if not terms and not normalized_hints:
            return False
        token = f"semantic-target-{uuid4().hex}"
        try:
            matched = bool(self._page.evaluate(
                """([x, y, token, terms, hints, occurrence, actionKind]) => {
                    const normalize = value => String(value || "")
                        .toLowerCase()
                        .replace(/[\\s:*?]+/g, " ")
                        .replace(/[^a-z0-9\\u4e00-\\u9fff /'-]/g, "")
                        .trim();
                    const normalizeId = value => String(value || "")
                        .toLowerCase().replace(/[^a-z0-9_-]/g, "");
                    const wanted = terms.map(normalize).filter(
                        term => term.length >= 2
                    );
                    const wantedHints = hints.map(normalizeId).filter(
                        hint => hint.length >= 3
                    );
                    const visible = element => {
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return style.display !== "none"
                            && style.visibility !== "hidden"
                            && style.pointerEvents !== "none"
                            && box.width > 0 && box.height > 0;
                    };
                    const allowed = control => {
                        if (!control || !visible(control)
                            || control.disabled) return false;
                        const tag = control.tagName.toLowerCase();
                        const type = String(
                            control.getAttribute("type") || "text"
                        ).toLowerCase();
                        if (actionKind === "select") {
                            return tag === "select"
                                || (
                                    tag === "input"
                                    && ["radio", "checkbox"].includes(type)
                                );
                        }
                        if (actionKind === "type") {
                            return tag === "textarea"
                                || control.isContentEditable
                                || (
                                    tag === "input"
                                    && ![
                                        "hidden", "radio", "checkbox",
                                        "button", "submit", "reset", "file",
                                        "image", "password"
                                    ].includes(type)
                                );
                        }
                        return ["input", "textarea", "select"].includes(tag)
                            || control.isContentEditable;
                    };
                    const directText = control => {
                        const values = [
                            control.getAttribute("aria-label"),
                            control.getAttribute("placeholder"),
                            control.getAttribute("title")
                        ];
                        for (const label of Array.from(control.labels || [])) {
                            values.push(label.innerText);
                        }
                        const labelledBy = String(
                            control.getAttribute("aria-labelledby") || ""
                        ).split(/\\s+/).filter(Boolean);
                        for (const id of labelledBy) {
                            values.push(document.getElementById(id)?.innerText);
                        }
                        return values.map(normalize).filter(Boolean);
                    };
                    const semanticEvidence = control => {
                        const identity = normalizeId([
                            control.id || "", control.getAttribute("name") || ""
                        ].join(" "));
                        const hintMatches = wantedHints.filter(
                            hint => identity.includes(hint)
                        );
                        const texts = directText(control);
                        const labelMatch = wanted.some(term => texts.some(
                            text => text === term
                                || (
                                    Math.min(text.length, term.length) >= 4
                                    && (
                                        text.startsWith(`${term} `)
                                        || term.startsWith(`${text} `)
                                    )
                                )
                        ));
                        return {
                            control,
                            hintCount: hintMatches.length,
                            hintLength: hintMatches.reduce(
                                (total, hint) => total + hint.length, 0
                            ),
                            labelMatch
                        };
                    };
                    let hit = document.elementFromPoint(x, y);
                    if (!hit) return false;
                    let candidate = hit.closest?.(
                        "input, textarea, select, [contenteditable='true']"
                    ) || null;
                    const label = hit.closest?.("label");
                    if (!candidate && label?.control) {
                        candidate = label.control;
                    }
                    if (!candidate || !allowed(candidate)) return false;
                    const evidence = Array.from(document.querySelectorAll(
                        "input, textarea, select, [contenteditable='true']"
                    )).filter(allowed).map(semanticEvidence).filter(
                        item => item.hintCount > 0 || item.labelMatch
                    );
                    const hinted = evidence.filter(
                        item => item.hintCount > 0
                    );
                    let ranked = [];
                    if (hinted.length) {
                        const bestCount = Math.max(
                            ...hinted.map(item => item.hintCount)
                        );
                        const bestLength = Math.max(
                            ...hinted.filter(
                                item => item.hintCount === bestCount
                            ).map(item => item.hintLength)
                        );
                        ranked = hinted.filter(item => (
                            item.hintCount === bestCount
                            && item.hintLength === bestLength
                        ));
                    } else {
                        ranked = evidence.filter(item => item.labelMatch);
                    }
                    const candidates = ranked.map(item => item.control);
                    if (!candidates.includes(candidate)) return false;
                    candidates.sort((left, right) => {
                        const a = left.getBoundingClientRect();
                        const b = right.getBoundingClientRect();
                        return a.top - b.top || a.left - b.left;
                    });
                    let selected = null;
                    if (occurrence === null) {
                        if (candidates.length !== 1) return false;
                        selected = candidates[0];
                    } else {
                        const index = occurrence - 1;
                        if (index < 0 || index >= candidates.length) {
                            return false;
                        }
                        selected = candidates[index];
                    }
                    if (selected !== candidate) return false;
                    candidate.setAttribute(
                        "data-docflow-semantic-target", token
                    );
                    return true;
                }""",
                [
                    self._pixel_x(action.coordinate_x),
                    self._pixel_y(action.coordinate_y),
                    token,
                    terms[:12],
                    normalized_hints[:12],
                    occurrence,
                    str(action.kind.value),
                ],
            ))
        except Exception:
            return False
        if not matched:
            return False
        locator = self._page.locator(
            f'[data-docflow-semantic-target="{token}"]'
        )
        try:
            if locator.count() != 1:
                return False
            self._mark_field(locator.first, action)
            return True
        except Exception:
            return False

    def _action_locator(self, action):
        if action.field_id and action.field_id in self._field_selectors:
            try:
                marked = self._page.locator(
                    self._field_selectors[action.field_id]
                )
                if (
                    action.field_id in self._semantic_field_bindings
                    and marked.count() == 1
                ):
                    return marked.first
            except Exception as error:
                raise ControlBindingUnavailable(
                    "The semantic DOM binding could not be inspected"
                ) from error
        if action.field_id:
            raise ControlBindingUnavailable(
                "Field action has no code-verified semantic DOM binding"
            )
        if action.coordinate_x is not None:
            raise RuntimeError(
                "Unbound coordinate clicks are forbidden"
            )
        focused = self._page.locator(":focus")
        if focused.count():
            return focused.first
        return self._page.get_by_label(action.target_hint, exact=False).first

    def constrain_action_value(self, action):
        """Fit a text value to the live control before any DOM mutation.

        CEAC silently truncates inputs whose ``maxlength`` is smaller than the
        approved DocFlow value.  Letting the browser perform that truncation
        creates a permanent verification mismatch.  Read the constraint from
        the already semantically bound control, normalize once, and let the
        workflow persist the effective value before it checkpoints/executes
        the action.
        """
        if (
            action.kind != ActionKind.TYPE
            or not str(action.field_id or "")
        ):
            return None
        locator = self._action_locator(action)
        try:
            details = locator.evaluate(
                """el => ({
                    tag: el.tagName.toLowerCase(),
                    type: String(el.getAttribute('type') || 'text').toLowerCase(),
                    hasMaxLength: el.hasAttribute('maxlength'),
                    maxLength: Number(el.maxLength),
                    composite: Boolean(
                        el.getAttribute('data-docflow-segment-group')
                        || el.getAttribute('data-docflow-date-group')
                        || el.getAttribute('data-docflow-duration-group')
                    )
                })"""
            )
        except Exception as error:
            raise ControlBindingUnavailable(
                "The semantic DOM binding disappeared during constraint inspection"
            ) from error
        if not isinstance(details, dict):
            raise ControlBindingUnavailable(
                "The live control constraint metadata is unavailable"
            )
        if (
            details.get("composite")
            or details.get("tag") not in {"input", "textarea"}
            or details.get("type") in {
                "date", "radio", "checkbox", "hidden", "button", "submit",
                "reset", "file", "image", "password",
            }
        ):
            return None
        original = str(action.value or "")
        normalized_field_id = str(action.field_id or "").casefold()
        phone_field = normalized_field_id.endswith((
            ".phone",
            ".homephone",
            ".primaryphone",
            ".secondaryphone",
            ".workphone",
        ))
        normalized = original
        normalization = ""
        if phone_field:
            normalized = re.sub(r"[^0-9]", "", original)
            normalization = "phone-digits-only"
            if not 5 <= len(normalized) <= 15:
                raise ControlValueConstraintError(
                    "The approved phone number must contain 5-15 digits"
                )

        maximum = None
        if details.get("hasMaxLength"):
            try:
                maximum = int(details.get("maxLength"))
            except (TypeError, ValueError, OverflowError) as error:
                raise ControlBindingUnavailable(
                    "The live control constraint metadata is unavailable"
                ) from error
            if maximum < 1:
                raise ControlValueConstraintError(
                    "The semantically bound CEAC control accepts no text"
                )
            if len(normalized) > maximum:
                normalized = normalized[:maximum].rstrip()
        elif not phone_field:
            return None

        if phone_field and not 5 <= len(normalized) <= 15:
            raise ControlValueConstraintError(
                "The approved phone number must contain 5-15 digits"
            )

        if normalized == original:
            return None
        if not normalized:
            raise ControlValueConstraintError(
                "The approved value cannot fit the CEAC control"
            )
        action.value = normalized
        return {
            "fieldId": str(action.field_id),
            "originalLength": len(original),
            "effectiveLength": len(normalized),
            "maxLength": maximum if maximum is not None else 15,
            "normalization": normalization or "maxlength",
        }

    @property
    def _visual_execution(self):
        return self.execution_mode in {
            "visual", "native-visual", "codex-like"
        }

    def _move_pointer(self, action):
        x = self._pixel_x(action.coordinate_x)
        y = self._pixel_y(action.coordinate_y)
        self._move_visible_pointer(x, y, clicking=True)

    def _move_pointer_to_locator(self, locator, clicking=False):
        try:
            box = locator.bounding_box(timeout=3000)
        except Exception:
            box = None
        if not box:
            return
        self._move_visible_pointer(
            box["x"] + box["width"] / 2,
            box["y"] + box["height"] / 2,
            clicking=clicking,
        )

    @staticmethod
    def _human_pointer_path(start_x, start_y, end_x, end_y):
        """Return a deterministic, gently curved human-style pointer path.

        The path intentionally contains no randomness so a run remains
        reproducible. Alternating the curve direction from the coordinates
        still prevents the mechanical two-point/straight-line appearance.
        """
        start_x = float(start_x)
        start_y = float(start_y)
        end_x = float(end_x)
        end_y = float(end_y)
        delta_x = end_x - start_x
        delta_y = end_y - start_y
        distance = math.hypot(delta_x, delta_y)
        if distance < 3:
            return [(end_x, end_y)]

        steps = min(28, max(10, int(distance / 46) + 9))
        perpendicular_x = -delta_y / distance
        perpendicular_y = delta_x / distance
        direction = -1 if int(start_x + start_y + end_x + end_y) % 2 else 1
        bend = direction * min(76.0, max(12.0, distance * 0.12))
        control_1 = (
            start_x + delta_x * 0.28 + perpendicular_x * bend,
            start_y + delta_y * 0.28 + perpendicular_y * bend,
        )
        control_2 = (
            start_x + delta_x * 0.74 - perpendicular_x * bend * 0.35,
            start_y + delta_y * 0.74 - perpendicular_y * bend * 0.35,
        )

        points = []
        for index in range(1, steps + 1):
            raw_t = index / steps
            # Smooth acceleration and deceleration before evaluating the
            # cubic Bezier curve.
            t = raw_t * raw_t * (3.0 - 2.0 * raw_t)
            inverse = 1.0 - t
            x = (
                inverse ** 3 * start_x
                + 3 * inverse ** 2 * t * control_1[0]
                + 3 * inverse * t ** 2 * control_2[0]
                + t ** 3 * end_x
            )
            y = (
                inverse ** 3 * start_y
                + 3 * inverse ** 2 * t * control_1[1]
                + 3 * inverse * t ** 2 * control_2[1]
                + t ** 3 * end_y
            )
            points.append((x, y))
        points[-1] = (end_x, end_y)
        return points

    @staticmethod
    def _pointer_travel_ms(start_x, start_y, end_x, end_y):
        distance = math.hypot(
            float(end_x) - float(start_x),
            float(end_y) - float(start_y),
        )
        # Keep the whole movement visible but comfortably below the old
        # half-second ceiling.  Normal same-form moves land around 120-260 ms.
        return min(380, max(100, int(82 + distance * 0.24)))

    def _move_visible_pointer(self, x, y, clicking=False):
        self._ensure_visible_cursor()
        target_x = float(x)
        target_y = float(y)
        path = self._human_pointer_path(
            self._cursor_x,
            self._cursor_y,
            target_x,
            target_y,
        )
        travel_ms = self._pointer_travel_ms(
            self._cursor_x,
            self._cursor_y,
            target_x,
            target_y,
        )
        step_ms = max(7, round(travel_ms / max(1, len(path))))
        for point_x, point_y in path:
            self._page.mouse.move(point_x, point_y)
            self._page.wait_for_timeout(step_ms)
        self._cursor_x = target_x
        self._cursor_y = target_y
        lease_ms = self._visual_lease_ms(self._visual_status_state)
        try:
            self._page.evaluate(
                """([x, y, clicking, state, message, leaseMs]) => {
                    const now = Date.now();
                    const cursor = document.getElementById(
                        "docflow-agent-visible-cursor"
                    );
                    if (!cursor) return;
                    cursor.style.left = `${x}px`;
                    cursor.style.top = `${y}px`;
                    cursor.dataset.state = state;
                    cursor.dataset.clicking = clicking ? "true" : "false";
                    const badge = document.getElementById(
                        "docflow-agent-visual-status"
                    );
                    const leaseUntil = leaseMs > 0
                        ? now + leaseMs
                        : 0;
                    if (badge) {
                        badge.dataset.leaseUntil = String(leaseUntil);
                    }
                    try {
                        const saved = JSON.parse(
                            sessionStorage.getItem(
                                "__docflowAgentVisualState"
                            ) || "{}"
                        );
                        sessionStorage.setItem(
                            "__docflowAgentVisualState",
                            JSON.stringify({
                                ...saved,
                                state,
                                message: message || "",
                                startedAt: Number(
                                    saved.startedAt || now
                                ),
                                x,
                                y,
                                leaseUntil
                            })
                        );
                    } catch (_error) {
                        // Some browser-owned/error documents deny storage.
                    }
                    if (clicking) {
                        window.clearTimeout(
                            window.__docflowAgentClickTimer
                        );
                        window.__docflowAgentClickTimer = window.setTimeout(
                            () => {
                                const current = document.getElementById(
                                    "docflow-agent-visible-cursor"
                                );
                                if (current) {
                                    current.dataset.clicking = "false";
                                }
                            },
                            320
                        );
                    }
                }""",
                [
                    target_x,
                    target_y,
                    bool(clicking),
                    self._visual_status_state,
                    self._visual_status_message,
                    lease_ms,
                ],
            )
            # Keep the press animation active for the real click that follows.
            if clicking:
                self._page.wait_for_timeout(55)
        except Exception:
            pass

    @staticmethod
    def _visual_document_init_script():
        """Install an early lightweight overlay in every newly created document.

        The complete styling is applied by ``_ensure_visible_cursor`` once the
        action returns.  This init script runs at document creation and inserts
        a minimal cursor/status as soon as ``body`` exists, covering the
        otherwise blank interval while Playwright is still waiting for CEAC
        navigation.
        """
        return r"""
        (() => {
          if (window.__docflowAgentOverlayBootstrapInstalled) {
            if (window.__docflowAgentRenderHeartbeat) {
              window.__docflowAgentRenderHeartbeat();
            }
            return;
          }
          window.__docflowAgentOverlayBootstrapInstalled = true;
          const labels = {
            observing: "Gemini · 读取页面",
            thinking: "Gemini · 规划本页",
            working: "Gemini · 正在填写",
            navigating: "Gemini · 正在进入下一页",
            paused: "Gemini · 已暂停，需要处理",
            blocked: "Gemini · 已停止，需要处理",
            disconnected: "Gemini · 连接中断",
            error: "Gemini · 运行失败",
            completed: "Gemini · 本轮完成"
          };
          const savedState = () => {
            try {
              const parsed = JSON.parse(
                sessionStorage.getItem(
                  "__docflowAgentVisualState"
                ) || "{}"
              );
              return {
                state: String(parsed.state || "observing"),
                message: String(parsed.message || ""),
                startedAt: Number(parsed.startedAt || Date.now()),
                x: Number.isFinite(Number(parsed.x))
                  ? Number(parsed.x) : 24,
                y: Number.isFinite(Number(parsed.y))
                  ? Number(parsed.y) : 24,
                leaseUntil: Number(
                  parsed.leaseUntil || Date.now() + 15000
                )
              };
            } catch (_error) {
              return {
                state: "observing",
                message: "正在读取新页面",
                startedAt: Date.now(),
                x: 24,
                y: 24,
                leaseUntil: Date.now() + 15000
              };
            }
          };
          const install = () => {
            const host = document.documentElement || document.body;
            if (!host) return;
            window.__docflowAgentOverlayInstalledReadyState =
              document.readyState;
            if (!document.getElementById(
              "docflow-agent-early-visual-style"
            )) {
              const style = document.createElement("style");
              style.id = "docflow-agent-early-visual-style";
              style.textContent = `
                #docflow-agent-visible-cursor {
                  position: fixed; left: 24px; top: 24px;
                  width: 18px; height: 24px; z-index: 2147483646;
                  pointer-events: none; background: #ff3b30;
                  clip-path: polygon(0 0,0 88%,25% 66%,42% 100%,
                    56% 92%,40% 60%,74% 58%);
                  filter: drop-shadow(0 1px 2px rgba(0,0,0,.75));
                }
                #docflow-agent-visual-status {
                  position: fixed; left: 12px; top: 12px;
                  z-index: 2147483647; pointer-events: none;
                  max-width: min(360px,calc(100vw - 36px));
                  padding: 9px 12px; border-radius: 12px;
                  color: #fff; background: rgba(16,18,17,.94);
                  box-shadow: 0 8px 26px rgba(0,0,0,.28);
                  font: 12px -apple-system,BlinkMacSystemFont,
                    "Segoe UI",sans-serif;
                }
                #docflow-agent-visual-status
                  [data-docflow-status-label] { font-weight: 700; }
                #docflow-agent-visual-status
                  [data-docflow-status-detail] {
                    display: block; margin-top: 4px; opacity: .74;
                    font-size: 11px;
                }
                #docflow-agent-visible-cursor[
                  data-docflow-capture-hidden="true"
                ],
                #docflow-agent-visual-status[
                  data-docflow-capture-hidden="true"
                ] {
                  visibility: hidden !important;
                }
              `;
              (document.head || host).appendChild(style);
            }
            const saved = savedState();
            let cursor = document.getElementById(
              "docflow-agent-visible-cursor"
            );
            if (!cursor) {
              cursor = document.createElement("div");
              cursor.id = "docflow-agent-visible-cursor";
              cursor.setAttribute("aria-hidden", "true");
              host.appendChild(cursor);
            }
            cursor.style.left = `${saved.x}px`;
            cursor.style.top = `${saved.y}px`;
            cursor.dataset.state = saved.state;
            let badge = document.getElementById(
              "docflow-agent-visual-status"
            );
            if (!badge) {
              badge = document.createElement("div");
              badge.id = "docflow-agent-visual-status";
              badge.setAttribute("role", "status");
              badge.setAttribute("aria-live", "polite");
              badge.innerHTML = `
                <span data-docflow-status-label></span>
                <span data-docflow-status-detail></span>
              `;
              host.appendChild(badge);
            }
            badge.dataset.state = saved.state;
            badge.dataset.baseMessage = saved.message;
            badge.dataset.startedAt = String(saved.startedAt);
            badge.dataset.leaseUntil = String(saved.leaseUntil);
            const placeStatus = () => {
              const current = document.getElementById(
                "docflow-agent-visual-status"
              );
              if (!current) return;
              const margin = 12;
              const viewportWidth = Math.max(
                1, document.documentElement?.clientWidth || innerWidth || 1
              );
              const viewportHeight = Math.max(
                1, document.documentElement?.clientHeight || innerHeight || 1
              );
              current.style.right = "auto";
              current.style.bottom = "auto";
              const currentRect = current.getBoundingClientRect();
              const width = Math.min(
                Math.max(150, currentRect.width || 260),
                Math.max(1, viewportWidth - margin * 2)
              );
              const height = Math.min(
                Math.max(42, currentRect.height || 58),
                Math.max(1, viewportHeight - margin * 2)
              );
              const clamp = (value, maximum) => Math.max(
                margin, Math.min(value, Math.max(margin, maximum - margin))
              );
              const candidates = [
                ["right-middle", viewportWidth - width - margin,
                  (viewportHeight - height) / 2],
                ["left-middle", margin, (viewportHeight - height) / 2],
                ["top-right", viewportWidth - width - margin, margin],
                ["top-left", margin, margin],
                ["bottom-right", viewportWidth - width - margin,
                  viewportHeight - height - margin],
                ["bottom-left", margin, viewportHeight - height - margin],
                ["top-middle", (viewportWidth - width) / 2, margin],
                ["bottom-middle", (viewportWidth - width) / 2,
                  viewportHeight - height - margin]
              ].map(([name, x, y]) => ({
                name,
                x: clamp(x, viewportWidth - width),
                y: clamp(y, viewportHeight - height),
                width,
                height
              }));
              const interactiveSelector = [
                "a[href]", "button", "input:not([type='hidden'])",
                "select", "textarea", "[role='button']", "[onclick]",
                "[contenteditable='true']"
              ].join(",");
              const interactives = Array.from(
                document.querySelectorAll(interactiveSelector)
              ).filter((element) => !element.closest(
                "#docflow-agent-visual-status, "
                + "#docflow-agent-visible-cursor"
              )).slice(0, 500);
              const score = (candidate) => {
                let total = 0;
                for (const element of interactives) {
                  const style = getComputedStyle(element);
                  if (
                    style.display === "none"
                    || style.visibility === "hidden"
                    || style.pointerEvents === "none"
                  ) continue;
                  const box = element.getBoundingClientRect();
                  const overlapWidth = Math.max(
                    0,
                    Math.min(candidate.x + candidate.width, box.right)
                      - Math.max(candidate.x, box.left)
                  );
                  const overlapHeight = Math.max(
                    0,
                    Math.min(candidate.y + candidate.height, box.bottom)
                      - Math.max(candidate.y, box.top)
                  );
                  if (overlapWidth && overlapHeight) {
                    total += 100000 + overlapWidth * overlapHeight;
                  }
                }
                for (const horizontal of [0.15, 0.5, 0.85]) {
                  for (const vertical of [0.2, 0.5, 0.8]) {
                    const under = document.elementFromPoint(
                      candidate.x + candidate.width * horizontal,
                      candidate.y + candidate.height * vertical
                    );
                    if (!under) continue;
                    if (under.closest?.(interactiveSelector)) {
                      total += 10000;
                    } else if (String(under.innerText || "").trim()) {
                      total += 4;
                    } else if (
                      getComputedStyle(under).backgroundColor
                        !== "rgba(0, 0, 0, 0)"
                    ) {
                      total += 1;
                    }
                  }
                }
                return total;
              };
              const selected = candidates.reduce((best, candidate) => (
                score(candidate) < score(best) ? candidate : best
              ));
              current.dataset.placement = selected.name;
              current.style.left = `${Math.round(selected.x)}px`;
              current.style.top = `${Math.round(selected.y)}px`;
            };
            window.__docflowAgentPlaceStatus = placeStatus;
            const label = badge.querySelector(
              "[data-docflow-status-label]"
            );
            if (label) {
              label.textContent = labels[saved.state]
                || "Gemini · 工作中";
            }
            const render = () => {
              const current = document.getElementById(
                "docflow-agent-visual-status"
              );
              if (!current) return;
              placeStatus();
              const detail = current.querySelector(
                "[data-docflow-status-detail]"
              );
              if (!detail) return;
              const activeStates = [
                "observing", "thinking", "working", "navigating"
              ];
              if (
                activeStates.includes(current.dataset.state)
                && Number(current.dataset.leaseUntil || 0) > 0
                && Date.now() > Number(current.dataset.leaseUntil)
              ) {
                current.dataset.state = "disconnected";
                current.dataset.baseMessage =
                  "超过运行租约，没有收到 Agent Core 更新";
                const currentCursor = document.getElementById(
                  "docflow-agent-visible-cursor"
                );
                if (currentCursor) {
                  currentCursor.dataset.state = "disconnected";
                }
                try {
                  const stored = savedState();
                  sessionStorage.setItem(
                    "__docflowAgentVisualState",
                    JSON.stringify({
                      ...stored,
                      state: "disconnected",
                      message: current.dataset.baseMessage,
                      leaseUntil: Number(current.dataset.leaseUntil)
                    })
                  );
                } catch (_error) {
                  // Storage can be denied on browser-owned pages.
                }
              }
              const currentLabel = current.querySelector(
                "[data-docflow-status-label]"
              );
              if (currentLabel) {
                currentLabel.textContent = labels[current.dataset.state]
                  || "Gemini · 工作中";
              }
              const elapsed = Math.max(
                0,
                Math.floor(
                  (
                    Date.now()
                    - Number(current.dataset.startedAt || Date.now())
                  ) / 1000
                )
              );
              const active = activeStates.includes(current.dataset.state);
              detail.textContent = [
                current.dataset.baseMessage || "",
                active && elapsed >= 2
                  ? `仍在工作 · ${elapsed} 秒` : ""
              ].filter(Boolean).join(" · ");
              detail.hidden = !detail.textContent;
              placeStatus();
            };
            window.__docflowAgentRenderHeartbeat = render;
            render();
            if (!window.__docflowAgentEarlyHeartbeatTimer) {
              window.__docflowAgentEarlyHeartbeatTimer =
                window.setInterval(render, 1000);
            }
            if (!window.__docflowAgentOverlayGuard) {
              window.__docflowAgentOverlayGuard = new MutationObserver(() => {
                if (
                  document.getElementById(
                    "docflow-agent-visible-cursor"
                  )
                  && document.getElementById(
                    "docflow-agent-visual-status"
                  )
                  && (
                    document.getElementById(
                      "docflow-agent-early-visual-style"
                    )
                    || document.getElementById(
                      "docflow-agent-visible-cursor-style"
                    )
                  )
                ) return;
                queueMicrotask(install);
              });
              window.__docflowAgentOverlayGuard.observe(
                document.documentElement,
                {childList: true, subtree: true}
              );
            }
          };
          if (document.documentElement) {
            install();
          } else {
            const observer = new MutationObserver(() => {
              if (!document.documentElement) return;
              observer.disconnect();
              install();
            });
            observer.observe(document, {childList: true});
          }
        })();
        """

    def _ensure_visible_cursor(self):
        try:
            self._page.evaluate(
                """([x, y, state, message, leaseMs]) => {
                    const now = Date.now();
                    let saved = {};
                    try {
                      saved = JSON.parse(
                        sessionStorage.getItem(
                          "__docflowAgentVisualState"
                        ) || "{}"
                      );
                    } catch (_error) {
                      saved = {};
                    }
                    const sameOperation = (
                      String(saved.state || "") === state
                      && String(saved.message || "") === (message || "")
                    );
                    const startedAt = sameOperation
                      ? Number(saved.startedAt || now)
                      : now;
                    const leaseUntil = leaseMs > 0
                      ? now + leaseMs
                      : 0;
                    if (!document.getElementById(
                        "docflow-agent-visible-cursor-style"
                    )) {
                      const style = document.createElement("style");
                      style.id = "docflow-agent-visible-cursor-style";
                      style.textContent = `
                      #docflow-agent-visible-cursor {
                        position: fixed;
                        width: 19px;
                        height: 25px;
                        z-index: 2147483646;
                        pointer-events: none;
                        transition: transform 105ms ease;
                        filter: drop-shadow(0 1px 2px rgba(0,0,0,.75));
                        background: transparent;
                        clip-path: none;
                        will-change: left, top, transform;
                      }
                      #docflow-agent-visible-cursor::before {
                        content: "";
                        position: absolute;
                        inset: 0;
                        background: #ff3b30;
                        clip-path: polygon(0 0, 0 88%, 24% 66%, 39% 100%,
                          54% 92%, 39% 60%, 73% 58%);
                      }
                      #docflow-agent-visible-cursor[
                        data-state="thinking"
                      ]::before,
                      #docflow-agent-visible-cursor[
                        data-state="observing"
                      ]::before,
                      #docflow-agent-visible-cursor[
                        data-state="navigating"
                      ]::before {
                        animation: docflow-agent-cursor-breathe 1.4s
                          ease-in-out infinite;
                      }
                      #docflow-agent-visible-cursor[data-clicking="true"] {
                        transform: scale(.78);
                      }
                      #docflow-agent-visual-status {
                        position: fixed;
                        left: 12px;
                        top: 12px;
                        z-index: 2147483647;
                        min-width: 150px;
                        max-width: min(360px, calc(100vw - 36px));
                        box-sizing: border-box;
                        padding: 10px 13px;
                        border: 1px solid rgba(255,255,255,.2);
                        border-radius: 13px;
                        color: #fff;
                        background: rgba(16,18,17,.94);
                        box-shadow: 0 8px 26px rgba(0,0,0,.28);
                        pointer-events: none;
                        font-family: -apple-system, BlinkMacSystemFont,
                          "Segoe UI", sans-serif;
                      }
                      #docflow-agent-visual-status::before {
                        content: "";
                        display: inline-block;
                        width: 8px;
                        height: 8px;
                        margin-right: 7px;
                        border-radius: 50%;
                        background: #34c759;
                        box-shadow: 0 0 0 4px rgba(52,199,89,.16);
                      }
                      #docflow-agent-visual-status[data-state="thinking"]::before,
                      #docflow-agent-visual-status[data-state="observing"]::before,
                      #docflow-agent-visual-status[data-state="navigating"]::before {
                        background: #64d2ff;
                        box-shadow: 0 0 0 4px rgba(100,210,255,.16);
                        animation: docflow-agent-pulse 1.1s infinite;
                      }
                      #docflow-agent-visual-status[data-state="paused"]::before {
                        background: #ff9f0a;
                        box-shadow: 0 0 0 4px rgba(255,159,10,.18);
                      }
                      #docflow-agent-visual-status[data-state="blocked"]::before,
                      #docflow-agent-visual-status[
                        data-state="disconnected"
                      ]::before,
                      #docflow-agent-visual-status[data-state="error"]::before {
                        background: #ff453a;
                        box-shadow: 0 0 0 4px rgba(255,69,58,.18);
                      }
                      #docflow-agent-visual-status [data-docflow-status-label] {
                        font-size: 13px;
                        font-weight: 700;
                      }
                      #docflow-agent-visual-status [data-docflow-status-detail] {
                        display: block;
                        margin-top: 5px;
                        color: rgba(255,255,255,.74);
                        font-size: 11px;
                        line-height: 1.35;
                      }
                      #docflow-agent-visible-cursor[
                        data-docflow-capture-hidden="true"
                      ],
                      #docflow-agent-visual-status[
                        data-docflow-capture-hidden="true"
                      ] {
                        visibility: hidden !important;
                      }
                      @keyframes docflow-agent-pulse {
                        50% { opacity: .38; }
                      }
                      @keyframes docflow-agent-cursor-breathe {
                        50% {
                          filter: brightness(1.35);
                          opacity: .72;
                        }
                      }
                    `;
                    (document.head || document.documentElement).appendChild(
                        style
                    );
                    }
                    let cursor = document.getElementById(
                        "docflow-agent-visible-cursor"
                    );
                    if (!cursor) {
                      cursor = document.createElement("div");
                      cursor.id = "docflow-agent-visible-cursor";
                      cursor.setAttribute("aria-hidden", "true");
                      cursor.dataset.clicking = "false";
                      (document.body || document.documentElement).appendChild(
                          cursor
                      );
                    }
                    cursor.style.left = `${x}px`;
                    cursor.style.top = `${y}px`;
                    cursor.dataset.state = state;
                    if (!window.__docflowAgentTracksMouse) {
                      document.addEventListener(
                        "mousemove",
                        event => {
                          const current = document.getElementById(
                            "docflow-agent-visible-cursor"
                          );
                          if (!current) return;
                          current.style.left = `${event.clientX}px`;
                          current.style.top = `${event.clientY}px`;
                          try {
                            const stored = JSON.parse(
                              sessionStorage.getItem(
                                "__docflowAgentVisualState"
                              ) || "{}"
                            );
                            sessionStorage.setItem(
                              "__docflowAgentVisualState",
                              JSON.stringify({
                                ...stored,
                                x: event.clientX,
                                y: event.clientY
                              })
                            );
                          } catch (_error) {
                            // Storage can be denied on browser-owned pages.
                          }
                        },
                        {passive: true}
                      );
                      window.__docflowAgentTracksMouse = true;
                    }

                    let badge = document.getElementById(
                        "docflow-agent-visual-status"
                    );
                    if (!badge) {
                      badge = document.createElement("div");
                      badge.id = "docflow-agent-visual-status";
                      badge.setAttribute("role", "status");
                      badge.setAttribute("aria-live", "polite");
                      badge.innerHTML = `
                        <span data-docflow-status-label></span>
                        <span data-docflow-status-detail></span>
                      `;
                      (document.body || document.documentElement).appendChild(
                          badge
                      );
                    }
                    const placeStatus = () => {
                      const current = document.getElementById(
                        "docflow-agent-visual-status"
                      );
                      if (!current) return;
                      const margin = 12;
                      const viewportWidth = Math.max(
                        1,
                        document.documentElement?.clientWidth
                          || innerWidth || 1
                      );
                      const viewportHeight = Math.max(
                        1,
                        document.documentElement?.clientHeight
                          || innerHeight || 1
                      );
                      current.style.right = "auto";
                      current.style.bottom = "auto";
                      const currentRect = current.getBoundingClientRect();
                      const width = Math.min(
                        Math.max(150, currentRect.width || 260),
                        Math.max(1, viewportWidth - margin * 2)
                      );
                      const height = Math.min(
                        Math.max(42, currentRect.height || 58),
                        Math.max(1, viewportHeight - margin * 2)
                      );
                      const positions = [
                        ["right-middle", viewportWidth - width - margin,
                          (viewportHeight - height) / 2],
                        ["left-middle", margin,
                          (viewportHeight - height) / 2],
                        ["top-right", viewportWidth - width - margin, margin],
                        ["top-left", margin, margin],
                        ["bottom-right", viewportWidth - width - margin,
                          viewportHeight - height - margin],
                        ["bottom-left", margin,
                          viewportHeight - height - margin],
                        ["top-middle", (viewportWidth - width) / 2, margin],
                        ["bottom-middle", (viewportWidth - width) / 2,
                          viewportHeight - height - margin]
                      ];
                      const candidates = positions.map(([name, x, y]) => ({
                        name,
                        x: Math.max(
                          margin,
                          Math.min(x, viewportWidth - width - margin)
                        ),
                        y: Math.max(
                          margin,
                          Math.min(y, viewportHeight - height - margin)
                        ),
                        width,
                        height
                      }));
                      const interactiveSelector = [
                        "a[href]", "button",
                        "input:not([type='hidden'])", "select", "textarea",
                        "[role='button']", "[onclick]",
                        "[contenteditable='true']"
                      ].join(",");
                      const controls = Array.from(
                        document.querySelectorAll(interactiveSelector)
                      ).filter((element) => !element.closest(
                        "#docflow-agent-visual-status, "
                        + "#docflow-agent-visible-cursor"
                      )).slice(0, 500);
                      const score = (candidate) => {
                        let total = 0;
                        for (const element of controls) {
                          const style = getComputedStyle(element);
                          if (
                            style.display === "none"
                            || style.visibility === "hidden"
                            || style.pointerEvents === "none"
                          ) continue;
                          const box = element.getBoundingClientRect();
                          const overlapWidth = Math.max(
                            0,
                            Math.min(
                              candidate.x + candidate.width,
                              box.right
                            ) - Math.max(candidate.x, box.left)
                          );
                          const overlapHeight = Math.max(
                            0,
                            Math.min(
                              candidate.y + candidate.height,
                              box.bottom
                            ) - Math.max(candidate.y, box.top)
                          );
                          if (overlapWidth && overlapHeight) {
                            total += (
                              100000 + overlapWidth * overlapHeight
                            );
                          }
                        }
                        for (const horizontal of [0.15, 0.5, 0.85]) {
                          for (const vertical of [0.2, 0.5, 0.8]) {
                            const under = document.elementFromPoint(
                              candidate.x
                                + candidate.width * horizontal,
                              candidate.y + candidate.height * vertical
                            );
                            if (!under) continue;
                            if (under.closest?.(interactiveSelector)) {
                              total += 10000;
                            } else if (
                              String(under.innerText || "").trim()
                            ) {
                              total += 4;
                            } else if (
                              getComputedStyle(under).backgroundColor
                                !== "rgba(0, 0, 0, 0)"
                            ) {
                              total += 1;
                            }
                          }
                        }
                        return total;
                      };
                      let selected = candidates[0];
                      let selectedScore = score(selected);
                      for (const candidate of candidates.slice(1)) {
                        const candidateScore = score(candidate);
                        if (candidateScore >= selectedScore) continue;
                        selected = candidate;
                        selectedScore = candidateScore;
                      }
                      current.dataset.placement = selected.name;
                      current.style.left = `${Math.round(selected.x)}px`;
                      current.style.top = `${Math.round(selected.y)}px`;
                    };
                    window.__docflowAgentPlaceStatus = placeStatus;
                    const labels = {
                      observing: "Gemini · 读取页面",
                      thinking: "Gemini · 规划本页",
                      working: "Gemini · 正在填写",
                      navigating: "Gemini · 正在进入下一页",
                      paused: "Gemini · 已暂停，需要处理",
                      blocked: "Gemini · 已停止，需要处理",
                      disconnected: "Gemini · 连接中断",
                      error: "Gemini · 运行失败",
                      completed: "Gemini · 本轮完成"
                    };
                    badge.dataset.state = state;
                    badge.dataset.baseMessage = message || "";
                    badge.dataset.startedAt = String(startedAt);
                    badge.dataset.leaseUntil = String(leaseUntil);
                    try {
                      sessionStorage.setItem(
                        "__docflowAgentVisualState",
                        JSON.stringify({
                          state,
                          message: message || "",
                          startedAt,
                          x,
                          y,
                          leaseUntil
                        })
                      );
                    } catch (_error) {
                      // Some browser-owned/error documents deny storage.
                    }
                    badge.querySelector(
                        "[data-docflow-status-label]"
                    ).textContent = labels[state] || "Gemini · 工作中";
                    window.__docflowAgentRenderHeartbeat = () => {
                      const current = document.getElementById(
                        "docflow-agent-visual-status"
                      );
                      if (!current) return;
                      placeStatus();
                      const detail = current.querySelector(
                        "[data-docflow-status-detail]"
                      );
                      if (!detail) return;
                      const activeStates = [
                        "observing",
                        "thinking",
                        "working",
                        "navigating"
                      ];
                      if (
                        activeStates.includes(current.dataset.state)
                        && Number(current.dataset.leaseUntil || 0) > 0
                        && Date.now()
                          > Number(current.dataset.leaseUntil)
                      ) {
                        current.dataset.state = "disconnected";
                        current.dataset.baseMessage =
                          "超过运行租约，没有收到 Agent Core 更新";
                        const currentCursor = document.getElementById(
                          "docflow-agent-visible-cursor"
                        );
                        if (currentCursor) {
                          currentCursor.dataset.state = "disconnected";
                        }
                        try {
                          const stored = JSON.parse(
                            sessionStorage.getItem(
                              "__docflowAgentVisualState"
                            ) || "{}"
                          );
                          sessionStorage.setItem(
                            "__docflowAgentVisualState",
                            JSON.stringify({
                              ...stored,
                              state: "disconnected",
                              message: current.dataset.baseMessage,
                              leaseUntil: Number(
                                current.dataset.leaseUntil
                              )
                            })
                          );
                        } catch (_error) {
                          // Storage can be denied on browser-owned pages.
                        }
                      }
                      const currentLabel = current.querySelector(
                        "[data-docflow-status-label]"
                      );
                      if (currentLabel) {
                        currentLabel.textContent = labels[
                          current.dataset.state
                        ] || "Gemini · 工作中";
                      }
                      const active = activeStates.includes(
                        current.dataset.state
                      );
                      const elapsed = Math.max(
                        0,
                        Math.floor(
                          (
                            Date.now()
                            - Number(current.dataset.startedAt || Date.now())
                          ) / 1000
                        )
                      );
                      const base = current.dataset.baseMessage || "";
                      const heartbeat = active && elapsed >= 2
                        ? `仍在工作 · ${elapsed} 秒`
                        : "";
                      detail.textContent = [base, heartbeat]
                        .filter(Boolean)
                        .join(" · ");
                      detail.hidden = !detail.textContent;
                      placeStatus();
                    };
                    window.__docflowAgentRenderHeartbeat();
                    if (window.__docflowAgentEarlyHeartbeatTimer) {
                      window.clearInterval(
                        window.__docflowAgentEarlyHeartbeatTimer
                      );
                      window.__docflowAgentEarlyHeartbeatTimer = null;
                    }
                    if (!window.__docflowAgentHeartbeatTimer) {
                      window.__docflowAgentHeartbeatTimer =
                        window.setInterval(
                          window.__docflowAgentRenderHeartbeat,
                          1000
                        );
                    }
                }""",
                [
                    self._cursor_x,
                    self._cursor_y,
                    self._visual_status_state,
                    self._visual_status_message,
                    self._visual_lease_ms(self._visual_status_state),
                ],
            )
        except Exception:
            pass

    def _set_visual_overlays_hidden(self, hidden):
        # Recreate observer-owned nodes before applying/removing the capture
        # marker.  If a page script replaced the body while a screenshot was
        # in flight, the unhide path therefore cannot leave a missing or
        # permanently invisible cursor/status behind.
        self._ensure_visible_cursor()
        try:
            self._page.evaluate(
                """(hidden) => {
                    for (const id of [
                        "docflow-agent-visible-cursor",
                        "docflow-agent-visual-status"
                    ]) {
                        const element = document.getElementById(id);
                        if (element) {
                            if (hidden) {
                                element.dataset.docflowCaptureHidden = "true";
                            } else {
                                delete element.dataset.docflowCaptureHidden;
                                element.style.removeProperty("visibility");
                            }
                        }
                    }
                }""",
                bool(hidden),
            )
        except Exception:
            pass

    def _apply_structured_field_value(self, locator, action):
        """Set a visually chosen structured control from the approved value.

        Gemini still chooses the field and visible coordinate. The browser
        adapter handles controls that cannot safely receive the complete value
        as raw keystrokes, then exposes only a value verified from live control
        state. This covers CEAC's day/month/year groups and choice controls.
        """
        if not action.field_id:
            return False
        segment_token = locator.get_attribute(
            "data-docflow-segment-group"
        )
        if segment_token:
            return self._fill_segmented_text(
                locator,
                action.field_id,
                action.value,
                segment_token,
            )
        parsed_duration = self._parse_duration(action.value)
        if parsed_duration and self._fill_composite_duration(
            locator,
            action.field_id,
            action.value,
            parsed_duration,
        ):
            return True
        parsed_date = self._parse_iso_date(action.value)
        if parsed_date and self._fill_composite_date(
            locator,
            action.field_id,
            action.value,
            parsed_date,
        ):
            return True
        try:
            metadata = locator.evaluate(
                """el => ({
                    tag: el.tagName.toLowerCase(),
                    type: String(el.getAttribute('type') || '').toLowerCase()
                })"""
            )
        except Exception:
            return False
        tag = str(metadata.get("tag") or "")
        control_type = str(metadata.get("type") or "")
        verified = False
        handled = False
        if tag == "select":
            handled = True
            verified = self._select_approved_option(locator, action.value)
        elif tag == "input" and control_type == "radio":
            handled = True
            verified = self._select_approved_radio(locator, action.value)
        elif tag == "input" and control_type == "checkbox":
            handled = True
            verified = self._set_approved_checkbox(locator, action.value)
        elif tag == "input" and control_type == "date" and parsed_date:
            handled = True
            try:
                locator.fill(action.value)
                verified = locator.input_value() == action.value
            except Exception:
                verified = False
        if verified:
            self._verified_field_values[action.field_id] = action.value
        return handled

    def _fill_segmented_text(
        self,
        locator,
        field_id,
        approved_value,
        token,
    ):
        """Split one approved numeric value across fixed maxlength controls.

        The full structure is validated before the first mutation, preventing a
        malformed value or an accidentally broad locator from partially
        overwriting the live form.
        """
        digits = self._numeric_segment_value(approved_value)
        host = self._page.locator(
            f'[data-docflow-segment-host="{token}"]'
        ).first
        if digits is None:
            return True

        def snapshot(target_index=None):
            target_token = (
                f"segment-target-{uuid4().hex}"
                if target_index is not None else ""
            )
            try:
                result = host.evaluate(
                    """(host, args) => {
                        const visible = (item) => {
                            const style = getComputedStyle(item);
                            const box = item.getBoundingClientRect();
                            return style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && box.width > 0 && box.height > 0;
                        };
                        const controls = Array.from(
                            host.querySelectorAll('input')
                        ).filter(item => {
                            const type = String(
                                item.getAttribute('type') || 'text'
                            ).toLowerCase();
                            return ['text', 'tel'].includes(type)
                                && !item.disabled && !item.readOnly
                                && visible(item)
                                && Number.isInteger(item.maxLength)
                                && item.maxLength >= 1
                                && item.maxLength <= 20;
                        }).sort((left, right) => {
                            const a = left.getBoundingClientRect();
                            const b = right.getBoundingClientRect();
                            return a.left - b.left || a.top - b.top;
                        });
                        if (
                            controls.length < 2
                            || controls.length > 6
                        ) return null;
                        controls.forEach(item => item.setAttribute(
                            'data-docflow-segment-group', args.groupToken
                        ));
                        if (Number.isInteger(args.targetIndex)) {
                            if (!controls[args.targetIndex]) return null;
                            controls[args.targetIndex].setAttribute(
                                'data-docflow-segment-target',
                                args.targetToken
                            );
                        }
                        return {
                            lengths: controls.map(
                                item => Number(item.maxLength)
                            ),
                            values: controls.map(
                                item => String(item.value || '')
                            ),
                            targetToken: args.targetToken
                        };
                    }""",
                    {
                        "groupToken": token,
                        "targetIndex": target_index,
                        "targetToken": target_token,
                    },
                    timeout=1000,
                )
            except Exception:
                return None
            return result if isinstance(result, dict) else None

        initial = snapshot()
        if initial is None:
            return True
        lengths = [int(item) for item in initial.get("lengths") or []]
        if sum(lengths) != len(digits):
            return True
        chunks = []
        offset = 0
        for length in lengths:
            chunks.append(digits[offset:offset + length])
            offset += length

        for index, chunk in enumerate(chunks):
            current = snapshot(index)
            if (
                current is None
                or [int(item) for item in current.get("lengths") or []]
                != lengths
            ):
                return True
            target = self._page.locator(
                '[data-docflow-segment-target="'
                f'{current.get("targetToken")}"]'
            ).first
            if self._visual_execution:
                self._move_pointer_to_locator(target, clicking=True)
            try:
                changed = target.evaluate(
                    """(el, value) => {
                        if (el.disabled || el.readOnly) return false;
                        el.value = value;
                        el.dispatchEvent(new Event(
                            'input', {bubbles: true}
                        ));
                        el.dispatchEvent(new Event(
                            'change', {bubbles: true}
                        ));
                        return true;
                    }""",
                    chunk,
                    timeout=1000,
                )
            except Exception:
                return True
            if not changed:
                return True

        final = snapshot()
        if final is None or list(final.get("values") or []) != chunks:
            return True
        fresh = self._page.locator(
            f'[data-docflow-segment-group="{token}"]'
        ).first
        try:
            self._mark_field(
                fresh,
                ComputerAction(
                    kind=ActionKind.TYPE,
                    field_id=field_id,
                    target_hint=field_id,
                ),
            )
        except Exception:
            return True
        self._verified_field_values[field_id] = approved_value
        return True

    def _live_control_value(self, field_id, selector, timeout):
        """Read the current DOM control state; never let a cache self-verify.

        Radio ``input_value()`` returns the element's value even when it is not
        checked, and checkbox ``input_value()`` commonly returns ``on`` in both
        states.  Structured values therefore have to be reconstructed from the
        live checked/selected state.  The approved-value cache is only used as
        a canonical representation after the live DOM proves it still matches.
        """
        controls = self._page.locator(selector)
        if not controls.count():
            return None
        locator = controls.first
        metadata = locator.evaluate(
            """el => ({
                tag: el.tagName.toLowerCase(),
                type: String(el.getAttribute('type') || '').toLowerCase(),
                name: String(el.getAttribute('name') || '')
            })"""
        )
        tag = str(metadata.get("tag") or "")
        control_type = str(metadata.get("type") or "")
        approved = self._verified_field_values.get(field_id)

        segment_token = locator.get_attribute(
            "data-docflow-segment-group"
        )
        approved_digits = self._numeric_segment_value(approved)
        if segment_token:
            if approved_digits is None:
                return None
            group = self._page.locator(
                f'[data-docflow-segment-group="{segment_token}"]'
            )
            try:
                count = min(group.count(), 7)
            except Exception:
                return None
            if count < 2 or count > 6:
                return None
            parts = []
            lengths = []
            for index in range(count):
                item = group.nth(index)
                try:
                    details = item.evaluate(
                        """el => ({
                            tag: el.tagName.toLowerCase(),
                            type: String(
                                el.getAttribute('type') || 'text'
                            ).toLowerCase(),
                            maxLength: Number(el.maxLength),
                            value: String(el.value || '')
                        })"""
                    )
                    length = int(details.get("maxLength"))
                    part = str(details.get("value") or "")
                    if (
                        details.get("tag") != "input"
                        or details.get("type") not in {"text", "tel"}
                        or length < 1
                        or length > 20
                        or len(part) != length
                        or not part.isdigit()
                    ):
                        return None
                except Exception:
                    return None
                lengths.append(length)
                parts.append(part)
            if (
                sum(lengths) == len(approved_digits)
                and "".join(parts) == approved_digits
            ):
                return approved
            return None

        parsed_date = self._parse_iso_date(approved)
        date_token = locator.get_attribute("data-docflow-date-group")
        if parsed_date and date_token:
            group = self._page.locator(
                f'[data-docflow-date-group="{date_token}"]'
            )
            day = None
            month = None
            year = None
            for index in range(min(group.count(), 6)):
                item = group.nth(index)
                details = item.evaluate(
                    """el => ({
                        tag: el.tagName.toLowerCase(),
                        value: String(el.value || ''),
                        text: el.tagName.toLowerCase() === 'select'
                            && el.selectedIndex >= 0
                            ? String(el.options[el.selectedIndex].text || '')
                            : ''
                    })"""
                )
                if details.get("tag") == "select":
                    option = {
                        "value": details.get("value"),
                        "text": details.get("text"),
                    }
                    candidate_day = self._integer_option(option)
                    candidate_month = self._month_option(option)
                    if candidate_month is not None and re.search(
                        r"[A-Za-z]", str(details.get("text") or "")
                    ):
                        month = candidate_month
                    elif candidate_day is not None:
                        day = candidate_day
                elif details.get("tag") == "input":
                    candidate = str(details.get("value") or "").strip()
                    if re.fullmatch(r"\d{4}", candidate):
                        year = int(candidate)
            if (year, month, day) == parsed_date:
                return approved
            return None

        parsed_duration = self._parse_duration(approved)
        duration_token = locator.get_attribute(
            "data-docflow-duration-group"
        )
        if parsed_duration and duration_token:
            group = self._page.locator(
                f'[data-docflow-duration-group="{duration_token}"]'
            )
            amount = ""
            unit = ""
            for index in range(min(group.count(), 4)):
                item = group.nth(index)
                details = item.evaluate(
                    """el => ({
                        tag: el.tagName.toLowerCase(),
                        value: String(el.value || ''),
                        text: el.tagName.toLowerCase() === 'select'
                            && el.selectedIndex >= 0
                            ? String(el.options[el.selectedIndex].text || '')
                            : ''
                    })"""
                )
                if details.get("tag") == "select":
                    unit = (
                        str(details.get("text") or "")
                        or str(details.get("value") or "")
                    )
                elif details.get("tag") == "input":
                    amount = str(details.get("value") or "").strip()
            expected_amount, expected_unit = parsed_duration
            if (
                self._normalize_number(amount)
                == self._normalize_number(expected_amount)
                and self._choice_matches(expected_unit, unit)
            ):
                return approved
            return None

        if tag == "select":
            current = locator.evaluate(
                """el => ({
                    value: String(el.value || ''),
                    text: el.selectedIndex >= 0
                        ? String(el.options[el.selectedIndex].text || '') : ''
                })"""
            )
            candidate = (
                f"{current.get('text', '')} {current.get('value', '')}".strip()
            )
            if approved is not None and self._choice_matches(
                approved, candidate
            ):
                return approved
            return candidate

        if tag == "input" and control_type == "radio":
            name = str(metadata.get("name") or "")
            if not name:
                return None
            checked = self._page.locator(
                f'input[type="radio"][name="{name}"]:checked'
            )
            if checked.count() != 1:
                return None
            current = checked.first.evaluate(
                """el => ({
                    value: String(el.value || ''),
                    label: Array.from(el.labels || [])
                        .map((label) => String(label.innerText || ''))
                        .join(' '),
                    nearby: String(el.parentElement?.innerText || '')
                })"""
            )
            candidates = tuple(filter(None, (
                str(current.get("label") or "").strip(),
                str(current.get("value") or "").strip(),
                str(current.get("nearby") or "").strip(),
            )))
            # Never concatenate both option labels from a shared radio-group
            # container (for example "Yes No"). Boolean parsing would see the
            # opposite option and could reject an otherwise exact checked
            # label/value. Direct checked-option evidence is evaluated one
            # candidate at a time, just like the write path.
            if approved is not None and any(
                self._choice_matches(approved, candidate)
                for candidate in candidates
            ):
                return approved
            return candidates[0] if candidates else None

        if tag == "input" and control_type == "checkbox":
            checked = bool(locator.is_checked())
            if approved is not None:
                desired = self._boolean_choice(approved)
                if desired is checked:
                    return approved
            return "true" if checked else "false"

        return locator.input_value(timeout=timeout)

    def _fill_composite_date(
        self,
        locator,
        field_id,
        approved_value,
        parsed_date,
    ):
        token = f"date-{uuid4().hex}"
        try:
            found = locator.evaluate(
                """(el, token) => {
                    const usable = (control) => {
                        const tag = control.tagName.toLowerCase();
                        const type = String(control.getAttribute('type') || '')
                            .toLowerCase();
                        return tag === 'select' || (
                            tag === 'input'
                            && ![
                                'hidden', 'radio', 'checkbox', 'button',
                                'submit', 'reset', 'file', 'image', 'password'
                            ].includes(type)
                        );
                    };
                    const dateFamily = (control) => {
                        const identity = String(
                            control.id || control.name || ''
                        ).toUpperCase();
                        if (!identity) return '';
                        return identity
                            .replace(
                                /(?:[_$-]?DTE)?[_$-]?(?:DAY|MONTH|YEAR)$/,
                                ''
                            )
                            // ASP.NET WebForms commonly names the two date
                            // selects ``ddlDOBDay/ddlDOBMonth`` but the text
                            // input ``tbxDOBYear``.  DDL/TBX describe the
                            // widget type, not the date family.  Keeping them
                            // made one real CEAC date look like two unrelated
                            // groups, while the remaining semantic stem still
                            // safely separates issuance from expiration.
                            .replace(/(^|[_$-])(?:DDL|TBX)(?=[A-Z0-9])/, '$1');
                    };
                    const targetFamily = dateFamily(el);
                    let current = el.parentElement;
                    for (let depth = 0; depth < 8 && current; depth += 1) {
                        const candidates = Array.from(
                            current.querySelectorAll('select, input')
                        ).filter(usable);
                        const controls = targetFamily
                            ? candidates.filter(
                                item => dateFamily(item) === targetFamily
                            )
                            : candidates;
                        const selectCount = controls.filter(
                            (item) => item.tagName.toLowerCase() === 'select'
                        ).length;
                        const inputCount = controls.filter(
                            (item) => item.tagName.toLowerCase() === 'input'
                        ).length;
                        if (
                            controls.includes(el)
                            && controls.length === 3
                            && selectCount === 2
                            && inputCount === 1
                        ) {
                            current.setAttribute(
                                'data-docflow-date-host', token
                            );
                            controls.forEach(
                                (item) => item.setAttribute(
                                    'data-docflow-date-group', token
                                )
                            );
                            return {familyKey: targetFamily};
                        }
                        current = current.parentElement;
                    }
                    return false;
                }""",
                token,
            )
        except Exception:
            return False
        if not found:
            return False
        host = self._page.locator(
            f'[data-docflow-date-host="{token}"]'
        ).first

        def snapshot(target_index=None):
            target_token = (
                f"date-target-{uuid4().hex}"
                if target_index is not None else ""
            )
            try:
                result = host.evaluate(
                    """(host, args) => {
                        const usable = (control) => {
                            const tag = control.tagName.toLowerCase();
                            const type = String(
                                control.getAttribute('type') || ''
                            ).toLowerCase();
                            return !control.disabled && (
                                tag === 'select'
                                || (
                                    tag === 'input'
                                    && ![
                                        'hidden', 'radio', 'checkbox',
                                        'button', 'submit', 'reset',
                                        'file', 'image', 'password'
                                    ].includes(type)
                                )
                            );
                        };
                        const dateFamily = (control) => {
                            const identity = String(
                                control.id || control.name || ''
                            ).toUpperCase();
                            if (!identity) return '';
                            return identity
                                .replace(
                                    /(?:[_$-]?DTE)?[_$-]?(?:DAY|MONTH|YEAR)$/,
                                    ''
                                )
                                .replace(
                                    /(^|[_$-])(?:DDL|TBX)(?=[A-Z0-9])/,
                                    '$1'
                                );
                        };
                        const candidates = Array.from(
                            host.querySelectorAll('select, input')
                        ).filter(usable);
                        const controls = args.familyKey
                            ? candidates.filter(
                                item => dateFamily(item) === args.familyKey
                            )
                            : candidates;
                        const selectCount = controls.filter(
                            item => item.tagName.toLowerCase() === 'select'
                        ).length;
                        const inputCount = controls.filter(
                            item => item.tagName.toLowerCase() === 'input'
                        ).length;
                        if (
                            controls.length !== 3
                            || selectCount !== 2
                            || inputCount !== 1
                        ) return null;
                        controls.forEach(item => item.setAttribute(
                            'data-docflow-date-group', args.groupToken
                        ));
                        if (Number.isInteger(args.targetIndex)) {
                            if (!controls[args.targetIndex]) return null;
                            controls[args.targetIndex].setAttribute(
                                'data-docflow-date-target',
                                args.targetToken
                            );
                        }
                        return {
                            targetToken: args.targetToken,
                            controls: controls.map((item, index) => ({
                                index,
                                tag: item.tagName.toLowerCase(),
                                type: String(
                                    item.getAttribute('type') || ''
                                ).toLowerCase(),
                                value: String(item.value || ''),
                                text: (
                                    item.tagName.toLowerCase() === 'select'
                                    && item.selectedIndex >= 0
                                ) ? String(
                                    item.options[item.selectedIndex].text || ''
                                ) : '',
                                options: (
                                    item.tagName.toLowerCase() === 'select'
                                ) ? Array.from(item.options).map(option => ({
                                    value: String(option.value || ''),
                                    text: String(option.text || '')
                                })) : []
                            }))
                        };
                    }""",
                    {
                        "groupToken": token,
                        "familyKey": str(found.get("familyKey") or ""),
                        "targetIndex": target_index,
                        "targetToken": target_token,
                    },
                    timeout=1000,
                )
            except Exception:
                return None
            return result if isinstance(result, dict) else None

        def roles(details):
            controls = list((details or {}).get("controls") or [])
            selects = [
                item for item in controls if item.get("tag") == "select"
            ]
            inputs = [
                item for item in controls if item.get("tag") == "input"
            ]
            day_control = next(
                (
                    item for item in selects
                    if sum(
                        1 for option in item.get("options") or []
                        if self._integer_option(option) in range(1, 32)
                    ) >= 28
                ),
                None,
            )
            month_control = next(
                (item for item in selects if item is not day_control),
                None,
            )
            year_control = inputs[0] if inputs else None
            return day_control, month_control, year_control

        def activate(control, desired):
            current = snapshot(int(control["index"]))
            if current is None:
                return False
            current_roles = roles(current)
            refreshed = next(
                (
                    item for item in current_roles
                    if item is not None
                    and int(item.get("index", -1))
                    == int(control["index"])
                ),
                None,
            )
            if refreshed is None:
                return False
            target = self._page.locator(
                '[data-docflow-date-target="'
                f'{current.get("targetToken")}"]'
            ).first
            if refreshed.get("tag") == "select":
                option = next((
                    item for item in refreshed.get("options") or ()
                    if str(item.get("value") or "") == str(desired)
                ), None)
                return bool(
                    option is not None
                    and self._activate_select_option(target, option)
                )
            if self._visual_execution:
                self._move_pointer_to_locator(target, clicking=True)
            try:
                return bool(target.evaluate(
                    """(el, value) => {
                        if (el.disabled || el.readOnly) return false;
                        el.value = value;
                        const selected = String(el.value || '') === value;
                        if (!selected) return false;
                        el.dispatchEvent(new Event(
                            'input', {bubbles: true}
                        ));
                        el.dispatchEvent(new Event(
                            'change', {bubbles: true}
                        ));
                        return true;
                    }""",
                    str(desired),
                    timeout=1000,
                ))
            except Exception:
                return False

        year, month, day = parsed_date
        initial = snapshot()
        day_control, month_control, year_control = roles(initial)
        if (
            initial is None
            or day_control is None
            or month_control is None
            or year_control is None
        ):
            return True
        day_option = next(
            (
                option for option in day_control.get("options") or []
                if self._integer_option(option) == day
            ),
            None,
        )
        month_option = next(
            (
                option for option in month_control.get("options") or []
                if self._month_option(option) == month
            ),
            None,
        )
        if day_option is None or month_option is None:
            return True
        if not activate(day_control, day_option.get("value")):
            return True
        refreshed = snapshot()
        _day_control, month_control, _year_control = roles(refreshed)
        if month_control is None:
            return True
        month_option = next(
            (
                option for option in month_control.get("options") or []
                if self._month_option(option) == month
            ),
            None,
        )
        if month_option is None or not activate(
            month_control, month_option.get("value")
        ):
            return True
        refreshed = snapshot()
        _day_control, _month_control, year_control = roles(refreshed)
        if year_control is None or not activate(year_control, str(year)):
            return True
        final = snapshot()
        final_day, final_month, final_year = roles(final)
        if (
            final_day is None
            or final_month is None
            or final_year is None
            or self._integer_option({
                "value": final_day.get("value"),
                "text": final_day.get("text"),
            }) != day
            or self._month_option({
                "value": final_month.get("value"),
                "text": final_month.get("text"),
            }) != month
            or str(final_year.get("value") or "").strip() != str(year)
        ):
            return True
        fresh = self._page.locator(
            f'[data-docflow-date-group="{token}"]'
        ).first
        try:
            self._mark_field(
                fresh,
                ComputerAction(
                    kind=ActionKind.SELECT,
                    field_id=field_id,
                    target_hint=field_id,
                ),
            )
        except Exception:
            return True
        self._verified_field_values[field_id] = approved_value
        return True

    def _fill_composite_duration(
        self,
        locator,
        field_id,
        approved_value,
        parsed_duration,
    ):
        token = f"duration-{uuid4().hex}"
        try:
            found = locator.evaluate(
                """(el, token) => {
                    const usable = (control) => {
                        const tag = control.tagName.toLowerCase();
                        const type = String(
                            control.getAttribute('type') || ''
                        ).toLowerCase();
                        return tag === 'select' || (
                            tag === 'input'
                            && ![
                                'hidden', 'radio', 'checkbox', 'button',
                                'submit', 'reset', 'file', 'image', 'password'
                            ].includes(type)
                        );
                    };
                    let current = el.parentElement;
                    for (let depth = 0; depth < 7 && current; depth += 1) {
                        const controls = Array.from(
                            current.querySelectorAll('input, select')
                        ).filter(usable);
                        const inputs = controls.filter(
                            item => item.tagName.toLowerCase() === 'input'
                        );
                        const selects = controls.filter(
                            item => item.tagName.toLowerCase() === 'select'
                        );
                        if (
                            controls.includes(el)
                            && inputs.length === 1
                            && selects.length === 1
                            && controls.length === 2
                        ) {
                            current.setAttribute(
                                'data-docflow-duration-host', token
                            );
                            controls.forEach(
                                item => item.setAttribute(
                                    'data-docflow-duration-group', token
                                )
                            );
                            return true;
                        }
                        current = current.parentElement;
                    }
                    return false;
                }""",
                token,
            )
        except Exception:
            return False
        if not found:
            return False
        host = self._page.locator(
            f'[data-docflow-duration-host="{token}"]'
        ).first

        def snapshot(target_tag=""):
            target_token = (
                f"duration-target-{uuid4().hex}" if target_tag else ""
            )
            try:
                result = host.evaluate(
                    """(host, args) => {
                        const controls = Array.from(
                            host.querySelectorAll('input, select')
                        ).filter(item => {
                            const tag = item.tagName.toLowerCase();
                            const type = String(
                                item.getAttribute('type') || ''
                            ).toLowerCase();
                            return !item.disabled && (
                                tag === 'select'
                                || (
                                    tag === 'input'
                                    && ![
                                        'hidden', 'radio', 'checkbox',
                                        'button', 'submit', 'reset',
                                        'file', 'image', 'password'
                                    ].includes(type)
                                )
                            );
                        });
                        const inputs = controls.filter(
                            item => item.tagName.toLowerCase() === 'input'
                        );
                        const selects = controls.filter(
                            item => item.tagName.toLowerCase() === 'select'
                        );
                        if (
                            inputs.length !== 1
                            || selects.length !== 1
                            || controls.length !== 2
                        ) return null;
                        controls.forEach(item => item.setAttribute(
                            'data-docflow-duration-group',
                            args.groupToken
                        ));
                        const target = args.targetTag === 'input'
                            ? inputs[0]
                            : args.targetTag === 'select'
                                ? selects[0] : null;
                        if (target) {
                            target.setAttribute(
                                'data-docflow-duration-target',
                                args.targetToken
                            );
                        }
                        const select = selects[0];
                        return {
                            targetToken: args.targetToken,
                            amount: String(inputs[0].value || ''),
                            unitValue: String(select.value || ''),
                            unitText: select.selectedIndex >= 0
                                ? String(
                                    select.options[select.selectedIndex].text
                                    || ''
                                ) : '',
                            options: Array.from(select.options).map(
                                option => ({
                                    value: String(option.value || ''),
                                    text: String(option.text || '')
                                })
                            )
                        };
                    }""",
                    {
                        "groupToken": token,
                        "targetTag": target_tag,
                        "targetToken": target_token,
                    },
                    timeout=1000,
                )
            except Exception:
                return None
            return result if isinstance(result, dict) else None

        amount, unit = parsed_duration
        initial = snapshot("input")
        if initial is None:
            return True
        amount_target = self._page.locator(
            '[data-docflow-duration-target="'
            f'{initial.get("targetToken")}"]'
        ).first
        if self._visual_execution:
            self._move_pointer_to_locator(amount_target, clicking=True)
        try:
            amount_ok = bool(amount_target.evaluate(
                """(el, value) => {
                    if (el.disabled || el.readOnly) return false;
                    el.value = value;
                    el.dispatchEvent(new Event(
                        'input', {bubbles: true}
                    ));
                    el.dispatchEvent(new Event(
                        'change', {bubbles: true}
                    ));
                    return true;
                }""",
                amount,
                timeout=1000,
            ))
        except Exception:
            amount_ok = False
        if not amount_ok:
            return True
        refreshed = snapshot("select")
        if refreshed is None:
            return True
        selected = next(
            (
                option for option in refreshed.get("options") or []
                if self._choice_matches(
                    unit,
                    f"{option.get('text', '')} "
                    f"{option.get('value', '')}",
                )
            ),
            None,
        )
        if selected is None:
            return True
        unit_target = self._page.locator(
            '[data-docflow-duration-target="'
            f'{refreshed.get("targetToken")}"]'
        ).first
        unit_ok = self._activate_select_option(unit_target, selected)
        if not unit_ok:
            return True
        final = snapshot()
        if (
            final is None
            or self._normalize_number(final.get("amount"))
            != self._normalize_number(amount)
            or not self._choice_matches(
                unit,
                f"{final.get('unitText', '')} "
                f"{final.get('unitValue', '')}",
            )
        ):
            return True
        fresh = self._page.locator(
            f'[data-docflow-duration-group="{token}"]'
        ).first
        try:
            self._mark_field(
                fresh,
                ComputerAction(
                    kind=ActionKind.SELECT,
                    field_id=field_id,
                    target_hint=field_id,
                ),
            )
        except Exception:
            return True
        self._verified_field_values[field_id] = approved_value
        return True

    def _select_date_option(self, locator, options, day=None, month=None):
        selected = None
        for option in options:
            if day is not None and self._integer_option(option) == day:
                selected = option
                break
            if month is not None and self._month_option(option) == month:
                selected = option
                break
        if selected is None:
            return False
        try:
            if not self._activate_select_option(locator, selected):
                return False
            current = locator.evaluate(
                """el => ({
                    value: String(el.value || ''),
                    text: el.selectedIndex >= 0
                        ? String(el.options[el.selectedIndex].text || '') : ''
                })""",
                timeout=800,
            )
        except Exception:
            return False
        if day is not None:
            return self._integer_option(current) == day
        return self._month_option(current) == month

    def _activate_select_option(self, locator, selected):
        """Commit an already-resolved option through the adapter's input path.

        The base driver keeps the existing bounded DOM implementation.  A
        concrete adapter can override this single hook when every select must
        use a native mouse/keyboard interaction without duplicating the
        composite date and duration binding logic.
        """
        try:
            current = locator.evaluate(
                """(el, desired) => {
                    if (el.disabled) return null;
                    const option = Array.from(el.options).find(
                        item => (
                            desired.value
                            ? String(item.value || '') === desired.value
                            : String(item.text || '').trim()
                                === desired.text
                        )
                    );
                    if (!option || option.disabled) return null;
                    el.value = String(option.value || '');
                    option.selected = true;
                    const result = {
                        value: String(option.value || ''),
                        text: String(option.text || '')
                    };
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return result;
                }""",
                {
                    "value": str(selected.get("value") or ""),
                    "text": str(
                        selected.get("text")
                        or selected.get("label")
                        or ""
                    ).strip(),
                },
                timeout=1000,
            )
        except Exception:
            return False
        if not isinstance(current, dict):
            return False
        return (
            str(current.get("value") or "")
            == str(selected.get("value") or "")
        )

    def _select_approved_option(self, locator, approved_value):
        try:
            options = locator.evaluate(
                """el => Array.from(el.options).map((option) => ({
                    value: String(option.value || ''),
                    text: String(option.text || '')
                }))"""
            )
        except Exception:
            return False
        selected = next(
            (
                option for option in options
                if self._choice_matches(
                    approved_value,
                    f"{option.get('text', '')} {option.get('value', '')}",
                )
            ),
            None,
        )
        if selected is None:
            return False
        if not self._activate_select_option(locator, selected):
            return False
        try:
            current = locator.evaluate(
                """el => ({
                    value: String(el.value || ''),
                    text: el.selectedIndex >= 0
                        ? String(el.options[el.selectedIndex].text || '') : ''
                })""",
                timeout=800,
            )
        except Exception:
            # A real change can synchronously replace an ASP.NET select.  The
            # caller's DOM-generation watcher and postcondition own the final
            # proof in that case.
            return True
        return isinstance(current, dict) and self._choice_matches(
            approved_value,
            f"{current.get('text', '')} {current.get('value', '')}",
        )

    def _select_approved_radio(self, locator, approved_value):
        token = f"radio-{uuid4().hex}"
        try:
            count = locator.evaluate(
                """(el, token) => {
                    if (
                        el.tagName.toLowerCase() !== 'input'
                        || String(el.type || '').toLowerCase() !== 'radio'
                    ) return 0;
                    const root = el.form || document;
                    const radios = Array.from(
                        root.querySelectorAll('input[type="radio"]')
                    ).filter((item) => item.name === el.name);
                    radios.forEach(
                        (item) => item.setAttribute(
                            'data-docflow-radio-group', token
                        )
                    );
                    return radios.length;
                }""",
                token,
            )
        except Exception:
            return False
        if not count:
            return False
        radios = self._page.locator(
            f'[data-docflow-radio-group="{token}"]'
        )
        for index in range(min(int(count), 20)):
            item = radios.nth(index)
            try:
                details = item.evaluate(
                    """el => ({
                        value: String(el.value || ''),
                        label: Array.from(el.labels || [])
                            .map((label) => String(label.innerText || ''))
                            .join(' '),
                        nearby: String(el.parentElement?.innerText || '')
                    })"""
                )
            except Exception:
                continue
            candidates = (
                str(details.get("label") or ""),
                str(details.get("value") or ""),
                str(details.get("nearby") or ""),
            )
            if not any(
                self._choice_matches(approved_value, candidate)
                for candidate in candidates if candidate.strip()
            ):
                continue
            try:
                # One bounded DOM activation avoids Playwright retrying a
                # locator that the radio's change postback just destroyed.
                # HTMLElement.click() still fires the native click/input/change
                # sequence used by ASP.NET.
                return bool(item.evaluate(
                    """el => {
                        if (el.disabled) return false;
                        if (!el.checked) el.click();
                        return Boolean(el.checked);
                    }""",
                    timeout=1000,
                ))
            except Exception:
                return False
        return False

    def _set_approved_checkbox(self, locator, approved_value):
        desired = self._boolean_choice(approved_value)
        if desired is None:
            return False
        try:
            # Dispatch and read the old element in one bounded evaluation. If
            # its change handler replaces it, settle_after_dynamic_refresh
            # rebinds and proves the replacement DOM before verification.
            return bool(locator.evaluate(
                """(el, desired) => {
                    if (el.disabled) return false;
                    if (Boolean(el.checked) !== desired) el.click();
                    return Boolean(el.checked) === desired;
                }""",
                bool(desired),
                timeout=1000,
            ))
        except Exception:
            return False

    @staticmethod
    def _parse_iso_date(value):
        matched = re.fullmatch(
            r"\s*(\d{4})-(\d{2})-(\d{2})\s*",
            str(value or ""),
        )
        if not matched:
            return None
        year, month, day = (int(item) for item in matched.groups())
        if not 1900 <= year <= 2100 or not 1 <= month <= 12:
            return None
        if not 1 <= day <= 31:
            return None
        return year, month, day

    @staticmethod
    def _parse_duration(value):
        matched = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)\s*"
            r"(DAY|DAYS|WEEK|WEEKS|MONTH|MONTHS|YEAR|YEARS)\s*",
            str(value or ""),
            flags=re.IGNORECASE,
        )
        if not matched:
            return None
        return matched.group(1), matched.group(2).upper().rstrip("S")

    @staticmethod
    def _numeric_segment_value(value):
        raw = str(value or "").strip()
        if not raw or not re.fullmatch(r"[0-9\s-]+", raw):
            return None
        digits = re.sub(r"\D", "", raw)
        return digits or None

    @staticmethod
    def _normalize_number(value):
        text = str(value or "").strip()
        try:
            number = float(text)
        except (TypeError, ValueError):
            return text
        return str(int(number)) if number.is_integer() else str(number)

    @staticmethod
    def _integer_option(option):
        for value in (option.get("text"), option.get("value")):
            matched = re.search(r"(?<!\d)(\d{1,2})(?!\d)", str(value or ""))
            if matched:
                return int(matched.group(1))
        return None

    @staticmethod
    def _month_option(option):
        month_names = {
            "jan": 1, "january": 1,
            "feb": 2, "february": 2,
            "mar": 3, "march": 3,
            "apr": 4, "april": 4,
            "may": 5,
            "jun": 6, "june": 6,
            "jul": 7, "july": 7,
            "aug": 8, "august": 8,
            "sep": 9, "sept": 9, "september": 9,
            "oct": 10, "october": 10,
            "nov": 11, "november": 11,
            "dec": 12, "december": 12,
        }
        for value in (option.get("text"), option.get("value")):
            normalized = re.sub(r"[^a-z0-9]", "", str(value or "").casefold())
            if normalized in month_names:
                return month_names[normalized]
            if normalized.isdigit() and 1 <= int(normalized) <= 12:
                return int(normalized)
        return None

    @staticmethod
    def _boolean_choice(value):
        normalized = str(value or "").strip().casefold()
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", normalized)
        if (
            compact in {"y", "yes", "true", "1", "on", "是", "是yes"}
            or re.search(r"(?:^|[^a-z])yes(?:$|[^a-z])", normalized)
        ):
            return True
        if (
            compact in {"n", "no", "false", "0", "off", "否", "否no"}
            or re.search(r"(?:^|[^a-z])no(?:$|[^a-z])", normalized)
        ):
            return False
        return None

    @classmethod
    def _choice_matches(cls, approved, candidate):
        approved_bool = cls._boolean_choice(approved)
        candidate_bool = cls._boolean_choice(candidate)
        if approved_bool is not None and candidate_bool is not None:
            return approved_bool is candidate_bool
        left = re.sub(
            r"[^a-z0-9\u4e00-\u9fff]",
            "",
            str(approved or "").casefold(),
        )
        right = re.sub(
            r"[^a-z0-9\u4e00-\u9fff]",
            "",
            str(candidate or "").casefold(),
        )
        if bool(
            left and right
            and (left == right or (min(len(left), len(right)) >= 2 and (
                left in right or right in left
            )))
        ):
            return True

        # CEAC option text and reviewed source text sometimes differ only by
        # harmless English connectors, for example:
        # "TEMP. BUSINESS PLEASURE VISITOR (B)" versus
        # "TEMP. BUSINESS OR PLEASURE VISITOR (B)".  Exact compact-string
        # matching rejects that valid option. Compare the ordered meaningful
        # tokens as a second, deliberately narrow normalization. De-duplicate
        # repeated option codes because callers may pass "label + value".
        connector_tokens = {"a", "an", "and", "of", "or", "the", "to"}

        def meaningful_tokens(value):
            tokens = [
                token
                for token in re.findall(
                    r"[a-z0-9]+|[\u4e00-\u9fff]+",
                    str(value or "").casefold(),
                )
                if token not in connector_tokens
            ]
            return list(dict.fromkeys(tokens))

        approved_tokens = meaningful_tokens(approved)
        candidate_tokens = meaningful_tokens(candidate)
        return bool(
            approved_tokens
            and approved_tokens == candidate_tokens
        )

    def _deterministic_control(self, field_id, labels, hints):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", field_id):
            return None
        occurrence, occurrence_is_valid = self._control_occurrence(labels)
        if not occurrence_is_valid:
            # An occurrence is structural, system-owned metadata.  Silently
            # ignoring a malformed/conflicting value could write one person's
            # repeated record into another person's control.
            return None
        control_kind = self._control_kind(labels)
        checkbox_control = control_kind in {
            "checkbox",
            "does_not_apply",
            "do_not_know",
        }

        def unique_control(
            locator,
            *,
            occurrence_value=None,
            group_visual_rows=False,
        ):
            # Text inputs and selects use the actionable-field resolver, which
            # intentionally excludes radios and checkboxes. Reusing it after
            # narrowing a selector to ``input[type=checkbox]`` therefore
            # discarded the exact CEAC D/N/A control and sent the field into
            # the slow visual-coordinate fallback. Once system-owned control
            # metadata has established that this is a checkbox field, visible
            # checkbox uniqueness is the correct structural test.
            resolver = (
                self._unique_visible_form_control
                if checkbox_control
                else self._unique_actionable_control
            )
            return resolver(
                locator,
                occurrence=occurrence_value,
                group_visual_rows=group_visual_rows,
            )

        def finalized(locator):
            if locator is None or control_kind != "text_segments":
                return locator
            return self._segmented_group_control(locator)

        selectors = [
            (
                f'input[data-docflow-field="{field_id}"], '
                f'textarea[data-docflow-field="{field_id}"], '
                f'select[data-docflow-field="{field_id}"]'
            ),
            (
                f'[data-docflow-field="{field_id}"] '
                "input, "
                f'[data-docflow-field="{field_id}"] '
                "textarea, "
                f'[data-docflow-field="{field_id}"] '
                "select"
            ),
            (
                f'[data-field-id="{field_id}"] '
                "input, "
                f'[data-field-id="{field_id}"] '
                "textarea, "
                f'[data-field-id="{field_id}"] '
                "select"
            ),
            (
                f'input[id="{field_id}"], textarea[id="{field_id}"], '
                f'select[id="{field_id}"], input[name="{field_id}"], '
                f'textarea[name="{field_id}"], select[name="{field_id}"]'
            ),
        ]
        for selector in selectors:
            matched = unique_control(
                self._page.locator(selector),
                occurrence_value=occurrence,
            )
            if matched is not None:
                matched = finalized(matched)
                if matched is not None:
                    return matched

        # Rank the complete candidate set by all system-owned identity hints
        # before applying occurrence.  The previous reversed single-hint loop
        # let generic suffixes such as CITY or STREET select occurrence 1 from
        # an unrelated address group even when another control matched
        # PAYER+ADDRESS+CITY.  Match count is authoritative; total matched hint
        # length breaks ties in favour of a specific alias over a broad token.
        normalized_hints = list(dict.fromkeys(
            normalized
            for normalized in (
                re.sub(r"[^A-Za-z0-9_-]", "", str(hint or ""))
                for hint in tuple(hints or ())
            )
            if len(normalized) >= 3
        ))
        explicit_repeated_labels = [
            str(label).split("[control=", 1)[0].strip()
            for label in labels or ()
            if str(label).split("[control=", 1)[0].strip()
        ]
        if occurrence is not None and explicit_repeated_labels:
            # Repeated CEAC rows must be indexed inside their exact visible
            # label group.  A broad id/name hint such as LANGUAGE also matches
            # the unrelated tooltip-language <select> in the page header.  It
            # previously became occurrence 1 while the actual Language Name
            # input became occurrence 2, so ENGLISH was written to the header
            # select, MANDARIN overwrote row 1, and Add Another looped forever.
            # Exact labels plus the reviewed occurrence are strictly stronger
            # identity evidence. Try them first, but retain the id/name hints
            # when the descriptor label is not actually present in the DOM.
            # Clearing hints merely because metadata contained a label made a
            # valid repeated control unresolvable on unlabeled legacy rows.
            for repeated_label in explicit_repeated_labels:
                pattern = re.compile(
                    rf"^\s*{re.escape(repeated_label)}\s*(?:\*|:)?\s*$",
                    re.IGNORECASE,
                )
                matched = self._unique_actionable_control(
                    self._page.get_by_label(pattern),
                    occurrence=occurrence,
                    group_visual_rows=control_kind in {
                        "date", "duration", "text_segments",
                    },
                )
                if matched is not None:
                    matched = finalized(matched)
                    if matched is not None:
                        return matched
        if normalized_hints:
            rank_token = f"hint-rank-{uuid4().hex}"
            ranking_inspected = False
            try:
                ranked = bool(self._page.evaluate(
                    """([hints, token, checkboxOnly]) => {
                        const normalize = value => String(value || '')
                            .toLowerCase().replace(/[^a-z0-9_-]/g, '');
                        const wanted = hints.map(normalize).filter(Boolean);
                        const visible = item => {
                            const style = getComputedStyle(item);
                            const box = item.getBoundingClientRect();
                            return style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && box.width > 0 && box.height > 0;
                        };
                        const controls = Array.from(
                            document.querySelectorAll(
                                checkboxOnly
                                    ? 'input[type="checkbox"]'
                                    : 'input, textarea, select'
                            )
                        ).filter(item => {
                            if (!visible(item) || item.disabled) return false;
                            if (checkboxOnly) return true;
                            const tag = item.tagName.toLowerCase();
                            const type = String(
                                item.getAttribute('type') || 'text'
                            ).toLowerCase();
                            return tag !== 'input' || ![
                                'hidden', 'radio', 'checkbox', 'button',
                                'submit', 'reset', 'file', 'image', 'password'
                            ].includes(type);
                        });
                        const ranked = controls.map(item => {
                            const identity = normalize([
                                item.id || '', item.getAttribute('name') || ''
                            ].join(' '));
                            const matches = wanted.filter(
                                hint => identity.includes(hint)
                            );
                            return {
                                item,
                                count: matches.length,
                                length: matches.reduce(
                                    (total, hint) => total + hint.length, 0
                                )
                            };
                        }).filter(entry => entry.count > 0);
                        if (!ranked.length) return false;
                        const bestCount = Math.max(
                            ...ranked.map(entry => entry.count)
                        );
                        const bestLength = Math.max(
                            ...ranked.filter(
                                entry => entry.count === bestCount
                            ).map(entry => entry.length)
                        );
                        for (const entry of ranked) {
                            if (
                                entry.count === bestCount
                                && entry.length === bestLength
                            ) {
                                entry.item.setAttribute(
                                    'data-docflow-hint-rank', token
                                );
                            }
                        }
                        return true;
                    }""",
                    [normalized_hints[:12], rank_token, checkbox_control],
                ))
                ranking_inspected = True
            except Exception:
                ranked = False
            if ranked:
                matched = unique_control(
                    self._page.locator(
                        '[data-docflow-hint-rank="'
                        f'{rank_token}"]'
                    ),
                    occurrence_value=occurrence,
                    group_visual_rows=control_kind in {
                        "date", "duration", "text_segments",
                    },
                )
                if matched is not None:
                    matched = finalized(matched)
                    if matched is not None:
                        return matched
            if not ranking_inspected:
                # Lightweight test/offline drivers may not expose page-level
                # evaluation. Preserve the old exact selector capability only
                # in that environment. A real inspected DOM never falls back
                # from ranked identity to a generic single hint.
                for hint in reversed(normalized_hints):
                    if checkbox_control:
                        selector = (
                            f'input[type="checkbox"][id*="{hint}" i], '
                            f'input[type="checkbox"][name*="{hint}" i]'
                        )
                    else:
                        selector = (
                            f'input[id*="{hint}" i], '
                            f'textarea[id*="{hint}" i], '
                            f'select[id*="{hint}" i], '
                            f'input[name*="{hint}" i], '
                            f'textarea[name*="{hint}" i], '
                            f'select[name*="{hint}" i]'
                        )
                    matched = unique_control(
                        self._page.locator(selector),
                        occurrence_value=occurrence,
                        group_visual_rows=control_kind in {
                            "date", "duration", "text_segments",
                        },
                    )
                    if matched is not None:
                        matched = finalized(matched)
                        if matched is not None:
                            return matched

        if checkbox_control:
            matched = self._nearest_labeled_checkbox(
                labels,
                occurrence=occurrence,
            )
            if matched is not None:
                return matched

        schema = DEFAULT_FIELD_SCHEMAS.get(field_id)
        label_terms = list(labels)
        if schema is not None and schema.label not in label_terms:
            label_terms.append(schema.label)
        for label in label_terms:
            text = str(label).split("[control=", 1)[0].strip()
            if not text:
                continue
            pattern = re.compile(
                rf"^\s*{re.escape(text)}\s*(?:\*|:)?\s*$",
                re.IGNORECASE,
            )
            matched = self._unique_actionable_control(
                self._page.get_by_label(pattern),
                occurrence=occurrence,
                group_visual_rows=control_kind in {
                    "date", "duration", "text_segments",
                },
            )
            if matched is not None:
                matched = finalized(matched)
                if matched is not None:
                    return matched
            if control_kind in {
                "text", "textarea", "date", "select", "select_text",
                "duration", "text_segments",
            }:
                matched = self._unique_visible_form_control(
                    self._page.get_by_label(pattern),
                    occurrence=occurrence,
                    group_visual_rows=control_kind in {
                        "date", "duration", "text_segments",
                    },
                )
                if matched is not None:
                    matched = finalized(matched)
                    if matched is not None:
                        return matched
        if control_kind in {
            "text", "textarea", "date", "select", "select_text",
            "duration", "text_segments",
        }:
            matched = self._nearest_labeled_form_control(
                label_terms,
                control_kind,
                occurrence=occurrence,
            )
            if matched is not None:
                return finalized(matched)
        return None

    @staticmethod
    def _control_kind(labels):
        for label in labels or ():
            matched = re.search(
                r"\[control=([a-z0-9_-]+)",
                str(label or ""),
                flags=re.IGNORECASE,
            )
            if matched:
                return matched.group(1).casefold()
        return ""

    @staticmethod
    def _descriptor_approved_value(labels):
        values = []
        for label in labels or ():
            for matched in re.finditer(
                r"(?:\[|;)\s*human-approved\s+value="
                r"\s*([^;\]]+?)(?=\s*(?:;|\]))",
                str(label or ""),
                flags=re.IGNORECASE,
            ):
                value = matched.group(1).strip()
                if value:
                    values.append(value)
        return values[0] if values and len(set(values)) == 1 else ""

    @staticmethod
    def _control_occurrence(labels):
        """Return a validated 1-based repeated-control occurrence.

        ``None`` means the descriptor intentionally omitted occurrence and the
        normal unique-control rule still applies.  The boolean distinguishes
        that case from malformed, out-of-policy, or conflicting metadata.
        """
        values = []
        saw_occurrence = False
        for raw_label in labels or ():
            label = str(raw_label or "")
            structural = re.split(
                r"\bhuman-approved\s+value\s*=",
                label,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            if re.search(r"\boccurrence\s*=", structural, re.IGNORECASE):
                saw_occurrence = True
            for matched in re.finditer(
                r"(?:\[|;)\s*occurrence\s*=\s*([0-9]+)"
                r"(?=\s*(?:;|\]))",
                structural,
                flags=re.IGNORECASE,
            ):
                values.append(int(matched.group(1)))
        if not saw_occurrence:
            return None, True
        if not values or len(set(values)) != 1:
            return None, False
        occurrence = values[0]
        if occurrence < 1 or occurrence > 20:
            return None, False
        return occurrence, True

    def _segmented_group_control(self, locator):
        """Validate and mark one compact group of fixed-width text inputs."""
        token = f"segments-{uuid4().hex}"
        try:
            count = locator.evaluate(
                """(el, token) => {
                    const visible = (item) => {
                        const style = getComputedStyle(item);
                        const box = item.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0 && box.height > 0;
                    };
                    const usable = (item) => {
                        if (
                            item.tagName.toLowerCase() !== 'input'
                            || item.disabled
                            || item.readOnly
                            || !visible(item)
                        ) return false;
                        const type = String(
                            item.getAttribute('type') || 'text'
                        ).toLowerCase();
                        return ['text', 'tel'].includes(type)
                            && Number.isInteger(item.maxLength)
                            && item.maxLength >= 1
                            && item.maxLength <= 20;
                    };
                    if (!usable(el)) return 0;
                    let current = el.parentElement;
                    for (let depth = 0; depth < 8 && current; depth += 1) {
                        const controls = Array.from(
                            current.querySelectorAll('input')
                        ).filter(usable);
                        if (
                            controls.includes(el)
                            && controls.length >= 2
                            && controls.length <= 6
                        ) {
                            const boxes = controls.map(
                                item => item.getBoundingClientRect()
                            );
                            const minTop = Math.min(
                                ...boxes.map(box => box.top)
                            );
                            const maxTop = Math.max(
                                ...boxes.map(box => box.top)
                            );
                            if (maxTop - minTop > 12) {
                                current = current.parentElement;
                                continue;
                            }
                            controls.sort((left, right) => {
                                const a = left.getBoundingClientRect();
                                const b = right.getBoundingClientRect();
                                return a.left - b.left || a.top - b.top;
                            });
                            current.setAttribute(
                                'data-docflow-segment-host', token
                            );
                            controls.forEach(item => item.setAttribute(
                                'data-docflow-segment-group', token
                            ));
                            return controls.length;
                        }
                        current = current.parentElement;
                    }
                    return 0;
                }""",
                token,
            )
        except Exception:
            return None
        if not isinstance(count, int) or count < 2 or count > 6:
            return None
        group = self._page.locator(
            f'[data-docflow-segment-group="{token}"]'
        )
        try:
            return group.first if group.count() == count else None
        except Exception:
            return None

    def _nearest_labeled_checkbox(self, labels, occurrence=None):
        terms = []
        for label in labels or ():
            term = str(label).split("[control=", 1)[0].strip()
            if term and term not in terms:
                terms.append(term)
        if not terms:
            return None
        token = f"checkbox-{uuid4().hex}"
        try:
            found = self._page.evaluate(
                """([terms, token, occurrence]) => {
                    const normalize = (value) => String(value || '')
                        .replace(/\\s+/g, ' ').trim().toLowerCase();
                    const visible = (element) => {
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0 && box.height > 0;
                    };
                    const checkboxes = Array.from(document.querySelectorAll(
                        'input[type="checkbox"]'
                    )).filter((item) => !item.disabled && visible(item));
                    if (!checkboxes.length) return false;
                    const elements = Array.from(document.querySelectorAll(
                        'label, legend, span, div, td, th, p, h1, h2, h3, h4'
                    )).filter((item) => {
                        if (!visible(item)) return false;
                        if (item.querySelector('input, textarea, select')) {
                            return false;
                        }
                        const text = normalize(item.innerText);
                        if (!text || text.length > 160) return false;
                        return terms.some((term) => {
                            const wanted = normalize(term);
                            return text === wanted
                                || text.startsWith(`${wanted} `);
                        });
                    });
                    const ranked = [];
                    for (const anchor of elements) {
                        const anchorBox = anchor.getBoundingClientRect();
                        for (const checkbox of checkboxes) {
                            const box = checkbox.getBoundingClientRect();
                            const vertical = box.top - anchorBox.bottom;
                            if (vertical < -24 || vertical > 360) continue;
                            const horizontal = Math.abs(
                                (box.left + box.width / 2)
                                - (anchorBox.left + anchorBox.width / 2)
                            );
                            ranked.push({
                                checkbox,
                                score: Math.max(0, vertical)
                                    + Math.min(horizontal, 600) * 0.04
                            });
                        }
                    }
                    ranked.sort((a, b) => a.score - b.score);
                    if (!ranked.length) return false;
                    let selected = ranked[0].checkbox;
                    if (occurrence !== null) {
                        const candidates = [];
                        for (const anchor of elements) {
                            const anchorBox = anchor.getBoundingClientRect();
                            const local = [];
                            for (const checkbox of checkboxes) {
                                const box = checkbox.getBoundingClientRect();
                                const vertical = box.top - anchorBox.bottom;
                                if (vertical < -24 || vertical > 360) continue;
                                const horizontal = Math.abs(
                                    (box.left + box.width / 2)
                                    - (
                                        anchorBox.left
                                        + anchorBox.width / 2
                                    )
                                );
                                local.push({
                                    checkbox,
                                    score: Math.max(0, vertical)
                                        + Math.min(horizontal, 600) * 0.04
                                });
                            }
                            local.sort((a, b) => a.score - b.score);
                            if (!local.length) continue;
                            if (
                                local[1]
                                && local[1].score - local[0].score < 12
                            ) {
                                continue;
                            }
                            if (!candidates.includes(local[0].checkbox)) {
                                candidates.push(local[0].checkbox);
                            }
                        }
                        candidates.sort((left, right) => {
                            const a = left.getBoundingClientRect();
                            const b = right.getBoundingClientRect();
                            return a.top - b.top || a.left - b.left;
                        });
                        const index = occurrence - 1;
                        if (index < 0 || index >= candidates.length) {
                            return false;
                        }
                        const targetBox = candidates[index]
                            .getBoundingClientRect();
                        const overlaps = candidates.some((item, itemIndex) => {
                            if (itemIndex === index) return false;
                            const box = item.getBoundingClientRect();
                            return Math.abs(box.top - targetBox.top) < 1
                                && Math.abs(box.left - targetBox.left) < 1;
                        });
                        if (overlaps) return false;
                        selected = candidates[index];
                    }
                    selected.setAttribute(
                        'data-docflow-structured-control', token
                    );
                    return true;
                }""",
                [terms, token, occurrence],
            )
        except Exception:
            return None
        if not found:
            return None
        locator = self._page.locator(
            f'[data-docflow-structured-control="{token}"]'
        )
        return locator.first if locator.count() == 1 else None

    def _nearest_labeled_form_control(
        self,
        labels,
        control_kind,
        occurrence=None,
    ):
        """Resolve one legacy CEAC control from visible label geometry.

        CEAC frequently renders question text in table cells without a
        ``label[for]`` association. Playwright's accessibility label lookup
        therefore cannot find otherwise fixed controls. This resolver is
        deliberately generic and high-confidence: the best label/control pair
        must be close, directionally plausible, and clearly better than the
        runner-up.
        """
        terms = []
        for label in labels or ():
            term = str(label).split("[control=", 1)[0].strip()
            if term and term not in terms:
                terms.append(term)
        if not terms:
            return None
        token = f"near-{uuid4().hex}"
        try:
            found = self._page.evaluate(
                """([terms, kind, token, occurrence]) => {
                    const normalize = (value) => String(value || '')
                        .toLowerCase()
                        .replace(/[\\s:*?]+/g, ' ')
                        .replace(/[^a-z0-9\\u4e00-\\u9fff /'-]/g, '')
                        .trim();
                    const wanted = terms.map(normalize).filter(
                        (term) => term.length >= 2
                    );
                    if (!wanted.length) return false;
                    const visible = (element) => {
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0
                            && box.height > 0;
                    };
                    const allowedControl = (control) => {
                        if (!visible(control)) return false;
                        const tag = control.tagName.toLowerCase();
                        const type = String(
                            control.getAttribute('type') || ''
                        ).toLowerCase();
                        if (tag === 'textarea') {
                            return ['text', 'textarea'].includes(kind);
                        }
                        if (tag === 'select') {
                            return [
                                'select', 'select_text', 'date', 'duration'
                            ].includes(kind);
                        }
                        if (tag !== 'input') return false;
                        if ([
                            'hidden', 'radio', 'checkbox', 'button',
                            'submit', 'reset', 'file', 'image', 'password'
                        ].includes(type)) return false;
                        return [
                            'text', 'textarea', 'date', 'duration',
                            'text_segments'
                        ].includes(kind);
                    };
                    const controls = Array.from(document.querySelectorAll(
                        'input, textarea, select'
                    )).filter(allowedControl);
                    if (!controls.length) return false;
                    const anchors = Array.from(document.querySelectorAll(
                        'label, legend, span, td, th, p, strong, div'
                    )).filter((element) => {
                        if (!visible(element)) return false;
                        const text = normalize(element.innerText);
                        if (!text || text.length > 220) return false;
                        return wanted.some((term) => (
                            text === term
                            || text.startsWith(`${term} `)
                            || (
                                term.length >= 8
                                && term.startsWith(`${text} `)
                            )
                        ));
                    });
                    const ranked = [];
                    for (const anchor of anchors) {
                        const anchorBox = anchor.getBoundingClientRect();
                        const associated = anchor.tagName.toLowerCase()
                            === 'label' ? anchor.control : null;
                        for (const control of controls) {
                            const box = control.getBoundingClientRect();
                            const verticalCenter = Math.abs(
                                (box.top + box.height / 2)
                                - (anchorBox.top + anchorBox.height / 2)
                            );
                            const below = box.top - anchorBox.bottom;
                            const right = box.left - anchorBox.right;
                            let score = Infinity;
                            if (associated === control) {
                                score = 0;
                            } else if (
                                anchor.contains(control)
                                || anchor.parentElement?.contains(control)
                            ) {
                                score = 4 + verticalCenter * 0.04;
                            } else if (
                                right >= -24
                                && right <= 760
                                && verticalCenter <= 70
                            ) {
                                score = 14
                                    + verticalCenter * 0.9
                                    + Math.max(0, right) * 0.025;
                            } else if (
                                below >= -12
                                && below <= 240
                                && Math.abs(box.left - anchorBox.left) <= 560
                            ) {
                                score = 22
                                    + Math.max(0, below) * 0.5
                                    + Math.abs(
                                        box.left - anchorBox.left
                                    ) * 0.025;
                            }
                            if (Number.isFinite(score)) {
                                ranked.push({anchor, control, score});
                            }
                        }
                    }
                    ranked.sort((a, b) => a.score - b.score);
                    if (!ranked.length || ranked[0].score > 125) {
                        return false;
                    }
                    let selected = ranked[0].control;
                    if (occurrence === null) {
                        const best = ranked[0];
                        const competing = ranked.find(
                            (item) => item.control !== best.control
                        );
                        if (
                            competing
                            && competing.score - best.score < 12
                        ) {
                            return false;
                        }
                    } else {
                        const candidates = [];
                        for (const anchor of anchors) {
                            const local = ranked.filter(
                                (item) => item.anchor === anchor
                            );
                            if (!local.length || local[0].score > 125) {
                                continue;
                            }
                            const best = local[0];
                            const competing = local.find(
                                (item) => item.control !== best.control
                            );
                            const bestBox = best.control
                                .getBoundingClientRect();
                            const competingBox = competing
                                ? competing.control.getBoundingClientRect()
                                : null;
                            const sameCompositeRow = (
                                [
                                    'date', 'duration', 'text_segments'
                                ].includes(kind)
                                && competingBox
                                && Math.abs(
                                    competingBox.top - bestBox.top
                                ) <= 12
                            );
                            if (
                                competing
                                && competing.score - best.score < 12
                                && !sameCompositeRow
                            ) {
                                continue;
                            }
                            if (!candidates.includes(best.control)) {
                                candidates.push(best.control);
                            }
                        }
                        candidates.sort((left, right) => {
                            const a = left.getBoundingClientRect();
                            const b = right.getBoundingClientRect();
                            return a.top - b.top || a.left - b.left;
                        });
                        const index = occurrence - 1;
                        if (index < 0 || index >= candidates.length) {
                            return false;
                        }
                        const targetBox = candidates[index]
                            .getBoundingClientRect();
                        const overlaps = candidates.some((item, itemIndex) => {
                            if (itemIndex === index) return false;
                            const box = item.getBoundingClientRect();
                            return Math.abs(box.top - targetBox.top) < 1
                                && Math.abs(box.left - targetBox.left) < 1;
                        });
                        if (overlaps) return false;
                        selected = candidates[index];
                    }
                    selected.setAttribute(
                        'data-docflow-near-control', token
                    );
                    return true;
                }""",
                [
                    terms,
                    str(control_kind or ""),
                    token,
                    occurrence,
                ],
            )
        except Exception:
            return None
        if not found:
            return None
        locator = self._page.locator(
            f'[data-docflow-near-control="{token}"]'
        )
        return locator.first if locator.count() == 1 else None

    @classmethod
    def _unique_actionable_control(
        cls,
        locator,
        occurrence=None,
        group_visual_rows=False,
    ):
        try:
            count = min(locator.count(), 25)
        except Exception:
            return None
        matches = []
        for index in range(count):
            item = locator.nth(index)
            try:
                metadata = item.evaluate(
                    """el => ({
                        tag: el.tagName.toLowerCase(),
                        type: String(el.getAttribute('type') || '').toLowerCase(),
                        disabled: Boolean(el.disabled),
                        readOnly: Boolean(el.readOnly),
                        rect: (() => {
                            const box = el.getBoundingClientRect();
                            return {
                                top: box.top,
                                left: box.left,
                                width: box.width,
                                height: box.height
                            };
                        })()
                    })"""
                )
                if not item.is_visible() or metadata.get("disabled"):
                    continue
                tag = metadata.get("tag")
                control_type = metadata.get("type")
                if tag == "select":
                    matches.append((item, metadata.get("rect"), index))
                elif tag == "textarea" and not metadata.get("readOnly"):
                    matches.append((item, metadata.get("rect"), index))
                elif (
                    tag == "input"
                    and control_type not in {
                        "hidden", "radio", "checkbox", "button",
                        "submit", "reset", "file", "image", "password",
                    }
                    and not metadata.get("readOnly")
                ):
                    matches.append((item, metadata.get("rect"), index))
            except Exception:
                continue
        return cls._select_visual_occurrence(
            matches,
            occurrence,
            group_visual_rows=group_visual_rows,
        )

    @classmethod
    def _unique_visible_form_control(
        cls,
        locator,
        occurrence=None,
        group_visual_rows=False,
    ):
        """Resolve one visible field even when a D/N/A guard disables it."""
        try:
            count = min(locator.count(), 25)
        except Exception:
            return None
        matches = []
        for index in range(count):
            item = locator.nth(index)
            try:
                tag = str(item.evaluate(
                    "el => el.tagName.toLowerCase()"
                ))
                if (
                    item.is_visible()
                    and tag in {"input", "textarea", "select"}
                ):
                    rect = item.evaluate(
                        """el => {
                            const box = el.getBoundingClientRect();
                            return {
                                top: box.top,
                                left: box.left,
                                width: box.width,
                                height: box.height
                            };
                        }"""
                    )
                    matches.append((item, rect, index))
            except Exception:
                continue
        return cls._select_visual_occurrence(
            matches,
            occurrence,
            group_visual_rows=group_visual_rows,
        )

    @staticmethod
    def _select_visual_occurrence(
        matches,
        occurrence,
        group_visual_rows=False,
    ):
        """Select one visible match using stable top/left/DOM order.

        With no occurrence, multiple controls remain deliberately ambiguous.
        With a 1-based occurrence, every candidate must provide geometry and an
        overlapping visual position is rejected rather than resolved by an
        arbitrary DOM implementation detail.
        """
        if occurrence is None and not group_visual_rows:
            return matches[0][0] if len(matches) == 1 else None
        if occurrence is not None and (
            not isinstance(occurrence, int) or occurrence < 1
        ):
            return None
        positioned = []
        for item, raw_rect, index in matches:
            rect = raw_rect if isinstance(raw_rect, dict) else {}
            try:
                top = float(rect["top"])
                left = float(rect["left"])
                width = float(rect.get("width") or 0)
                height = float(rect.get("height") or 0)
            except (KeyError, TypeError, ValueError):
                return None
            positioned.append((
                top,
                left,
                int(index),
                width,
                height,
                item,
            ))
        positioned.sort(key=lambda match: match[:3])
        if group_visual_rows:
            rows = []
            for match in positioned:
                if not rows or abs(match[0] - rows[-1][0][0]) > 12:
                    rows.append([match])
                else:
                    rows[-1].append(match)
            positioned = [
                sorted(row, key=lambda match: (match[1], match[2]))[0]
                for row in rows
            ]
        if occurrence is None:
            return positioned[0][-1] if len(positioned) == 1 else None
        index = occurrence - 1
        if index >= len(positioned):
            return None
        selected = positioned[index]
        if any(
            other_index != index
            and abs(other[0] - selected[0]) < 1
            and abs(other[1] - selected[1]) < 1
            for other_index, other in enumerate(positioned)
        ):
            return None
        return selected[-1]

    def _enable_guarded_control(self, locator):
        """Uncheck a nearby D/N/A guard when an approved value must be typed."""
        try:
            if locator.is_enabled():
                return True
        except Exception:
            return False
        token = f"guard-{uuid4().hex}"
        try:
            found = locator.evaluate(
                """(el, token) => {
                    const visible = (item) => {
                        const style = getComputedStyle(item);
                        const box = item.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0 && box.height > 0;
                    };
                    let current = el.parentElement;
                    for (let depth = 0; depth < 8 && current; depth += 1) {
                        const candidates = Array.from(
                            current.querySelectorAll(
                                'input[type="checkbox"]:checked'
                            )
                        ).filter((item) => {
                            if (item.disabled || !visible(item)) return false;
                            const label = Array.from(item.labels || [])
                                .map((node) => node.innerText || '')
                                .join(' ');
                            const nearby = item.parentElement?.innerText || '';
                            return /does?\\s+not\\s+(?:apply|know)/i.test(
                                `${label} ${nearby}`
                            );
                        });
                        if (candidates.length === 1) {
                            candidates[0].setAttribute(
                                'data-docflow-enable-guard', token
                            );
                            return true;
                        }
                        current = current.parentElement;
                    }
                    return false;
                }""",
                token,
            )
        except Exception:
            return False
        if not found:
            return False
        guard = self._page.locator(
            f'[data-docflow-enable-guard="{token}"]'
        ).first
        try:
            if self._visual_execution:
                self._move_pointer_to_locator(guard, clicking=True)
            guard.uncheck()
            locator.wait_for(state="visible", timeout=2000)
            return bool(locator.is_enabled())
        except Exception:
            return False

    def _mark_field(self, locator, action):
        if not action.field_id:
            return
        token = re.sub(r"[^A-Za-z0-9_.-]", "_", action.field_id)
        requested_owner = str(action.field_id)
        marker = f"mark-{uuid4().hex}"
        try:
            ownership = locator.evaluate(
                """(el, values) => {
                    const [marker, token, owner] = values;
                    const existingToken = String(
                        el.getAttribute('data-docflow-field') || ''
                    );
                    const existingOwner = String(
                        el.getAttribute('data-docflow-field-owner') || ''
                    );
                    const collision = Boolean(
                        (existingOwner && existingOwner !== owner)
                        || (
                            !existingOwner
                            && existingToken
                            && existingToken !== token
                        )
                    );
                    if (!collision) {
                        el.setAttribute(
                            'data-docflow-mark-target', marker
                        );
                    }
                    return {
                        token: existingToken,
                        owner: existingOwner,
                        collision
                    };
                }""",
                [marker, token, requested_owner],
            )
        except Exception as error:
            raise ControlBindingUnavailable(
                "The resolved DOM control disappeared before it could be marked"
            ) from error
        if not isinstance(ownership, dict):
            raise ControlBindingUnavailable(
                "The resolved DOM control did not expose binding ownership"
            )
        if bool(ownership.get("collision")):
            raise ControlBindingCollision(
                "The resolved DOM control is already owned by another field"
            )
        # A locator may itself be backed by the marker that is about to be
        # refreshed (notably deterministic repeater buttons).  Removing every
        # existing marker first invalidates that locator and Playwright waits
        # for 30 seconds before timing out.  Pin the resolved element with a
        # one-use marker, clear only duplicates, then update it in place.
        try:
            self._page.locator(
                f'[data-docflow-field="{token}"]'
            ).evaluate_all(
                """(items, marker) => items.forEach((item) => {
                    if (
                        item.getAttribute('data-docflow-mark-target')
                        !== marker
                    ) {
                        item.removeAttribute('data-docflow-field');
                        item.removeAttribute('data-docflow-field-owner');
                    }
                })""",
                marker,
            )
        except Exception:
            pass
        pinned = self._page.locator(
            f'[data-docflow-mark-target="{marker}"]'
        ).first
        pinned.evaluate(
            """(el, values) => {
                const [token, owner] = values;
                el.setAttribute('data-docflow-field', token);
                el.setAttribute('data-docflow-field-owner', owner);
                el.removeAttribute('data-docflow-mark-target');
            }""",
            [token, requested_owner],
        )
        self._field_selectors[action.field_id] = (
            f'[data-docflow-field="{token}"]'
        )
        self._semantic_field_bindings.add(action.field_id)
        if self._action_watch_active:
            self._action_field_tokens_before.add(token)

    def _pixel_x(self, normalized):
        return int(int(normalized) / 1000 * self.width)

    def _pixel_y(self, normalized):
        return int(int(normalized) / 1000 * self.height)

    def _require_page(self):
        if self._page is None:
            raise RuntimeError("Browser session has not been started")

    def __del__(self):
        self.close()


def register_builtin_providers(registry):
    """Register every built-in provider name used by the default environment."""
    registrations = (
        ("document_parser", "mineru", MinerUAdapter),
        ("ocr_fallback", "mineru", MinerUAdapter),
        ("ocr", "mineru", MinerUAdapter),
        ("ocr", "paddle", PaddleOCRAdapter),
        ("ocr_fallback", "paddle", PaddleOCRAdapter),
        ("extraction", "deepseek", DeepSeekAdapter),
        ("review", "deepseek", DeepSeekAdapter),
        ("translation", "deepseek", DeepSeekAdapter),
        ("computer_use", "google", GeminiComputerUseAdapter),
        ("computer_use", "gemini", GeminiComputerUseAdapter),
        ("computer_use", "openrouter", OpenRouterComputerUseAdapter),
        ("browser", "playwright", PlaywrightBrowserDriver),
    )
    for capability, name, factory in registrations:
        if not registry.has(capability, name):
            registry.register(capability, name, factory)
    return registry
