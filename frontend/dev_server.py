"""Standalone static server for the WestoryVisa frontend."""

import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


FRONTEND_DIR = Path(__file__).resolve().parent
STATIC_ALIASES = {
    "": "product.html",
    "/": "product.html",
    "/index.html": "product.html",
    "/workspace": "workspace.html",
    "/workspace/": "workspace.html",
    "/membership": "membership.html",
    "/membership/": "membership.html",
    "/admin/payments": "admin-payments.html",
    "/admin/payments/": "admin-payments.html",
    "/landing-page": "product.html",
    "/landing-page/": "product.html",
    "/landingpage": "product.html",
    "/landingpage/": "product.html",
    "/product": "product.html",
    "/product/": "product.html",
    "/analytics": "analytics.html",
    "/analytics/": "analytics.html",
    "/promo/promo-video.html": "promo/promo-video.html",
    "/promo/promo-video.css": "promo/promo-video.css",
}


def normalize_api_base_url(value):
    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        return "/api"
    parsed = urlparse(normalized)
    if normalized.startswith("/") or (
        parsed.scheme in {"http", "https"} and parsed.netloc
    ):
        return normalized
    raise ValueError("API base URL must be an absolute http(s) URL or an absolute path")


def runtime_config_source(api_base_url):
    encoded = json.dumps(normalize_api_base_url(api_base_url), ensure_ascii=False)
    return (
        "(function (global) {\n"
        '  "use strict";\n'
        f"  global.DOCFLOW_CONFIG = Object.freeze({{ apiBaseUrl: {encoded} }});\n"
        "})(window);\n"
    ).encode("utf-8")


class FrontendHandler(BaseHTTPRequestHandler):
    server_version = "WestoryVisaFrontend/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/runtime-config.js":
            return self.send_content(
                runtime_config_source(self.server.api_base_url),
                "application/javascript; charset=utf-8",
            )
        relative_path = STATIC_ALIASES.get(
            parsed.path, unquote(parsed.path.lstrip("/"))
        )
        allowed_nested_assets = {"promo/promo-video.html", "promo/promo-video.css"}
        if not relative_path or (
            ("/" in relative_path or "\\" in relative_path)
            and relative_path not in allowed_nested_assets
        ):
            return self.send_not_found()
        target = FRONTEND_DIR / relative_path
        if not target.is_file():
            return self.send_not_found()
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return self.send_content(target.read_bytes(), mime)

    def send_content(self, content, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def send_not_found(self):
        return self.send_content(
            b'{"error":"File not found"}',
            "application/json; charset=utf-8",
            status=404,
        )


def create_server(host, port, api_base_url):
    server = ThreadingHTTPServer((host, port), FrontendHandler)
    server.api_base_url = normalize_api_base_url(api_base_url)
    return server


def main():
    host = os.environ.get("DOCFLOW_FRONTEND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("DOCFLOW_FRONTEND_PORT", "4175")
    )
    api_base_url = (
        sys.argv[2]
        if len(sys.argv) > 2
        else os.environ.get("DOCFLOW_API_BASE_URL", "http://127.0.0.1:4176/api")
    )
    server = create_server(host, port, api_base_url)
    print(f"WestoryVisa frontend running at http://{host}:{port}")
    print(f"Backend API: {server.api_base_url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
