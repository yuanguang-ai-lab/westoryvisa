import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEGACY_TESTS = ROOT / "standalone-agent" / "tests"
for path in (ROOT, LEGACY_TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.browser_use_bridge import build_browser_workflow  # noqa: E402
from backend.country_execution import (  # noqa: E402
    build_country_plan,
    require_country_plan_ready,
)
from backend.gemini_v2_bridge import gemini_v2_manifest  # noqa: E402
from scripts.international_acceptance import synthetic_case  # noqa: E402
from test_playwright_service_continuous_e2e import (  # noqa: E402
    PAGE_1_URL,
    PAGE_2_URL,
    REVIEW_URL,
    page_one_html,
    review_html,
)
from visa_agent.adapters import PlaywrightBrowserDriver  # noqa: E402
from visa_agent.config import AgentConfig, ProviderConfig  # noqa: E402
from visa_agent.providers import ProviderNotConfigured  # noqa: E402
from visa_agent.service import AgentService, ServiceError  # noqa: E402
from visa_agent_v2.workflow import FastComputerUseAgent  # noqa: E402


class ModelMustNotRun:
    def __init__(self):
        self.calls = 0

    def propose_action(self, *_args):
        self.calls += 1
        raise AssertionError("International semantic controls must not call Gemini")

    def propose_actions(self, *_args):
        self.calls += 1
        raise AssertionError("International semantic controls must not call Gemini")


class RecordingBrowser(PlaywrightBrowserDriver):
    """Real Chromium text-control gate; native selects remain separately gated."""

    def __init__(self):
        super().__init__(ProviderConfig(
            provider="playwright",
            model="chromium-headless",
        ))
        self.executed_actions = []

    def execute(self, action):
        self.executed_actions.append({
            "kind": action.kind.value,
            "field_id": action.field_id,
            "target": action.target_hint,
        })
        return super().execute(action)


def page_two_html():
    return r"""
<!doctype html>
<html>
<head>
  <title>Personal Information 2</title>
  <style>
    body { margin: 0; font: 18px Arial, sans-serif; color: #15244a; }
    main { width: 850px; margin: 36px auto; }
    .field { margin: 28px 0; }
    label { display: block; margin-bottom: 7px; font-weight: 700; }
    input { width: 600px; height: 38px; font-size: 18px; }
    button { margin-top: 24px; padding: 12px 28px; font-size: 18px; }
  </style>
</head>
<body>
  <main>
    <h1>Personal Information 2</h1>
    <form onsubmit="return false">
      <input type="hidden" name="__VIEWSTATE" value="synthetic-viewstate-2">
      <div class="field">
        <label for="NationalityInput">Nationality</label>
        <input id="NationalityInput" name="APP_NATL" maxlength="100">
      </div>
      <div class="field">
        <label for="NationalIdInput">National Identification Number</label>
        <input id="NationalIdInput" name="NATIONAL_ID" maxlength="18">
      </div>
      <button id="next" type="button">Next: Review</button>
    </form>
  </main>
  <script>
    const stateKey = '__docflowInternationalPage2';
    const statsKey = '__docflowE2EStats';
    const reviewUrl = __REVIEW_URL__;
    const state = { nationality: '', nationalId: '' };
    const save = () => sessionStorage.setItem(stateKey, JSON.stringify(state));
    document.getElementById('NationalityInput').addEventListener(
      'input', event => { state.nationality = event.target.value; save(); }
    );
    document.getElementById('NationalIdInput').addEventListener(
      'input', event => { state.nationalId = event.target.value; save(); }
    );
    document.getElementById('next').addEventListener('click', () => {
      let stats = {};
      try { stats = JSON.parse(localStorage.getItem(statsKey) || '{}'); }
      catch (_) {}
      stats.page2NextCount = Number(stats.page2NextCount || 0) + 1;
      localStorage.setItem(statsKey, JSON.stringify(stats));
      window.location.href = reviewUrl;
    });
  </script>
</body>
</html>
""".replace("__REVIEW_URL__", json.dumps(REVIEW_URL))


def route_international_ceac(browser):
    def fulfill(route):
        url = route.request.url
        if "node=Personal1" in url:
            body = page_one_html()
        elif "node=Personal2" in url:
            body = page_two_html()
        elif "node=ReviewReview" in url:
            body = review_html()
        else:
            route.abort()
            return
        route.fulfill(status=200, content_type="text/html", body=body)

    browser._page.route("https://ceac.state.gov/**", fulfill)


def representative_manifest(country_code):
    case = synthetic_case(country_code, "B1/B2", 0)
    plan = build_browser_workflow(case)
    prepared, _metadata = build_country_plan(case, plan)
    require_country_plan_ready(prepared)
    fields, _decisions = gemini_v2_manifest(prepared)
    labels = (
        "Surnames [",
        "Given Names [",
        "Nationality [",
        "National Identification Number [",
    )
    selected = [
        item for item in fields
        if str(item.get("label") or "").startswith(labels)
    ]
    if len(selected) != 4:
        raise AssertionError(
            f"Expected four representative fields for {country_code}, "
            f"got {len(selected)}"
        )
    expected = {}
    for item in selected:
        label = item["label"]
        if label.startswith("Surnames ["):
            expected["surname"] = item["value"]
        elif label.startswith("Given Names ["):
            expected["given"] = item["value"]
        elif label.startswith("Nationality ["):
            expected["nationality"] = item["value"]
        else:
            expected["nationalId"] = item["value"]
    return selected, expected


class V2InternationalPlaywrightAcceptanceTests(unittest.TestCase):
    def test_three_country_manifests_fill_browser_and_stop_at_review(self):
        for country_code in ("MX", "BR", "IN"):
            with self.subTest(country=country_code):
                self._run_country(country_code)

    def _run_country(self, country_code):
        fields, expected = representative_manifest(country_code)
        required = [item["id"] for item in fields]
        startup_errors = []

        with tempfile.TemporaryDirectory() as directory:
            model = ModelMustNotRun()

            def runtime_factory(job):
                browser = RecordingBrowser()
                browser.set_execution_mode("visual")
                try:
                    browser.start("about:blank")
                    route_international_ceac(browser)
                    browser._page.goto(
                        job.start_url,
                        wait_until="domcontentloaded",
                        timeout=browser.NAVIGATION_TIMEOUT_MS,
                    )
                except Exception as error:
                    startup_errors.append(error)
                    browser.close()
                    raise ProviderNotConfigured(
                        "Playwright Chromium is unavailable for international acceptance"
                    ) from error
                return FastComputerUseAgent(
                    model,
                    browser,
                    max_steps=30,
                    execution_mode="hybrid",
                )

            service = AgentService(
                AgentConfig(data_dir=Path(directory) / "checkpoints"),
                runtime_factory=runtime_factory,
            )
            try:
                created = service.create_job({
                    "startUrl": PAGE_1_URL,
                    "requiredFieldIds": required,
                    "fields": fields,
                    "autoNext": True,
                })
                reviewed = service.review_job(created["id"], {
                    "actor": f"international-{country_code.lower()}-acceptance",
                    "decisions": [
                        {
                            "fieldId": item["id"],
                            "approved": True,
                            "value": item["value"],
                        }
                        for item in fields
                    ],
                })
                try:
                    result = service.start_job(reviewed["id"])
                except ServiceError:
                    if startup_errors:
                        self.skipTest(
                            "Playwright/Chromium unavailable: "
                            f"{startup_errors[-1]}"
                        )
                    raise

                self.assertEqual(result["state"], "review_required")
                self.assertEqual(set(result["completed_field_ids"]), set(required))
                self.assertEqual(model.calls, 0)

                with service._runtime_lock:
                    worker = service._runtimes[reviewed["id"]]

                def inspect(runtime):
                    browser = runtime.browser
                    return browser._page.evaluate(
                        """() => ({
                          url: window.location.href,
                          page1: JSON.parse(sessionStorage.getItem(
                            '__docflowE2EPage1State') || '{}'),
                          page2: JSON.parse(sessionStorage.getItem(
                            '__docflowInternationalPage2') || '{}'),
                          stats: JSON.parse(localStorage.getItem(
                            '__docflowE2EStats') || '{}')
                        })"""
                    )

                snapshot = worker.call(inspect, timeout=10)
                self.assertEqual(snapshot["url"], REVIEW_URL)
                self.assertEqual(snapshot["page1"]["surname"], expected["surname"])
                self.assertEqual(snapshot["page1"]["given"], expected["given"])
                self.assertEqual(
                    snapshot["page2"]["nationality"], expected["nationality"]
                )
                self.assertEqual(
                    snapshot["page2"]["nationalId"], expected["nationalId"]
                )
                self.assertEqual(snapshot["stats"].get("page1NextCount"), 1)
                self.assertEqual(snapshot["stats"].get("page2NextCount"), 1)
                self.assertEqual(snapshot["stats"].get("finalActionCount", 0), 0)
            finally:
                service.shutdown(timeout=15)


if __name__ == "__main__":
    unittest.main()
