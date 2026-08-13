import hashlib
import hmac
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
        stamped = server.now_iso()
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
            "STRIPE_SECRET_KEY": "sk_test_example",
            "STRIPE_WEBHOOK_SECRET": "whsec_example",
            "BILLING_PUBLIC_BASE_URL": "https://westoryvisa.com",
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
                {"productId": "membership-monthly"}, self.user
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

    def test_schema_seeds_products_and_requires_real_gateway_configuration(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            summary = server.billing_summary(self.user)
        self.assertFalse(summary["gateway"]["configured"])
        self.assertEqual(
            [item["id"] for item in summary["products"]],
            ["membership-monthly", "membership-yearly"],
        )
        with self.assertRaises(server.BillingConfigurationError):
            server.create_checkout_order(
                {"productId": "membership-monthly"}, self.user
            )
        with server.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS count FROM billing_orders").fetchone()["count"],
                0,
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


if __name__ == "__main__":
    unittest.main()
