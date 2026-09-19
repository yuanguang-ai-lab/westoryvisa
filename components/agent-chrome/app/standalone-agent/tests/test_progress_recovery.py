import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from visa_agent.config import AgentConfig
from visa_agent.mocks import MockBrowserDriver
from visa_agent.models import (
    ActionKind,
    AgentJob,
    BrowserObservation,
    ComputerAction,
    ExtractedField,
    JobState,
    job_from_primitive,
    to_primitive,
)
from visa_agent.workflow import ComputerUseAgent
from visa_agent.service import AgentService


def confirmed_field(field_id, value):
    return ExtractedField(
        id=field_id,
        value=value,
        confirmed=True,
    )


class ModelMustNotRun:
    def __init__(self):
        self.calls = 0

    def propose_action(self, *_args):
        self.calls += 1
        raise AssertionError("Deterministic page controls must not use the model")


class DeterministicPagesBrowser(MockBrowserDriver):
    def __init__(self, pages):
        super().__init__(
            url=pages[0][0],
            title=pages[0][1],
            visible_text=pages[0][1],
        )
        self.pages = list(pages)
        self.page_index = 0
        self.next_clicks = 0
        self.dispatch_receipt_scope = ""
        self.dispatch_receipts_authoritative = False
        self.dispatch_receipt_conflict = False
        self.dispatched_action_ids = []

    def observe(self):
        observed = super().observe()
        return BrowserObservation(
            url=observed.url,
            title=observed.title,
            visible_text=observed.visible_text,
            screenshot_ref=observed.screenshot_ref,
            page_id=f"page-{self.page_index}",
            control_values=observed.control_values,
            errors=observed.errors,
            acknowledged_action_ids=observed.acknowledged_action_ids,
            dispatched_action_ids=list(self.dispatched_action_ids),
            dispatch_receipt_scope=self.dispatch_receipt_scope,
            dispatch_receipts_authoritative=(
                self.dispatch_receipts_authoritative
            ),
            dispatch_receipt_conflict=self.dispatch_receipt_conflict,
        )

    def plan_fields(self, field_ids, _field_labels, _control_hints):
        return (
            [
                ComputerAction(
                    kind=ActionKind.TYPE,
                    field_id=field_id,
                    target_hint=field_id,
                    reason="Deterministic DOM match",
                )
                for field_id in field_ids
            ],
            [],
        )

    def plan_next(self):
        return ComputerAction(
            kind=ActionKind.CLICK,
            target_hint="Next: Continue",
            reason="Deterministic fixed CEAC Next control",
        )

    def _advance(self):
        self.page_index += 1
        self.url, self.title = self.pages[self.page_index]
        self.visible_text = self.title

    def execute(self, action):
        if (
            action.kind == ActionKind.CLICK
            and action.target_hint.startswith("Next")
        ):
            self.executed.append(action)
            self.next_clicks += 1
            self._advance()
            return
        super().execute(action)


class DelayedPostbackBrowser(DeterministicPagesBrowser):
    navigation_outcome_timeout_seconds = 1

    def __init__(self, pages):
        super().__init__(pages)
        self.next_pending = False
        self.release_postback = False

    def execute(self, action):
        if (
            action.kind == ActionKind.CLICK
            and action.target_hint.startswith("Next")
        ):
            self.executed.append(action)
            self.next_clicks += 1
            self.next_pending = True
            return
        super().execute(action)

    def observe(self):
        if self.next_pending and self.release_postback:
            self.next_pending = False
            self._advance()
        return super().observe()


class LostObservationAfterNextBrowser(DeterministicPagesBrowser):
    def __init__(self, pages):
        super().__init__(pages)
        self.failed_observations = 0

    def execute(self, action):
        if (
            action.kind == ActionKind.CLICK
            and action.target_hint.startswith("Next")
        ):
            super().execute(action)
            self.failed_observations = 3
            return
        super().execute(action)

    def observe_lightweight(self):
        if self.failed_observations:
            self.failed_observations -= 1
            raise RuntimeError("CDP response lost after successful Next")
        return self.observe()


class RetainedPageWithValidationError(DelayedPostbackBrowser):
    def observe(self):
        observed = super().observe()
        errors = (
            ["A required value is missing"]
            if self.next_pending
            else []
        )
        return BrowserObservation(
            url=observed.url,
            title=observed.title,
            visible_text=observed.visible_text,
            screenshot_ref=observed.screenshot_ref,
            page_id=observed.page_id,
            control_values=observed.control_values,
            errors=errors,
            acknowledged_action_ids=observed.acknowledged_action_ids,
            dispatched_action_ids=observed.dispatched_action_ids,
            dispatch_receipt_scope=observed.dispatch_receipt_scope,
            dispatch_receipts_authoritative=(
                observed.dispatch_receipts_authoritative
            ),
            dispatch_receipt_conflict=(
                observed.dispatch_receipt_conflict
            ),
        )


class NoProgressBrowser(MockBrowserDriver):
    def plan_fields(self, field_ids, _field_labels, _control_hints):
        return [], list(field_ids)


class VisualBindingLoopBrowser(NoProgressBrowser):
    def __init__(self):
        super().__init__()
        self.binding_attempts = 0
        self.invalidated = []

    def bind_visual_field(self, *_args, **_kwargs):
        self.binding_attempts += 1
        return False

    def invalidate_field_binding(self, field_id):
        self.invalidated.append(field_id)


class VisualBindingLoopModel:
    def __init__(self):
        self.calls = 0

    def propose_actions(self, *_args):
        self.calls += 1
        return [ComputerAction(
            kind=ActionKind.TYPE,
            field_id="personal.surname",
            target_hint="personal.surname",
            coordinate_x=800,
            coordinate_y=400,
        )]

    def record_action_result(self, *_args, **_kwargs):
        return None


class WaitOnlyModel:
    def __init__(self):
        self.calls = 0

    def propose_actions(self, *_args):
        self.calls += 1
        return [
            ComputerAction(
                kind=ActionKind.WAIT,
                reason="No visible approved control yet",
            )
        ]


class ProgressRecoveryTests(unittest.TestCase):
    PERSONAL_1 = (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_personal.aspx?node=Personal1",
        "Personal Information 1",
    )
    PERSONAL_2 = (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_personalcont.aspx?node=Personal2",
        "Personal Information 2",
    )
    REVIEW = (
        "https://ceac.state.gov/GenNIV/General/Review/"
        "ReviewReview.aspx?node=ReviewReview",
        "Review Application",
    )

    @staticmethod
    def completed_personal_job(browser):
        field_id = "ceac.personal1.001.personal.surname"
        browser.control_values[field_id] = "XIA"
        return AgentJob(
            fields=[confirmed_field(field_id, "XIA")],
            start_url=browser.url,
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
            continuous_run_requested=True,
        )

    def test_same_page_delayed_postback_yields_then_recovers_without_reclick(self):
        browser = DelayedPostbackBrowser([self.PERSONAL_1, self.REVIEW])
        job = self.completed_personal_job(browser)
        agent = ComputerUseAgent(ModelMustNotRun(), browser)

        first = agent.run(job)

        self.assertEqual(first.state, JobState.WAITING_HUMAN)
        self.assertTrue(first.automatic_retry_pending)
        self.assertEqual(
            first.automatic_retry_kind,
            "navigation_observation",
        )
        self.assertIsNotNone(first.pending_action)
        self.assertEqual(browser.next_clicks, 1)

        restored = job_from_primitive(to_primitive(first))
        second_wait = agent.run(restored)

        self.assertEqual(second_wait.state, JobState.WAITING_HUMAN)
        self.assertEqual(second_wait.automatic_retry_count, 2)
        self.assertEqual(
            second_wait.events[-1].detail["retryDelaySeconds"],
            2,
        )
        self.assertEqual(browser.next_clicks, 1)
        self.assertIsNotNone(second_wait.pending_action)

        browser.release_postback = True
        final = agent.run(second_wait)

        self.assertEqual(final.state, JobState.REVIEW_REQUIRED)
        self.assertEqual(browser.next_clicks, 1)
        self.assertIsNone(final.pending_action)
        self.assertFalse(final.automatic_retry_pending)
        self.assertTrue(any(
            event.kind == "page_navigation_recovered"
            for event in final.events
        ))

    def test_prepared_next_without_dispatch_receipt_is_safely_sent_once(self):
        browser = DeterministicPagesBrowser([self.PERSONAL_1, self.REVIEW])
        browser.dispatch_receipt_scope = "dispatch-proof-scope"
        browser.dispatch_receipts_authoritative = True
        job = self.completed_personal_job(browser)
        pending = ComputerAction(
            kind=ActionKind.CLICK,
            target_hint="Next: Continue",
            reason="Deterministic fixed CEAC Next control",
            dispatch_receipt_required=True,
            dispatch_receipt_scope=browser.dispatch_receipt_scope,
            id="action-prepared-before-crash",
        )
        job.pending_action = pending
        job.current_page_plan_id = "personal-information"
        job.last_safe_url = self.PERSONAL_1[0]
        job = job_from_primitive(to_primitive(job))

        result = ComputerUseAgent(
            ModelMustNotRun(),
            browser,
        ).run(job)

        self.assertEqual(result.state, JobState.REVIEW_REQUIRED)
        self.assertEqual(browser.next_clicks, 1)
        self.assertNotIn(pending.id, result.applied_action_ids)
        self.assertTrue(any(
            event.kind == "pending_next_not_dispatched"
            and event.detail.get("actionId") == pending.id
            for event in result.events
        ))

    def test_dispatched_next_receipt_is_only_observed_after_crash(self):
        browser = DelayedPostbackBrowser([self.PERSONAL_1, self.REVIEW])
        browser.dispatch_receipt_scope = "dispatch-proof-scope"
        browser.dispatch_receipts_authoritative = True
        job = self.completed_personal_job(browser)
        pending = ComputerAction(
            kind=ActionKind.CLICK,
            target_hint="Next: Continue",
            reason="Deterministic fixed CEAC Next control",
            dispatch_receipt_required=True,
            dispatch_receipt_scope=browser.dispatch_receipt_scope,
            id="action-dispatched-before-crash",
        )
        browser.dispatched_action_ids = [pending.id]
        job.pending_action = pending
        job.current_page_plan_id = "personal-information"
        job.last_safe_url = self.PERSONAL_1[0]
        job = job_from_primitive(to_primitive(job))

        result = ComputerUseAgent(
            ModelMustNotRun(),
            browser,
        ).run(job)

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertEqual(
            result.automatic_retry_kind,
            "navigation_observation",
        )
        self.assertIsNotNone(result.pending_action)
        self.assertEqual(result.pending_action.id, pending.id)
        self.assertEqual(browser.next_clicks, 0)

    def test_divergent_dispatch_ledgers_stop_without_reclick(self):
        browser = DeterministicPagesBrowser([self.PERSONAL_1, self.REVIEW])
        browser.dispatch_receipt_scope = "dispatch-proof-scope"
        browser.dispatch_receipts_authoritative = False
        browser.dispatch_receipt_conflict = True
        job = self.completed_personal_job(browser)
        pending = ComputerAction(
            kind=ActionKind.CLICK,
            target_hint="Next: Continue",
            reason="Deterministic fixed CEAC Next control",
            dispatch_receipt_required=True,
            dispatch_receipt_scope=browser.dispatch_receipt_scope,
            id="action-conflicting-ledgers",
        )
        job.pending_action = pending
        job.current_page_plan_id = "personal-information"
        job.last_safe_url = self.PERSONAL_1[0]

        result = ComputerUseAgent(
            ModelMustNotRun(),
            browser,
        ).run(job)

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertEqual(result.wait_kind, "manual_hard_boundary")
        self.assertIsNotNone(result.pending_action)
        self.assertEqual(browser.next_clicks, 0)
        self.assertTrue(any(
            event.kind == "dispatch_receipt_conflict"
            for event in result.events
        ))

    def test_next_execute_exception_before_dispatch_replans_safely_once(self):
        class Browser(DeterministicPagesBrowser):
            def __init__(browser_self, pages):
                super().__init__(pages)
                browser_self.dispatch_receipt_scope = "receipt-scope-before"
                browser_self.dispatch_receipts_authoritative = True
                browser_self.execute_calls = 0

            def plan_next(browser_self):
                return ComputerAction(
                    kind=ActionKind.CLICK,
                    target_hint="Next: Continue",
                    reason="Deterministic fixed CEAC Next control",
                    dispatch_receipt_required=True,
                    dispatch_receipt_scope=(
                        browser_self.dispatch_receipt_scope
                    ),
                )

            def execute(browser_self, action):
                if (
                    action.kind == ActionKind.CLICK
                    and action.target_hint.startswith("Next")
                ):
                    browser_self.execute_calls += 1
                    if browser_self.execute_calls == 1:
                        raise RuntimeError("click failed before dispatch")
                return super().execute(action)

        browser = Browser([self.PERSONAL_1, self.REVIEW])
        result = ComputerUseAgent(
            ModelMustNotRun(),
            browser,
        ).run(self.completed_personal_job(browser))

        self.assertEqual(result.state, JobState.REVIEW_REQUIRED)
        self.assertEqual(browser.execute_calls, 2)
        self.assertEqual(browser.next_clicks, 1)
        self.assertTrue(any(
            event.kind == "interrupted_next_not_dispatched"
            for event in result.events
        ))

    def test_next_execute_exception_after_dispatch_only_observes(self):
        class Browser(DelayedPostbackBrowser):
            def __init__(browser_self, pages):
                super().__init__(pages)
                browser_self.dispatch_receipt_scope = "receipt-scope-after"
                browser_self.dispatch_receipts_authoritative = True
                browser_self.execute_calls = 0

            def plan_next(browser_self):
                return ComputerAction(
                    kind=ActionKind.CLICK,
                    target_hint="Next: Continue",
                    reason="Deterministic fixed CEAC Next control",
                    dispatch_receipt_required=True,
                    dispatch_receipt_scope=(
                        browser_self.dispatch_receipt_scope
                    ),
                )

            def execute(browser_self, action):
                if (
                    action.kind == ActionKind.CLICK
                    and action.target_hint.startswith("Next")
                ):
                    browser_self.execute_calls += 1
                    browser_self.next_clicks += 1
                    browser_self.next_pending = True
                    browser_self.dispatched_action_ids.append(action.id)
                    raise RuntimeError("connection lost after click dispatch")
                return super().execute(action)

        browser = Browser([self.PERSONAL_1, self.REVIEW])
        result = ComputerUseAgent(
            ModelMustNotRun(),
            browser,
        ).run(self.completed_personal_job(browser))

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertEqual(
            result.automatic_retry_kind,
            "navigation_observation",
        )
        self.assertEqual(browser.execute_calls, 1)
        self.assertEqual(browser.next_clicks, 1)
        self.assertIsNotNone(result.pending_action)
        self.assertTrue(any(
            event.kind
            == "interrupted_next_observation_retry_scheduled"
            for event in result.events
        ))

    def test_new_action_id_is_namespaced_by_execution_generation(self):
        browser = DeterministicPagesBrowser(
            [self.PERSONAL_1, self.REVIEW]
        )
        job = self.completed_personal_job(browser)
        job.execution_generation = 7

        ComputerUseAgent(ModelMustNotRun(), browser).run(job)

        self.assertEqual(browser.next_clicks, 1)
        self.assertIn("-g7-", browser.executed[0].id)
        self.assertEqual(browser.executed[0].execution_generation, 7)

    def test_changed_prior_page_field_stops_at_cross_page_consistency_boundary(
        self,
    ):
        field_id = "ceac.personal1.001.personal.surname"
        browser = DeterministicPagesBrowser(
            [self.PERSONAL_2, self.REVIEW]
        )
        pending = ComputerAction(
            kind=ActionKind.CLICK,
            target_hint="Next: Personal 2",
            reason="Deterministic fixed CEAC Next control",
            id="action-next-before-sync",
        )
        job = AgentJob(
            fields=[confirmed_field(field_id, "UPDATED")],
            start_url=self.PERSONAL_1[0],
            required_field_ids=[field_id],
            pending_action=pending,
            current_page_plan_id="personal-information",
            last_safe_url=self.PERSONAL_1[0],
            continuous_run_requested=True,
            sync_reconciliation_field_ids=[field_id],
            sync_reconciliation_page_plan_id="personal-information",
        )

        result = ComputerUseAgent(ModelMustNotRun(), browser).run(job)

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertIsNone(result.pending_action)
        self.assertIn(pending.id, result.applied_action_ids)
        self.assertIn("没有把同步后的新值伪报为已应用", result.human_checkpoint)
        self.assertEqual(browser.next_clicks, 0)

    def test_legacy_dirty_next_without_origin_is_conservative_after_cross_page(
        self,
    ):
        field_id = "ceac.personal1.001.personal.surname"
        browser = DeterministicPagesBrowser(
            [self.PERSONAL_2, self.REVIEW]
        )
        job = AgentJob(
            fields=[confirmed_field(field_id, "UPDATED")],
            start_url=self.PERSONAL_1[0],
            required_field_ids=[field_id],
            pending_action=ComputerAction(
                kind=ActionKind.CLICK,
                target_hint="Next: Personal 2",
                reason="Deterministic fixed CEAC Next control",
                id="legacy-next-without-origin",
            ),
            last_safe_url=self.PERSONAL_1[0],
            continuous_run_requested=True,
            sync_reconciliation_field_ids=[field_id],
        )

        result = ComputerUseAgent(ModelMustNotRun(), browser).run(job)

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertIn("来源页标识缺失", result.human_checkpoint)
        self.assertEqual(browser.next_clicks, 0)

    def test_successful_next_with_lost_observation_recovers_without_reclick(self):
        browser = LostObservationAfterNextBrowser(
            [self.PERSONAL_1, self.REVIEW]
        )
        job = self.completed_personal_job(browser)
        agent = ComputerUseAgent(ModelMustNotRun(), browser)

        first = agent.run(job)

        self.assertEqual(first.state, JobState.WAITING_HUMAN)
        self.assertEqual(first.automatic_retry_kind, "browser")
        self.assertIsNotNone(first.pending_action)
        self.assertEqual(browser.next_clicks, 1)

        final = agent.run(first)

        self.assertEqual(final.state, JobState.REVIEW_REQUIRED)
        self.assertEqual(browser.next_clicks, 1)
        self.assertIsNone(final.pending_action)
        self.assertFalse(final.automatic_retry_pending)

    def test_service_rebuilds_runtime_after_next_observation_loss_once(self):
        with tempfile.TemporaryDirectory() as directory:
            shared = {
                "page_index": 0,
                "next_clicks": 0,
                "fail_observations": 0,
                "post_action_sample_pending": False,
            }
            pages = [self.PERSONAL_1, self.REVIEW]

            class Browser(MockBrowserDriver):
                def __init__(browser_self):
                    url, title = pages[shared["page_index"]]
                    super().__init__(
                        url=url,
                        title=title,
                        visible_text=title,
                    )
                    browser_self.closed = False
                    browser_self.profile_purge_requested = False
                    browser_self.control_values[
                        "personal.surname"
                    ] = "XIA"

                def observe(browser_self):
                    if shared["fail_observations"]:
                        shared["fail_observations"] -= 1
                        raise RuntimeError(
                            "CDP response lost after successful Next"
                        )
                    url, title = pages[shared["page_index"]]
                    browser_self.url = url
                    browser_self.title = title
                    browser_self.visible_text = title
                    observed = super().observe()
                    result = BrowserObservation(
                        url=observed.url,
                        title=observed.title,
                        visible_text=observed.visible_text,
                        screenshot_ref=observed.screenshot_ref,
                        page_id=f"page-{shared['page_index']}",
                        control_values=observed.control_values,
                        errors=observed.errors,
                        acknowledged_action_ids=(
                            observed.acknowledged_action_ids
                        ),
                    )
                    if shared["post_action_sample_pending"]:
                        # The first sample after the click is a valid but stale
                        # retained-page observation, so workflow enters its
                        # slow-navigation waiter. The actual page then advances,
                        # while both navigation-outcome reads lose CDP.
                        shared["post_action_sample_pending"] = False
                        shared["page_index"] = 1
                        shared["fail_observations"] = 2
                    return result

                def observe_lightweight(browser_self):
                    return browser_self.observe()

                def plan_fields(
                    browser_self,
                    field_ids,
                    _field_labels,
                    _control_hints,
                ):
                    return ([
                        ComputerAction(
                            kind=ActionKind.TYPE,
                            field_id=field_id,
                            target_hint=field_id,
                            reason="Deterministic DOM match",
                        )
                        for field_id in field_ids
                    ], [])

                def plan_next(browser_self):
                    return ComputerAction(
                        kind=ActionKind.CLICK,
                        target_hint="Next: Continue",
                        reason="Deterministic fixed CEAC Next control",
                    )

                def execute(browser_self, action):
                    if (
                        action.kind == ActionKind.CLICK
                        and action.target_hint.startswith("Next")
                    ):
                        shared["next_clicks"] += 1
                        shared["post_action_sample_pending"] = True
                        return
                    super().execute(action)

                def close(browser_self):
                    browser_self.closed = True
                    shared["fail_observations"] = 0

                def purge_profile_on_close(browser_self):
                    browser_self.profile_purge_requested = True

            agents = []

            def runtime_factory(_job):
                agent = ComputerUseAgent(ModelMustNotRun(), Browser())
                agents.append(agent)
                return agent

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=runtime_factory,
            )
            created = service.create_job({
                "startUrl": self.PERSONAL_1[0],
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "XIA",
                    "confidence": 1.0,
                }],
            })
            reviewed = service.review_job(created["id"], {
                "actor": "progress-test",
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "XIA",
                }],
            })
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.completed_field_ids = ["personal.surname"]
            service.checkpoint_store.save(stored)

            first = service.start_job(reviewed["id"])
            self.assertEqual(first["state"], "waiting_human")
            self.assertEqual(first["automatic_retry_kind"], "browser")
            self.assertIsNotNone(first["pending_action"])
            self.assertEqual(shared["next_clicks"], 1)
            self.assertTrue(agents[0].browser.closed)
            self.assertFalse(
                agents[0].browser.profile_purge_requested
            )
            self.assertTrue(any(
                event["kind"] == "browser_runtime_retry_scheduled"
                and event["detail"].get("purpose")
                == "navigation-outcome"
                for event in first["events"]
            ))
            self.assertFalse(any(
                event["kind"] == "page_navigation_retry_scheduled"
                for event in first["events"]
            ))

            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            service.checkpoint_store.save(stored)
            for _ in range(300):
                current = service.get_job(reviewed["id"])
                if (
                    current["state"] == "review_required"
                    and not current["continuous_run_requested"]
                ):
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertEqual(shared["next_clicks"], 1)
            self.assertEqual(len(agents), 2)
            self.assertIsNone(current["pending_action"])
            self.assertEqual(
                current["applied_action_ids"],
                [first["pending_action"]["id"]],
            )
            self.assertFalse(current["automatic_retry_pending"])
            service._release_runtime(reviewed["id"])

    def test_service_resume_honors_durable_visual_failure_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = VisualBindingLoopBrowser()
            model = VisualBindingLoopModel()
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: ComputerUseAgent(
                    model,
                    browser,
                    execution_mode="visual",
                ),
            )
            # Keep the test deterministic: two explicit service start calls
            # stand in for the initial execution and watcher/service resume.
            service._arm_continuous_resume = lambda *_args, **_kwargs: None
            created = service.create_job({
                "startUrl": browser.url,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "XIA",
                    "confidence": 1.0,
                }],
            })
            reviewed = service.review_job(created["id"], {
                "actor": "visual-budget-test",
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "XIA",
                }],
            })
            service.open_job(reviewed["id"])

            first = service.start_job(reviewed["id"])
            key = "ceac-plan-personal1::personal.surname"
            self.assertEqual(first["visual_failure_counts"], {key: 3})
            self.assertEqual(model.calls, 3)
            self.assertEqual(browser.binding_attempts, 3)
            self.assertEqual(first["wait_kind"], "automatic_retry")

            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            service.checkpoint_store.save(stored)

            second = service.start_job(reviewed["id"])
            self.assertEqual(second["visual_failure_counts"], {key: 3})
            self.assertEqual(model.calls, 3)
            self.assertEqual(browser.binding_attempts, 3)
            self.assertTrue(any(
                event["kind"] == "visual_semantic_rebind_retry_scheduled"
                for event in second["events"]
            ))
            service._release_runtime(reviewed["id"])

    def test_sync_prunes_visual_budget_for_removed_field_only(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
            )
            created = service.create_job({
                "startUrl": self.PERSONAL_1[0],
                "requiredFieldIds": [
                    "personal.surname",
                    "personal.givenNames",
                ],
                "fields": [
                    {
                        "id": "personal.surname",
                        "value": "XIA",
                        "confidence": 1.0,
                    },
                    {
                        "id": "personal.givenNames",
                        "value": "YICHENG",
                        "confidence": 1.0,
                    },
                ],
            })
            reviewed = service.review_job(created["id"], {
                "actor": "visual-budget-sync-test",
                "decisions": [
                    {
                        "fieldId": "personal.surname",
                        "approved": True,
                        "value": "XIA",
                    },
                    {
                        "fieldId": "personal.givenNames",
                        "approved": True,
                        "value": "YICHENG",
                    },
                ],
            })
            surname_key = "ceac-plan-personal1::personal.surname"
            given_key = "ceac-plan-personal1::personal.givenNames"
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.visual_failure_counts = {
                surname_key: 3,
                given_key: 2,
            }
            service.checkpoint_store.save(stored)

            synchronized = service.sync_job(reviewed["id"], {
                "actor": "visual-budget-sync-test",
                "requiredFieldIds": ["personal.givenNames"],
                "fields": [{
                    "id": "personal.givenNames",
                    "value": "YICHENG",
                    "confidence": 1.0,
                }],
                "decisions": [{
                    "fieldId": "personal.givenNames",
                    "approved": True,
                    "value": "YICHENG",
                }],
            })

            self.assertEqual(
                synchronized["visual_failure_counts"],
                {given_key: 2},
            )
            self.assertTrue(any(
                event["kind"]
                == "visual_failure_budgets_pruned_by_sync"
                and event["detail"]["fieldIds"]
                == ["personal.surname"]
                for event in synchronized["events"]
            ))

    def test_long_no_progress_uses_persistent_exponential_yield(self):
        browser = NoProgressBrowser()
        model = WaitOnlyModel()
        field_id = "personal.surname"
        job = AgentJob(
            fields=[confirmed_field(field_id, "XIA")],
            start_url=browser.url,
            required_field_ids=[field_id],
            continuous_run_requested=True,
        )
        agent = ComputerUseAgent(model, browser)

        first = agent.run(job)
        first_retry_after = first.automatic_retry_after
        first_action_count = len(browser.executed)

        self.assertEqual(first.state, JobState.WAITING_HUMAN)
        self.assertEqual(first.automatic_retry_kind, "progress_stall")
        self.assertEqual(first.automatic_retry_count, 1)
        self.assertEqual(first_action_count, 12)
        self.assertEqual(first.completed_field_ids, [])

        second = agent.run(first)

        self.assertEqual(second.state, JobState.WAITING_HUMAN)
        self.assertEqual(second.automatic_retry_kind, "progress_stall")
        self.assertEqual(second.automatic_retry_count, 2)
        self.assertNotEqual(second.automatic_retry_after, first_retry_after)
        self.assertEqual(len(browser.executed), first_action_count * 2)
        self.assertEqual(second.completed_field_ids, [])

    def test_max_steps_yields_and_continues_to_review_in_one_run_intent(self):
        browser = DeterministicPagesBrowser([
            self.PERSONAL_1,
            self.PERSONAL_2,
            self.REVIEW,
        ])
        first_id = "ceac.personal1.001.personal.surname"
        second_id = "ceac.personal2.001.personal.nationality"
        job = AgentJob(
            fields=[
                confirmed_field(first_id, "XIA"),
                confirmed_field(second_id, "CHINA"),
            ],
            start_url=browser.url,
            required_field_ids=[first_id, second_id],
            continuous_run_requested=True,
        )
        model = ModelMustNotRun()
        agent = ComputerUseAgent(model, browser, max_steps=1)

        states = []
        for _ in range(6):
            job = agent.run(job)
            states.append(job.state)
            if job.state == JobState.REVIEW_REQUIRED:
                break

        self.assertEqual(job.state, JobState.REVIEW_REQUIRED)
        self.assertIn(JobState.WAITING_HUMAN, states)
        self.assertEqual(model.calls, 0)
        self.assertEqual(browser.next_clicks, 2)
        self.assertEqual(
            [
                action.field_id
                for action in browser.executed
                if action.kind == ActionKind.TYPE
            ],
            [first_id, second_id],
        )
        self.assertEqual(
            set(job.completed_field_ids),
            {first_id, second_id},
        )
        # The workflow reaches the hard boundary; AgentService is the owner
        # that disarms the durable intent after receiving this result.
        self.assertTrue(job.continuous_run_requested)
        self.assertFalse(job.automatic_retry_pending)

    def test_explicit_ceac_validation_error_is_a_real_human_boundary(self):
        browser = RetainedPageWithValidationError(
            [self.PERSONAL_1, self.REVIEW]
        )
        job = self.completed_personal_job(browser)
        agent = ComputerUseAgent(ModelMustNotRun(), browser)

        result = agent.run(job)

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertFalse(result.automatic_retry_pending)
        self.assertIsNone(result.pending_action)
        self.assertEqual(browser.next_clicks, 1)
        self.assertIn("校验错误", result.human_checkpoint)

    def test_one_service_start_reaches_review_after_delayed_postback(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = DelayedPostbackBrowser(
                [self.PERSONAL_1, self.REVIEW]
            )
            model = ModelMustNotRun()
            agents = []

            def runtime_factory(_job):
                agent = ComputerUseAgent(model, browser)
                agents.append(agent)
                return agent

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=runtime_factory,
            )
            created = service.create_job({
                "startUrl": browser.url,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "XIA",
                    "confidence": 1.0,
                }],
            })
            reviewed = service.review_job(created["id"], {
                "actor": "progress-test",
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "XIA",
                }],
            })
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.completed_field_ids = ["personal.surname"]
            browser.control_values["personal.surname"] = "XIA"
            service.checkpoint_store.save(stored)

            first = service.start_job(reviewed["id"])

            self.assertEqual(first["state"], "waiting_human")
            self.assertEqual(
                first["automatic_retry_kind"],
                "navigation_observation",
            )
            self.assertEqual(first["execution_generation"], 1)
            self.assertEqual(browser.next_clicks, 1)

            browser.release_postback = True
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            service.checkpoint_store.save(stored)
            for _ in range(250):
                current = service.get_job(reviewed["id"])
                if current["state"] == "review_required":
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            # The observation-only watcher fences the now-retired execution
            # generation before publishing Review as an authoritative final
            # boundary. A stale runtime return can therefore never revive it.
            self.assertEqual(current["execution_generation"], 2)
            self.assertTrue(current["final_submission_boundary_reached"])
            self.assertFalse(current["continuous_run_requested"])
            self.assertFalse(current["automatic_retry_pending"])
            self.assertEqual(browser.next_clicks, 1)
            self.assertEqual(len(agents), 1)
            self.assertTrue(any(
                event["kind"] == "auto_resume_terminal_observed"
                for event in current["events"]
            ))
            service._release_runtime(reviewed["id"])

    def test_stable_unknown_page_converts_retry_to_safe_human_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            class UnknownBrowser(MockBrowserDriver):
                def set_visual_status(browser_self, state, message=""):
                    browser_self.visual_status = (state, message)

            browser = UnknownBrowser(
                url=(
                    "https://ceac.state.gov/GenNIV/General/complete/"
                    "unknown_form.aspx?node=Unknown"
                ),
                title="Unknown CEAC Page",
                visible_text="Unknown CEAC Page",
            )
            model = ModelMustNotRun()
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: ComputerUseAgent(
                    model,
                    browser,
                ),
            )
            created = service.create_job({
                "startUrl": browser.url,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "XIA",
                    "confidence": 1.0,
                }],
            })
            reviewed = service.review_job(created["id"], {
                "actor": "progress-test",
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "XIA",
                }],
            })
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.automatic_retry_pending = True
            stored.automatic_retry_kind = "navigation_observation"
            stored.automatic_retry_count = 1
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            service.checkpoint_store.save(stored)

            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=False,
            )
            for _ in range(450):
                current = service.get_job(reviewed["id"])
                if (
                    not current["automatic_retry_pending"]
                    and "页面计划" in str(
                        current.get("human_checkpoint") or ""
                    )
                ):
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "waiting_human")
            self.assertFalse(current["automatic_retry_pending"])
            self.assertTrue(current["continuous_run_requested"])
            self.assertEqual(model.calls, 0)
            self.assertEqual(browser.visual_status[0], "paused")
            self.assertTrue(any(
                event["kind"]
                == "automatic_retry_replaced_by_human_boundary"
                for event in current["events"]
            ))
            service.cancel_job(
                reviewed["id"],
                {"actor": "progress-test-cleanup"},
            )
            service._release_runtime(reviewed["id"])

    def test_nonretryable_block_and_failure_disarm_run_intent(self):
        for terminal_state in (JobState.BLOCKED, JobState.FAILED):
            with self.subTest(state=terminal_state.value):
                with tempfile.TemporaryDirectory() as directory:
                    class HardStopRuntime:
                        def __init__(runtime_self):
                            runtime_self.browser = MockBrowserDriver()
                            runtime_self.checkpoint_store = None

                        def run(runtime_self, job):
                            # Deliberately return without checkpoint_store.save:
                            # AgentService must still normalize custom runtimes.
                            job.state = terminal_state
                            job.automatic_retry_pending = True
                            job.automatic_retry_kind = "provider"
                            job.automatic_retry_count = 3
                            job.automatic_retry_after = (
                                datetime.now(timezone.utc)
                                + timedelta(seconds=30)
                            ).isoformat()
                            return job

                    service = AgentService(
                        AgentConfig(data_dir=Path(directory)),
                        runtime_factory=lambda _job: HardStopRuntime(),
                    )
                    created = service.create_job({
                        "startUrl": self.PERSONAL_1[0],
                        "requiredFieldIds": ["personal.surname"],
                        "fields": [{
                            "id": "personal.surname",
                            "value": "XIA",
                            "confidence": 1.0,
                        }],
                    })
                    reviewed = service.review_job(created["id"], {
                        "actor": "progress-test",
                        "decisions": [{
                            "fieldId": "personal.surname",
                            "approved": True,
                            "value": "XIA",
                        }],
                    })
                    service.open_job(reviewed["id"])

                    result = service.start_job(reviewed["id"])

                    self.assertEqual(
                        result["state"],
                        terminal_state.value,
                    )
                    self.assertFalse(
                        result["continuous_run_requested"]
                    )
                    self.assertFalse(
                        result["automatic_retry_pending"]
                    )
                    self.assertEqual(
                        result["automatic_retry_kind"],
                        "",
                    )
                    with service._auto_resume_lock:
                        self.assertNotIn(
                            reviewed["id"],
                            service._auto_resume_jobs,
                        )
                    service._release_runtime(reviewed["id"])


if __name__ == "__main__":
    unittest.main()
