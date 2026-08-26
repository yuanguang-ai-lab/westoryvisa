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
        self.assertIn('href="/membership"', product_html)
        self.assertNotIn('href="/payment-console"', product_html)
        self.assertNotIn('href="/admin/payments"', product_html)

    def test_home_is_consultant_focused_and_contains_accessible_demo(self):
        product_html = (server.FRONTEND_DIR / "product.html").read_text(encoding="utf-8")
        product_js = (server.FRONTEND_DIR / "product.js").read_text(encoding="utf-8")
        self.assertEqual(product_html.count("<h1"), 1)
        self.assertIn("<h1>WestoryVisa</h1>", product_html)
        self.assertIn("为签证顾问打造", product_html)
        self.assertEqual(product_html.count("data-demo-chapter="), 10)
        self.assertIn("固定模拟数据", product_html)
        self.assertIn("不调用案件 API", product_html)
        self.assertIn("DEMO_STAGES = [", product_js)
        self.assertEqual(product_js.count('eyebrow: "'), 10)
        self.assertNotIn("WestoryVisa Agent", product_html)
        self.assertNotIn("/api/cases", product_js)
        self.assertNotIn("/cases", product_js)

    def test_home_includes_required_product_story_and_boundaries(self):
        product_html = (server.FRONTEND_DIR / "product.html").read_text(encoding="utf-8")
        for phrase in (
            "约 6 分钟",
            "RPA",
            "DeepSeek",
            "Gemini",
            "档案",
            "资料",
            "整理",
            "字段核查",
            "待确认项",
            "风险复核",
            "DS-160 初稿",
            "核查清单",
            "预约开户",
            "预约资料",
            "验证码、电子签名、敏感背景和最终提交保留人工控制",
        ):
            self.assertIn(phrase, product_html)
        self.assertNotIn('class="value-grid"', product_html)
        self.assertNotIn('class="comparison-grid"', product_html)

    def test_product_home_has_explicit_mobile_layout_rules(self):
        css = (server.FRONTEND_DIR / "product.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 720px)", css)
        self.assertIn(".workflow-tabs { display: grid", css)
        self.assertIn("overflow-x: auto", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("100svh", css)

    def test_home_demo_request_and_membership_trial_are_published(self):
        product_html = (server.FRONTEND_DIR / "product.html").read_text(encoding="utf-8")
        product_css = (server.FRONTEND_DIR / "product.css").read_text(encoding="utf-8")
        product_js = (server.FRONTEND_DIR / "product.js").read_text(encoding="utf-8")
        membership_html = (server.FRONTEND_DIR / "membership.html").read_text(encoding="utf-8")
        billing_js = (server.FRONTEND_DIR / "billing.js").read_text(encoding="utf-8")
        app_js = (server.FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("6 分钟填完 DS-160 表格", product_html)
        self.assertNotIn("查看完整操作台", product_html)
        self.assertGreaterEqual(product_html.count("data-demo-open"), 3)
        self.assertIn('id="demoRequestForm"', product_html)
        for field in ("name", "phone", "email", "message"):
            self.assertIn(f'name="{field}"', product_html)
        for removed_field in ("organizationName", "city", "teamSize"):
            self.assertNotIn(f'name="{removed_field}"', product_html)
        self.assertIn("/product/demo-requests", product_js)
        self.assertIn(".hero > .eyebrow", product_css)
        self.assertIn("font-size: clamp(22px, 2vw, 32px)", product_css)
        self.assertIn(".hero-actions .button", product_css)
        self.assertIn("font-size: clamp(17px", product_css)

        self.assertIn("注册后 30 天内可免费试验 3 次", membership_html)
        self.assertIn('id="freeTrialDetail"', membership_html)
        self.assertIn('id="membershipAccountAction"', membership_html)
        self.assertIn("billing.trial", billing_js)
        self.assertIn('request("/logout"', billing_js)
        self.assertIn("state.trial", app_js)

        mock_data = (server.FRONTEND_DIR / "mockData.js").read_text(encoding="utf-8")
        step_labels = mock_data.split("];", 1)[0]
        self.assertEqual(step_labels.count('"'), 14)
        self.assertNotIn("核查清单", step_labels)
        self.assertIn("report: 6", app_js)

    def test_home_membership_prices_reuse_current_product_configuration(self):
        product_html = (server.FRONTEND_DIR / "product.html").read_text(encoding="utf-8")
        products = {product["id"]: product for product in server.BILLING_PRODUCTS}
        self.assertIn('data-product-id="membership-monthly"', product_html)
        self.assertIn('data-product-id="membership-yearly"', product_html)
        self.assertIn(f'¥{products["membership-monthly"]["amount"] // 100}', product_html)
        yearly = f'{products["membership-yearly"]["amount"] // 100:,}'
        self.assertIn(f'¥{yearly}', product_html)
        self.assertIn('href="/membership"', product_html)

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

    def test_public_merchant_legal_pages_and_routes_are_published(self):
        expected = {
            "/terms": "terms.html",
            "/privacy": "privacy.html",
            "/refund-policy": "refund-policy.html",
            "/contact": "contact.html",
        }
        for route, filename in expected.items():
            self.assertEqual(dev_server.STATIC_ALIASES[route], filename)
            self.assertIn(filename, server.PUBLIC_FILES)
            html = (server.FRONTEND_DIR / filename).read_text(encoding="utf-8")
            self.assertIn('src="legal.js?', html)
            self.assertIn('src="api-client.js?', html)
            self.assertIn('data-merchant-summary', html)
        self.assertIn("legal.css", server.PUBLIC_FILES)
        self.assertIn("legal.js", server.PUBLIC_FILES)

    def test_checkout_requires_legal_acceptance_and_discloses_policies(self):
        membership_html = (server.FRONTEND_DIR / "membership.html").read_text(
            encoding="utf-8"
        )
        billing_js = (server.FRONTEND_DIR / "billing.js").read_text(encoding="utf-8")
        self.assertIn('id="legalAcceptance"', membership_html)
        self.assertIn('href="/terms"', membership_html)
        self.assertIn('href="/privacy"', membership_html)
        self.assertIn('href="/refund-policy"', membership_html)
        self.assertIn('legalAcceptance: {', billing_js)
        self.assertIn('termsVersion: global.WESTORY_LEGAL.termsVersion', billing_js)

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
        self.assertIn('<section class="pricing-section"', home_html)
        self.assertIn('href="/membership"', home_html)
        self.assertNotIn('class="billing-checkout"', home_html)
        self.assertIn("直接在本页选择月付或年付并完成购买", membership_html)
        self.assertIn("立即购买月度会员", membership_html)
        self.assertIn("立即购买年度会员", membership_html)
        self.assertIn('<nav class="organization-nav"', membership_html)
        for label in ("操作台", "会员中心", "个人中心", "帮助中心"):
            self.assertIn(label, membership_html)
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
        self.assertIn("renderWorkspacePortalHeader", app_js)
        self.assertIn('href="/membership#account"', app_js)

    def test_workspace_keeps_one_brand_link_to_landing_page(self):
        app_js = (server.FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn(
            '<a class="workspace-portal-brand" href="/" aria-label="返回 WestoryVisa 首页">',
            app_js,
        )
        self.assertNotIn('<button class="brand"', app_js)

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
