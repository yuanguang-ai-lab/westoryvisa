#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

from env_config import ENV_PATH, load_env_file, update_env_file


CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 53682
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}/callback"
SMTP_SCOPE = "https://outlook.office.com/SMTP.Send offline_access"
ROOT = Path(__file__).resolve().parent


class OAuthSetupError(RuntimeError):
    pass


def base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def exchange_code(settings, code, verifier):
    tenant = quote(settings["MICROSOFT_TENANT_ID"], safe="")
    endpoint = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    body = urlencode({
        "client_id": settings["MICROSOFT_CLIENT_ID"],
        "client_secret": settings["MICROSOFT_CLIENT_SECRET"],
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
        "scope": SMTP_SCOPE,
    }).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        error_code = f"HTTP {error.code}"
        try:
            payload = json.loads(error.read().decode("utf-8", errors="replace"))
            error_code = payload.get("error") or error_code
        except (json.JSONDecodeError, OSError):
            pass
        raise OAuthSetupError(f"Microsoft 授权码交换失败：{error_code}") from error
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise OAuthSetupError("无法连接 Microsoft 令牌服务，请检查网络后重试") from error
    refresh_token = payload.get("refresh_token") or ""
    if not refresh_token:
        raise OAuthSetupError("Microsoft 未返回 Refresh Token，请确认授权范围包含 offline_access")
    return refresh_token


def main():
    file_values = load_env_file()
    settings = {key: os.environ.get(key) or file_values.get(key, "") for key in (
        "MICROSOFT_TENANT_ID", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MAIL_FROM"
    )}
    missing = [key for key, value in settings.items() if not value]
    if missing:
        raise OAuthSetupError("缺少配置：" + "、".join(missing) + "。请先运行“配置邮箱验证.command”。")

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    tenant = quote(settings["MICROSOFT_TENANT_ID"], safe="")
    authorize_url = (
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?"
        + urlencode({
            "client_id": settings["MICROSOFT_CLIENT_ID"],
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "response_mode": "query",
            "scope": SMTP_SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
            "login_hint": settings["MAIL_FROM"],
        })
    )
    result = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            query = parse_qs(parsed.query)
            if query.get("state", [""])[0] != state:
                result["error"] = "授权状态校验失败，请重新开始授权"
                status = 400
            elif query.get("error"):
                result["error"] = "Microsoft 未完成授权：" + query["error"][0]
                status = 400
            elif not query.get("code", [""])[0]:
                result["error"] = "授权回调中没有授权码"
                status = 400
            else:
                result["code"] = query["code"][0]
                status = 200
            body = (
                "<!doctype html><meta charset='utf-8'><title>DocFlow Microsoft 授权</title>"
                "<style>body{margin:0;background:#f6f5f2;color:#171717;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif}"
                ".wrap{max-width:560px;margin:12vh auto;padding:32px}.box{background:#fff;border:1px solid rgba(0,0,0,.08);border-radius:24px;padding:36px}"
                "h1{font-size:24px;font-weight:600}p{line-height:1.8;color:#5d5d58}</style>"
                "<div class='wrap'><div class='box'><h1>授权信息已收到</h1>"
                "<p>请返回终端等待保存结果。这个本地回调将在处理后自动关闭。</p></div></div>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format_string, *args):
            return

    try:
        server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), CallbackHandler)
    except OSError as error:
        raise OAuthSetupError(f"本地回调端口 {CALLBACK_PORT} 被占用，请关闭占用程序后重试") from error
    server.timeout = 300
    print(f"本地回调已启动：{REDIRECT_URI}")
    print("浏览器将打开 Microsoft 登录页；请使用发件邮箱登录并同意 SMTP.Send 权限。")
    webbrowser.open(authorize_url)
    server.handle_request()
    server.server_close()
    if not result:
        raise OAuthSetupError("等待 Microsoft 授权超时，请重新运行授权脚本")
    if result.get("error"):
        raise OAuthSetupError(result["error"])

    refresh_token = exchange_code(settings, result["code"], verifier)
    update_env_file({"MICROSOFT_REFRESH_TOKEN": refresh_token})
    rotated_token_file = ROOT / "data" / "microsoft_refresh_token"
    try:
        rotated_token_file.unlink()
    except FileNotFoundError:
        pass
    print(f"授权成功。Refresh Token 已安全写入 {ENV_PATH.name}，本地回调已关闭。")
    print("授权脚本不会启动常驻接口；完成后无需保留任何回调服务。")


if __name__ == "__main__":
    try:
        main()
    except OAuthSetupError as error:
        print(f"授权失败：{error}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\n已取消授权。", file=sys.stderr)
        raise SystemExit(1)

