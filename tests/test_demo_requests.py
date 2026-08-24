import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


class DemoRequestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_paths = (server.DATA_DIR, server.UPLOAD_DIR, server.DB_PATH)
        root = Path(self.temporary.name)
        server.DATA_DIR = root / "data"
        server.UPLOAD_DIR = server.DATA_DIR / "uploads"
        server.DB_PATH = server.DATA_DIR / "test.sqlite3"
        server.init_db()

    def tearDown(self):
        server.DATA_DIR, server.UPLOAD_DIR, server.DB_PATH = self.original_paths
        self.temporary.cleanup()

    def payload(self, **overrides):
        payload = {
            "name": "林老师",
            "phone": "+86 138 0000 0000",
            "email": "lin@example.com",
            "message": "希望了解完整流程。",
            "sourcePath": "/landing-page",
            "privacyConsentVersion": server.DEMO_REQUEST_PRIVACY_VERSION,
            "website": "",
        }
        payload.update(overrides)
        return payload

    def test_request_is_saved_and_emailed_to_configured_recipient(self):
        with mock.patch.dict(
            os.environ, {"DEMO_REQUEST_RECIPIENT_EMAIL": "owner@example.com"}
        ), mock.patch.object(server, "sendEmail") as send_email:
            result = server.create_demo_request(self.payload())

        self.assertTrue(result["ok"])
        self.assertTrue(result["notificationSent"])
        self.assertEqual(send_email.call_args.args[0], "owner@example.com")
        self.assertIn("林老师", send_email.call_args.args[2])
        self.assertIn("希望了解完整流程", send_email.call_args.args[2])
        with server.connect() as connection:
            row = connection.execute(
                "SELECT * FROM demo_requests WHERE id = ?", (result["id"],)
            ).fetchone()
        self.assertEqual(row["email"], "lin@example.com")
        self.assertEqual(row["notification_status"], "sent")

    def test_rejects_invalid_or_unconsented_requests(self):
        for payload, message in (
            (self.payload(email="not-an-email"), "有效的邮箱"),
            (self.payload(phone="abc"), "有效的联系电话"),
            (self.payload(privacyConsentVersion=""), "隐私政策"),
            (self.payload(website="spam.example"), "无法提交"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    server.create_demo_request(payload)


if __name__ == "__main__":
    unittest.main()
