import unittest

import server
from backend.status_page import render_status_page


class BackendStatusPageTests(unittest.TestCase):
    def test_health_payload_keeps_existing_api_contract(self):
        payload = server.health_payload()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["auth"], "cookie-v1")
        self.assertEqual(payload["apiRevision"], 20)
        self.assertIn("emailVerification", payload)
        self.assertIn("translation", payload)
        self.assertIn("screenAgent", payload)

    def test_status_page_is_backend_owned_and_does_not_expose_secrets(self):
        page = render_status_page(
            {
                "ok": True,
                "auth": "cookie-v1",
                "apiVersion": "test-v20",
                "apiRevision": 20,
                "registrationVerification": {"mode": "none", "required": False},
                "emailVerification": {
                    "configured": False,
                    "message": "<not configured>",
                },
                "translation": {"provider": "auto", "ollamaFallback": True},
                "screenAgent": {"available": False, "message": "not installed"},
            },
            {"available": False, "message": "OCR offline"},
        )
        self.assertIn("DocFlow Backend", page)
        self.assertIn("Standalone API", page)
        self.assertIn("GET /api/health", page)
        self.assertIn("&lt;not configured&gt;", page)
        self.assertNotIn("password", page.lower())
        self.assertNotIn("api key", page.lower())


if __name__ == "__main__":
    unittest.main()
