#!/usr/bin/env python3
import base64
import json
import logging
import os
import re
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTBOX = ROOT / "data" / "email_outbox.eml"
DEFAULT_REFRESH_TOKEN_FILE = ROOT / "data" / "microsoft_refresh_token"
MICROSOFT_SMTP_SCOPE = "https://outlook.office.com/SMTP.Send offline_access"
LOGGER = logging.getLogger("docflow.email")
_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE = {}


class EmailDeliveryError(RuntimeError):
    pass


def _clean_header(value):
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def _env(name, legacy_name="", default=""):
    value = os.environ.get(name)
    if value is None and legacy_name:
        value = os.environ.get(legacy_name)
    return str(default if value is None else value)


def _bool_env(value, default=False):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_secret_file(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def mail_settings():
    legacy_mode = os.environ.get("DOCFLOW_MAIL_MODE", "").strip().lower()
    provider = os.environ.get("MAIL_PROVIDER", "").strip().lower()
    if not provider:
        provider = {
            "": "smtp",
            "auto": "smtp",
            "smtp": "smtp",
            "mailpit": "mailpit",
            "file": "file",
            "disabled": "disabled",
        }.get(legacy_mode, legacy_mode or "smtp")

    host = _env("SMTP_HOST", "DOCFLOW_SMTP_HOST").strip()
    username = _env("SMTP_USERNAME", "DOCFLOW_SMTP_USERNAME").strip()
    password = _env("SMTP_PASSWORD", "DOCFLOW_SMTP_PASSWORD")
    from_email = _env("MAIL_FROM", "DOCFLOW_SMTP_FROM_EMAIL").strip() or username
    from_name = _clean_header(_env("MAIL_FROM_NAME", "DOCFLOW_SMTP_FROM_NAME", "WestoryVisa"))

    if provider == "mailpit":
        host = host or "127.0.0.1"
        from_email = from_email or "noreply@westoryvisa.local"
    if provider == "microsoft_oauth2":
        host = host or "smtp-mail.outlook.com"
        username = from_email

    default_port = 1025 if provider == "mailpit" else (587 if provider == "microsoft_oauth2" else 465)
    try:
        port = int(_env("SMTP_PORT", "DOCFLOW_SMTP_PORT", default_port))
    except ValueError:
        port = default_port

    legacy_security = os.environ.get("DOCFLOW_SMTP_SECURITY", "").strip().lower()
    secure_value = os.environ.get("SMTP_SECURE")
    if secure_value is not None:
        security = "ssl" if _bool_env(secure_value) else "starttls"
    elif legacy_security:
        security = legacy_security
    else:
        security = "none" if provider == "mailpit" else ("ssl" if port == 465 else "starttls")
    if provider == "microsoft_oauth2":
        security = "starttls"

    tenant_id = os.environ.get("MICROSOFT_TENANT_ID", "").strip()
    client_id = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET", "")
    refresh_token_file = Path(os.environ.get("MICROSOFT_REFRESH_TOKEN_FILE", DEFAULT_REFRESH_TOKEN_FILE))
    refresh_token = _read_secret_file(refresh_token_file) or os.environ.get("MICROSOFT_REFRESH_TOKEN", "").strip()

    missing = []
    if provider == "smtp":
        for key, value in (
            ("SMTP_HOST", host), ("MAIL_FROM", from_email),
            ("SMTP_USERNAME", username), ("SMTP_PASSWORD", password),
        ):
            if not value:
                missing.append(key)
    elif provider == "microsoft_oauth2":
        for key, value in (
            ("MAIL_FROM", from_email), ("MICROSOFT_TENANT_ID", tenant_id),
            ("MICROSOFT_CLIENT_ID", client_id), ("MICROSOFT_CLIENT_SECRET", client_secret),
            ("MICROSOFT_REFRESH_TOKEN", refresh_token), ("SMTP_HOST", host),
        ):
            if not value:
                missing.append(key)
    elif provider == "mailpit":
        if not host or not from_email:
            missing.extend(["SMTP_HOST", "MAIL_FROM"])
    elif provider not in {"file", "disabled"}:
        missing.append("MAIL_PROVIDER")

    configured = provider in {"smtp", "microsoft_oauth2", "mailpit", "file"} and not missing
    return {
        "provider": provider,
        "host": host,
        "port": port,
        "security": security,
        "username": username,
        "password": password,
        "fromEmail": from_email,
        "fromName": from_name,
        "tenantId": tenant_id,
        "clientId": client_id,
        "clientSecret": client_secret,
        "refreshToken": refresh_token,
        "refreshTokenFile": refresh_token_file,
        "scope": os.environ.get("MICROSOFT_SCOPES", MICROSOFT_SMTP_SCOPE).strip(),
        "configured": configured,
        "missing": missing,
        "outbox": Path(os.environ.get("DOCFLOW_EMAIL_OUTBOX", DEFAULT_OUTBOX)),
    }


def mail_service_status():
    settings = mail_settings()
    provider = settings["provider"]
    labels = {
        "smtp": "普通 SMTP",
        "microsoft_oauth2": "Microsoft Outlook OAuth2",
        "mailpit": "Mailpit 本地测试",
        "file": "本地文件测试",
        "disabled": "未启用",
    }
    if settings["configured"]:
        message = f"{labels.get(provider, provider)} 已配置"
    elif settings["missing"]:
        message = "缺少环境变量：" + "、".join(settings["missing"])
    else:
        message = "尚未配置发信服务"
    return {
        "configured": settings["configured"],
        "mode": provider,
        "provider": provider,
        "deliversExternalEmail": provider in {"smtp", "microsoft_oauth2"} and settings["configured"],
        "message": message,
    }


def reset_token_cache():
    with _TOKEN_LOCK:
        _TOKEN_CACHE.clear()


def _safe_oauth_error(value):
    normalized = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "oauth_error"))
    return normalized[:80] or "oauth_error"


def _persist_rotated_refresh_token(settings, refresh_token):
    if not refresh_token or refresh_token == settings["refreshToken"]:
        return
    target = settings["refreshTokenFile"]
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    temporary.write_text(refresh_token, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    os.chmod(target, 0o600)


def refresh_microsoft_access_token(settings=None):
    settings = settings or mail_settings()
    tenant = quote(settings["tenantId"], safe="")
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    body = urlencode({
        "client_id": settings["clientId"],
        "client_secret": settings["clientSecret"],
        "grant_type": "refresh_token",
        "refresh_token": settings["refreshToken"],
        "scope": settings["scope"],
    }).encode("utf-8")
    request = Request(
        token_url,
        data=body,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        oauth_error = f"http_{error.code}"
        try:
            payload = json.loads(error.read().decode("utf-8", errors="replace"))
            oauth_error = _safe_oauth_error(payload.get("error"))
        except (json.JSONDecodeError, OSError):
            pass
        LOGGER.error("Microsoft OAuth2 token refresh failed: %s", oauth_error)
        raise EmailDeliveryError(f"Microsoft OAuth2 令牌刷新失败（{oauth_error}）") from error
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        LOGGER.error("Microsoft OAuth2 token refresh failed: %s", type(error).__name__)
        raise EmailDeliveryError("Microsoft OAuth2 令牌服务连接失败") from error

    access_token = payload.get("access_token") or ""
    if not access_token:
        oauth_error = _safe_oauth_error(payload.get("error"))
        LOGGER.error("Microsoft OAuth2 token response missing access token: %s", oauth_error)
        raise EmailDeliveryError(f"Microsoft OAuth2 未返回访问令牌（{oauth_error}）")
    try:
        expires_in = max(60, int(payload.get("expires_in") or 3600))
    except (TypeError, ValueError):
        expires_in = 3600
    _persist_rotated_refresh_token(settings, payload.get("refresh_token") or "")
    return {"accessToken": access_token, "expiresIn": expires_in}


def get_microsoft_access_token(settings=None, force_refresh=False):
    settings = settings or mail_settings()
    cache_key = f"{settings['tenantId']}|{settings['clientId']}|{settings['fromEmail']}"
    with _TOKEN_LOCK:
        cached = _TOKEN_CACHE.get(cache_key)
        if not force_refresh and cached and cached["expiresAt"] > time.time() + 60:
            return cached["accessToken"]
        token = refresh_microsoft_access_token(settings)
        _TOKEN_CACHE[cache_key] = {
            "accessToken": token["accessToken"],
            "expiresAt": time.time() + token["expiresIn"],
        }
        return token["accessToken"]


def _connect_smtp(settings):
    if settings["security"] == "ssl":
        return smtplib.SMTP_SSL(
            settings["host"], settings["port"], timeout=20,
            context=ssl.create_default_context(),
        )
    client = smtplib.SMTP(settings["host"], settings["port"], timeout=20)
    client.ehlo()
    if settings["security"] == "starttls":
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
    return client


def _send_with_password_smtp(message, settings):
    with _connect_smtp(settings) as client:
        if settings["username"]:
            client.login(settings["username"], settings["password"])
        client.send_message(message)


def _xoauth2_value(username, access_token):
    raw = f"user={username}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _send_with_microsoft_oauth2(message, settings):
    last_error = None
    for attempt in range(2):
        access_token = get_microsoft_access_token(settings, force_refresh=attempt > 0)
        try:
            with _connect_smtp(settings) as client:
                code, _ = client.docmd("AUTH", "XOAUTH2 " + _xoauth2_value(settings["fromEmail"], access_token))
                if code != 235:
                    raise smtplib.SMTPAuthenticationError(code, b"XOAUTH2 authentication failed")
                client.send_message(message)
            return
        except smtplib.SMTPAuthenticationError as error:
            last_error = error
            if attempt == 0:
                continue
            break
    raise EmailDeliveryError("Microsoft SMTP XOAUTH2 身份验证失败，请重新授权或检查 SMTP AUTH 策略") from last_error


def sendEmail(recipient, subject, text_content, html_content=""):
    settings = mail_settings()
    if not settings["configured"]:
        missing = "、".join(settings["missing"]) if settings["missing"] else "MAIL_PROVIDER"
        raise EmailDeliveryError(f"邮箱发送配置不完整，缺少：{missing}")

    recipient = _clean_header(recipient)
    message = EmailMessage()
    message["Subject"] = _clean_header(subject)
    message["From"] = formataddr((settings["fromName"], settings["fromEmail"]))
    message["To"] = recipient
    message.set_content(text_content)
    if html_content:
        message.add_alternative(html_content, subtype="html")

    if settings["provider"] == "file":
        settings["outbox"].parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with settings["outbox"].open("ab") as outbox:
            outbox.write(message.as_bytes() + b"\n\n")
        os.chmod(settings["outbox"], 0o600)
        return {"mode": "file", "provider": "file"}

    try:
        if settings["provider"] == "microsoft_oauth2":
            _send_with_microsoft_oauth2(message, settings)
        else:
            _send_with_password_smtp(message, settings)
    except EmailDeliveryError:
        raise
    except (OSError, smtplib.SMTPException) as error:
        LOGGER.error("Email delivery failed via provider=%s: %s", settings["provider"], type(error).__name__)
        raise EmailDeliveryError("验证码邮件发送失败，请检查邮件服务配置和服务器策略") from error
    return {"mode": settings["provider"], "provider": settings["provider"]}


send_email = sendEmail
