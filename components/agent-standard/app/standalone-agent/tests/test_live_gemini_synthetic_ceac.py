"""Opt-in live Gemini + real Chromium acceptance test on synthetic CEAC pages.

The HTML is intercepted locally at CEAC-shaped URLs.  No request reaches CEAC
and every value is deliberately fictional.  The test is opt-in because it
uses the configured Gemini API credential and therefore makes billable network
requests.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from visa_agent.adapters import GeminiComputerUseAdapter
from visa_agent.config import AgentConfig, load_config
from visa_agent.providers import ProviderNotConfigured
from visa_agent.service import AgentService
from visa_agent.workflow import ComputerUseAgent

from tests.test_playwright_service_continuous_e2e import (
    BRANCH,
    CONDITIONAL,
    GIVEN_NAMES,
    NATIONALITY,
    PAGE_1_URL,
    REVIEW_URL,
    SURNAME,
    ModelPlannedPlaywrightDriver,
    route_synthetic_ceac,
)


SYNTHETIC_REFERENCE = "ceac.personal2.999.synthetic_reference"


class TimedGeminiComputerUseAdapter(GeminiComputerUseAdapter):
    """Record non-sensitive page-batch telemetry for acceptance assertions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page_batches = []

    def propose_actions(
        self,
        observation,
        available_field_ids,
        completed_field_ids,
        page_field_ids=None,
    ):
        started = time.monotonic()
        record = {
            "url": observation.url,
            "page_id": observation.page_id,
            "pending_count": len([
                field_id for field_id in (page_field_ids or [])
                if field_id not in set(completed_field_ids)
            ]),
        }
        try:
            actions = super().propose_actions(
                observation,
                available_field_ids,
                completed_field_ids,
                page_field_ids,
            )
            record.update({
                "action_count": len(actions),
                "action_kinds": [action.kind.value for action in actions],
            })
            return actions
        except Exception as error:
            record.update({
                "error_type": type(error).__name__,
                "error": str(error)[:500],
            })
            raise
        finally:
            record["elapsed_seconds"] = round(
                time.monotonic() - started,
                3,
            )
            self.page_batches.append(record)


@unittest.skipUnless(
    os.environ.get("DOCFLOW_RUN_LIVE_GEMINI_E2E") == "1",
    "set DOCFLOW_RUN_LIVE_GEMINI_E2E=1 to call the configured Gemini API",
)
class LiveGeminiSyntheticCeacTests(unittest.TestCase):
    @staticmethod
    def _field(field_id, value, label):
        return {
            "id": field_id,
            "value": value,
            "label": label,
            "confidence": 1.0,
            "risk_level": "high",
        }

    def test_one_start_uses_live_gemini_and_stops_before_submit(self):
        configured = load_config()
        self.assertTrue(
            configured.computer_use.api_key,
            "The opt-in live test requires a configured Gemini API key",
        )
        self.assertIn(
            configured.computer_use.provider.casefold(),
            {"google", "gemini"},
        )

        # All values are synthetic and intentionally unrelated to the user's
        # application or uploaded documents.
        fields = [
            self._field(SURNAME, "TESTER", "Surname"),
            self._field(GIVEN_NAMES, "ALEX MORGAN", "Given Names"),
            self._field(
                BRANCH,
                "true",
                "Have you ever used other names? "
                "[control=checkbox; refresh_after_change=true; "
                "control_hints=OtherNamesToggle; human-approved value=true]",
            ),
            self._field(
                CONDITIONAL,
                "SAMPLE",
                "Conditional Other Surnames "
                "[control=text; control_hints=OtherSurname]",
            ),
            self._field(NATIONALITY, "CANADA", "Nationality"),
            self._field(
                SYNTHETIC_REFERENCE,
                "DEMO-REFERENCE-0001",
                "National Identification Number "
                "[control=text; control_hints=NationalIdInput]",
            ),
        ]
        required = [field["id"] for field in fields]
        model = TimedGeminiComputerUseAdapter(configured.computer_use)
        service = None

        with tempfile.TemporaryDirectory() as directory:
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
                    driver.close()
                    raise ProviderNotConfigured(
                        "Playwright Chromium is unavailable for live E2E"
                    ) from error
                return ComputerUseAgent(
                    model,
                    driver,
                    max_steps=40,
                    execution_mode="visual",
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
                    "actor": "live-gemini-synthetic-e2e",
                    "decisions": [
                        {
                            "fieldId": field["id"],
                            "approved": True,
                            "value": field["value"],
                        }
                        for field in fields
                    ],
                })
                run_started = time.monotonic()
                start_call_count = 1
                result = service.start_job(reviewed["id"])
                initial_state = result.get("state")
                # A fully exhausted provider batch deliberately returns a
                # durable automatic-retry checkpoint quickly.  Do not call
                # resume: the one-click contract is that the service watcher
                # continues from this same browser on its own.
                deadline = time.monotonic() + 180
                while (
                    result.get("state") not in {
                        "review_required", "completed", "blocked", "failed",
                        "cancelled",
                    }
                    and result.get("continuous_run_requested") is True
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.5)
                    result = service.get_job(reviewed["id"])
                run_elapsed_seconds = round(
                    time.monotonic() - run_started,
                    3,
                )

                diagnostic = {
                    "state": result.get("state"),
                    "human_checkpoint": result.get("human_checkpoint"),
                    "blocked_reason": result.get("blocked_reason"),
                    "last_error": result.get("last_error"),
                    "wait_kind": result.get("wait_kind"),
                    "completed_field_ids": result.get("completed_field_ids"),
                    "event_tail": [
                        {
                            "kind": event.get("kind"),
                            "message": event.get("message"),
                            "detail": event.get("detail"),
                        }
                        for event in result.get("events", [])[-12:]
                    ],
                    "model_batches": list(model.page_batches),
                    "interaction_count": model.interaction_count,
                    "request_count": model.request_count,
                    "run_elapsed_seconds": run_elapsed_seconds,
                    "initial_state": initial_state,
                    "start_call_count": start_call_count,
                }
                self.assertEqual(
                    result["state"],
                    "review_required",
                    diagnostic,
                )
                self.assertTrue(result["final_submission_boundary_reached"])
                self.assertEqual(start_call_count, 1)
                self.assertEqual(
                    sum(
                        event.get("kind") == "started"
                        for event in result.get("events", [])
                    ),
                    1,
                    diagnostic,
                )
                self.assertEqual(
                    set(result["completed_field_ids"]),
                    set(required),
                )

                with service._runtime_lock:
                    worker = service._runtimes[reviewed["id"]]

                def inspect(runtime):
                    browser = runtime.browser
                    observation = browser.observe_lightweight()
                    page_state = browser._page.evaluate(
                        """() => ({
                            stats: JSON.parse(localStorage.getItem(
                                '__docflowE2EStats') || '{}'),
                            moves: JSON.parse(sessionStorage.getItem(
                                '__docflowE2EMoves') || '[]')
                        })"""
                    )
                    return {
                        "url": observation.url,
                        "executed": list(browser.executed_actions),
                        "pointer_paths": list(browser.pointer_paths),
                        "page_state": page_state,
                    }

                snapshot = worker.call(inspect, timeout=10)
                self.assertEqual(snapshot["url"], REVIEW_URL)
                self.assertEqual(
                    snapshot["page_state"]["stats"].get("page1NextCount"),
                    1,
                )
                self.assertEqual(
                    snapshot["page_state"]["stats"].get("page2NextCount"),
                    1,
                )
                self.assertEqual(
                    snapshot["page_state"]["stats"].get(
                        "finalActionCount", 0
                    ),
                    0,
                )
                self.assertGreaterEqual(len(snapshot["page_state"]["moves"]), 30)
                self.assertTrue(any(
                    len(trace["points"]) >= 3
                    for trace in snapshot["pointer_paths"]
                ))
                self.assertGreaterEqual(model.interaction_count, 3)
                self.assertLessEqual(
                    model.interaction_count,
                    5,
                    model.page_batches,
                )
                self.assertTrue(all(
                    batch["elapsed_seconds"]
                    <= model.PLANNING_TOTAL_BUDGET_SECONDS + 2
                    for batch in model.page_batches
                ))
                self.assertFalse(any(
                    "sign" in action["target"].casefold()
                    or "submit" in action["target"].casefold()
                    for action in snapshot["executed"]
                ))
                if os.environ.get("DOCFLOW_LIVE_E2E_REPORT") == "1":
                    print(
                        "DOCFLOW_LIVE_GEMINI_E2E="
                        + json.dumps(diagnostic, sort_keys=True)
                    )
            finally:
                if service is not None:
                    service.shutdown(timeout=15)


if __name__ == "__main__":
    unittest.main()
