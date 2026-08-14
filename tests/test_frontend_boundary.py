import unittest
from pathlib import Path

import server
from frontend import dev_server


ROOT = Path(__file__).resolve().parents[1]


class FrontendBoundaryTests(unittest.TestCase):
    def test_public_frontend_files_live_in_dedicated_directory(self):
        self.assertEqual(server.FRONTEND_DIR, ROOT / "frontend")
        for relative_path in server.PUBLIC_FILES:
            self.assertTrue(
                (server.FRONTEND_DIR / relative_path).is_file(),
                f"missing frontend asset: {relative_path}",
            )
            self.assertFalse(
                (ROOT / relative_path).exists(),
                f"frontend asset leaked back into project root: {relative_path}",
            )

    def test_main_html_references_only_published_local_assets(self):
        workspace_html = (server.FRONTEND_DIR / "workspace.html").read_text(encoding="utf-8")
        for asset in (
            "styles.css",
            "mockData.js",
            "runtime-config.js",
            "api-client.js",
            "app.js",
        ):
            self.assertIn(asset, workspace_html)
            self.assertIn(asset, server.PUBLIC_FILES)

    def test_product_website_is_the_home_and_landing_page(self):
        product_html = (server.FRONTEND_DIR / "product.html").read_text(encoding="utf-8")
        self.assertFalse((server.FRONTEND_DIR / "index.html").exists())
        self.assertEqual(dev_server.STATIC_ALIASES["/"], "product.html")
        self.assertEqual(dev_server.STATIC_ALIASES["/landing-page"], "product.html")
        self.assertIn('href="/workspace"', product_html)
        self.assertNotIn('href="/membership"', product_html)
        self.assertNotIn('href="/payment-console"', product_html)
        self.assertNotIn('href="/admin/payments"', product_html)

    def test_home_promotes_product_evidence_instead_of_pricing(self):
        product_html = (server.FRONTEND_DIR / "product.html").read_text(encoding="utf-8")
        self.assertIn("约 8 分钟跑通", product_html)
        self.assertIn("DEEPSEEK API", product_html)
        self.assertIn("GEMINI API", product_html)
        self.assertIn("Human in control".upper(), product_html)
        self.assertNotIn('<section class="pricing-section"', product_html)
        self.assertNotIn("会员定价", product_html)
        self.assertNotIn("¥1,990", product_html)

    def test_product_home_has_explicit_mobile_layout_rules(self):
        css = (server.FRONTEND_DIR / "product.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 720px)", css)
        self.assertIn(".proof-grid { grid-template-columns: 1fr", css)
        self.assertIn(".model-stack { width: 100%", css)
        self.assertIn("100svh", css)

    def test_workspace_assets_work_from_http_and_local_files(self):
        workspace_html = (server.FRONTEND_DIR / "workspace.html").read_text(
            encoding="utf-8"
        )
        for asset in (
            "styles.css",
            "mockData.js",
            "runtime-config.js",
            "api-client.js",
            "app.js",
        ):
            self.assertIn(f'="{asset}', workspace_html)
            self.assertNotIn(f'="/{asset}', workspace_html)

    def test_billing_pages_use_real_api_workflow(self):
        membership_html = (server.FRONTEND_DIR / "membership.html").read_text(
            encoding="utf-8"
        )
        console_html = (server.FRONTEND_DIR / "admin-payments.html").read_text(
            encoding="utf-8"
        )
        billing_js = (server.FRONTEND_DIR / "billing.js").read_text(encoding="utf-8")
        for html in (membership_html, console_html):
            self.assertIn('src="runtime-config.js?', html)
            self.assertIn('src="api-client.js?', html)
            self.assertIn('src="billing.js?', html)
            self.assertIn('href="styles.css?', html)
            self.assertNotIn('href="/styles.css?', html)
        self.assertIn('request("/billing/checkout"', billing_js)
        self.assertIn('/refresh`', billing_js)
        self.assertIn('/refunds`', billing_js)
        self.assertIn("billing.js", server.PUBLIC_FILES)

    def test_payment_operations_are_separate_from_consultant_pages(self):
        home_html = (server.FRONTEND_DIR / "product.html").read_text(encoding="utf-8")
        membership_html = (server.FRONTEND_DIR / "membership.html").read_text(
            encoding="utf-8"
        )
        admin_html = (server.FRONTEND_DIR / "admin-payments.html").read_text(
            encoding="utf-8"
        )
        billing_js = (server.FRONTEND_DIR / "billing.js").read_text(encoding="utf-8")
        for public_html in (home_html, membership_html):
            self.assertNotIn("真实支付工程", public_html)
            self.assertNotIn("/admin/payments", public_html)
            self.assertNotIn("/payment-console", public_html)
        self.assertIn("仅限平台管理员", admin_html)
        self.assertIn('id="paymentConsoleContent" hidden', admin_html)
        self.assertIn('request("/admin/billing")', billing_js)
        self.assertIn("session.user.platformAdmin", billing_js)

    def test_membership_page_is_the_focused_purchase_page(self):
        home_html = (server.FRONTEND_DIR / "product.html").read_text(encoding="utf-8")
        membership_html = (server.FRONTEND_DIR / "membership.html").read_text(
            encoding="utf-8"
        )
        billing_js = (server.FRONTEND_DIR / "billing.js").read_text(encoding="utf-8")
        self.assertNotIn("立即购买会员", home_html)
        self.assertNotIn('<section class="pricing-section"', home_html)
        self.assertIn("直接在本页选择月付或年付并完成购买", membership_html)
        self.assertIn("立即购买月度会员", membership_html)
        self.assertIn("立即购买年度会员", membership_html)
        self.assertNotIn('<nav class="organization-nav"', membership_html)
        self.assertNotIn("托管收银台", billing_js)
        self.assertIn("支付通道接入中", billing_js)

    def test_login_and_workspace_enforce_membership_purchase(self):
        app_js = (server.FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
        api_client = (server.FRONTEND_DIR / "api-client.js").read_text(
            encoding="utf-8"
        )
        membership_html = (server.FRONTEND_DIR / "membership.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('window.location.replace(`/membership?auth=', app_js)
        self.assertIn('state.user && !state.membership?.active', app_js)
        self.assertIn('response.status === 402', api_client)
        self.assertIn('/membership?access=required', api_client)
        self.assertIn('id="membershipWorkspaceLink"', membership_html)

    def test_browser_requests_use_the_shared_api_client(self):
        api_client = (server.FRONTEND_DIR / "api-client.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('global.fetch.bind(global)', api_client)
        self.assertIn('value: Object.freeze({ apiBaseUrl, request })', api_client)
        self.assertIn('credentials: "include"', api_client)
        for script_name in ("app.js", "product.js", "analytics.js"):
            source = (server.FRONTEND_DIR / script_name).read_text(encoding="utf-8")
            self.assertNotIn("fetch(", source)
            self.assertIn("DocFlowApi.request(", source)

    def test_frontend_runtime_config_supports_separate_backend(self):
        source = dev_server.runtime_config_source(
            "https://api.example.test/api/"
        ).decode("utf-8")
        self.assertIn('"https://api.example.test/api"', source)
        self.assertEqual(dev_server.normalize_api_base_url("/api/"), "/api")
        with self.assertRaises(ValueError):
            dev_server.normalize_api_base_url("api.example.test/api")


if __name__ == "__main__":
    unittest.main()
