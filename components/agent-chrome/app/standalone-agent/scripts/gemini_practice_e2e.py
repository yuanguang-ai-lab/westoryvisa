"""Visible DOM-first Computer Use smoke test with Gemini fallback.

This runner intentionally uses the production Gemini adapter, safe workflow,
deterministic verifier, and Playwright driver. The only test-specific pieces
are a localhost-only navigation policy and a page plan for the non-government
Practice Lab import page.
"""

import json
import re
import sys
import time
from pathlib import Path
from secrets import token_hex
from urllib.parse import urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visa_agent.adapters import GeminiComputerUseAdapter, PlaywrightBrowserDriver
from visa_agent.config import load_config
from visa_agent.models import AgentJob, ExtractedField, JobState, RiskLevel
from visa_agent.page_plans import PagePlan, PagePlanRegistry
from visa_agent.safety import PolicyDecision, VisaFormSafetyPolicy
from visa_agent.workflow import ComputerUseAgent


FIELD_VALUES = {
    "personal.surname": "DEMO",
    "personal.givenNames": "JAMIE",
    "personal.dateOfBirth": "2001-02-03",
}


class LocalPracticePolicy(VisaFormSafetyPolicy):
    """Allow only the fixed local Practice Lab import surface."""

    def inspect_navigation_target(self, target_url):
        parsed = urlparse(str(target_url))
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 4188
            or parsed.path != "/screen-agent-import.html"
        ):
            return PolicyDecision(
                False,
                True,
                "Gemini practice test attempted to leave the local allowlist",
            )
        return PolicyDecision(True)

    def inspect_page(self, observation):
        navigation = self.inspect_navigation_target(observation.url)
        if not navigation.allowed:
            return navigation
        visible = f"{observation.title}\n{observation.visible_text}"
        for pattern in self.UNTRUSTED_INSTRUCTION_PATTERNS:
            if re.search(pattern, visible, flags=re.IGNORECASE):
                return PolicyDecision(
                    False,
                    True,
                    "Untrusted page instruction requires human review",
                )
        return PolicyDecision(True)


class VisibleAuditBrowser(PlaywrightBrowserDriver):
    """Print Gemini actions and keep them visible long enough to observe."""

    def start(self, url):
        if self._page is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            # Use the machine's installed Google Chrome so the independently
            # deployable smoke test does not depend on Codex's browser runtime
            # or a separately downloaded Playwright browser bundle.
            self._browser = self._playwright.chromium.launch(
                headless=False,
                channel="chrome",
            )
            self._context = self._browser.new_context(
                viewport={"width": self.width, "height": self.height},
                accept_downloads=False,
            )
            self._page = self._context.new_page()
        self._page.goto(str(url), wait_until="domcontentloaded", timeout=60000)

    def execute(self, action):
        print(
            json.dumps(
                {
                    "browserAction": action.kind.value,
                    "source": (
                        "deterministic-dom"
                        if action.reason.startswith("Deterministic DOM")
                        else "gemini"
                    ),
                    "fieldId": action.field_id,
                    "target": action.target_hint,
                    "x": action.coordinate_x,
                    "y": action.coordinate_y,
                    "reason": action.reason,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        super().execute(action)
        self._page.wait_for_timeout(900)


def main():
    config = load_config()
    if config.computer_use.provider not in {"google", "gemini"}:
        raise RuntimeError("COMPUTER_USE_PROVIDER must be google or gemini")
    if not config.computer_use.api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    fields = []
    for field_id, value in FIELD_VALUES.items():
        item = ExtractedField(
            id=field_id,
            value=value,
            label=field_id,
            confidence=1.0,
            risk_level=RiskLevel.LOW,
        )
        item.confirm(
            value,
            confirmed_by="local-e2e-fixture",
            source="synthetic-practice-data",
            reason="Fictitious value for local Gemini Computer Use test",
        )
        fields.append(item)

    job_id = f"screen-agent-{token_hex(12)}"
    query = urlencode({
        "job": job_id,
        "fields": ",".join(FIELD_VALUES),
    })
    start_url = f"http://127.0.0.1:4188/screen-agent-import.html?{query}"
    plan = PagePlan(
        id="local-practice-import",
        path_patterns=(r"127\.0\.0\.1:4188/screen-agent-import\.html",),
        title_patterns=(r"Screen Agent Import",),
        allowed_field_ids=frozenset(FIELD_VALUES),
        required_field_ids=frozenset(FIELD_VALUES),
        allow_next=False,
    )
    browser = VisibleAuditBrowser(config.browser)
    browser.set_execution_mode(
        config.computer_use_execution or "visual"
    )
    model = GeminiComputerUseAdapter(config.computer_use)
    job = AgentJob(
        fields=fields,
        start_url=start_url,
        required_field_ids=list(FIELD_VALUES),
    )

    print(
        json.dumps(
            {
                "test": "Stateful visual Gemini Computer Use",
                "model": config.computer_use.model,
                "startUrl": start_url,
                "approvedFields": list(FIELD_VALUES),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    browser.start(start_url)
    try:
        result = ComputerUseAgent(
            model=model,
            browser=browser,
            policy=LocalPracticePolicy(),
            page_plans=PagePlanRegistry([plan], version="local-e2e-1"),
            use_model_verification=False,
            max_steps=20,
            execution_mode=(
                config.computer_use_execution or "visual"
            ),
        ).run(job)
        final_observation = browser.observe()
        print(
            json.dumps(
                {
                    "state": result.state.value,
                    "geminiInteractions": model.interaction_count,
                    "geminiHttpAttempts": model.request_count,
                    "completedFields": result.completed_field_ids,
                    "stepCount": result.step_count,
                    "checkpoint": result.human_checkpoint,
                    "verifiedValues": final_observation.control_values,
                    "events": [
                        {
                            "kind": event.kind,
                            "message": event.message,
                            "detail": event.detail,
                        }
                        for event in result.events
                    ],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        time.sleep(8)
        return 0 if result.state == JobState.COMPLETED else 1
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
