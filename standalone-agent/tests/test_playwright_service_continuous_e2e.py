import json
import math
import tempfile
import unittest
from pathlib import Path

from visa_agent.adapters import PlaywrightBrowserDriver
from visa_agent.config import AgentConfig, ProviderConfig
from visa_agent.models import ActionKind, ComputerAction
from visa_agent.providers import ProviderNotConfigured
from visa_agent.service import AgentService, ServiceError
from visa_agent.workflow import ComputerUseAgent


PAGE_1_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_personal.aspx?node=Personal1"
)
PAGE_2_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_personalcont.aspx?node=Personal2"
)
REVIEW_URL = (
    "https://ceac.state.gov/GenNIV/General/Review/"
    "ReviewReview.aspx?node=ReviewReview"
)

SURNAME = "personal.surname"
GIVEN_NAMES = "personal.givenNames"
BRANCH = "ceac.personal1.001.other_names.used"
CONDITIONAL = "ceac.personal1.002.other_names.surname"
NATIONALITY = "personal.nationality"
NATIONAL_ID = "personal.nationalId"


def browser_provider():
    return ProviderConfig(
        provider="playwright",
        model="chromium-headless",
    )


class ModelPlannedPlaywrightDriver(PlaywrightBrowserDriver):
    """Use real Playwright, while forcing fields through the visual model."""

    def __init__(self):
        super().__init__(browser_provider())
        self.pointer_paths = []
        self.executed_actions = []
        self.next_plans = []
        self.local_plan_calls = []
        self.refresh_checks = []

    def plan_fields(self, field_ids, field_labels=None, control_hints=None):
        del field_labels, control_hints
        field_ids = list(field_ids)
        self.local_plan_calls.append(field_ids)
        return [], field_ids

    def plan_choice_fields(
        self,
        field_ids,
        field_labels=None,
        control_hints=None,
    ):
        del field_labels, control_hints
        return [], list(field_ids)

    def plan_next(self):
        action = super().plan_next()
        if action is not None:
            self.next_plans.append({
                "target": action.target_hint,
                "scope": action.dispatch_receipt_scope,
            })
        return action

    def execute(self, action):
        self.executed_actions.append({
            "id": action.id,
            "kind": action.kind.value,
            "field_id": action.field_id,
            "target": action.target_hint,
            "receipt_required": action.dispatch_receipt_required,
            "receipt_scope": action.dispatch_receipt_scope,
        })
        return super().execute(action)

    def dynamic_refresh_detected(self, action=None):
        detected = super().dynamic_refresh_detected(action)
        self.refresh_checks.append({
            "field_id": getattr(action, "field_id", ""),
            "detected": detected,
            "evidence": dict(self._last_dynamic_refresh_evidence),
        })
        return detected

    def _move_visible_pointer(self, x, y, clicking=False):
        start = (self._cursor_x, self._cursor_y)
        end = (float(x), float(y))
        self.pointer_paths.append({
            "start": start,
            "end": end,
            "points": self._human_pointer_path(*start, *end),
            "clicking": bool(clicking),
        })
        return super()._move_visible_pointer(x, y, clicking=clicking)


class PageBatchFakeGemini:
    """Return screenshot-style coordinates, never approved field values."""

    COORDINATES = {
        SURNAME: (260, 165),
        GIVEN_NAMES: (260, 255),
        BRANCH: (190, 355),
        CONDITIONAL: (260, 445),
        NATIONALITY: (260, 190),
        NATIONAL_ID: (260, 310),
    }

    def __init__(self):
        self.calls = []

    def propose_actions(
        self,
        observation,
        available_field_ids,
        completed_field_ids,
        page_field_ids,
    ):
        del available_field_ids
        page_fields = set(page_field_ids)
        completed = set(completed_field_ids)
        visible_text = str(observation.visible_text or "")
        if "node=Personal1" in observation.url:
            ordered = [SURNAME, GIVEN_NAMES, BRANCH]
            if "Conditional Other Surnames" in visible_text:
                ordered.append(CONDITIONAL)
        elif "node=Personal2" in observation.url:
            ordered = [NATIONALITY, NATIONAL_ID]
        else:
            raise AssertionError(
                "Fake Gemini must never be called on Review/Sign"
            )
        pending = [
            field_id
            for field_id in ordered
            if field_id in page_fields and field_id not in completed
        ]
        actions = [
            ComputerAction(
                kind=(
                    ActionKind.SELECT
                    if field_id == BRANCH
                    else ActionKind.TYPE
                ),
                field_id=field_id,
                target_hint=field_id,
                reason="Fake Gemini visual page batch",
                coordinate_x=self.COORDINATES[field_id][0],
                coordinate_y=self.COORDINATES[field_id][1],
            )
            for field_id in pending
        ]
        self.calls.append({
            "url": observation.url,
            "completed": sorted(completed),
            "returned": [action.field_id for action in actions],
        })
        return actions


def page_one_html():
    return r"""
<!doctype html>
<html>
<head>
  <title>Personal Information 1</title>
  <style>
    body { margin: 0; font: 18px Arial, sans-serif; color: #15244a; }
    main { width: 850px; margin: 36px auto; }
    .field { margin: 20px 0; }
    label { display: block; margin-bottom: 7px; font-weight: 700; }
    input[type=text] { width: 600px; height: 38px; font-size: 18px; }
    .choice label { display: inline; margin-left: 8px; }
    button { margin-top: 24px; padding: 12px 28px; font-size: 18px; }
  </style>
</head>
<body>
  <main>
    <h1>Personal Information 1</h1>
    <p>ASP.NET-like dynamic branch test page</p>
    <form id="aspnet-form" onsubmit="return false">
      <input type="hidden" name="__VIEWSTATE" value="synthetic-viewstate">
      <div id="form-host"></div>
    </form>
  </main>
  <script>
    const stateKey = '__docflowE2EPage1State';
    const statsKey = '__docflowE2EStats';
    const movesKey = '__docflowE2EMoves';
    const nextUrl = __PAGE_2_URL__;
    const parse = (key, fallback) => {
      try { return JSON.parse(sessionStorage.getItem(key) || fallback); }
      catch (_) { return JSON.parse(fallback); }
    };
    const readStats = () => {
      try { return JSON.parse(localStorage.getItem(statsKey) || '{}'); }
      catch (_) { return {}; }
    };
    const bump = name => {
      const stats = readStats();
      stats[name] = Number(stats[name] || 0) + 1;
      localStorage.setItem(statsKey, JSON.stringify(stats));
    };
    let state = parse(stateKey, '{"surname":"","given":"","branch":false,"conditional":""}');
    const save = () => sessionStorage.setItem(stateKey, JSON.stringify(state));
    document.addEventListener('mousemove', event => {
      const moves = parse(movesKey, '[]');
      moves.push([Math.round(event.clientX), Math.round(event.clientY), 'page1']);
      sessionStorage.setItem(movesKey, JSON.stringify(moves.slice(-1000)));
    }, true);
    function render() {
      bump('renderCount');
      document.getElementById('form-host').innerHTML = `
        <div class="field">
          <label for="SurnameInput">Surname</label>
          <input id="SurnameInput" name="APP_SURNAME" type="text"
                 maxlength="100" value="${state.surname}">
        </div>
        <div class="field">
          <label for="GivenNamesInput">Given Names</label>
          <input id="GivenNamesInput" name="APP_GIVEN_NAME" type="text"
                 maxlength="100" value="${state.given}">
        </div>
        <div class="field choice">
          <span id="branch-prompt">Have you ever used other names?</span>
          <input id="OtherNamesToggle" name="OTHER_NAMES" type="checkbox"
                 ${state.branch ? 'checked' : ''}>
          <label for="OtherNamesToggle">Yes</label>
        </div>
        ${state.branch ? `
          <div class="field">
            <label for="OtherSurname">Conditional Other Surnames</label>
            <input id="OtherSurname" name="OTHER_SURNAME" type="text"
                   maxlength="100" value="${state.conditional}">
          </div>` : ''}
        <button id="next" type="button">Next: Personal 2</button>
      `;
      document.getElementById('SurnameInput').addEventListener('input', event => {
        state.surname = event.target.value; save();
      });
      document.getElementById('GivenNamesInput').addEventListener('input', event => {
        state.given = event.target.value; save();
      });
      document.getElementById('OtherNamesToggle').addEventListener('change', event => {
        state.branch = Boolean(event.target.checked);
        save();
        bump('postbackCount');
        render();
      });
      const conditional = document.getElementById('OtherSurname');
      if (conditional) conditional.addEventListener('input', event => {
        state.conditional = event.target.value; save();
      });
      document.getElementById('next').addEventListener('click', () => {
        bump('page1NextCount');
        window.location.href = nextUrl;
      });
    }
    render();
  </script>
</body>
</html>
""".replace("__PAGE_2_URL__", json.dumps(PAGE_2_URL))


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
    const statsKey = '__docflowE2EStats';
    const movesKey = '__docflowE2EMoves';
    const reviewUrl = __REVIEW_URL__;
    const parse = (key, fallback) => {
      try { return JSON.parse(sessionStorage.getItem(key) || fallback); }
      catch (_) { return JSON.parse(fallback); }
    };
    document.addEventListener('mousemove', event => {
      const moves = parse(movesKey, '[]');
      moves.push([Math.round(event.clientX), Math.round(event.clientY), 'page2']);
      sessionStorage.setItem(movesKey, JSON.stringify(moves.slice(-1000)));
    }, true);
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


def review_html():
    return r"""
<!doctype html>
<html>
<head><title>Review Application</title></head>
<body>
  <main>
    <h1>Review Application</h1>
    <p>Final human review boundary.</p>
    <button id="sign" type="button">Sign</button>
    <button id="submit" type="button">Submit Application</button>
  </main>
  <script>
    const statsKey = '__docflowE2EStats';
    const movesKey = '__docflowE2EMoves';
    const parse = (key, fallback) => {
      try { return JSON.parse(sessionStorage.getItem(key) || fallback); }
      catch (_) { return JSON.parse(fallback); }
    };
    document.addEventListener('mousemove', event => {
      const moves = parse(movesKey, '[]');
      moves.push([Math.round(event.clientX), Math.round(event.clientY), 'review']);
      sessionStorage.setItem(movesKey, JSON.stringify(moves.slice(-1000)));
    }, true);
    for (const id of ['sign', 'submit']) {
      document.getElementById(id).addEventListener('click', () => {
        let stats = {};
        try { stats = JSON.parse(localStorage.getItem(statsKey) || '{}'); }
        catch (_) {}
        stats.finalActionCount = Number(stats.finalActionCount || 0) + 1;
        localStorage.setItem(statsKey, JSON.stringify(stats));
      });
    }
  </script>
</body>
</html>
"""


def route_synthetic_ceac(driver):
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

    driver._page.route("https://ceac.state.gov/**", fulfill)


class PlaywrightServiceContinuousE2ETests(unittest.TestCase):
    @staticmethod
    def _field(field_id, value, label):
        return {
            "id": field_id,
            "value": value,
            "label": label,
            "confidence": 1.0,
            "risk_level": "high",
        }

    @staticmethod
    def _maximum_curve_deviation(trace):
        start_x, start_y = trace["start"]
        end_x, end_y = trace["end"]
        delta_x = end_x - start_x
        delta_y = end_y - start_y
        length = math.hypot(delta_x, delta_y)
        if length < 1:
            return 0.0
        return max(
            abs(
                delta_y * (point_x - start_x)
                - delta_x * (point_y - start_y)
            ) / length
            for point_x, point_y in trace["points"]
        )

    def test_dom_watch_uses_sync_page_evaluate_contract(self):
        class StrictSyncPage:
            def __init__(self):
                self.evaluate_calls = 0

            @staticmethod
            def wait_for_timeout(_milliseconds):
                return None

            def evaluate(self, _expression, _arg=None):
                self.evaluate_calls += 1
                return {
                    "generation": "document-one",
                    "fields": ["personal.surname"],
                    "removed": False,
                    "postback": False,
                }

        driver = PlaywrightBrowserDriver(browser_provider())
        page = StrictSyncPage()
        driver._page = page
        try:
            driver._begin_action_dom_watch()
            self.assertEqual(
                driver._action_field_tokens_before,
                {"personal.surname"},
            )
            self.assertFalse(driver.dynamic_refresh_detected())
            self.assertEqual(page.evaluate_calls, 2)

            driver._last_dynamic_refresh_evidence = {
                "postbackStarted": True,
                "generationChanged": False,
                "markedControlRemoved": False,
                "missingFieldTokens": [],
            }
            driver._action_dom_generation_before = "document-zero"
            driver._action_field_tokens_before = {"personal.surname"}
            driver._wait_for_watched_dom_replacement()
            self.assertEqual(page.evaluate_calls, 3)
        finally:
            driver._page = None
            driver._temporary.cleanup()

    def test_unavailable_dynamic_inspection_is_not_an_empty_dom(self):
        class UnavailablePage:
            @staticmethod
            def wait_for_timeout(_milliseconds):
                return None

            @staticmethod
            def evaluate(_expression, _arg=None):
                raise RuntimeError("transient page inspection failure")

        driver = PlaywrightBrowserDriver(browser_provider())
        driver._page = UnavailablePage()
        driver._action_watch_active = True
        driver._action_dom_generation_before = "document-one"
        driver._action_field_tokens_before = {"personal.surname"}
        try:
            self.assertFalse(driver.dynamic_refresh_detected())
            self.assertEqual(
                driver._last_dynamic_refresh_evidence,
                {
                    "generationChanged": False,
                    "markedControlRemoved": False,
                    "postbackStarted": False,
                    "missingFieldTokens": [],
                    "inspectionUnavailable": True,
                },
            )
        finally:
            driver._page = None
            driver._temporary.cleanup()

    def test_postback_settle_retries_an_unavailable_dom_inspection(self):
        class FlakySyncPage:
            def __init__(self):
                self.evaluate_calls = 0
                self.wait_calls = []

            def wait_for_timeout(self, milliseconds):
                self.wait_calls.append(milliseconds)

            def evaluate(self, _expression, _arg=None):
                self.evaluate_calls += 1
                if self.evaluate_calls == 1:
                    raise RuntimeError("transient postback inspection")
                return {
                    "generation": "document-two",
                    "fields": [],
                }

        driver = PlaywrightBrowserDriver(browser_provider())
        page = FlakySyncPage()
        driver._page = page
        driver._last_dynamic_refresh_evidence = {
            "postbackStarted": True,
            "generationChanged": False,
            "markedControlRemoved": False,
            "missingFieldTokens": [],
        }
        driver._action_dom_generation_before = "document-one"
        driver._action_field_tokens_before = {"personal.surname"}
        try:
            driver._wait_for_watched_dom_replacement()
            self.assertEqual(page.evaluate_calls, 2)
            self.assertIn(120, page.wait_calls)
        finally:
            driver._page = None
            driver._temporary.cleanup()

    def test_one_service_start_visually_fills_two_pages_and_stops_at_review(self):
        fields = [
            self._field(SURNAME, "XIA", "Surname"),
            self._field(GIVEN_NAMES, "YICHENG", "Given Names"),
            self._field(
                BRANCH,
                "true",
                "Have you ever used other names? "
                "[control=checkbox; refresh_after_change=true; "
                "control_hints=OtherNamesToggle; human-approved value=true]",
            ),
            self._field(
                CONDITIONAL,
                "CHEN",
                "Conditional Other Surnames "
                "[control=text; control_hints=OtherSurname]",
            ),
            self._field(NATIONALITY, "CHINA", "Nationality"),
            self._field(
                NATIONAL_ID,
                "110000200001010001",
                "National Identification Number",
            ),
        ]
        required = [item["id"] for item in fields]
        startup_errors = []
        service = None

        with tempfile.TemporaryDirectory() as directory:
            model = PageBatchFakeGemini()

            def runtime_factory(job):
                driver = ModelPlannedPlaywrightDriver()
                driver.set_execution_mode("visual")
                try:
                    driver.start("about:blank")
                    route_synthetic_ceac(driver)
                    driver._page.goto(
                        job.start_url,
                        wait_until="domcontentloaded",
                        timeout=driver.NAVIGATION_TIMEOUT_MS,
                    )
                except Exception as error:
                    startup_errors.append(error)
                    driver.close()
                    raise ProviderNotConfigured(
                        "Playwright Chromium is unavailable for E2E"
                    ) from error
                runtime = ComputerUseAgent(
                    model,
                    driver,
                    max_steps=40,
                    execution_mode="visual",
                )
                return runtime

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
                    "actor": "playwright-e2e",
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
                self.assertTrue(result["final_submission_boundary_reached"])
                self.assertFalse(result["continuous_run_requested"])
                self.assertEqual(
                    set(result["completed_field_ids"]),
                    set(required),
                )
                self.assertIn("最终签名和提交前停止", result["human_checkpoint"])

                with service._runtime_lock:
                    worker = service._runtimes[reviewed["id"]]

                def inspect(runtime):
                    browser = runtime.browser
                    observation = browser.observe_lightweight()
                    page_state = browser._page.evaluate(
                        """() => {
                            const parse = (storage, key, fallback) => {
                                try {
                                    return JSON.parse(
                                        storage.getItem(key) || fallback
                                    );
                                } catch (_) {
                                    return JSON.parse(fallback);
                                }
                            };
                            const cursor = document.getElementById(
                                'docflow-agent-visible-cursor'
                            );
                            const style = cursor
                                ? getComputedStyle(cursor) : null;
                            return {
                                stats: parse(
                                    localStorage,
                                    '__docflowE2EStats',
                                    '{}'
                                ),
                                moves: parse(
                                    sessionStorage,
                                    '__docflowE2EMoves',
                                    '[]'
                                ),
                                cursor: {
                                    present: Boolean(cursor),
                                    display: style?.display || '',
                                    visibility: style?.visibility || '',
                                    width: cursor?.getBoundingClientRect()
                                        .width || 0
                                }
                            };
                        }"""
                    )
                    return {
                        "url": observation.url,
                        "dispatch_ids": observation.dispatched_action_ids,
                        "dispatch_scope": observation.dispatch_receipt_scope,
                        "dispatch_authoritative": (
                            observation.dispatch_receipts_authoritative
                        ),
                        "executed": list(browser.executed_actions),
                        "next_plans": list(browser.next_plans),
                        "pointer_paths": list(browser.pointer_paths),
                        "refresh_checks": list(browser.refresh_checks),
                        "model_calls": list(model.calls),
                        "page_state": page_state,
                    }

                snapshot = worker.call(inspect, timeout=10)

                self.assertEqual(snapshot["url"], REVIEW_URL)
                stats = snapshot["page_state"]["stats"]
                self.assertEqual(stats.get("renderCount"), 2)
                self.assertEqual(stats.get("postbackCount"), 1)
                self.assertEqual(stats.get("page1NextCount"), 1)
                self.assertEqual(stats.get("page2NextCount"), 1)
                self.assertEqual(stats.get("finalActionCount", 0), 0)

                self.assertEqual(
                    [
                        len(call["returned"])
                        for call in snapshot["model_calls"]
                    ],
                    [3, 1, 2],
                    snapshot["model_calls"],
                )
                self.assertEqual(
                    [
                        "Personal1" if "Personal1" in call["url"]
                        else "Personal2"
                        for call in snapshot["model_calls"]
                    ],
                    ["Personal1", "Personal1", "Personal2"],
                )
                refresh_by_field = {
                    check["field_id"]: check
                    for check in snapshot["refresh_checks"]
                }
                self.assertTrue(refresh_by_field[BRANCH]["detected"])
                self.assertTrue(
                    refresh_by_field[BRANCH]["evidence"].get(
                        "markedControlRemoved"
                    )
                    or refresh_by_field[BRANCH]["evidence"].get(
                        "missingFieldTokens"
                    )
                )
                for field_id in {
                    SURNAME,
                    GIVEN_NAMES,
                    CONDITIONAL,
                    NATIONALITY,
                    NATIONAL_ID,
                }:
                    self.assertFalse(
                        refresh_by_field[field_id]["detected"],
                        refresh_by_field[field_id],
                    )
                    self.assertFalse(
                        refresh_by_field[field_id]["evidence"].get(
                            "inspectionUnavailable"
                        ),
                        refresh_by_field[field_id],
                    )

                executed = snapshot["executed"]
                field_actions = [
                    action for action in executed if action["field_id"]
                ]
                next_actions = [
                    action
                    for action in executed
                    if action["kind"] == ActionKind.CLICK.value
                    and action["target"].lower().startswith("next")
                ]
                self.assertEqual(
                    {action["field_id"] for action in field_actions},
                    set(required),
                )
                self.assertEqual(len(field_actions), len(required))
                self.assertEqual(len(next_actions), 2)
                self.assertTrue(all(
                    action["receipt_required"] for action in next_actions
                ))
                self.assertEqual(
                    len({action["id"] for action in executed}),
                    len(executed),
                )
                self.assertFalse(any(
                    "sign" in action["target"].lower()
                    or "submit" in action["target"].lower()
                    for action in executed
                ))

                self.assertEqual(len(snapshot["next_plans"]), 2)
                scopes = {
                    plan["scope"] for plan in snapshot["next_plans"]
                }
                self.assertEqual(len(scopes), 1)
                self.assertNotEqual(scopes, {""})
                self.assertTrue(snapshot["dispatch_authoritative"])
                self.assertIn(snapshot["dispatch_scope"], scopes)
                dispatch_ids = set(snapshot["dispatch_ids"])
                self.assertTrue({
                    action["id"] for action in next_actions
                }.issubset(dispatch_ids))

                pointer_paths = snapshot["pointer_paths"]
                self.assertGreaterEqual(len(pointer_paths), len(executed))
                long_paths = [
                    trace
                    for trace in pointer_paths
                    if len(trace["points"]) >= 3
                ]
                self.assertGreaterEqual(len(long_paths), 4)
                self.assertTrue(any(
                    self._maximum_curve_deviation(trace) > 2.0
                    for trace in long_paths
                ))
                self.assertGreaterEqual(
                    len(snapshot["page_state"]["moves"]),
                    40,
                )
                cursor = snapshot["page_state"]["cursor"]
                self.assertTrue(cursor["present"])
                self.assertNotEqual(cursor["display"], "none")
                self.assertNotEqual(cursor["visibility"], "hidden")
                self.assertGreater(cursor["width"], 0)

                event_kinds = [event["kind"] for event in result["events"]]
                self.assertEqual(
                    event_kinds.count("page_navigation_verified"),
                    2,
                )
                self.assertIn("dynamic_refresh_replanned", event_kinds)
            finally:
                if service is not None:
                    service.shutdown(timeout=15)


if __name__ == "__main__":
    unittest.main()
