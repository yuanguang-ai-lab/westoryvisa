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

    def test_home_merges_account_and_workspace_into_one_entry_card(self):
        home_html = (server.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        self.assertEqual(home_html.count('<article class="portal-step'), 2)
        self.assertEqual(home_html.count("登录 / 注册并进入工作台 →"), 1)
        self.assertNotIn("登录 / 注册账号</a>", home_html)
        self.assertIn('href="styles.css?', home_html)
        self.assertNotIn('href="/styles.css?', home_html)

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
