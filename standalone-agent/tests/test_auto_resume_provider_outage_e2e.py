"""Real service/browser/API proof for durable one-click Gemini recovery.

This test deliberately exhausts both HTTP requests in the production Gemini
page-batch adapter.  The first and only caller-owned ``start_job`` therefore
returns a visible automatic-retry checkpoint before any form mutation.  The
durable AgentService watcher must wake at the checkpoint deadline, invoke a
new model batch on the unchanged page, and continue through every synthetic
DS-160 page to Review without another caller action.

The CEAC-shaped pages are fulfilled entirely inside Playwright and all values
are fictitious.  The Interactions endpoint is a localhost HTTP server; no
request reaches CEAC or Google.
"""

import json
import math
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from visa_agent.adapters import GeminiComputerUseAdapter
from visa_agent.config import AgentConfig, ProviderConfig
from visa_agent.models import ActionKind, JobState
from visa_agent.providers import ProviderNotConfigured
from visa_agent.service import AgentService, ServiceError
from visa_agent.workflow import ComputerUseAgent

from tests.test_mock_ds160_api_playwright_e2e import (
    FIELD_LABELS,
    PAGE_FIELDS,
    PERSONAL_URL,
    REVIEW_URL,
    SYNTHETIC_VALUES,
    LocalGeminiInteractionsAPI,
    RecordedVisualDriver,
    _route_synthetic_ds160,
)


class TwoFailureGeminiInteractionsAPI(LocalGeminiInteractionsAPI):
    """Fail one complete adapter interaction, then serve normal batches."""

    def __init__(self):
        # Do not call the parent initializer: its handler fails only one HTTP
        # request.  The response builder is intentionally inherited so this
        # server exercises the same page-batch schema as the main E2E.
        self.requests = []
        self.successful_batches = []
        self._lock = threading.Lock()
        self._remaining_failures = 2
        self._page_success_counts = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    self.send_error(400)
                    return

                with owner._lock:
                    request_index = len(owner.requests)
                    owner.requests.append({
                        "path": self.path,
                        "api_key": self.headers.get("x-goog-api-key", ""),
                        "payload": payload,
                    })
                    should_fail = owner._remaining_failures > 0
                    if should_fail:
                        owner._remaining_failures -= 1

                if should_fail:
                    body = json.dumps({
                        "error": {
                            "code": 503,
                            "message": "synthetic provider outage",
                        }
                    }).encode("utf-8")
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                response = owner._response_for(payload, request_index)
                body = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mock-gemini-two-request-outage",
            daemon=True,
        )


class InstrumentedAgentService(AgentService):
    """Record caller vs watcher starts without changing production flow."""

    def __init__(self, *args, **kwargs):
        self.start_invocations = []
        super().__init__(*args, **kwargs)

    def start_job(self, job_id, expected_watcher_epoch=None):
        self.start_invocations.append({
            "thread": threading.current_thread().name,
            "watcher": expected_watcher_epoch is not None,
        })
        return super().start_job(
            job_id,
            expected_watcher_epoch=expected_watcher_epoch,
        )


class ProviderOutageAutoResumeE2ETests(unittest.TestCase):
    @staticmethod
    def _field(field_id):
        return {
            "id": field_id,
            "value": SYNTHETIC_VALUES[field_id],
            "label": FIELD_LABELS[field_id],
            "confidence": 1.0,
            "risk_level": "high",
        }

    @staticmethod
    def _curve_deviation(trace):
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

    def test_one_start_survives_full_provider_batch_outage(self):
        required = list(SYNTHETIC_VALUES)
        startup_errors = []
        service = None
        try:
            api = TwoFailureGeminiInteractionsAPI().start()
        except OSError as error:
            self.skipTest(
                "Local HTTP sockets are unavailable for Gemini API E2E: "
                f"{error}"
            )

        with tempfile.TemporaryDirectory() as directory:
            def runtime_factory(job):
                driver = RecordedVisualDriver()
                driver.set_execution_mode("visual")
                try:
                    driver.start("about:blank")
                    _route_synthetic_ds160(driver)
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

                model = GeminiComputerUseAdapter(ProviderConfig(
                    provider="google",
                    model="gemini-synthetic-auto-resume-test",
                    api_base_url=api.base_url,
                    api_key="synthetic-local-key",
                ))
                # Production factory connects these two callbacks.  Keep the
                # custom E2E factory behavior-identical so both request retry
                # phases and the service backoff stay visible in the page.
                model.set_status_callback(driver.set_visual_status)
                runtime = ComputerUseAgent(
                    model,
                    driver,
                    max_steps=100,
                    execution_mode="visual",
                )
                runtime._mock_api_model = model
                return runtime

            service = InstrumentedAgentService(
                AgentConfig(data_dir=Path(directory) / "checkpoints"),
                runtime_factory=runtime_factory,
            )
            try:
                created = service.create_job({
                    "startUrl": PERSONAL_URL,
                    "requiredFieldIds": required,
                    "fields": [self._field(field_id) for field_id in required],
                    "autoNext": True,
                })
                reviewed = service.review_job(created["id"], {
                    "actor": "synthetic-provider-outage-e2e",
                    "decisions": [
                        {
                            "fieldId": field_id,
                            "approved": True,
                            "value": SYNTHETIC_VALUES[field_id],
                        }
                        for field_id in required
                    ],
                })

                # This is the only caller/user-equivalent start in the test.
                try:
                    waiting = service.start_job(reviewed["id"])
                except ServiceError:
                    if startup_errors:
                        self.skipTest(
                            "Playwright/Chromium unavailable: "
                            f"{startup_errors[-1]}"
                        )
                    raise

                self.assertEqual(waiting["state"], "waiting_human", waiting)
                self.assertEqual(waiting["wait_kind"], "automatic_retry")
                self.assertEqual(waiting["automatic_retry_kind"], "provider")
                self.assertTrue(waiting["automatic_retry_pending"])
                self.assertTrue(waiting["continuous_run_requested"])
                self.assertEqual(waiting["automatic_retry_count"], 1)
                self.assertIn("无需再次点击", waiting["human_checkpoint"])
                self.assertEqual(len(api.requests), 2)
                self.assertEqual(api.successful_batches, [])

                # A single status read proves the first start armed a watcher.
                # The completion loop below reads the checkpoint store directly
                # so status polling itself cannot create/re-arm the watcher.
                live_wait = service.get_job(reviewed["id"])
                self.assertTrue(live_wait["runtime_open"])
                self.assertFalse(live_wait["execution_active"])
                self.assertTrue(live_wait["auto_resume_watcher_armed"])

                with service._runtime_lock:
                    worker = service._runtimes[reviewed["id"]]

                def inspect_wait(runtime):
                    browser = runtime.browser
                    return {
                        "executed": list(browser.executed_actions),
                        "page": browser._page.evaluate(
                            """() => {
                                const badge = document.getElementById(
                                    'docflow-agent-visual-status'
                                );
                                const cursor = document.getElementById(
                                    'docflow-agent-visible-cursor'
                                );
                                const saved = JSON.parse(
                                    sessionStorage.getItem(
                                        '__docflowAgentVisualState'
                                    ) || '{}'
                                );
                                const stats = JSON.parse(
                                    localStorage.getItem(
                                        '__mockDs160Stats'
                                    ) || '{}'
                                );
                                return {
                                    badgeState: badge?.dataset.state || '',
                                    badgeText: badge?.innerText || '',
                                    cursorPresent: Boolean(cursor),
                                    savedState: saved.state || '',
                                    savedMessage: saved.message || '',
                                    stats
                                };
                            }"""
                        ),
                    }

                paused_snapshot = worker.call(inspect_wait, timeout=10)
                self.assertEqual(paused_snapshot["executed"], [])
                self.assertEqual(
                    paused_snapshot["page"]["savedState"], "thinking"
                )
                self.assertIn(
                    "自动重试", paused_snapshot["page"]["savedMessage"]
                )
                self.assertTrue(paused_snapshot["page"]["cursorPresent"])
                self.assertEqual(paused_snapshot["page"]["stats"], {})

                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    durable = service.checkpoint_store.load_job(reviewed["id"])
                    if durable.state == JobState.REVIEW_REQUIRED:
                        break
                    time.sleep(0.05)
                else:
                    self.fail(
                        "Durable auto-resume watcher did not reach Review: "
                        + json.dumps({
                            "state": durable.state.value,
                            "waitKind": durable.wait_kind,
                            "checkpoint": durable.human_checkpoint,
                            "retryPending": durable.automatic_retry_pending,
                            "events": [
                                event.kind for event in durable.events[-15:]
                            ],
                            "starts": service.start_invocations,
                            "requestCount": len(api.requests),
                        }, ensure_ascii=False)
                    )

                final = service.get_job(reviewed["id"])
                self.assertEqual(final["state"], "review_required")
                self.assertTrue(final["final_submission_boundary_reached"])
                self.assertFalse(final["continuous_run_requested"])
                self.assertFalse(final["automatic_retry_pending"])
                self.assertEqual(
                    set(final["completed_field_ids"]), set(required)
                )

                caller_starts = [
                    item for item in service.start_invocations
                    if not item["watcher"]
                ]
                watcher_starts = [
                    item for item in service.start_invocations
                    if item["watcher"]
                ]
                self.assertEqual(len(caller_starts), 1, service.start_invocations)
                self.assertEqual(len(watcher_starts), 1, service.start_invocations)

                def inspect_final(runtime):
                    browser = runtime.browser
                    observation = browser.observe_lightweight()
                    page_state = browser._page.evaluate(
                        """() => ({
                            stats: JSON.parse(localStorage.getItem(
                                '__mockDs160Stats') || '{}'),
                            saved: JSON.parse(localStorage.getItem(
                                '__mockDs160SavedPages') || '{}')
                        })"""
                    )
                    return {
                        "url": observation.url,
                        "executed": list(browser.executed_actions),
                        "pointer_paths": list(browser.pointer_paths),
                        "model_interactions": (
                            runtime._mock_api_model.interaction_count
                        ),
                        "model_requests": runtime._mock_api_model.request_count,
                        "page_state": page_state,
                    }

                snapshot = worker.call(inspect_final, timeout=15)
                self.assertEqual(snapshot["url"], REVIEW_URL)
                stats = snapshot["page_state"]["stats"]
                for page_key in (
                    "personal1", "travel", "addressPhone", "passport"
                ):
                    self.assertEqual(stats.get(page_key + "Next"), 1, stats)
                    self.assertEqual(
                        stats.get(page_key + "SaveCommit"), 1, stats
                    )
                self.assertEqual(stats.get("personal1Postback"), 1, stats)
                self.assertEqual(stats.get("manualSaveClicks", 0), 0, stats)
                self.assertEqual(stats.get("finalActionCount", 0), 0, stats)

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
                self.assertEqual(len(field_actions), len(required), field_actions)
                self.assertEqual(
                    {action["field_id"] for action in field_actions},
                    set(required),
                )
                self.assertEqual(len(next_actions), 4, next_actions)
                self.assertFalse(any(
                    "sign" in action["target"].casefold()
                    or "submit" in action["target"].casefold()
                    for action in executed
                ))

                self.assertEqual(
                    [batch["node"] for batch in api.successful_batches],
                    [
                        "Personal1", "Personal1", "Travel",
                        "AddressPhone", "PptVisa",
                    ],
                )
                self.assertEqual(snapshot["model_interactions"], 6)
                self.assertEqual(snapshot["model_requests"], 7)
                self.assertEqual(len(api.requests), 7)
                self.assertTrue(all(
                    request["path"] == "/v1beta/interactions"
                    for request in api.requests
                ))
                self.assertTrue(all(
                    LocalGeminiInteractionsAPI._has_screenshot(
                        request["payload"]
                    )
                    for request in api.requests
                ))

                paths = snapshot["pointer_paths"]
                curved = [
                    trace for trace in paths
                    if len(trace["points"]) >= 3
                    and self._curve_deviation(trace) > 2.0
                ]
                self.assertGreaterEqual(len(curved), 6)

                event_kinds = [event["kind"] for event in final["events"]]
                self.assertEqual(event_kinds.count("automatic_retry_scheduled"), 1)
                self.assertIn("automatic_retry_cleared", event_kinds)
                self.assertEqual(event_kinds.count("page_navigation_verified"), 4)
            finally:
                if service is not None:
                    service.shutdown(timeout=15)
                api.close()


if __name__ == "__main__":
    unittest.main()
