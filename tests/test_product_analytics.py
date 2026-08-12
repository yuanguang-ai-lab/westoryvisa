import tempfile
import unittest
from pathlib import Path

import server


class ProductAnalyticsTests(unittest.TestCase):
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

    def event(self, event_id, event_type, **overrides):
        payload = {
            "sessionId": "visit_abcdefghijklmnop",
            "eventId": event_id,
            "eventType": event_type,
            "pagePath": "/product.html",
            "target": "",
            "section": "",
            "activeMs": 0,
            "referrerHost": "example.com",
            "utmSource": "wechat",
            "utmMedium": "social",
            "utmCampaign": "launch",
            "deviceType": "mobile",
            "locale": "zh-CN",
            "consentVersion": server.PRODUCT_ANALYTICS_CONSENT_VERSION,
        }
        payload.update(overrides)
        return payload

    def test_records_summary_and_deduplicates_client_event(self):
        self.assertTrue(server.record_product_analytics_event(
            self.event("event_0000000000000001", "page_view")
        )["accepted"])
        self.assertTrue(server.record_product_analytics_event(
            self.event("event_0000000000000002", "click", target="查看工作流程")
        )["accepted"])
        self.assertTrue(server.record_product_analytics_event(
            self.event("event_0000000000000003", "section_view", section="工作流程")
        )["accepted"])
        self.assertTrue(server.record_product_analytics_event(
            self.event("event_0000000000000004", "dwell", activeMs=6500)
        )["accepted"])
        self.assertTrue(server.record_product_analytics_event(
            self.event("event_0000000000000005", "wjx_open", target="预约产品演示")
        )["accepted"])

        duplicate = server.record_product_analytics_event(
            self.event("event_0000000000000004", "dwell", activeMs=6500)
        )
        self.assertFalse(duplicate["accepted"])

        summary = server.product_analytics_summary(30)
        self.assertEqual(summary["totals"]["visitors"], 1)
        self.assertEqual(summary["totals"]["pageViews"], 1)
        self.assertEqual(summary["totals"]["averageActiveMs"], 6500)
        self.assertEqual(summary["totals"]["wjxVisitors"], 1)
        self.assertEqual(summary["totals"]["wjxConversionRate"], 100.0)
        self.assertEqual(summary["clicks"], [{"target": "查看工作流程", "count": 1}])
        self.assertEqual(summary["sections"], [{"section_name": "工作流程", "visitors": 1}])
        self.assertEqual(summary["devices"], [{"device": "mobile", "visitors": 1}])

        details = server.product_analytics_session("visit_abcdefghijklmnop")
        self.assertEqual(details["session"]["active_ms"], 6500)
        self.assertEqual(len(details["events"]), 5)

    def test_rejects_event_without_current_consent_version(self):
        payload = self.event("event_0000000000000006", "page_view")
        payload["consentVersion"] = ""
        with self.assertRaisesRegex(ValueError, "授权无效"):
            server.record_product_analytics_event(payload)

        with server.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM product_analytics_events"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_wjx_setting_accepts_https_and_can_be_cleared(self):
        configured = server.update_product_settings(
            {"wjxSurveyUrl": "https://www.wjx.cn/vm/example.aspx?source=docflow"},
            {},
        )
        self.assertTrue(configured["wjxConfigured"])
        self.assertEqual(
            configured["wjxSurveyUrl"],
            "https://www.wjx.cn/vm/example.aspx?source=docflow",
        )
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            server.update_product_settings(
                {"wjxSurveyUrl": "http://www.wjx.cn/vm/example.aspx"}, {},
            )
        cleared = server.update_product_settings({"wjxSurveyUrl": ""}, {})
        self.assertFalse(cleared["wjxConfigured"])

    def test_schema_does_not_store_raw_ip_or_form_values(self):
        with server.connect() as connection:
            session_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(product_visitor_sessions)"
                ).fetchall()
            }
            event_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(product_analytics_events)"
                ).fetchall()
            }
        all_columns = session_columns | event_columns
        self.assertNotIn("ip", all_columns)
        self.assertNotIn("ip_address", all_columns)
        self.assertNotIn("form_values", all_columns)


if __name__ == "__main__":
    unittest.main()
