import unittest

from visa_agent.mocks import MockBrowserDriver
from visa_agent.models import (
    ActionKind,
    AgentJob,
    ComputerAction,
    ExtractedField,
    JobState,
    RiskLevel,
)
from visa_agent.workflow import ComputerUseAgent


FIELD_ID = "personal.surname"


def approved_field():
    return ExtractedField(
        id=FIELD_ID,
        value="XIA",
        label="Surnames [control=text; control_hints=SURNAME]",
        confirmed=True,
        risk_level=RiskLevel.HIGH,
    )


def job(*, continuous=False):
    return AgentJob(
        fields=[approved_field()],
        start_url=(
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_personal.aspx?node=Personal1"
        ),
        required_field_ids=[FIELD_ID],
        continuous_run_requested=continuous,
    )


class DeterministicCapableBrowser(MockBrowserDriver):
    def __init__(self):
        super().__init__(url=job().start_url)
        self.plan_calls = 0
        self.visual_bindings = []

    def plan_fields(self, field_ids, _field_labels, _control_hints):
        self.plan_calls += 1
        return [
            ComputerAction(
                kind=ActionKind.TYPE,
                field_id=field_id,
                target_hint=field_id,
                reason="deterministic DOM fast path",
            )
            for field_id in field_ids
        ], []

    def bind_visual_field(self, action, labels=(), hints=()):
        self.visual_bindings.append((
            action.field_id,
            tuple(labels or ()),
            tuple(hints or ()),
        ))
        return True


class RecordingPageBatchModel:
    def __init__(self):
        self.calls = []

    def propose_actions(
        self,
        observation,
        _available_field_ids,
        completed_field_ids,
        page_field_ids,
    ):
        self.calls.append((
            observation.screenshot_ref,
            tuple(page_field_ids),
            tuple(completed_field_ids),
        ))
        return [ComputerAction(
            kind=ActionKind.TYPE,
            field_id=FIELD_ID,
            target_hint=FIELD_ID,
            reason="Gemini screenshot page batch",
            coordinate_x=412,
            coordinate_y=287,
        )]


class ProviderExhaustedModel:
    def __init__(self):
        self.calls = 0

    def propose_actions(self, *_args):
        self.calls += 1
        error = TimeoutError("provider page batch exhausted")
        error.provider_retry_exhausted = True
        raise error


class ModelMustStayOffline:
    def __init__(self):
        self.calls = 0

    def propose_actions(self, *_args):
        self.calls += 1
        raise AssertionError("non-visual deterministic path called Gemini")

    def propose_action(self, *_args):
        self.calls += 1
        raise AssertionError("non-visual deterministic path called Gemini")


class VisualPageBatchParticipationTests(unittest.TestCase):
    def test_every_visual_execution_mode_uses_page_batch_before_dom(self):
        for execution_mode in ("visual", "native-visual", "codex-like"):
            with self.subTest(execution_mode=execution_mode):
                browser = DeterministicCapableBrowser()
                model = RecordingPageBatchModel()

                result = ComputerUseAgent(
                    model,
                    browser,
                    execution_mode=execution_mode,
                ).run(job())

                self.assertEqual(result.state, JobState.COMPLETED)
                self.assertEqual(len(model.calls), 1)
                self.assertEqual(model.calls[0][1], (FIELD_ID,))
                self.assertEqual(browser.plan_calls, 0)
                self.assertEqual(len(browser.visual_bindings), 1)
                self.assertEqual(
                    browser.executed[0].reason,
                    "Gemini screenshot page batch",
                )

    def test_visual_provider_failure_backs_off_before_dom_side_effects(self):
        browser = DeterministicCapableBrowser()
        model = ProviderExhaustedModel()

        result = ComputerUseAgent(
            model,
            browser,
            execution_mode="visual",
        ).run(job(continuous=True))

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertEqual(result.wait_kind, "automatic_retry")
        self.assertEqual(result.automatic_retry_kind, "provider")
        self.assertEqual(model.calls, 1)
        self.assertEqual(browser.plan_calls, 0)
        self.assertEqual(browser.executed, [])

    def test_non_visual_and_offline_modes_keep_dom_fast_path(self):
        for execution_mode in ("hybrid", "offline"):
            with self.subTest(execution_mode=execution_mode):
                browser = DeterministicCapableBrowser()
                model = ModelMustStayOffline()

                result = ComputerUseAgent(
                    model,
                    browser,
                    execution_mode=execution_mode,
                ).run(job())

                self.assertEqual(result.state, JobState.COMPLETED)
                self.assertEqual(model.calls, 0)
                self.assertEqual(browser.plan_calls, 1)
                self.assertEqual(len(browser.executed), 1)
                self.assertEqual(browser.visual_bindings, [])

    def test_exhausted_visual_field_uses_semantic_fallback_without_model(self):
        browser = DeterministicCapableBrowser()
        model = ModelMustStayOffline()
        current_job = job()
        failure_key = f"ceac-plan-personal1::{FIELD_ID}"
        current_job.visual_failure_counts = {
            failure_key: ComputerUseAgent.VISUAL_FIELD_FAILURE_LIMIT,
        }

        result = ComputerUseAgent(
            model,
            browser,
            execution_mode="visual",
        ).run(current_job)

        self.assertEqual(result.state, JobState.COMPLETED)
        self.assertEqual(model.calls, 0)
        self.assertEqual(browser.plan_calls, 1)
        self.assertEqual(len(browser.executed), 1)
        self.assertEqual(
            browser.executed[0].reason,
            "deterministic DOM fast path",
        )
        self.assertNotIn(failure_key, result.visual_failure_counts)


if __name__ == "__main__":
    unittest.main()
