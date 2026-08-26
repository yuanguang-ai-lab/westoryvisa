import hashlib
import hmac
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import server


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_paths = (server.DATA_DIR, server.UPLOAD_DIR, server.DB_PATH)
        root = Path(self.temporary.name)
        server.DATA_DIR = root / "data"
        server.UPLOAD_DIR = server.DATA_DIR / "uploads"
        server.DB_PATH = server.DATA_DIR / "billing.sqlite3"
        server.init_db()
        self.user = {
            "id": "user-billing-test",
            "organizationId": "org-billing-test",
            "email": "billing@example.com",
        }
        stamped = "2020-01-01T00:00:00+00:00"
        with server.connect() as connection:
            connection.execute(
                "INSERT INTO organizations (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (self.user["organizationId"], "Billing Test", stamped, stamped),
            )
            connection.execute(
                """
                INSERT INTO users (
                  id, organization_id, name, email, password_iterations,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.user["id"], self.user["organizationId"], "Tester",
                    self.user["email"], server.PASSWORD_ITERATIONS, stamped, stamped,
                ),
            )
        self.environment = {
            "PAYMENT_PROVIDER": "stripe",
            "STRIPE_SECRET_KEY": "sk_test_example",
            "STRIPE_WEBHOOK_SECRET": "whsec_example",
            "BILLING_PUBLIC_BASE_URL": "https://westoryvisa.com",
            "MERCHANT_LEGAL_NAME_EN": "Westory Visa Test Limited",
            "MERCHANT_BUSINESS_REGISTRATION_NUMBER": "12345678",
            "MERCHANT_REGISTERED_ADDRESS": "Hong Kong",
            "MERCHANT_SUPPORT_EMAIL": "support@westoryvisa.com",
        }

    def tearDown(self):
        server.DATA_DIR, server.UPLOAD_DIR, server.DB_PATH = self.original_paths
        self.temporary.cleanup()

    def create_order(self):
        checkout = {
            "id": "cs_test_checkout_1",
            "url": "https://checkout.stripe.com/test/session",
        }
        with mock.patch.dict(os.environ, self.environment, clear=False), mock.patch.object(
            server, "stripe_request", return_value=checkout
        ) as gateway:
            order = server.create_checkout_order(
                {
                    "productId": "membership-monthly",
                    "legalAcceptance": {
                        "accepted": True,
                        "termsVersion": server.MERCHANT_TERMS_VERSION,
                    },
                },
                self.user,
            )
        self.assertEqual(order["status"], "pending")
        self.assertEqual(order["amount"], 19900)
        self.assertEqual(order["checkoutUrl"], checkout["url"])
        fields = gateway.call_args.args[2]
        self.assertEqual(fields["metadata[order_id]"], order["id"])
        self.assertEqual(fields["line_items[0][price_data][unit_amount]"], "19900")
        return order

    def signed_event(self, event):
        raw = json.dumps(event, separators=(",", ":")).encode("utf-8")
        timestamp = int(time.time())
        signature = hmac.new(
            self.environment["STRIPE_WEBHOOK_SECRET"].encode("utf-8"),
            f"{timestamp}.".encode("utf-8") + raw,
            hashlib.sha256,
        ).hexdigest()
        return raw, f"t={timestamp},v1={signature}"

    def authenticated_request(self, method, path, token, body=None):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", httpd.server_port, timeout=5
            )
            headers = {"Cookie": f"{server.AUTH_COOKIE}={token}"}
            encoded = None
            if body is not None:
                encoded = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(encoded))
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            return response.status, payload
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def pay_order(self, order):
        event = {
            "id": "evt_checkout_completed_1",
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": order["providerCheckoutId"],
                "client_reference_id": order["id"],
                "metadata": {"order_id": order["id"]},
                "payment_intent": "pi_test_paid_1",
                "payment_status": "paid",
                "amount_total": order["amount"],
                "currency": order["currency"],
            }},
        }
        raw, signature = self.signed_event(event)
        with mock.patch.dict(os.environ, self.environment, clear=False):
            result = server.process_stripe_webhook(raw, signature)
            duplicate = server.process_stripe_webhook(raw, signature)
        self.assertFalse(result["duplicate"])
        self.assertTrue(duplicate["duplicate"])

    def test_schema_seeds_products_and_requires_merchant_configuration(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            summary = server.billing_summary(self.user)
        self.assertFalse(summary["gateway"]["configured"])
        self.assertEqual(summary["gateway"]["provider"], "pending_selection")
        self.assertIn("公司法定名称", summary["gateway"]["message"])
        self.assertNotIn("STRIPE", summary["gateway"]["message"])
        self.assertEqual(
            [item["id"] for item in summary["products"]],
            ["membership-monthly", "membership-yearly"],
        )
        with self.assertRaises(server.BillingConfigurationError):
            server.create_checkout_order(
                {
                    "productId": "membership-monthly",
                    "legalAcceptance": {
                        "accepted": True,
                        "termsVersion": server.MERCHANT_TERMS_VERSION,
                    },
                },
                self.user,
            )
        with server.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS count FROM billing_orders").fetchone()["count"],
                0,
            )

    def test_checkout_requires_current_legal_acceptance(self):
        with mock.patch.dict(os.environ, self.environment, clear=False):
            with self.assertRaisesRegex(ValueError, "阅读并同意"):
                server.create_checkout_order(
                    {"productId": "membership-monthly"}, self.user
                )
            with self.assertRaisesRegex(ValueError, "条款已更新"):
                server.create_checkout_order(
                    {
                        "productId": "membership-monthly",
                        "legalAcceptance": {
                            "accepted": True,
                            "termsVersion": "outdated",
                        },
                    },
                    self.user,
                )

    def test_checkout_webhook_activates_membership_once(self):
        order = self.create_order()
        self.pay_order(order)
        summary = server.billing_summary(self.user)
        self.assertEqual(summary["orders"][0]["status"], "paid")
        self.assertTrue(summary["membership"]["active"])
        with server.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS count FROM payment_transactions").fetchone()["count"],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS count FROM billing_webhook_events").fetchone()["count"],
                1,
            )

    def test_webhook_rejects_wrong_amount_and_signature(self):
        order = self.create_order()
        event = {
            "id": "evt_bad_amount",
            "type": "checkout.session.completed",
            "data": {"object": {
                "client_reference_id": order["id"],
                "metadata": {"order_id": order["id"]},
                "payment_intent": "pi_bad",
                "payment_status": "paid",
                "amount_total": 1,
                "currency": "cny",
            }},
        }
        raw, signature = self.signed_event(event)
        with mock.patch.dict(os.environ, self.environment, clear=False):
            with self.assertRaisesRegex(ValueError, "金额"):
                server.process_stripe_webhook(raw, signature)
            with self.assertRaisesRegex(PermissionError, "验签"):
                server.process_stripe_webhook(raw, f"t={int(time.time())},v1=bad")
        self.assertEqual(server.order_status(order["id"], self.user)["status"], "pending")

    def test_paid_order_can_be_refunded_through_gateway(self):
        order = self.create_order()
        self.pay_order(order)
        stripe_refund = {"id": "re_test_1", "status": "succeeded"}
        with mock.patch.dict(os.environ, self.environment, clear=False), mock.patch.object(
            server, "stripe_request", return_value=stripe_refund
        ) as gateway:
            refund = server.create_refund(order["id"], {}, self.user)
        self.assertEqual(refund["status"], "succeeded")
        self.assertEqual(refund["amount"], order["amount"])
        self.assertEqual(gateway.call_args.args[1], "refunds")
        self.assertEqual(server.order_status(order["id"], self.user)["status"], "refunded")
        self.assertFalse(server.billing_summary(self.user)["membership"]["active"])

    def test_pending_order_can_be_reconciled_with_gateway(self):
        order = self.create_order()
        checkout = {
            "id": order["providerCheckoutId"],
            "payment_status": "paid",
            "status": "complete",
            "payment_intent": "pi_reconciled_1",
            "amount_total": order["amount"],
            "currency": order["currency"],
        }
        with mock.patch.dict(os.environ, self.environment, clear=False), mock.patch.object(
            server, "stripe_request", return_value=checkout
        ) as gateway:
            refreshed = server.refresh_checkout_order(order["id"], self.user)
        self.assertEqual(refreshed["status"], "paid")
        self.assertTrue(server.billing_summary(self.user)["membership"]["active"])
        self.assertEqual(gateway.call_args.args[0], "GET")

    def test_concurrent_full_refund_is_reserved_only_once(self):
        order = self.create_order()
        self.pay_order(order)
        gateway_started = threading.Event()
        allow_gateway = threading.Event()
        result = {}

        def delayed_refund(*_args, **_kwargs):
            gateway_started.set()
            allow_gateway.wait(timeout=3)
            return {"id": "re_concurrent_1", "status": "succeeded"}

        def first_refund():
            result.update(server.create_refund(order["id"], {}, self.user))

        with mock.patch.dict(os.environ, self.environment, clear=False), mock.patch.object(
            server, "stripe_request", side_effect=delayed_refund
        ):
            thread = threading.Thread(target=first_refund)
            thread.start()
            self.assertTrue(gateway_started.wait(timeout=2))
            with self.assertRaisesRegex(ValueError, "超出"):
                server.create_refund(order["id"], {}, self.user)
            allow_gateway.set()
            thread.join(timeout=3)
        self.assertEqual(result["status"], "succeeded")

    def test_workspace_api_requires_active_membership_but_billing_remains_available(self):
        token = server.create_auth_session(self.user["id"])
        status, payload = self.authenticated_request("GET", "/api/cases", token)
        self.assertEqual(status, 402)
        self.assertEqual(payload["code"], "membership_required")
        self.assertEqual(payload["redirect"], "/membership")

        billing_status, billing = self.authenticated_request(
            "GET", "/api/billing", token
        )
        self.assertEqual(billing_status, 200)
        self.assertEqual(len(billing["products"]), 2)

        order = self.create_order()
        self.pay_order(order)
        active_status, active_payload = self.authenticated_request(
            "GET", "/api/cases", token
        )
        self.assertEqual(active_status, 200)
        self.assertEqual(active_payload, {"cases": []})

        with server.connect() as connection:
            connection.execute(
                "UPDATE billing_subscriptions SET current_period_end = ? WHERE organization_id = ?",
                ("2020-01-01T00:00:00+00:00", self.user["organizationId"]),
            )
        expired_status, expired_payload = self.authenticated_request(
            "GET", "/api/cases", token
        )
        self.assertEqual(expired_status, 402)
        self.assertEqual(expired_payload["code"], "membership_required")

    def test_new_account_gets_three_case_trials_for_thirty_days(self):
        trial_user = {
            "id": "user-trial-test",
            "organizationId": "org-trial-test",
            "email": "trial@example.com",
            "identity": "Trial Test",
            "name": "Trial User",
            "accountKeyId": "trial-key",
        }
        stamped = server.now_iso()
        with server.connect() as connection:
            connection.execute(
                "INSERT INTO organizations (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (trial_user["organizationId"], "Trial Test", stamped, stamped),
            )
            connection.execute(
                """
                INSERT INTO users (
                  id, organization_id, name, email, password_iterations,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trial_user["id"], trial_user["organizationId"], "Trial User",
                    trial_user["email"], server.PASSWORD_ITERATIONS, stamped, stamped,
                ),
            )
        initial = server.billing_summary(trial_user)["trial"]
        self.assertTrue(initial["active"])
        self.assertEqual(initial["remaining"], 3)

        for index in range(3):
            server.upsert_case(
                {
                    "id": f"trial-case-{index}",
                    "applicantName": f"Trial Client {index}",
                    "visaType": "B1/B2",
                },
                trial_user,
                enforce_trial_limit=True,
            )
        self.assertEqual(server.billing_summary(trial_user)["trial"]["remaining"], 0)

        with self.assertRaisesRegex(PermissionError, "3 次免费试验"):
            server.upsert_case(
                {"id": "trial-case-four", "visaType": "B1/B2"},
                trial_user,
                enforce_trial_limit=True,
            )

        updated = server.upsert_case(
            {
                "id": "trial-case-2",
                "applicantName": "Trial Client 2",
                "visaType": "B1/B2",
                "currentStep": 6,
            },
            trial_user,
            enforce_trial_limit=True,
        )
        self.assertEqual(updated["id"], "trial-case-2")
        self.assertEqual(updated["caseMeta"]["status"], "已完成")

        with self.assertRaisesRegex(PermissionError, "不能更换客户姓名或签证类型"):
            server.upsert_case(
                {
                    "id": "trial-case-2",
                    "applicantName": "Another Client",
                    "visaType": "B1/B2",
                    "currentStep": 6,
                },
                trial_user,
            )

    def test_payment_admin_api_rejects_consultants_and_accepts_platform_admin(self):
        token = server.create_auth_session(self.user["id"])
        denied_status, denied = self.authenticated_request(
            "GET", "/api/admin/billing", token
        )
        self.assertEqual(denied_status, 403)
        self.assertEqual(denied["code"], "platform_admin_required")

        with server.connect() as connection:
            connection.execute(
                "UPDATE users SET is_platform_admin = 1 WHERE id = ?",
                (self.user["id"],),
            )
        allowed_status, allowed = self.authenticated_request(
            "GET", "/api/admin/billing", token
        )
        self.assertEqual(allowed_status, 200)
        self.assertEqual(allowed["totals"]["organizations"], 1)
        self.assertEqual(len(allowed["products"]), 2)


if __name__ == "__main__":
    unittest.main()
