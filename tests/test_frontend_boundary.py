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

    def test_public_routes_keep_home_workspace_and_landing_page_separate(self):
        home_html = (server.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        landing_html = (server.FRONTEND_DIR / "product.html").read_text(encoding="utf-8")
        self.assertIn('href="/workspace"', home_html)
        self.assertIn('href="/membership"', home_html)
        self.assertIn('href="/payment-console"', home_html)
        self.assertNotIn('href="/landing-page', home_html)
        self.assertIn('href="/workspace"', landing_html)

    def test_home_has_no_cross_page_header_navigation(self):
        home_html = (server.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="organization-header home-header"', home_html)
        self.assertNotIn('<nav class="organization-nav"', home_html)
        self.assertNotIn('aria-label="机构接入导航"', home_html)

    def test_home_merges_account_and_workspace_into_one_entry_card(self):
        home_html = (server.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        self.assertEqual(home_html.count('<article class="portal-step'), 2)
        self.assertEqual(home_html.count("登录 / 注册并选择会员 →"), 1)
        self.assertNotIn("登录 / 注册账号</a>", home_html)
        self.assertIn('href="styles.css?', home_html)
        self.assertNotIn('href="/styles.css?', home_html)
        self.assertIn('window.location.protocol !== "file:"', home_html)
        self.assertIn('"/workspace": "workspace.html"', home_html)

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
        console_html = (server.FRONTEND_DIR / "payment-console.html").read_text(
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
