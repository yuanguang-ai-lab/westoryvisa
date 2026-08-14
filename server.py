#!/usr/bin/env python3
"""Legacy combined frontend/backend server and backend compatibility alias."""

import mimetypes
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from backend import application as _application


ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
PUBLIC_FILES = {
    "workspace.html", "membership.html", "admin-payments.html",
    "styles.css", "runtime-config.js", "api-client.js", "app.js", "billing.js",
    "mockData.js", "product.html", "product.css", "product.js",
    "terms.html", "privacy.html", "refund-policy.html", "contact.html",
    "legal.css", "legal.js",
    "analytics.html", "analytics.css", "analytics.js",
    "screen-agent-target.html", "screen-agent-target.css",
    "screen-agent-target.js", "promo/promo-video.html", "promo/promo-video.css",
}


class CombinedHandler(_application.ApiHandler):
    """Legacy handler that adds static files around the standalone API."""

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return super().do_GET()
        return self.serve_static(parsed.path)

    def serve_static(self, path):
        aliases = {
            "": "product.html",
            "/": "product.html",
            "/index.html": "product.html",
            "/workspace": "workspace.html",
            "/workspace/": "workspace.html",
            "/membership": "membership.html",
            "/membership/": "membership.html",
            "/terms": "terms.html",
            "/terms/": "terms.html",
            "/privacy": "privacy.html",
            "/privacy/": "privacy.html",
            "/refund-policy": "refund-policy.html",
            "/refund-policy/": "refund-policy.html",
            "/contact": "contact.html",
            "/contact/": "contact.html",
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
        relative_path = aliases.get(path, unquote(path.lstrip("/")))
        if relative_path not in PUBLIC_FILES:
            return self.json_response({"error": "File not found"}, status=404)
        target = FRONTEND_DIR / relative_path
        if not target.is_file():
            return self.json_response({"error": "File not found"}, status=404)
        content = target.read_bytes()
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content)


def combined_main():
    preferred_port = int(sys.argv[1]) if len(sys.argv) > 1 else 4175
    host = _application.bind_host()
    httpd, port = _application.create_server(host, preferred_port, CombinedHandler)
    print(f"DocFlow legacy combined server running at http://{host}:{port}")
    print(f"SQLite database: {_application.DB_PATH}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    combined_main()
else:
    _application.FRONTEND_DIR = FRONTEND_DIR
    _application.PUBLIC_FILES = PUBLIC_FILES
    _application.CombinedHandler = CombinedHandler
    _application.combined_main = combined_main
    _application.__file__ = __file__
    sys.modules[__name__] = _application
