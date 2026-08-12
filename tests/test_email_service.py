import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs

import email_service
import server
from email_service import EmailDeliveryError, mail_settings, refresh_microsoft_access_token, sendEmail
from env_config import load_env_file, update_env_file


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


class EmailProviderTests(unittest.TestCase):
    def tearDown(self):
        email_service.reset_token_cache()

    def test_password_smtp_configuration_and_send(self):
        environment = {
            "MAIL_PROVIDER": "smtp",
            "MAIL_FROM": "noreply@example.com",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_SECURE": "false",
            "SMTP_USERNAME": "smtp-user",
            "SMTP_PASSWORD": "smtp-app-password",
        }
        client = mock.MagicMock()
        connection = mock.MagicMock()
        connection.__enter__.return_value = client
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "email_service._connect_smtp", return_value=connection
        ):
            result = sendEmail("user@example.com", "Test", "Body")
            settings = mail_settings()
        self.assertEqual(result["provider"], "smtp")
        self.assertEqual(settings["security"], "starttls")
        client.login.assert_called_once_with("smtp-user", "smtp-app-password")
        client.send_message.assert_called_once()

    def test_microsoft_oauth2_configuration_uses_xoauth2(self):
        environment = {
            "MAIL_PROVIDER": "microsoft_oauth2",
            "MAIL_FROM": "sender@outlook.com",
            "MICROSOFT_TENANT_ID": "consumers",
            "MICROSOFT_CLIENT_ID": "client-id",
            "MICROSOFT_CLIENT_SECRET": "client-secret",
            "MICROSOFT_REFRESH_TOKEN": "refresh-token",
            "MICROSOFT_REFRESH_TOKEN_FILE": "/missing/token/file",
            "SMTP_HOST": "smtp-mail.outlook.com",
            "SMTP_PORT": "587",
            "SMTP_SECURE": "false",
        }
        client = mock.MagicMock()
        client.docmd.return_value = (235, b"2.7.0 Authentication successful")
        connection = mock.MagicMock()
        connection.__enter__.return_value = client
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "email_service._connect_smtp", return_value=connection
        ), mock.patch("email_service.get_microsoft_access_token", return_value="access-token"):
            result = sendEmail("user@example.com", "Test", "Body")
            settings = mail_settings()
        self.assertEqual(result["provider"], "microsoft_oauth2")
        self.assertEqual(settings["security"], "starttls")
        auth_args = client.docmd.call_args.args
        self.assertEqual(auth_args[0], "AUTH")
        self.assertTrue(auth_args[1].startswith("XOAUTH2 "))
        self.assertNotIn("access-token", auth_args[1])
        client.login.assert_not_called()
        client.send_message.assert_called_once()

    def test_refresh_token_fetches_short_lived_access_token(self):
        environment = {
            "MAIL_PROVIDER": "microsoft_oauth2",
            "MAIL_FROM": "sender@outlook.com",
            "MICROSOFT_TENANT_ID": "consumers",
            "MICROSOFT_CLIENT_ID": "client-id",
            "MICROSOFT_CLIENT_SECRET": "client-secret",
            "MICROSOFT_REFRESH_TOKEN": "refresh-token",
            "MICROSOFT_REFRESH_TOKEN_FILE": "/missing/token/file",
            "SMTP_HOST": "smtp-mail.outlook.com",
            "SMTP_PORT": "587",
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "email_service.urlopen",
            return_value=FakeResponse({"access_token": "new-access-token", "expires_in": 3600}),
        ) as urlopen_mock:
            token = refresh_microsoft_access_token()
        self.assertEqual(token["accessToken"], "new-access-token")
        request = urlopen_mock.call_args.args[0]
        body = parse_qs(request.data.decode("utf-8"))
        self.assertEqual(body["grant_type"], ["refresh_token"])
        self.assertEqual(body["refresh_token"], ["refresh-token"])
        self.assertIn("SMTP.Send", body["scope"][0])

    def test_missing_configuration_is_explicit(self):
        with mock.patch.dict(os.environ, {"MAIL_PROVIDER": "microsoft_oauth2"}, clear=True):
            with self.assertRaisesRegex(EmailDeliveryError, "MICROSOFT_CLIENT_ID"):
                sendEmail("user@example.com", "Test", "Body")

    def test_smtp_failure_raises_delivery_error(self):
        environment = {
            "MAIL_PROVIDER": "smtp",
            "MAIL_FROM": "noreply@example.com",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_SECURE": "false",
            "SMTP_USERNAME": "smtp-user",
            "SMTP_PASSWORD": "smtp-app-password",
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "email_service._connect_smtp", side_effect=OSError("network unavailable")
        ):
            with self.assertRaisesRegex(EmailDeliveryError, "发送失败"):
                sendEmail("user@example.com", "Test", "Body")

    def test_env_update_preserves_unrelated_values_and_special_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("export DATABASE_URL='postgres://local/db'\n", encoding="utf-8")
            special_secret = "value with spaces $quotes'and#symbols"
            update_env_file(
                {"MAIL_PROVIDER": "smtp", "SMTP_PASSWORD": special_secret},
                path=path,
            )
            update_env_file({"MAIL_FROM": "noreply@example.com"}, path=path)
            values = load_env_file(path)
        self.assertEqual(values["DATABASE_URL"], "postgres://local/db")
        self.assertEqual(values["SMTP_PASSWORD"], special_secret)
        self.assertEqual(values["MAIL_FROM"], "noreply@example.com")


class VerificationDeliveryStateTests(unittest.TestCase):
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

    def test_failed_delivery_is_never_marked_sent(self):
        service = {"configured": True, "provider": "smtp", "message": "普通 SMTP 已配置"}
        with mock.patch.object(server, "registration_verification_mode", return_value="email"), mock.patch.object(
            server, "mail_service_status", return_value=service
        ), mock.patch.object(server, "sendEmail", side_effect=EmailDeliveryError("mock failure")):
            with self.assertRaises(EmailDeliveryError):
                server.request_email_verification({"email": "failed@example.com"})
        with server.connect() as connection:
            row = connection.execute(
                "SELECT send_status, failure_reason, consumed_at FROM email_verifications WHERE email = ?",
                ("failed@example.com",),
            ).fetchone()
        self.assertEqual(row["send_status"], "failed")
        self.assertEqual(row["failure_reason"], "delivery_failed")
        self.assertIsNone(row["consumed_at"])
        with self.assertRaisesRegex(ValueError, "先获取"):
            server.verify_and_consume_email_code("failed@example.com", "123456")

    def test_concurrent_request_cannot_bypass_resend_limit(self):
        service = {"configured": True, "provider": "smtp", "message": "普通 SMTP 已配置"}
        sending_started = threading.Event()
        allow_send_to_finish = threading.Event()
        first_result = {}

        def delayed_send(*args):
            sending_started.set()
            allow_send_to_finish.wait(timeout=3)
            return {"mode": "smtp", "provider": "smtp"}

        def first_request():
            first_result.update(server.request_email_verification({"email": "race@example.com"}))

        with mock.patch.object(server, "registration_verification_mode", return_value="email"), mock.patch.object(
            server, "mail_service_status", return_value=service
        ), mock.patch.object(server, "sendEmail", side_effect=delayed_send):
            thread = threading.Thread(target=first_request)
            thread.start()
            self.assertTrue(sending_started.wait(timeout=2))
            with self.assertRaises(server.EmailRateLimitError):
                server.request_email_verification({"email": "race@example.com"})
            allow_send_to_finish.set()
            thread.join(timeout=3)
        self.assertTrue(first_result["ok"])
        with server.connect() as connection:
            rows = connection.execute(
                "SELECT send_status FROM email_verifications WHERE email = ?",
                ("race@example.com",),
            ).fetchall()
        self.assertEqual([row["send_status"] for row in rows], ["sent"])

    def test_registered_email_receives_same_generic_response_shape(self):
        service = {"configured": True, "provider": "smtp", "message": "普通 SMTP 已配置"}
        stamped = server.now_iso()
        with server.connect() as connection:
            connection.execute(
                """
                INSERT INTO users (id, name, email, password_iterations, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("user-existing", "Existing", "existing@example.com", 240000, stamped, stamped),
            )
        delivery = {"mode": "smtp", "provider": "smtp"}
        with mock.patch.object(server, "registration_verification_mode", return_value="email"), mock.patch.object(
            server, "mail_service_status", return_value=service
        ), mock.patch.object(server, "sendEmail", return_value=delivery) as send_mock:
            available = server.request_email_verification({"email": "available@example.com"})
            registered = server.request_email_verification({"email": "existing@example.com"})
        self.assertEqual(set(available), set(registered))
        self.assertEqual(available["message"], registered["message"])
        self.assertEqual(send_mock.call_count, 1)

    def test_registration_succeeds_without_verification_code_when_disabled(self):
        with mock.patch.object(server, "registration_verification_mode", return_value="none"):
            user = server.register_user({
                "email": "no-verification@example.com",
                "password": "secure-pass-123",
                "organizationName": "Internal Test Team",
                "name": "Test User",
                "phone": "+8613900000000",
                "role": "copywriter",
            })
        with server.connect() as connection:
            stored_user = connection.execute(
                "SELECT email_verified_at FROM users WHERE email = ?",
                ("no-verification@example.com",),
            ).fetchone()
            verification_count = connection.execute(
                "SELECT COUNT(*) AS count FROM email_verifications WHERE email = ?",
                ("no-verification@example.com",),
            ).fetchone()["count"]
        self.assertFalse(user["emailVerified"])
        self.assertIsNone(stored_user["email_verified_at"])
        self.assertEqual(verification_count, 0)
