import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentBoundaryTests(unittest.TestCase):
    def test_backend_container_does_not_copy_frontend(self):
        dockerfile = (ROOT / "deploy" / "backend.Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn("COPY backend /app/backend", dockerfile)
        self.assertNotIn("COPY frontend", dockerfile)
        self.assertIn('backend.main", "4176"', dockerfile)

    def test_frontend_container_does_not_copy_backend(self):
        dockerfile = (ROOT / "deploy" / "frontend.Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn("COPY frontend", dockerfile)
        self.assertNotIn("COPY backend", dockerfile)

    def test_nginx_is_the_only_public_service_and_proxies_api(self):
        compose = (ROOT / "deploy" / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
        nginx = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
        self.assertIn('${DOCFLOW_PUBLIC_PORT:-8080}:80', compose)
        self.assertIn('expose:\n      - "4176"', compose)
        self.assertIn("location /api/", nginx)
        self.assertIn("proxy_pass http://backend:4176", nginx)
        self.assertIn("location = /backend-status", nginx)
        self.assertIn("location = /workspace", nginx)
        self.assertIn("location = /membership", nginx)
        self.assertIn("location = /admin/payments", nginx)
        self.assertIn("alias /usr/share/nginx/html/admin-payments.html", nginx)
        self.assertIn("return 308 /admin/payments", nginx)
        self.assertIn("location = /landing-page", nginx)

    def test_backend_application_has_no_static_frontend_dependency(self):
        source = (ROOT / "backend" / "application.py").read_text(encoding="utf-8")
        self.assertNotIn("FRONTEND_DIR", source)
        self.assertNotIn("serve_static", source)


if __name__ == "__main__":
    unittest.main()
