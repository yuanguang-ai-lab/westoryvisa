import tempfile
import threading
import time
import unittest
from pathlib import Path

from visa_agent.config import AgentConfig
from visa_agent.models import (
    ActionKind,
    AgentJob,
    BrowserObservation,
    ComputerAction,
    JobState,
)
from visa_agent.service import (
    AgentService,
    ServiceError,
    _RuntimeBusy,
    _RuntimeWorker,
)
from visa_agent.workflow import ComputerUseAgent


PERSONAL_1_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_personal.aspx?node=Personal1"
)


class LifecycleAndProvenanceInvariantTests(unittest.TestCase):
    class FakeBrowser:
        def __init__(self):
            self.closed = False
            self.profile_purge_requested = False

        def close(self):
            self.closed = True

        def purge_profile_on_close(self):
            self.profile_purge_requested = True

    class FakeRuntime:
        def __init__(self):
            self.browser = (
                LifecycleAndProvenanceInvariantTests.FakeBrowser()
            )
            self.checkpoint_store = None

        def run(self, job):
            return job

    @staticmethod
    def create_reviewed_job(
        service,
        fields,
        required_field_ids,
        actor="root-invariant-test",
    ):
        created = service.create_job({
            "startUrl": PERSONAL_1_URL,
            "requiredFieldIds": list(required_field_ids),
            "fields": list(fields),
        })
        return service.review_job(created["id"], {
            "actor": actor,
            "decisions": [
                {
                    "fieldId": item["id"],
                    "approved": True,
                    "value": item["value"],
                }
                for item in fields
            ],
        })

    @staticmethod
    def sync_payload(
        fields,
        required_field_ids,
        actor="root-invariant-test",
    ):
        return {
            "actor": actor,
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

    def test_terminal_checkpoint_wins_over_late_runtime_return(self):
        for terminal_kind in ("cancelled", "runtime_failure"):
            with self.subTest(terminalKind=terminal_kind):
                with tempfile.TemporaryDirectory() as directory:
                    run_started = threading.Event()
                    release_runtime_return = threading.Event()
                    runtimes = []

                    class LateReturningRuntime:
                        def __init__(runtime_self):
                            runtime_self.browser = self.FakeBrowser()
                            runtime_self.checkpoint_store = None

                        def run(runtime_self, job):
                            run_started.set()
                            release_runtime_return.wait(timeout=2)
                            # Deliberately return a stale, runnable object after
                            # a terminal checkpoint has already been committed.
                            job.state = JobState.WAITING_HUMAN
                            job.wait_kind = "runtime_recovery"
                            job.continuous_run_requested = True
                            job.human_checkpoint = "stale runtime return"
                            return job

                    def factory(_job):
                        runtime = LateReturningRuntime()
                        runtimes.append(runtime)
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
                    outcomes = {}

                    def start():
                        try:
                            outcomes["start"] = service.start_job(
                                reviewed["id"]
                            )
                        except Exception as error:
                            outcomes["start_error"] = error

                    start_thread = threading.Thread(target=start)
                    start_thread.start()
                    self.assertTrue(run_started.wait(timeout=1))

                    if terminal_kind == "cancelled":
                        def cancel():
                            outcomes["terminal"] = service.cancel_job(
                                reviewed["id"],
                                {"actor": "root-invariant-cancel"},
                            )

                        terminal_thread = threading.Thread(target=cancel)
                        terminal_thread.start()
                        for _ in range(200):
                            durable = service.checkpoint_store.load_job(
                                reviewed["id"]
                            )
                            if durable.state == JobState.CANCELLED:
                                break
                            time.sleep(0.005)
                        self.assertEqual(
                            durable.state,
                            JobState.CANCELLED,
                        )
                    else:
                        terminal_thread = None
                        terminal = service._record_terminal_runtime_failure(
                            reviewed["id"],
                            ValueError("injected terminal failure"),
                            source="root_invariant_test",
                        )
                        outcomes["terminal"] = terminal
                        durable = service.checkpoint_store.load_job(
                            reviewed["id"]
                        )
                        self.assertEqual(durable.state, JobState.FAILED)

                    # The runtime result arrives only after the terminal state
                    # is durable, exercising the final CAS/commit window rather
                    # than only a late checkpoint_store.save call.
                    release_runtime_return.set()
                    start_thread.join(timeout=2)
                    if terminal_thread is not None:
                        terminal_thread.join(timeout=2)

                    self.assertFalse(start_thread.is_alive())
                    if terminal_thread is not None:
                        self.assertFalse(terminal_thread.is_alive())
                    self.assertNotIn("start_error", outcomes)
                    stored = service.checkpoint_store.load_job(
                        reviewed["id"]
                    )
                    expected = (
                        JobState.CANCELLED
                        if terminal_kind == "cancelled"
                        else JobState.FAILED
                    )
                    self.assertEqual(stored.state, expected)
                    self.assertFalse(stored.continuous_run_requested)
                    self.assertNotEqual(
                        stored.human_checkpoint,
                        "stale runtime return",
                    )
                    self.assertEqual(
                        outcomes["start"]["state"],
                        expected.value,
                    )
                    service._release_runtime(
                        reviewed["id"],
                        purge_profile=True,
                    )

    def test_retiring_tombstone_blocks_replacement_and_upgrades_purge(self):
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
            release_worker = threading.Event()
            release_close = threading.Event()
            close_started = threading.Event()
            stale_thread = threading.Thread(
                target=release_worker.wait,
                daemon=True,
            )
            stale_thread.start()

            class PurgeAwareBrowser:
                def __init__(browser_self):
                    browser_self.purge_requests = 0

                def purge_profile_on_close(browser_self):
                    browser_self.purge_requests += 1

            class RuntimePayload:
                def __init__(runtime_self, browser):
                    runtime_self.browser = browser

            class RetiringRuntime:
                def __init__(runtime_self):
                    runtime_self._job = stored
                    runtime_self._thread = stale_thread
                    runtime_self._execution_lease = None
                    runtime_self.browser = PurgeAwareBrowser()
                    runtime_self._runtime = RuntimePayload(
                        runtime_self.browser
                    )
                    runtime_self.close_purge_arguments = []

                @property
                def is_available(runtime_self):
                    return False

                @property
                def is_alive(runtime_self):
                    return runtime_self._thread.is_alive()

                def close(runtime_self, purge_profile=False):
                    runtime_self.close_purge_arguments.append(
                        bool(purge_profile)
                    )
                    close_started.set()
                    release_close.wait(timeout=2)

            retiring = RetiringRuntime()
            with service._runtime_lock:
                service._runtimes[reviewed["id"]] = retiring

            # Status detection removes the unusable runtime but atomically
            # leaves a job/profile tombstone before asynchronous close begins.
            self.assertFalse(
                service._runtime_is_open(
                    reviewed["id"],
                    purge_if_stale=False,
                )
            )
            self.assertTrue(close_started.wait(timeout=1))
            self.assertTrue(
                service._job_has_retired_runtime(reviewed["id"])
            )
            with self.assertRaisesRegex(RuntimeError, "still retiring"):
                service._ensure_auto_resume_runtime(
                    reviewed["id"],
                    stored,
                )
            self.assertEqual(replacement_factories, [])

            # A later cancellation/final cleanup must upgrade the already
            # retiring worker to purge its exact private profile.
            service._release_runtime(
                reviewed["id"],
                purge_profile=True,
            )
            self.assertGreaterEqual(
                retiring.browser.purge_requests,
                1,
            )
            with service._runtime_lock:
                self.assertIn(
                    reviewed["id"],
                    service._retiring_runtime_purge_jobs,
                )

            release_close.set()
            for _ in range(200):
                with service._retired_runtime_lock:
                    tracked = (
                        retiring in service._retired_runtime_workers
                    )
                if tracked:
                    break
                time.sleep(0.005)
            self.assertTrue(tracked)

            # close() returning is insufficient: the underlying worker/profile
            # still lives, so replacement remains forbidden.
            with self.assertRaisesRegex(RuntimeError, "still retiring"):
                service._ensure_auto_resume_runtime(
                    reviewed["id"],
                    stored,
                )
            self.assertEqual(replacement_factories, [])

            release_worker.set()
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
            service._release_runtime(
                reviewed["id"],
                purge_profile=True,
            )

    def test_factory_failure_close_hang_keeps_tombstone_until_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            factory_calls = []
            close_started = threading.Event()
            release_close = threading.Event()
            close_finished = threading.Event()

            class PublishedBrowser:
                def close(browser_self):
                    close_started.set()
                    self.assertTrue(release_close.wait(timeout=2))
                    close_finished.set()

            def factory(_job, startup_control):
                factory_calls.append(len(factory_calls) + 1)
                if len(factory_calls) == 1:
                    startup_control.publish_browser(PublishedBrowser())
                    raise ValueError(
                        "factory failed after publishing browser"
                    )
                return self.FakeRuntime()

            factory._docflow_accepts_startup_control = True
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

            with self.assertRaisesRegex(
                ValueError,
                "after publishing browser",
            ):
                service._ensure_auto_resume_runtime(
                    reviewed["id"],
                    stored,
                )

            self.assertTrue(close_started.wait(timeout=1))
            self.assertTrue(
                service._job_has_retired_runtime(reviewed["id"])
            )
            with self.assertRaisesRegex(RuntimeError, "still retiring"):
                service._ensure_auto_resume_runtime(
                    reviewed["id"],
                    stored,
                )
            self.assertEqual(factory_calls, [1])
            with service._runtime_lock:
                self.assertNotIn(reviewed["id"], service._runtimes)

            release_close.set()
            self.assertTrue(close_finished.wait(timeout=1))
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
            self.assertEqual(factory_calls, [1, 2])
            service._release_runtime(
                reviewed["id"],
                purge_profile=True,
            )

    def test_sync_reconciles_new_visited_personal_field_not_future_page(self):
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
        future_passport_issuance = {
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
                fields=[surname],
                required_field_ids=["personal.surname"],
            )
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.visited_page_plan_ids = [
                "personal-information",
                "travel-information",
            ]
            stored.current_page_plan_id = "travel-information"
            service.checkpoint_store.save(stored)

            synchronized = service.sync_job(
                reviewed["id"],
                self.sync_payload(
                    [
                        surname,
                        given_names,
                        future_passport_issuance,
                    ],
                    [
                        "personal.surname",
                        "personal.givenNames",
                        "passport.issuance",
                    ],
                ),
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
            self.assertNotIn(
                "passport.issuance",
                synchronized["sync_reconciliation_field_ids"],
            )
            self.assertNotIn(
                "passport.issuance",
                synchronized[
                    "sync_reconciliation_page_plan_by_field"
                ],
            )

    def test_sync_normalizes_legacy_personal_page_alias_for_new_field(self):
        surname = {
            "id": "personal.surname",
            "value": "ZHANG",
            "confidence": 0.9,
        }
        dynamic_given_names = {
            "id": "ceac.personal1.given_names",
            "value": "SAN",
            "label": "Given Names [control=text]",
            "confidence": 0.9,
        }
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            reviewed = self.create_reviewed_job(
                service,
                fields=[surname],
                required_field_ids=["personal.surname"],
            )
            stored = service.checkpoint_store.load_job(reviewed["id"])
            # This is an intentionally broad-only pre-upgrade checkpoint.
            # Sync must migrate both route history and verified provenance to
            # the exact live plan before it targets a newly confirmed field.
            stored.completed_field_ids = ["personal.surname"]
            stored.completed_field_page_plan_by_id = {
                "personal.surname": "personal-information",
            }
            stored.visited_page_plan_ids = [
                "personal-information",
                "ceac-plan-passport",
            ]
            stored.current_page_plan_id = "ceac-plan-passport"
            service.checkpoint_store.save(stored)

            synchronized = service.sync_job(
                reviewed["id"],
                self.sync_payload(
                    [surname, dynamic_given_names],
                    [
                        "personal.surname",
                        "ceac.personal1.given_names",
                    ],
                ),
            )

            self.assertIn(
                "ceac.personal1.given_names",
                synchronized["sync_reconciliation_field_ids"],
            )
            self.assertEqual(
                synchronized[
                    "sync_reconciliation_page_plan_by_field"
                ]["ceac.personal1.given_names"],
                "ceac-plan-personal1",
            )
            self.assertEqual(
                synchronized["completed_field_page_plan_by_id"],
                {"personal.surname": "ceac-plan-personal1"},
            )
            self.assertIn(
                "ceac-plan-personal1",
                synchronized["visited_page_plan_ids"],
            )
            self.assertNotIn(
                "personal-information",
                synchronized["visited_page_plan_ids"],
            )

            class ExactPersonalBrowser:
                def __init__(browser_self):
                    browser_self.values = {
                        "personal.surname": "ZHANG",
                    }
                    browser_self.acknowledged = []
                    browser_self.planned = []
                    browser_self.executed = []

                def observe(browser_self):
                    return BrowserObservation(
                        url=PERSONAL_1_URL,
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="exact-personal1",
                        control_values=dict(browser_self.values),
                        acknowledged_action_ids=list(
                            browser_self.acknowledged
                        ),
                    )

                def observe_lightweight(browser_self):
                    return browser_self.observe()

                def observe_action(browser_self, _action):
                    return browser_self.observe()

                def plan_fields(
                    browser_self,
                    field_ids,
                    _field_labels,
                    _control_hints,
                ):
                    browser_self.planned.append(tuple(field_ids))
                    return [
                        ComputerAction(
                            kind=ActionKind.TYPE,
                            field_id=field_id,
                            target_hint=field_id,
                            reason="Exact Personal1 control",
                        )
                        for field_id in field_ids
                    ], []

                def execute(browser_self, action):
                    browser_self.executed.append(action.field_id)
                    browser_self.values[action.field_id] = action.value
                    browser_self.acknowledged.append(action.id)

            class ModelMustNotRun:
                def propose_actions(model_self, *_args, **_kwargs):
                    raise AssertionError(
                        "Exact Personal1 reconciliation called the model"
                    )

                def propose_action(model_self, *_args, **_kwargs):
                    raise AssertionError(
                        "Exact Personal1 reconciliation called the model"
                    )

            synchronized_job = service.checkpoint_store.load_job(
                reviewed["id"]
            )
            synchronized_job.auto_next = False
            browser = ExactPersonalBrowser()

            reconciled = ComputerUseAgent(
                ModelMustNotRun(),
                browser,
                max_steps=10,
            ).run(synchronized_job)

            self.assertEqual(reconciled.state, JobState.WAITING_HUMAN)
            self.assertEqual(
                browser.planned,
                [("ceac.personal1.given_names",)],
            )
            self.assertEqual(
                browser.executed,
                ["ceac.personal1.given_names"],
            )
            self.assertEqual(
                reconciled.sync_reconciliation_field_ids,
                [],
            )
            self.assertEqual(
                reconciled.sync_reconciliation_page_plan_by_field,
                {},
            )
            self.assertEqual(
                reconciled.completed_field_page_plan_by_id[
                    "ceac.personal1.given_names"
                ],
                "ceac-plan-personal1",
            )

    def test_sync_migrates_legacy_completion_and_page_provenance(self):
        surname = {
            "id": "personal.surname",
            "value": "ZHANG",
            "confidence": 0.9,
        }
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            reviewed = self.create_reviewed_job(
                service,
                fields=[surname],
                required_field_ids=["personal.surname"],
            )
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.completed_field_ids = ["personal.surname"]
            stored.completed_field_page_plan_by_id = {}
            stored.visited_page_plan_ids = []
            stored.current_page_plan_id = "passport-information"
            stored.record(
                "action_started",
                "legacy action start",
                actionId="legacy-surname-action",
                fieldId="personal.surname",
                pagePlanId="personal-information",
            )
            stored.record(
                "action_verified",
                "legacy action verified",
                actionId="legacy-surname-action",
                fieldId="personal.surname",
            )
            stored.record(
                "page_navigation_started",
                "legacy navigation start",
                actionId="legacy-next",
                fromPagePlanId="personal-information",
            )
            stored.record(
                "page_navigation_recovered",
                "legacy navigation recovered",
                actionId="legacy-next",
                toPagePlanId="passport-information",
            )
            service.checkpoint_store.save(stored)

            synchronized = service.sync_job(
                reviewed["id"],
                self.sync_payload(
                    [surname],
                    ["personal.surname"],
                ),
            )

            self.assertEqual(
                synchronized["completed_field_page_plan_by_id"],
                {"personal.surname": "ceac-plan-personal1"},
            )
            self.assertEqual(
                synchronized["visited_page_plan_ids"],
                [
                    "ceac-plan-personal1",
                    "ceac-plan-passport",
                ],
            )
            self.assertEqual(
                synchronized["current_page_plan_id"],
                "ceac-plan-passport",
            )
            self.assertTrue(any(
                event["kind"] == "completed_page_provenance_migrated"
                for event in synchronized["events"]
            ))
            self.assertTrue(any(
                event["kind"] == "visited_page_plans_migrated"
                for event in synchronized["events"]
            ))
            self.assertTrue(any(
                event["kind"] == "current_page_plan_migrated"
                for event in synchronized["events"]
            ))

    def test_final_review_boundary_survives_noop_and_material_sync(self):
        surname = {
            "id": "personal.surname",
            "value": "ZHANG",
            "confidence": 0.9,
        }
        for material_change in (False, True):
            with self.subTest(materialChange=material_change):
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
                        fields=[surname],
                        required_field_ids=["personal.surname"],
                    )
                    stored = service.checkpoint_store.load_job(
                        reviewed["id"]
                    )
                    initial_generation = stored.execution_generation
                    stored.state = JobState.REVIEW_REQUIRED
                    stored.final_submission_boundary_reached = True
                    # Deliberately retain a corrupt legacy run flag: sync must
                    # still normalize the final boundary to non-runnable.
                    stored.continuous_run_requested = True
                    stored.sync_resume_pending = True
                    stored.human_checkpoint = (
                        "Review/Sign reached; final submission remains manual"
                    )
                    service.checkpoint_store.save(stored)
                    synchronized_field = (
                        {**surname, "value": "LI"}
                        if material_change
                        else surname
                    )

                    synchronized = service.sync_job(
                        reviewed["id"],
                        self.sync_payload(
                            [synchronized_field],
                            ["personal.surname"],
                        ),
                    )

                    self.assertEqual(
                        synchronized["state"],
                        "review_required",
                    )
                    self.assertTrue(
                        synchronized[
                            "final_submission_boundary_reached"
                        ]
                    )
                    self.assertEqual(
                        synchronized["human_checkpoint"],
                        "Review/Sign reached; final submission remains manual",
                    )
                    self.assertFalse(
                        synchronized["continuous_run_requested"]
                    )
                    self.assertFalse(synchronized["sync_resume_pending"])
                    self.assertEqual(
                        synchronized["execution_generation"],
                        initial_generation + int(material_change),
                    )
                    with self.assertRaises(ServiceError) as raised:
                        service.start_job(reviewed["id"])
                    self.assertEqual(raised.exception.status, 409)
                    self.assertEqual(
                        service.recover_durable_continuous_runs(),
                        [],
                    )
                    self.assertEqual(runtime_creations, [])
                    with service._auto_resume_lock:
                        self.assertNotIn(
                            reviewed["id"],
                            service._auto_resume_jobs,
                        )

    def test_empty_runtime_review_cannot_clear_hard_boundary(self):
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
            stored = service.checkpoint_store.load_job(reviewed["id"])
            # This shape would otherwise satisfy all required confirmations,
            # so accepting an empty review would incorrectly make it READY.
            stored.state = JobState.REVIEW_REQUIRED
            stored.wait_kind = "manual_hard_boundary"
            stored.continuous_run_requested = False
            stored.human_checkpoint = "dispatch receipt conflict"
            service.checkpoint_store.save(stored)

            with self.assertRaises(ServiceError) as raised:
                service.review_job(reviewed["id"], {
                    "actor": "runtime-review",
                    "decisions": [],
                })

            self.assertEqual(raised.exception.status, 409)
            current = service.get_job(reviewed["id"])
            self.assertEqual(current["state"], "review_required")
            self.assertEqual(
                current["wait_kind"],
                "manual_hard_boundary",
            )
            self.assertEqual(
                current["human_checkpoint"],
                "dispatch receipt conflict",
            )
            self.assertFalse(current["continuous_run_requested"])

    def test_watcher_try_call_never_queues_behind_blocking_runtime_call(self):
        blocking_started = threading.Event()
        release_blocking = threading.Event()
        blocking_result = {}
        watcher_calls = []
        worker = _RuntimeWorker(
            lambda _job: self.FakeRuntime(),
            AgentJob(fields=[], start_url=PERSONAL_1_URL),
            startup_timeout=1,
        )

        def blocking_call():
            def hold_runtime(_runtime):
                blocking_started.set()
                self.assertTrue(release_blocking.wait(timeout=2))
                return "blocking-finished"

            blocking_result["value"] = worker.call(
                hold_runtime,
                timeout=2,
            )

        blocking_thread = threading.Thread(target=blocking_call)
        blocking_thread.start()
        self.assertTrue(blocking_started.wait(timeout=1))

        started_at = time.monotonic()
        with self.assertRaises(_RuntimeBusy):
            worker.try_call(
                lambda _runtime: watcher_calls.append("queued"),
                timeout=0.05,
            )
        elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 0.1)
        self.assertEqual(watcher_calls, [])
        self.assertTrue(worker.is_available)
        self.assertEqual(worker.last_failure, "")

        release_blocking.set()
        blocking_thread.join(timeout=1)
        self.assertFalse(blocking_thread.is_alive())
        self.assertEqual(
            blocking_result["value"],
            "blocking-finished",
        )
        self.assertEqual(
            worker.try_call(
                lambda _runtime: "watcher-after-idle",
                timeout=0.2,
            ),
            "watcher-after-idle",
        )
        self.assertTrue(worker.is_available)
        worker.close()


if __name__ == "__main__":
    unittest.main()
