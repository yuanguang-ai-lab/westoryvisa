import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from visa_agent.config import AgentConfig
from visa_agent.mocks import MockBrowserDriver
from visa_agent.models import (
    ActionKind,
    AgentJob,
    BrowserObservation,
    ComputerAction,
    ExecutionLeaseRevoked,
    ExtractedField,
    JobState,
    job_from_primitive,
    observation_fingerprint,
    to_primitive,
)
from visa_agent.page_plans import PagePlanRegistry
from visa_agent.service import (
    AgentService,
    ServiceError,
    _ExecutionLease,
)
from visa_agent.workflow import ComputerUseAgent


PERSONAL_1_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_personal.aspx?node=Personal1"
)
PERSONAL_2_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_personalcont.aspx?node=Personal2"
)


def confirmed_field(field_id, value, label=""):
    return ExtractedField(
        id=field_id,
        value=value,
        label=label,
        confirmed=True,
    )


class ModelMustNotRun:
    def propose_action(self, *_args):
        raise AssertionError("This invariant must not require a model call")

    def propose_actions(self, *_args):
        raise AssertionError("This invariant must not require a model call")


class RootInvariantRegressionTests(unittest.TestCase):
    class FakeBrowser:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeRuntime:
        def __init__(self):
            self.browser = RootInvariantRegressionTests.FakeBrowser()
            self.checkpoint_store = None

        def run(self, job):
            return job

    @staticmethod
    def create_reviewed_job(
        service,
        fields,
        required_field_ids,
        approved_field_ids=None,
    ):
        approved = set(
            approved_field_ids
            if approved_field_ids is not None
            else (item["id"] for item in fields)
        )
        created = service.create_job({
            "startUrl": PERSONAL_1_URL,
            "requiredFieldIds": list(required_field_ids),
            "fields": list(fields),
        })
        reviewed = service.review_job(created["id"], {
            "actor": "root-invariant-test",
            "decisions": [
                {
                    "fieldId": item["id"],
                    "approved": item["id"] in approved,
                    "value": item["value"],
                }
                for item in fields
            ],
        })
        return reviewed

    @staticmethod
    def sync_payload(fields, required_field_ids):
        return {
            "actor": "root-invariant-sync",
            "autoNext": True,
            "requiredFieldIds": list(required_field_ids),
            "fields": list(fields),
            "decisions": [
                {
                    "fieldId": item["id"],
                    "approved": True,
                    "value": item["value"],
                }
                for item in fields
            ],
        }

    def test_execution_lease_fences_new_effects_during_inflight_effect(self):
        lease = _ExecutionLease("job-lease-race", 9)
        action_started = threading.Event()
        release_action = threading.Event()
        revoke_returned = threading.Event()
        timeline = []

        def side_effect():
            timeline.append("effect-start")
            action_started.set()
            self.assertTrue(release_action.wait(timeout=2))
            timeline.append("effect-end")

        action_thread = threading.Thread(
            target=lambda: lease.run_side_effect(side_effect)
        )

        def revoke():
            lease.revoke()
            timeline.append("revoke-return")
            revoke_returned.set()

        revoke_thread = threading.Thread(target=revoke)
        action_thread.start()
        self.assertTrue(action_started.wait(timeout=1))
        revoke_thread.start()

        # Revocation is linearized with authorization but must not deadlock
        # behind a browser call that may itself be hung. The already-authorized
        # mutation may finish; every mutation authorized after revoke returns
        # must be rejected.
        self.assertTrue(revoke_returned.wait(timeout=0.1))
        self.assertEqual(lease.inflight_side_effects, 1)
        late_effects = []
        with self.assertRaises(ExecutionLeaseRevoked):
            lease.run_side_effect(lambda: late_effects.append("late"))
        self.assertEqual(late_effects, [])

        release_action.set()
        action_thread.join(timeout=1)
        revoke_thread.join(timeout=1)

        self.assertFalse(action_thread.is_alive())
        self.assertFalse(revoke_thread.is_alive())
        self.assertEqual(
            timeline,
            ["effect-start", "revoke-return", "effect-end"],
        )
        self.assertEqual(lease.inflight_side_effects, 0)
        with self.assertRaises(ExecutionLeaseRevoked):
            lease.run_side_effect(lambda: late_effects.append("late"))
        self.assertEqual(late_effects, [])

    def test_manual_hard_boundary_survives_degraded_watcher_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_creations = []

            def factory(_job):
                runtime_creations.append(True)
                return self.FakeRuntime()

            config = AgentConfig(data_dir=Path(directory))
            service = AgentService(config, runtime_factory=factory)
            reviewed = self.create_reviewed_job(
                service,
                fields=[{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "confidence": 0.9,
                }],
                required_field_ids=["personal.surname"],
            )
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.wait_kind = "manual_hard_boundary"
            stored.human_checkpoint = "receipt conflict must remain visible"
            # Simulate a legacy/partially-written checkpoint whose old run
            # intent and retry flags were accidentally left armed.
            stored.continuous_run_requested = True
            stored.sync_resume_pending = True
            stored.automatic_retry_pending = True
            stored.automatic_retry_kind = "browser"
            service.checkpoint_store.save(stored)

            # The centralized load invariant disarms a malformed legacy hard
            # boundary before any watcher/degraded handler gets a chance to
            # reinterpret it.
            normalized = service._load_job(reviewed["id"])
            self.assertEqual(
                normalized.wait_kind,
                "manual_hard_boundary",
            )
            self.assertEqual(
                normalized.human_checkpoint,
                "receipt conflict must remain visible",
            )
            self.assertFalse(normalized.continuous_run_requested)
            self.assertFalse(normalized.sync_resume_pending)
            self.assertFalse(normalized.automatic_retry_pending)

            degraded = service._record_auto_resume_degraded(
                reviewed["id"],
                "injected monitor exception",
            )
            self.assertIsNone(degraded)
            preserved = service._load_job(reviewed["id"])
            self.assertEqual(
                preserved.wait_kind,
                "manual_hard_boundary",
            )
            self.assertEqual(
                preserved.human_checkpoint,
                "receipt conflict must remain visible",
            )

            # Even an accidental explicit arm must observe and retire without
            # opening a runtime or crossing the hard boundary.
            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=False,
            )
            for _ in range(200):
                with service._auto_resume_lock:
                    armed = reviewed["id"] in service._auto_resume_jobs
                    wake_retained = (
                        reviewed["id"] in service._auto_resume_wake_events
                    )
                if not armed and not wake_retained:
                    break
                time.sleep(0.005)
            self.assertFalse(armed)
            self.assertFalse(wake_retained)
            self.assertEqual(runtime_creations, [])

            restarted_creations = []
            restarted = AgentService(
                config,
                runtime_factory=lambda _job: (
                    restarted_creations.append(True)
                    or self.FakeRuntime()
                ),
            )
            self.assertEqual(
                restarted.recover_durable_continuous_runs(),
                [],
            )
            after_restart = restarted.get_job(reviewed["id"])
            self.assertEqual(
                after_restart["wait_kind"],
                "manual_hard_boundary",
            )
            self.assertEqual(
                after_restart["human_checkpoint"],
                "receipt conflict must remain visible",
            )
            self.assertFalse(after_restart["continuous_run_requested"])
            # Watchers and process recovery still cannot cross the boundary,
            # but the consultant's explicit Continue Gemini click is the one
            # authority that may reopen this non-terminal checkpoint.
            resumed = restarted.start_job(reviewed["id"])
            self.assertEqual(restarted_creations, [True])
            self.assertTrue(any(
                event["kind"] == "explicit_manual_boundary_reopened"
                for event in resumed["events"]
            ))

    def test_route_transition_beats_conflicting_next_receipt_ledgers(self):
        class TransitionedBrowser(MockBrowserDriver):
            def __init__(browser_self):
                super().__init__(
                    url=PERSONAL_2_URL,
                    title="Personal Information 2",
                    visible_text="Personal Information 2",
                )

            def observe(browser_self):
                return BrowserObservation(
                    url=browser_self.url,
                    title=browser_self.title,
                    visible_text=browser_self.visible_text,
                    page_id="personal-2",
                    dispatch_receipt_scope="receipt-scope",
                    dispatch_receipts_authoritative=False,
                    dispatch_receipt_conflict=True,
                )

        browser = TransitionedBrowser()
        pending = ComputerAction(
            kind=ActionKind.CLICK,
            target_hint="Next: Personal 2",
            reason="Deterministic fixed CEAC Next control",
            dispatch_receipt_required=True,
            dispatch_receipt_scope="receipt-scope",
            id="pending-next-with-conflicting-ledgers",
        )
        job = AgentJob(
            fields=[confirmed_field("personal.surname", "ZHANG")],
            start_url=PERSONAL_1_URL,
            required_field_ids=["personal.surname"],
            completed_field_ids=["personal.surname"],
            pending_action=pending,
            current_page_plan_id="personal-information",
            last_safe_url=PERSONAL_1_URL,
            continuous_run_requested=True,
            auto_next=False,
        )

        result = ComputerUseAgent(ModelMustNotRun(), browser).run(job)

        self.assertIsNone(result.pending_action)
        self.assertIn(pending.id, result.applied_action_ids)
        self.assertEqual(browser.executed, [])
        self.assertFalse(any(
            event.kind == "dispatch_receipt_conflict"
            for event in result.events
        ))
        self.assertTrue(any(
            event.kind == "page_navigation_recovered"
            for event in result.events
        ))

    def test_pending_next_sync_tracks_new_and_first_confirmed_source_fields(
        self,
    ):
        surname = {
            "id": "personal.surname",
            "value": "ZHANG",
            "confidence": 0.9,
        }
        given_names = {
            "id": "personal.givenNames",
            "value": "SAN",
            "confidence": 0.9,
        }
        for existing_unconfirmed in (False, True):
            with self.subTest(existingUnconfirmed=existing_unconfirmed):
                with tempfile.TemporaryDirectory() as directory:
                    service = AgentService(
                        AgentConfig(data_dir=Path(directory)),
                        runtime_factory=lambda _job: self.FakeRuntime(),
                    )
                    initial_fields = [surname]
                    approved = ["personal.surname"]
                    if existing_unconfirmed:
                        initial_fields.append(given_names)
                    reviewed = self.create_reviewed_job(
                        service,
                        fields=initial_fields,
                        required_field_ids=["personal.surname"],
                        approved_field_ids=approved,
                    )
                    stored = service.checkpoint_store.load_job(
                        reviewed["id"]
                    )
                    stored.current_page_plan_id = "personal-information"
                    stored.last_safe_url = PERSONAL_1_URL
                    stored.pending_action = ComputerAction(
                        kind=ActionKind.CLICK,
                        target_hint="Next: Personal 2",
                        reason="Deterministic fixed CEAC Next control",
                        id="pending-next-before-new-field",
                    )
                    service.checkpoint_store.save(stored)

                    synchronized = service.sync_job(
                        reviewed["id"],
                        self.sync_payload(
                            [surname, given_names],
                            [
                                "personal.surname",
                                "personal.givenNames",
                            ],
                        ),
                    )

                    self.assertEqual(
                        synchronized["pending_action"]["id"],
                        "pending-next-before-new-field",
                    )
                    self.assertIn(
                        "personal.givenNames",
                        synchronized["sync_reconciliation_field_ids"],
                    )
                    self.assertEqual(
                        synchronized[
                            "sync_reconciliation_page_plan_by_field"
                        ]["personal.givenNames"],
                        "ceac-plan-personal1",
                    )

    def test_removed_completed_field_hard_stops_before_next(self):
        surname = {
            "id": "personal.surname",
            "value": "ZHANG",
            "confidence": 0.9,
        }
        given_names = {
            "id": "personal.givenNames",
            "value": "SAN",
            "confidence": 0.9,
        }
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            reviewed = self.create_reviewed_job(
                service,
                fields=[surname, given_names],
                required_field_ids=["personal.surname"],
            )
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.completed_field_ids = [
                "personal.surname",
                "personal.givenNames",
            ]
            stored.current_page_plan_id = "personal-information"
            stored.last_safe_url = PERSONAL_1_URL
            stored.pending_action = ComputerAction(
                kind=ActionKind.CLICK,
                target_hint="Next: Personal 2",
                reason="Deterministic fixed CEAC Next control",
                dispatch_receipt_required=True,
                dispatch_receipt_scope="source-page-receipt",
                id="pending-next-before-removal",
            )
            service.checkpoint_store.save(stored)
            service.sync_job(
                reviewed["id"],
                self.sync_payload(
                    [surname],
                    ["personal.surname"],
                ),
            )
            synchronized = service.checkpoint_store.load_job(reviewed["id"])

            class SourcePageBrowser(MockBrowserDriver):
                def __init__(browser_self):
                    super().__init__(
                        url=PERSONAL_1_URL,
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                    )

                def observe(browser_self):
                    return BrowserObservation(
                        url=browser_self.url,
                        title=browser_self.title,
                        visible_text=browser_self.visible_text,
                        page_id="personal-1",
                        dispatch_receipt_scope="source-page-receipt",
                        dispatch_receipts_authoritative=True,
                        dispatch_receipt_conflict=False,
                        dispatched_action_ids=[],
                    )

            browser = SourcePageBrowser()
            result = ComputerUseAgent(
                ModelMustNotRun(),
                browser,
            ).run(synchronized)

            self.assertEqual(result.state, JobState.WAITING_HUMAN)
            self.assertEqual(result.wait_kind, "manual_hard_boundary")
            self.assertFalse(result.continuous_run_requested)
            self.assertIsNone(result.pending_action)
            self.assertIn(
                "personal.givenNames",
                result.sync_reconciliation_field_ids,
            )
            self.assertIn("删除或取消确认", result.human_checkpoint)
            self.assertEqual(browser.executed, [])

    def test_sync_tracks_dirty_fields_on_each_owning_page(self):
        surname = {
            "id": "personal.surname",
            "value": "ZHANG",
            "confidence": 0.9,
        }
        issuance = {
            "id": "passport.issuance",
            "value": "2024-07-11",
            "confidence": 0.9,
        }
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            reviewed = self.create_reviewed_job(
                service,
                fields=[surname, issuance],
                required_field_ids=[
                    "personal.surname",
                    "passport.issuance",
                ],
            )
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.completed_field_ids = [
                "personal.surname",
                "passport.issuance",
            ]
            stored.completed_field_page_plan_by_id = {
                "personal.surname": "personal-information",
                "passport.issuance": "passport-information",
            }
            stored.visited_page_plan_ids = [
                "personal-information",
                "passport-information",
            ]
            service.checkpoint_store.save(stored)

            synchronized = service.sync_job(
                reviewed["id"],
                self.sync_payload(
                    [
                        {**surname, "value": "LI"},
                        {**issuance, "value": "2025-01-02"},
                    ],
                    [
                        "personal.surname",
                        "passport.issuance",
                    ],
                ),
            )

            self.assertCountEqual(
                synchronized["sync_reconciliation_field_ids"],
                ["personal.surname", "passport.issuance"],
            )
            self.assertEqual(
                synchronized["sync_reconciliation_page_plan_by_field"],
                {
                    "personal.surname": "ceac-plan-personal1",
                    "passport.issuance": "ceac-plan-passport",
                },
            )
            self.assertEqual(
                synchronized["sync_reconciliation_page_plan_id"],
                "",
            )

    def test_visible_text_only_change_changes_boundary_fingerprint(self):
        job = AgentJob(
            id="agent-job-visible-text-boundary",
            fields=[confirmed_field("personal.surname", "ZHANG")],
            start_url=PERSONAL_1_URL,
        )
        before = BrowserObservation(
            url=PERSONAL_1_URL,
            title="Personal Information 1",
            visible_text="CAPTCHA challenge visible",
            page_id="personal-1",
            control_values={"personal.surname": "ZHANG"},
        )
        after = BrowserObservation(
            url=PERSONAL_1_URL,
            title="Personal Information 1",
            visible_text="CAPTCHA challenge cleared",
            page_id="personal-1",
            control_values={"personal.surname": "ZHANG"},
        )

        self.assertNotEqual(
            observation_fingerprint(job, before),
            observation_fingerprint(job, after),
        )

    def test_all_terminal_workflow_helpers_discard_pending_without_applying(
        self,
    ):
        agent = ComputerUseAgent(
            ModelMustNotRun(),
            MockBrowserDriver(),
        )
        helpers = (
            ("review_required", agent._review_required, JobState.REVIEW_REQUIRED),
            ("blocked", agent._block, JobState.BLOCKED),
            ("failed", agent._fail, JobState.FAILED),
        )
        for terminal_kind, helper, expected_state in helpers:
            with self.subTest(terminalKind=terminal_kind):
                pending = ComputerAction(
                    kind=ActionKind.TYPE,
                    field_id="personal.surname",
                    value="ZHANG",
                    id=f"pending-before-{terminal_kind}",
                )
                job = AgentJob(
                    fields=[confirmed_field("personal.surname", "ZHANG")],
                    start_url=PERSONAL_1_URL,
                    pending_action=pending,
                    continuous_run_requested=True,
                )

                result = helper(job, f"{terminal_kind} reason")

                self.assertEqual(result.state, expected_state)
                self.assertIsNone(result.pending_action)
                self.assertNotIn(pending.id, result.applied_action_ids)
                discarded = [
                    event
                    for event in result.events
                    if event.kind == "terminal_pending_action_discarded"
                ]
                self.assertEqual(len(discarded), 1)
                self.assertEqual(
                    discarded[0].detail["actionId"],
                    pending.id,
                )

    def test_live_retired_runtime_blocks_replacement_until_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            replacement_factories = []
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: (
                    replacement_factories.append(True)
                    or self.FakeRuntime()
                ),
            )
            reviewed = self.create_reviewed_job(
                service,
                fields=[{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "confidence": 0.9,
                }],
                required_field_ids=["personal.surname"],
            )
            stored = service.checkpoint_store.load_job(reviewed["id"])
            release_stale = threading.Event()
            stale_thread = threading.Thread(
                target=release_stale.wait,
                daemon=True,
            )
            stale_thread.start()

            class StaleRuntime:
                def __init__(runtime_self):
                    runtime_self._thread = stale_thread
                    runtime_self._job = stored
                    runtime_self._execution_lease = None
                    runtime_self.closed = False

                @property
                def is_available(runtime_self):
                    return False

                @property
                def is_alive(runtime_self):
                    return runtime_self._thread.is_alive()

                def close(runtime_self, purge_profile=False):
                    runtime_self.closed = True

            stale = StaleRuntime()
            with service._runtime_lock:
                service._runtimes[reviewed["id"]] = stale

            with self.assertRaisesRegex(
                RuntimeError,
                "still retiring",
            ):
                service._ensure_auto_resume_runtime(
                    reviewed["id"],
                    stored,
                )
            self.assertTrue(stale.closed)
            self.assertEqual(replacement_factories, [])

            release_stale.set()
            stale_thread.join(timeout=1)
            for _ in range(200):
                if not service._job_has_retired_runtime(reviewed["id"]):
                    break
                time.sleep(0.005)
            self.assertFalse(
                service._job_has_retired_runtime(reviewed["id"])
            )

            replacement = service._ensure_auto_resume_runtime(
                reviewed["id"],
                stored,
            )
            self.assertTrue(replacement.is_available)
            self.assertEqual(replacement_factories, [True])
            service._release_runtime(reviewed["id"])

    def test_watcher_exit_reclaims_its_wake_event(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_creations = []
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: (
                    runtime_creations.append(True)
                    or self.FakeRuntime()
                ),
            )
            reviewed = self.create_reviewed_job(
                service,
                fields=[{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "confidence": 0.9,
                }],
                required_field_ids=["personal.surname"],
            )
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = False
            service.checkpoint_store.save(stored)

            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=False,
            )
            for _ in range(200):
                with service._auto_resume_lock:
                    armed = reviewed["id"] in service._auto_resume_jobs
                    wake_retained = (
                        reviewed["id"] in service._auto_resume_wake_events
                    )
                if not armed and not wake_retained:
                    break
                time.sleep(0.005)

            self.assertFalse(armed)
            self.assertFalse(wake_retained)
            self.assertEqual(runtime_creations, [])

    def test_explicit_release_waits_until_watcher_is_quiescent(self):
        watcher_load_entered = threading.Event()
        allow_watcher_load = threading.Event()
        release_join_started = threading.Event()
        release_returned = threading.Event()
        release_errors = []

        class ObservableBrowser:
            def __init__(browser_self):
                browser_self.closed = threading.Event()

            def close(browser_self):
                browser_self.closed.set()

        class ObservableRuntime:
            def __init__(runtime_self):
                runtime_self.browser = ObservableBrowser()
                runtime_self.checkpoint_store = None

            def run(runtime_self, job):
                return job

        class JoinProbeService(AgentService):
            def _wait_for_auto_resume_watcher_exit(
                service_self,
                job_id,
                ready_event=None,
            ):
                release_join_started.set()
                return super()._wait_for_auto_resume_watcher_exit(
                    job_id,
                    ready_event=ready_event,
                )

        with tempfile.TemporaryDirectory() as directory:
            created_runtimes = []

            def factory(_job):
                runtime = ObservableRuntime()
                created_runtimes.append(runtime)
                return runtime

            service = JoinProbeService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=factory,
            )
            reviewed = self.create_reviewed_job(
                service,
                fields=[{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "confidence": 0.9,
                }],
                required_field_ids=["personal.surname"],
            )
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.wait_kind = "manual_page_change"
            stored.continuous_run_requested = True
            service.checkpoint_store.save(stored)

            original_load = service._load_job
            intercepted = threading.Event()

            def controlled_load(job_id):
                loaded = original_load(job_id)
                if (
                    threading.current_thread().name.startswith(
                        "agent-auto-resume-"
                    )
                    and not intercepted.is_set()
                ):
                    intercepted.set()
                    watcher_load_entered.set()
                    if not allow_watcher_load.wait(timeout=2):
                        raise AssertionError(
                            "test did not release watcher load barrier"
                        )
                return loaded

            service._load_job = controlled_load
            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=True,
            )
            self.assertTrue(watcher_load_entered.wait(timeout=1))

            def release():
                try:
                    service._release_runtime(reviewed["id"])
                except Exception as error:
                    release_errors.append(error)
                finally:
                    release_returned.set()

            release_thread = threading.Thread(target=release)
            release_thread.start()
            self.assertTrue(release_join_started.wait(timeout=1))
            self.assertFalse(release_returned.is_set())

            allow_watcher_load.set()
            self.assertTrue(release_returned.wait(timeout=2))
            release_thread.join(timeout=1)
            self.assertFalse(release_thread.is_alive())
            self.assertEqual(release_errors, [])
            self.assertEqual(len(created_runtimes), 1)
            self.assertTrue(created_runtimes[0].browser.closed.is_set())

            job_key = str(reviewed["id"])
            with service._auto_resume_lock:
                self.assertNotIn(job_key, service._auto_resume_jobs)
                self.assertNotIn(job_key, service._auto_resume_threads)
                self.assertNotIn(
                    job_key,
                    service._auto_resume_stop_events,
                )
                self.assertNotIn(
                    job_key,
                    service._auto_resume_thread_ready_events,
                )
                self.assertNotIn(
                    job_key,
                    service._auto_resume_pending_rearms,
                )
                self.assertNotIn(
                    job_key,
                    service._auto_resume_wake_events,
                )
            with service._runtime_lock:
                self.assertNotIn(reviewed["id"], service._runtimes)
            released = service.checkpoint_store.load_job(reviewed["id"])
            self.assertFalse(released.continuous_run_requested)
            self.assertFalse(released.sync_resume_pending)

    def test_arm_during_active_watcher_is_handed_to_successor(self):
        first_watcher_load_entered = threading.Event()
        release_first_watcher_load = threading.Event()
        run_entered = threading.Event()

        class ApprovedBrowser:
            def __init__(browser_self):
                browser_self.closed = threading.Event()

            def observe_lightweight(browser_self):
                return BrowserObservation(
                    url=PERSONAL_1_URL,
                    title="Personal Information 1",
                    visible_text="Personal Information 1",
                    page_id="personal-1",
                )

            def close(browser_self):
                browser_self.closed.set()

        class ApprovedRuntime:
            def __init__(runtime_self):
                runtime_self.browser = ApprovedBrowser()
                runtime_self.page_plans = PagePlanRegistry.default()
                runtime_self.policy = None
                runtime_self.checkpoint_store = None

            def run(runtime_self, job):
                run_entered.set()
                job.state = JobState.REVIEW_REQUIRED
                job.final_submission_boundary_reached = True
                job.continuous_run_requested = False
                return job

        with tempfile.TemporaryDirectory() as directory:
            created_runtimes = []

            def factory(_job):
                runtime = ApprovedRuntime()
                created_runtimes.append(runtime)
                return runtime

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=factory,
            )
            reviewed = self.create_reviewed_job(
                service,
                fields=[{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "confidence": 0.9,
                }],
                required_field_ids=["personal.surname"],
            )
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.wait_kind = ""
            stored.continuous_run_requested = True
            service.checkpoint_store.save(stored)

            original_load = service._load_job
            intercepted = threading.Event()

            def controlled_load(job_id):
                loaded = original_load(job_id)
                if (
                    threading.current_thread().name.startswith(
                        "agent-auto-resume-"
                    )
                    and not intercepted.is_set()
                ):
                    intercepted.set()
                    first_watcher_load_entered.set()
                    if not release_first_watcher_load.wait(timeout=2):
                        raise AssertionError(
                            "test did not release first watcher barrier"
                        )
                    stale = job_from_primitive(to_primitive(loaded))
                    stale.continuous_run_requested = False
                    return stale
                return loaded

            service._load_job = controlled_load
            try:
                service._arm_continuous_resume(
                    reviewed["id"],
                    require_page_change=True,
                )
                self.assertTrue(
                    first_watcher_load_entered.wait(timeout=1)
                )

                # This request arrives after the active watcher already took
                # its loop snapshot. It must be consumed by that watcher or
                # handed to exactly one successor during teardown.
                service._arm_continuous_resume(
                    reviewed["id"],
                    require_page_change=False,
                )
                release_first_watcher_load.set()
                self.assertTrue(run_entered.wait(timeout=2))
            finally:
                release_first_watcher_load.set()
                service._release_runtime(reviewed["id"])

            self.assertEqual(len(created_runtimes), 1)
            self.assertTrue(created_runtimes[0].browser.closed.is_set())
            job_key = str(reviewed["id"])
            with service._auto_resume_lock:
                self.assertNotIn(job_key, service._auto_resume_jobs)
                self.assertNotIn(job_key, service._auto_resume_threads)
                self.assertNotIn(
                    job_key,
                    service._auto_resume_pending_rearms,
                )
                self.assertNotIn(
                    job_key,
                    service._auto_resume_wake_events,
                )
            released = service.checkpoint_store.load_job(reviewed["id"])
            self.assertFalse(released.continuous_run_requested)

    def test_watcher_start_failure_rolls_back_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            reviewed = self.create_reviewed_job(
                service,
                fields=[{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "confidence": 0.9,
                }],
                required_field_ids=["personal.surname"],
            )
            job_key = str(reviewed["id"])

            with mock.patch(
                "visa_agent.service.threading.Thread.start",
                side_effect=RuntimeError("injected watcher start failure"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "injected watcher start failure",
                ):
                    service._arm_continuous_resume(
                        reviewed["id"],
                        require_page_change=False,
                    )

            with service._auto_resume_lock:
                self.assertNotIn(job_key, service._auto_resume_jobs)
                self.assertNotIn(job_key, service._auto_resume_threads)
                self.assertNotIn(
                    job_key,
                    service._auto_resume_stop_events,
                )
                self.assertNotIn(
                    job_key,
                    service._auto_resume_thread_ready_events,
                )
                self.assertNotIn(
                    job_key,
                    service._auto_resume_pending_rearms,
                )
                self.assertNotIn(
                    job_key,
                    service._auto_resume_wake_events,
                )

    def test_start_job_rejects_stale_watcher_epoch_before_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_creations = []
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: (
                    runtime_creations.append(True)
                    or self.FakeRuntime()
                ),
            )
            reviewed = self.create_reviewed_job(
                service,
                fields=[{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "confidence": 0.9,
                }],
                required_field_ids=["personal.surname"],
            )
            before_sync = service.checkpoint_store.load_job(reviewed["id"])
            stale_epoch = service._watcher_epoch(before_sync)
            before_sync.execution_generation += 1
            before_sync.record(
                "test_generation_advanced",
                "Simulate a sync racing a watcher observation",
            )
            service.checkpoint_store.save(before_sync)

            with self.assertRaisesRegex(
                ServiceError,
                "observation is stale",
            ) as raised:
                service.start_job(
                    reviewed["id"],
                    expected_watcher_epoch=stale_epoch,
                )

            self.assertEqual(raised.exception.status, 409)
            self.assertEqual(runtime_creations, [])
            latest = service.checkpoint_store.load_job(reviewed["id"])
            self.assertEqual(
                latest.execution_generation,
                before_sync.execution_generation,
            )
            self.assertFalse(latest.continuous_run_requested)


if __name__ == "__main__":
    unittest.main()
