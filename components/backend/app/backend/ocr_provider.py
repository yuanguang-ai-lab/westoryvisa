"""Select the configured OCR/document parsing provider on the backend only."""

import os

from . import docling_client, mineru_client


def selected_provider():
    configured = os.environ.get("OCR_PROVIDER", "auto").strip().lower()
    if configured in {"mineru", "miner-u"}:
        return "mineru"
    if configured in {"docling", "rapidocr", "local"}:
        return "docling"
    return "mineru" if mineru_client.token() else "docling"


def service_status(docling_installed=False):
    if selected_provider() == "mineru":
        return mineru_client.status()
    result = docling_client.check_docling()
    result.update({
        "configured": True,
        "installed": bool(docling_installed),
        "starting": False,
        "remote": False,
        "provider": "docling",
        "providerLabel": "Docling / RapidOCR 本地解析",
    })
    if result.get("available"):
        result["message"] = "Docling Serve + RapidOCR 已连接，可进行本地版面与中英文识别"
    elif not docling_installed:
        result["message"] = "尚未安装本地文档扫描环境"
    else:
        result["message"] = "本地扫描环境已安装，但服务尚未启动"
    return result


def convert_file(*args, **kwargs):
    if selected_provider() != "mineru":
        return docling_client.convert_file(*args, **kwargs)
    try:
        return mineru_client.convert_file(*args, **kwargs)
    except mineru_client.MinerUError as error:
        fallback = os.environ.get(
            "MINERU_FALLBACK_TO_DOCLING", "true"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not fallback or not docling_client.check_docling().get("available"):
            raise
        result = docling_client.convert_file(*args, **kwargs)
        result["fallbackReason"] = str(error)[:300]
        return result
