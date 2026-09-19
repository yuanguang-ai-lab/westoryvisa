#!/usr/bin/env python3
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:5001"
DEFAULT_KEY_FILE = Path(__file__).resolve().parent.parent / "data" / "docling_api_key"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
DOCUMENT_ORIENTATION_MARKERS = {
    "passport": (
        "passport", "护照", "中华人民共和国", "surname", "given names",
        "nationality", "date of birth", "date of expiry", "p<chn",
    ),
    "national_id": (
        "居民身份证", "公民身份号码", "姓名", "性别", "民族", "出生",
        "住址", "签发机关", "有效期限",
    ),
}


class DoclingError(RuntimeError):
    pass


def docling_base_url():
    return os.environ.get("DOCLING_SERVE_URL", DEFAULT_BASE_URL).rstrip("/")


def docling_api_key():
    configured = os.environ.get("DOCLING_SERVE_API_KEY", "").strip()
    if configured:
        return configured
    key_file = Path(os.environ.get("DOCLING_SERVE_API_KEY_FILE", DEFAULT_KEY_FILE))
    try:
        return key_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def request_headers(extra=None):
    headers = dict(extra or {})
    api_key = docling_api_key()
    if api_key:
        headers["X-Api-Key"] = api_key
    return headers


def check_docling(timeout=2):
    request = Request(
        f"{docling_base_url()}/openapi.json",
        headers=request_headers({"Accept": "application/json"}),
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return {
                "available": response.status == 200,
                "service": "Docling Serve",
                "ocrEngine": "RapidOCR",
            }
    except (HTTPError, URLError, TimeoutError, OSError):
        return {
            "available": False,
            "service": "Docling Serve",
            "ocrEngine": "RapidOCR",
        }


def multipart_body(file_path, filename, mime_type, ocr_mode="preset", force_ocr=False):
    boundary = f"----DocFlow{uuid.uuid4().hex}"
    chunks = []

    def add_field(name, value):
        chunks.extend([
            f"--{boundary}\r\n".encode("ascii"),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
            str(value).encode("utf-8"),
            b"\r\n",
        ])

    safe_filename = filename.replace('"', "").replace("\r", "").replace("\n", "")
    chunks.extend([
        f"--{boundary}\r\n".encode("ascii"),
        f'Content-Disposition: form-data; name="files"; filename="{safe_filename}"\r\n'.encode("utf-8"),
        f"Content-Type: {mime_type}\r\n\r\n".encode("ascii"),
        Path(file_path).read_bytes(),
        b"\r\n",
    ])

    extension = Path(filename).suffix.lower()
    add_field("from_formats", "pdf" if extension == ".pdf" else "image")
    add_field("to_formats", "json")
    add_field("to_formats", "md")
    add_field("to_formats", "text")
    add_field("do_ocr", "true")
    add_field("force_ocr", "true" if force_ocr else "false")
    add_field("image_export_mode", "placeholder")
    add_field("table_mode", "accurate")
    if ocr_mode == "preset":
        add_field("ocr_preset", "rapidocr")
    elif ocr_mode == "legacy":
        add_field("ocr_engine", "rapidocr")

    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), boundary


def _convert_request(file_path, filename, mime_type, ocr_mode, timeout, force_ocr=False):
    body, boundary = multipart_body(
        file_path, filename, mime_type, ocr_mode, force_ocr=force_ocr
    )
    request = Request(
        f"{docling_base_url()}/v1/convert/file",
        data=body,
        method="POST",
        headers=request_headers({
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }),
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def request_conversion(path, filename, mime_type, timeout, force_ocr):
    try:
        return _convert_request(
            path, filename, mime_type, "preset", timeout, force_ocr=force_ocr
        )
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        if error.code == 422:
            try:
                return _convert_request(
                    path, filename, mime_type, "legacy", timeout,
                    force_ocr=force_ocr,
                )
            except HTTPError as retry_error:
                retry_body = retry_error.read().decode("utf-8", errors="replace")
                raise DoclingError(
                    f"Docling 不接受 RapidOCR 配置（HTTP {retry_error.code}）：{retry_body[:500]}"
                ) from retry_error
            except (URLError, TimeoutError, OSError) as retry_error:
                raise DoclingError(_friendly_error(retry_error)) from retry_error
        else:
            raise DoclingError(f"Docling 解析失败（HTTP {error.code}）：{error_body[:500]}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise DoclingError(_friendly_error(error)) from error


def convert_file(file_path, filename=None, mime_type=None, timeout=240, document_type=""):
    path = Path(file_path)
    filename = filename or path.name
    mime_type = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    is_image = path.suffix.lower() in IMAGE_EXTENSIONS
    force_ocr = is_image or os.environ.get("DOCLING_FORCE_OCR", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    result = request_conversion(path, filename, mime_type, timeout, force_ocr)
    parsed = parse_conversion_result(result, force_ocr)

    if is_image:
        parsed = choose_image_orientation(
            path, filename, mime_type, timeout, parsed, document_type=document_type
        )

    if not force_ocr and text_quality_score(parsed["text"]) < 0.42:
        try:
            forced_result = request_conversion(path, filename, mime_type, timeout, True)
            forced = parse_conversion_result(forced_result, True)
            if text_quality_score(forced["text"]) > text_quality_score(parsed["text"]):
                parsed = forced
        except DoclingError:
            pass
    return parsed


def choose_image_orientation(path, filename, mime_type, timeout, initial, document_type=""):
    """Run low-confidence images through 90-degree rotations and retain the best OCR."""
    expected_kind = expected_document_kind(document_type)
    initial_score = orientation_result_score(initial, expected_kind)
    candidates = [(0, initial_score, initial)]
    initial["rotationApplied"] = 0
    initial["autoRotated"] = False

    threshold = 0.82 if expected_kind else 0.68
    if initial_score >= threshold or not image_rotation_available():
        initial["orientationScore"] = round(initial_score, 3)
        initial["orientationCandidates"] = [{"rotation": 0, "score": round(initial_score, 3)}]
        return initial

    with tempfile.TemporaryDirectory(prefix="docflow-rotation-") as directory:
        for degrees in (90, 180, 270):
            rotated_path = Path(directory) / f"rotated-{degrees}{path.suffix.lower()}"
            try:
                rotate_image(path, rotated_path, degrees)
                result = request_conversion(
                    rotated_path, filename, mime_type, timeout, True
                )
                parsed = parse_conversion_result(result, True)
            except (DoclingError, OSError, subprocess.SubprocessError):
                continue
            score = orientation_result_score(parsed, expected_kind)
            parsed["rotationApplied"] = degrees
            parsed["autoRotated"] = True
            candidates.append((degrees, score, parsed))

    best_degrees, best_score, best = max(candidates, key=lambda item: item[1])
    # Avoid changing a readable image for a negligible OCR-score difference.
    if best_degrees and best_score < initial_score + 0.045:
        best_degrees, best_score, best = candidates[0]
    best["rotationApplied"] = best_degrees
    best["autoRotated"] = bool(best_degrees)
    best["orientationScore"] = round(best_score, 3)
    best["orientationCandidates"] = [
        {"rotation": degrees, "score": round(score, 3)}
        for degrees, score, _ in candidates
    ]
    return best


def expected_document_kind(document_type):
    normalized = str(document_type or "").strip().lower()
    if "护照" in normalized or "passport" in normalized:
        return "passport"
    if any(marker in normalized for marker in ("身份证", "national id", "identity card")):
        return "national_id"
    return ""


def orientation_text_score(text, expected_kind=""):
    """Score OCR readability, favoring stable anchors found on Chinese identity documents."""
    value = str(text or "")
    compact = re.sub(r"\s+", "", value).lower()
    if not compact:
        return 0.0

    base = text_quality_score(value)
    line_count = len([line for line in value.splitlines() if line.strip()])
    layout_score = min(1.0, line_count / 7) * 0.08
    length_score = min(1.0, len(compact) / 120) * 0.12
    score = base * 0.55 + layout_score + length_score

    markers = DOCUMENT_ORIENTATION_MARKERS.get(expected_kind, ())
    marker_hits = sum(
        1 for marker in markers if re.sub(r"\s+", "", marker.lower()) in compact
    )
    if markers:
        score += min(0.28, marker_hits * 0.055)

    if re.search(r"(?<!\d)\d{17}[0-9x](?![0-9a-z])", compact, flags=re.IGNORECASE):
        score += 0.2
    mrz_lines = [re.sub(r"[^A-Z0-9<]", "", line.upper()) for line in value.splitlines()]
    if any(line.startswith("P<CHN") and len(line) >= 36 for line in mrz_lines):
        score += 0.18
    if any(re.match(r"^[A-Z][A-Z0-9<]{8}\dCHN\d{6}\d", line) for line in mrz_lines):
        score += 0.18
    return min(1.0, score)


def orientation_layout_score(document):
    """Measure whether OCR text boxes are horizontal rather than sideways."""
    weighted_score = 0.0
    total_weight = 0.0

    def visit(item):
        nonlocal weighted_score, total_weight
        if isinstance(item, dict):
            text = item.get("text") or item.get("orig")
            provenance = item.get("prov")
            if isinstance(text, str) and len(text.strip()) >= 3 and isinstance(provenance, list):
                for source in provenance:
                    bbox = source.get("bbox") if isinstance(source, dict) else None
                    if not isinstance(bbox, dict):
                        continue
                    try:
                        width = abs(float(bbox.get("r")) - float(bbox.get("l")))
                        height = abs(float(bbox.get("t")) - float(bbox.get("b")))
                    except (TypeError, ValueError):
                        continue
                    if width <= 0 or height <= 0:
                        continue
                    weight = min(80, len(text.strip()))
                    weighted_score += (width / (width + height)) * weight
                    total_weight += weight
                    break
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(document)
    return weighted_score / total_weight if total_weight else 0.5


def orientation_result_score(parsed, expected_kind=""):
    text_score = orientation_text_score((parsed or {}).get("text"), expected_kind)
    layout_score = orientation_layout_score((parsed or {}).get("json") or {})
    return min(1.0, text_score * 0.58 + layout_score * 0.42)


def image_rotation_available():
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return bool(shutil.which("sips"))


def rotate_image(source, target, degrees):
    """Rotate an image for OCR, using Pillow when available and macOS sips otherwise."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        sips = shutil.which("sips")
        if not sips:
            raise OSError("当前环境没有可用的图片旋转工具")
        completed = subprocess.run(
            [sips, "--rotate", str(degrees), str(source), "--out", str(target)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if completed.returncode or not target.is_file():
            message = completed.stderr.decode("utf-8", errors="replace")[:300]
            raise OSError(f"图片旋转失败：{message}")
        return

    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        rotated = image.rotate(degrees, expand=True)
        image_format = opened.format or {
            ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".tif": "TIFF", ".tiff": "TIFF",
        }.get(target.suffix.lower(), "PNG")
        if image_format == "JPEG" and rotated.mode not in {"RGB", "L"}:
            rotated = rotated.convert("RGB")
        save_options = {"quality": 95} if image_format == "JPEG" else {}
        rotated.save(target, format=image_format, **save_options)


def parse_conversion_result(result, forced_ocr=False):

    status = result.get("status")
    if status not in {"success", "partial_success"}:
        errors = result.get("errors") or []
        raise DoclingError(f"Docling 未完成解析：{json.dumps(errors, ensure_ascii=False)[:500]}")

    document = result.get("document") or {}
    json_content = document.get("json_content") or {}
    if isinstance(json_content, str):
        try:
            json_content = json.loads(json_content)
        except json.JSONDecodeError:
            json_content = {"raw": json_content}

    text = document.get("text_content") or document.get("md_content") or flatten_text(json_content)
    return {
        "status": status,
        "text": text.strip(),
        "json": json_content,
        "pages": extract_page_texts(json_content),
        "markdown": document.get("md_content") or "",
        "processingTime": result.get("processing_time"),
        "timings": result.get("timings") or {},
        "parser": "docling-serve",
        "ocrEngine": "rapidocr",
        "forcedOcr": forced_ocr,
        "textQuality": round(text_quality_score(text), 3),
    }


def text_quality_score(text):
    compact = "".join(str(text or "").split())
    if not compact:
        return 0.0
    useful = sum(
        1 for character in compact
        if character.isalnum() or "\u4e00" <= character <= "\u9fff" or character in "<@/:-_."
    )
    useful_ratio = useful / len(compact)
    length_score = min(1.0, len(compact) / 180)
    corrupted_ratio = (compact.count("�") + compact.count("□")) / len(compact)
    return max(0.0, min(1.0, useful_ratio * 0.68 + length_score * 0.32 - corrupted_ratio))


def extract_page_texts(document):
    """Extract page-aware text blocks from Docling JSON without depending on Docling itself."""
    pages = {}
    seen = set()

    def visit(item):
        if isinstance(item, dict):
            block_text = item.get("text")
            provenance = item.get("prov")
            if isinstance(block_text, str) and block_text.strip() and isinstance(provenance, list):
                page_number = None
                for source in provenance:
                    if not isinstance(source, dict):
                        continue
                    raw_page = source.get("page_no")
                    if isinstance(raw_page, int):
                        page_number = max(1, raw_page)
                        break
                if page_number is not None:
                    normalized = block_text.strip()
                    marker = (page_number, normalized)
                    if marker not in seen:
                        pages.setdefault(page_number, []).append(normalized)
                        seen.add(marker)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(document)
    return [
        {"page": page_number, "text": "\n".join(lines)}
        for page_number, lines in sorted(pages.items())
    ]


def flatten_text(value):
    output = []

    def visit(item, key=""):
        if isinstance(item, dict):
            for child_key, child in item.items():
                visit(child, child_key)
        elif isinstance(item, list):
            for child in item:
                visit(child, key)
        elif isinstance(item, str) and item.strip() and key.lower() in {
            "text", "orig", "content", "caption", "label", "value"
        }:
            output.append(item.strip())

    visit(value)
    return "\n".join(dict.fromkeys(output))


def _friendly_error(error):
    if isinstance(error, HTTPError):
        return f"Docling 解析失败（HTTP {error.code}）"
    return (
        "无法连接 Docling Serve。请先运行“启动文档扫描.command”，"
        f"并确认服务地址为 {docling_base_url()}。"
    )
