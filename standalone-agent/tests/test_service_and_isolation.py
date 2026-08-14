import tempfile
import unittest
import base64
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from visa_agent.config import AgentConfig, ProviderConfig
from visa_agent.models import (
    ActionKind,
    BrowserObservation,
    ComputerAction,
    Evidence,
    JobState,
    job_from_primitive,
    observation_fingerprint,
    to_primitive,
)
from visa_agent.service import AgentService, Handler, ServiceError, run_server
from visa_agent import service as service_module
from visa_agent.providers import UnconfiguredExtractionModel
from visa_agent.recognition import DocumentRecognizer
from visa_agent.workflow import ComputerUseAgent


ROOT = Path(__file__).resolve().parents[1]


class ServiceAndIsolationTests(unittest.TestCase):
    class FakeBrowser:
        def __init__(self):
            self.closed = False
            self.profile_purge_requested = False

        def close(self):
            self.closed = True

        def purge_profile_on_close(self):
            self.profile_purge_requested = True

    def test_disconnected_health_probe_does_not_print_broken_pipe(self):
        handler = mock.MagicMock()
        handler.wfile.write.side_effect = BrokenPipeError("probe closed")

        Handler.json_response(handler, {"ok": True})

        handler.send_response.assert_called_once_with(200)
        handler.wfile.write.assert_called_once()

    def test_legacy_travel_duration_boundary_reopens_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(AgentConfig(data_dir=Path(directory)))
            reviewed = self.reviewed_job(service)
            job = service.checkpoint_store.load_job(reviewed["id"])
            field_id = "ceac.travel.travel.stayduration"
            job.state = JobState.WAITING_HUMAN
            job.wait_kind = "manual_hard_boundary"
            job.current_page_plan_id = "ceac-plan-travel"
            job.human_checkpoint = (
                "本页有字段在自动修复后仍与网页实际值不一致；"
                "Gemini 已阻止重复重填。"
            )
            job.control_normalized_values[field_id] = "7 D"
            job.record(
                "control_value_normalized",
                "legacy composite truncation",
                fieldId=field_id,
                originalLength=5,
                effectiveLength=3,
                maxLength=3,
            )
            job.record(
                "page_revalidation_stalled",
                "legacy duration readback stalled",
                fieldIds=[field_id],
                pagePlanId="ceac-plan-travel",
                durable=True,
            )

            self.assertTrue(
                service._reopen_legacy_travel_duration_boundary(job)
            )
            self.assertEqual(job.wait_kind, "")
            self.assertIsNone(job.human_checkpoint)
            self.assertNotIn(field_id, job.control_normalized_values)
            self.assertFalse(
                service._reopen_legacy_travel_duration_boundary(job)
            )

    def test_legacy_address_phone_postal_boundary_reopens_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(AgentConfig(data_dir=Path(directory)))
            reviewed = self.reviewed_job(service)
            job = service.checkpoint_store.load_job(reviewed["id"])
            field_id = "ceac.address_phone.contact.homepostalcode"
            job.state = JobState.WAITING_HUMAN
            job.wait_kind = "manual_hard_boundary"
            job.current_page_plan_id = "ceac-plan-address_phone"
            job.human_checkpoint = (
                "本页有字段在自动修复后仍与网页实际值不一致；"
                "Gemini 已阻止重复重填。"
            )
            job.completed_field_ids = [field_id]
            job.record(
                "v2_address_phone_postal_dna_revalidated",
                "native checkbox was reset",
                provedFieldIds=[],
                resetFieldIds=[field_id],
            )
            job.record(
                "page_revalidation_stalled",
                "legacy postal checkbox repair stalled",
                fieldIds=[field_id],
                pagePlanId="ceac-plan-address_phone",
                durable=True,
            )

            self.assertTrue(
                service._reopen_legacy_address_phone_postal_boundary(job)
            )
            self.assertEqual(job.wait_kind, "")
            self.assertIsNone(job.human_checkpoint)
            self.assertNotIn(field_id, job.completed_field_ids)
            self.assertFalse(
                service._reopen_legacy_address_phone_postal_boundary(job)
            )

    class FakeRuntime:
        def __init__(self):
            self.browser = ServiceAndIsolationTests.FakeBrowser()
            self.checkpoint_store = None
            self.run_count = 0

        def run(self, job):
            self.run_count += 1
            job.state = JobState.WAITING_HUMAN
            job.human_checkpoint = "manual step"
            return job

    @staticmethod
    def reviewed_job(service):
        created = service.create_job({
            "startUrl": "https://ceac.state.gov/GenNIV/Default.aspx",
            "requiredFieldIds": ["personal.surname"],
            "fields": [{
                "id": "personal.surname",
                "value": "ZHANG",
                "confidence": 0.9,
            }],
        })
        return service.review_job(created["id"], {
            "actor": "consultant-1",
            "decisions": [{
                "fieldId": "personal.surname",
                "approved": True,
                "value": "ZHANG",
            }],
        })

    def test_browser_open_is_manual_only_and_resume_reuses_same_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            runtimes = []
            runtime_threads = []

            def factory(_job):
                runtime_threads.append(threading.get_ident())
                runtime = self.FakeRuntime()
                original_run = runtime.run

                def run_on_runtime_thread(job):
                    runtime_threads.append(threading.get_ident())
                    return original_run(job)

                runtime.run = run_on_runtime_thread
                runtimes.append(runtime)
                return runtime

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=factory,
            )
            reviewed = self.reviewed_job(service)
            opened = service.open_job(reviewed["id"])
            opened_again = service.open_job(reviewed["id"])
            observed = service.get_job(reviewed["id"])
            resumed = service.start_job(reviewed["id"])

            self.assertEqual(opened["state"], "waiting_human")
            self.assertEqual(opened_again["state"], "waiting_human")
            self.assertTrue(observed["runtime_open"])
            self.assertEqual(resumed["state"], "waiting_human")
            self.assertEqual(len(runtimes), 1)
            self.assertEqual(runtimes[0].run_count, 1)
            self.assertFalse(runtimes[0].browser.closed)
            self.assertIs(
                runtimes[0].checkpoint_store._store,
                service.checkpoint_store,
            )
            self.assertEqual(runtime_threads[0], runtime_threads[1])
            self.assertNotEqual(runtime_threads[0], threading.get_ident())
            service._release_runtime(reviewed["id"])

    def test_shutdown_quiesces_all_process_resources_without_purging_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_dir = Path(directory) / "private-browser-profile"
            profile_dir.mkdir()
            profile_marker = profile_dir / "profile-state"
            profile_marker.write_text("keep", encoding="utf-8")
            observation = BrowserObservation(
                url=(
                    "https://ceac.state.gov/GenNIV/General/complete/"
                    "complete_personal.aspx?node=Personal1"
                ),
                title="Personal Information 1",
                visible_text="Personal Information 1",
                page_id="personal-1",
            )

            class Browser(self.FakeBrowser):
                def __init__(browser_self):
                    super().__init__()
                    browser_self.close_count = 0

                def observe_lightweight(browser_self):
                    return observation

                def set_visual_status(browser_self, *_args):
                    return None

                def close(browser_self):
                    browser_self.close_count += 1
                    browser_self.closed = True
                    if browser_self.profile_purge_requested:
                        profile_marker.unlink(missing_ok=True)

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class Runtime(self.FakeRuntime):
                def __init__(runtime_self):
                    runtime_self.browser = Browser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

            runtimes = []
            service = AgentService(
                AgentConfig(data_dir=Path(directory) / "checkpoints"),
                runtime_factory=lambda _job: (
                    runtimes.append(Runtime()) or runtimes[-1]
                ),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.wait_kind = "manual_page_change"
            stored.continuous_run_requested = True
            stored.last_safe_url = observation.url
            stored.wait_boundary_fingerprint = observation_fingerprint(
                stored,
                observation,
            )
            service.checkpoint_store.save(stored)
            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=True,
            )

            for _ in range(100):
                with service._auto_resume_lock:
                    watcher = service._auto_resume_threads.get(
                        reviewed["id"]
                    )
                if watcher is not None and watcher.is_alive():
                    break
                time.sleep(0.01)
            self.assertIsNotNone(watcher)
            self.assertTrue(watcher.is_alive())

            current = service.checkpoint_store.load_job(reviewed["id"])
            lease = service._create_execution_lease(current)
            with service._runtime_lock:
                worker = service._runtimes[reviewed["id"]]
                worker._execution_lease = lease

            barrier = threading.Barrier(3)
            reports = []

            def stop_service():
                barrier.wait(timeout=2)
                reports.append(service.shutdown(timeout=5))

            callers = [
                threading.Thread(target=stop_service)
                for _ in range(2)
            ]
            for caller in callers:
                caller.start()
            barrier.wait(timeout=2)
            for caller in callers:
                caller.join(timeout=6)

            self.assertTrue(all(not caller.is_alive() for caller in callers))
            self.assertEqual(len(reports), 2)
            self.assertTrue(all(report["complete"] for report in reports))
            self.assertTrue(lease.revoked)
            self.assertEqual(service._execution_leases, {})
            self.assertEqual(service._runtimes, {})
            self.assertEqual(service._retiring_runtime_refs, {})
            self.assertEqual(service._retired_runtime_workers, set())
            self.assertEqual(service._active_jobs, set())
            self.assertEqual(service._auto_resume_jobs, set())
            self.assertEqual(service._auto_resume_threads, {})
            self.assertTrue(runtimes[0].browser.closed)
            self.assertEqual(runtimes[0].browser.close_count, 1)
            self.assertFalse(
                runtimes[0].browser.profile_purge_requested
            )
            self.assertTrue(profile_marker.exists())
            self.assertTrue(
                service.checkpoint_store.load_job(
                    reviewed["id"]
                ).continuous_run_requested
            )

            with self.assertRaises(ServiceError) as stopping:
                service.open_job(reviewed["id"])
            self.assertEqual(stopping.exception.status, 503)
            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=False,
            )
            self.assertEqual(service._auto_resume_threads, {})
            with self.assertRaises(ServiceError) as lease_error:
                service._create_execution_lease(current)
            self.assertEqual(lease_error.exception.status, 503)

    def test_shutdown_revokes_and_waits_for_an_active_runtime_call(self):
        with tempfile.TemporaryDirectory() as directory:
            run_started = threading.Event()
            emergency_release = threading.Event()
            result = {}

            class Browser(self.FakeBrowser):
                def __init__(browser_self):
                    super().__init__()
                    browser_self.emergency_close_count = 0

                def emergency_close(browser_self):
                    browser_self.emergency_close_count += 1
                    emergency_release.set()
                    return True

            class Runtime(self.FakeRuntime):
                def __init__(runtime_self):
                    runtime_self.browser = Browser()
                    runtime_self.checkpoint_store = None

                def run(runtime_self, job):
                    run_started.set()
                    self.assertTrue(emergency_release.wait(timeout=2))
                    return job

            runtimes = []
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: (
                    runtimes.append(Runtime()) or runtimes[-1]
                ),
            )
            reviewed = self.reviewed_job(service)
            start_thread = threading.Thread(
                target=lambda: result.setdefault(
                    "job", service.start_job(reviewed["id"])
                ),
            )
            start_thread.start()
            self.assertTrue(run_started.wait(timeout=1))
            with service._active_jobs_lock:
                self.assertIn(reviewed["id"], service._active_jobs)
            with service._execution_leases_lock:
                leases = list(service._execution_leases.values())
            self.assertEqual(len(leases), 1)

            with mock.patch.object(
                service_module._RuntimeWorker,
                "CLOSE_TIMEOUT_SECONDS",
                0.05,
            ):
                report = service.shutdown(timeout=3)
            start_thread.join(timeout=2)

            self.assertFalse(start_thread.is_alive())
            self.assertTrue(report["complete"])
            self.assertTrue(leases[0].revoked)
            self.assertEqual(service._active_jobs, set())
            self.assertEqual(service._execution_leases, {})
            self.assertEqual(service._runtimes, {})
            self.assertTrue(runtimes[0].browser.closed)
            self.assertEqual(
                runtimes[0].browser.emergency_close_count,
                1,
            )
            self.assertFalse(
                runtimes[0].browser.profile_purge_requested
            )

    def test_get_job_reports_process_liveness_without_persisting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            reviewed = self.reviewed_job(service)

            idle = service.get_job(reviewed["id"])
            self.assertFalse(idle["execution_active"])
            self.assertFalse(idle["auto_resume_watcher_armed"])

            with service._active_jobs_lock:
                service._active_jobs.add(reviewed["id"])
            with service._auto_resume_lock:
                service._auto_resume_jobs.add(reviewed["id"])
            active = service.get_job(reviewed["id"])

            self.assertTrue(active["execution_active"])
            self.assertTrue(active["auto_resume_watcher_armed"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            self.assertFalse(hasattr(stored, "execution_active"))
            self.assertFalse(
                hasattr(stored, "auto_resume_watcher_armed")
            )

            with service._active_jobs_lock:
                service._active_jobs.discard(reviewed["id"])
            with service._auto_resume_lock:
                service._auto_resume_jobs.discard(reviewed["id"])

    def test_status_read_does_not_block_during_model_planning(self):
        with tempfile.TemporaryDirectory() as directory:
            planning_started = threading.Event()
            release_planning = threading.Event()
            outcome = {}

            class PlanningRuntime(self.FakeRuntime):
                def run(runtime_self, job):
                    job.state = JobState.FILLING_FORM
                    job.record(
                        "model_planning_started",
                        "Waiting for a deliberately slow test model",
                    )
                    runtime_self.checkpoint_store.save(job)
                    planning_started.set()
                    self.assertTrue(release_planning.wait(timeout=2))
                    job.state = JobState.REVIEW_REQUIRED
                    job.continuous_run_requested = False
                    job.human_checkpoint = "test review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: PlanningRuntime(),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            start_thread = threading.Thread(
                target=lambda: outcome.setdefault(
                    "result", service.start_job(reviewed["id"])
                ),
            )
            start_thread.start()
            self.assertTrue(planning_started.wait(timeout=1))

            started_at = time.monotonic()
            current = service.get_job(reviewed["id"])
            elapsed = time.monotonic() - started_at

            self.assertLess(elapsed, 0.1)
            self.assertEqual(current["state"], "filling_form")
            self.assertTrue(current["runtime_open"])
            self.assertFalse(current["runtime_transitioning"])
            self.assertTrue(current["execution_active"])

            release_planning.set()
            start_thread.join(timeout=2)
            self.assertFalse(start_thread.is_alive())
            self.assertEqual(outcome["result"]["state"], "review_required")
            service._release_runtime(reviewed["id"])

    def test_status_read_does_not_wait_for_browser_startup_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            startup_started = threading.Event()
            release_startup = threading.Event()
            outcome = {}

            def factory(_job):
                startup_started.set()
                self.assertTrue(release_startup.wait(timeout=2))

                class Runtime(self.FakeRuntime):
                    def run(runtime_self, job):
                        job.state = JobState.REVIEW_REQUIRED
                        job.continuous_run_requested = False
                        job.human_checkpoint = "test review boundary"
                        runtime_self.checkpoint_store.save(job)
                        return job

                return Runtime()

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=factory,
            )
            reviewed = self.reviewed_job(service)
            start_thread = threading.Thread(
                target=lambda: outcome.setdefault(
                    "result", service.start_job(reviewed["id"])
                ),
            )
            start_thread.start()
            self.assertTrue(startup_started.wait(timeout=1))

            started_at = time.monotonic()
            current = service.get_job(reviewed["id"])
            elapsed = time.monotonic() - started_at

            self.assertLess(elapsed, 0.1)
            self.assertTrue(current["runtime_transitioning"])
            self.assertFalse(current["runtime_open"])
            self.assertTrue(current["execution_active"])

            release_startup.set()
            start_thread.join(timeout=2)
            self.assertFalse(start_thread.is_alive())
            self.assertEqual(outcome["result"]["state"], "review_required")
            service._release_runtime(reviewed["id"])

    def test_start_recreates_missing_browser_without_second_user_action(self):
        with tempfile.TemporaryDirectory() as directory:
            factory_calls = []

            def factory(_job):
                factory_calls.append(True)
                return self.FakeRuntime()

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=factory,
            )
            reviewed = self.reviewed_job(service)
            resumed = service.start_job(reviewed["id"])

            self.assertEqual(resumed["state"], "waiting_human")
            self.assertTrue(resumed["continuous_run_requested"])
            self.assertEqual(factory_calls, [True])
            service._release_runtime(reviewed["id"])

    def test_browser_rebuild_resolves_pending_action_without_reexecution(self):
        with tempfile.TemporaryDirectory() as directory:
            shared = {"value": "", "execute_count": 0}

            class Browser(self.FakeBrowser):
                def __init__(browser_self, fail_after_action):
                    super().__init__()
                    browser_self.fail_after_action = fail_after_action
                    browser_self.action_sent = False
                    browser_self.url = (
                        "https://ceac.state.gov/GenNIV/General/complete/"
                        "complete_personal.aspx?node=Personal1"
                    )

                def observe(browser_self):
                    if (
                        browser_self.fail_after_action
                        and browser_self.action_sent
                    ):
                        raise RuntimeError("CDP target closed after action")
                    values = {}
                    if shared["value"]:
                        values["personal.surname"] = shared["value"]
                    return BrowserObservation(
                        url=browser_self.url,
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                        control_values=values,
                    )

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

                def execute(browser_self, action):
                    shared["execute_count"] += 1
                    shared["value"] = action.value
                    browser_self.action_sent = True

            class Model:
                def propose_action(self, *_args):
                    raise AssertionError(
                        "The deterministic pending action must be resolved "
                        "without another model call"
                    )

            runtimes = []

            def factory(_job):
                browser = Browser(fail_after_action=not runtimes)
                runtime = ComputerUseAgent(Model(), browser)
                runtimes.append(runtime)
                return runtime

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=factory,
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])

            first = service.start_job(reviewed["id"])
            self.assertEqual(first["state"], "waiting_human")
            self.assertEqual(first["automatic_retry_kind"], "browser")
            self.assertIsNotNone(first["pending_action"])
            self.assertEqual(shared["execute_count"], 1)

            for _ in range(600):
                current = service.get_job(reviewed["id"])
                if current["state"] == "completed":
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "completed")
            self.assertEqual(shared["execute_count"], 1)
            self.assertEqual(len(runtimes), 2)
            self.assertTrue(runtimes[0].browser.closed)
            self.assertFalse(
                runtimes[0].browser.profile_purge_requested
            )
            self.assertIsNone(current["pending_action"])
            self.assertEqual(len(current["applied_action_ids"]), 1)
            self.assertTrue(any(
                event["kind"] == "pending_action_recovered"
                for event in current["events"]
            ))

    def test_runtime_inactivity_timeout_rebuilds_worker_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            release_stuck_call = threading.Event()

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class HealthyBrowser(self.FakeBrowser):
                def observe_lightweight(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/complete/"
                            "complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                    )

            class SlowRuntime:
                def __init__(runtime_self):
                    class StuckBrowser(self.FakeBrowser):
                        def __init__(browser_self):
                            super().__init__()
                            browser_self.emergency_close_calls = 0

                        def emergency_close(browser_self):
                            browser_self.emergency_close_calls += 1
                            release_stuck_call.set()
                            return True

                    runtime_self.browser = StuckBrowser()
                    runtime_self.checkpoint_store = None

                def run(runtime_self, job):
                    release_stuck_call.wait()
                    job.state = JobState.WAITING_HUMAN
                    # This deliberately late save belongs to the poisoned
                    # generation. The checkpoint wrapper must reject it
                    # instead of erasing the service's browser-retry marker.
                    runtime_self.checkpoint_store.save(job)
                    return job

            class HealthyRuntime:
                def __init__(runtime_self):
                    runtime_self.browser = HealthyBrowser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, job):
                    runtime_self.run_count += 1
                    job.state = JobState.REVIEW_REQUIRED
                    job.human_checkpoint = "Review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            runtimes = []

            def factory(_job):
                runtime = (
                    SlowRuntime()
                    if not runtimes
                    else HealthyRuntime()
                )
                runtimes.append(runtime)
                return runtime

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=factory,
            )
            service.RUN_INACTIVITY_TIMEOUT_SECONDS = 0.03
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])

            worker_type = type(service._runtimes[reviewed["id"]])
            with mock.patch.object(
                worker_type,
                "CLOSE_TIMEOUT_SECONDS",
                0.03,
            ), mock.patch.object(
                worker_type,
                "EMERGENCY_CLOSE_TIMEOUT_SECONDS",
                0.2,
            ):
                timed_out = service.start_job(reviewed["id"])
            self.assertEqual(timed_out["state"], "waiting_human")
            self.assertEqual(
                timed_out["automatic_retry_kind"],
                "browser",
            )
            self.assertTrue(runtimes[0].browser.closed)
            self.assertEqual(
                runtimes[0].browser.emergency_close_calls,
                1,
            )
            self.assertFalse(
                runtimes[0].browser.profile_purge_requested
            )

            for _ in range(600):
                current = service.get_job(reviewed["id"])
                if (
                    current["state"] == "review_required"
                    and not current["continuous_run_requested"]
                ):
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertEqual(len(runtimes), 2)
            self.assertEqual(runtimes[1].run_count, 1)
            self.assertFalse(current["continuous_run_requested"])
            self.assertTrue(any(
                event["kind"] == "browser_runtime_retry_scheduled"
                and event["detail"]["source"] == "runtime_command"
                for event in current["events"]
            ))
            service._release_runtime(reviewed["id"])

    def test_timed_out_worker_late_model_return_cannot_execute_action(self):
        with tempfile.TemporaryDirectory() as directory:
            class Browser(self.FakeBrowser):
                def __init__(browser_self):
                    super().__init__()
                    browser_self.execute_count = 0

                def observe(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/complete/"
                            "complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                    )

                observe_lightweight = observe

                def execute(browser_self, _action):
                    browser_self.execute_count += 1

            class SlowModel:
                def propose_actions(model_self, *_args):
                    time.sleep(0.3)
                    return [ComputerAction(
                        kind=ActionKind.TYPE,
                        field_id="personal.surname",
                        target_hint="Surnames",
                    )]

            runtimes = []

            def factory(_job):
                runtime = ComputerUseAgent(SlowModel(), Browser())
                runtimes.append(runtime)
                return runtime

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=factory,
            )
            service.RUN_INACTIVITY_TIMEOUT_SECONDS = 0.02
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])

            timed_out = service.start_job(reviewed["id"])

            self.assertEqual(timed_out["automatic_retry_kind"], "browser")
            self.assertEqual(runtimes[0].browser.execute_count, 0)
            service.cancel_job(reviewed["id"], {"actor": "cleanup"})
            for _ in range(100):
                if not service._execution_leases:
                    break
                time.sleep(0.01)
            self.assertEqual(runtimes[0].browser.execute_count, 0)
            self.assertEqual(service._execution_leases, {})

    def test_runtime_startup_timeout_is_reaped_and_auto_retries_once(self):
        with tempfile.TemporaryDirectory() as directory:
            release_startup = threading.Event()

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class Browser(self.FakeBrowser):
                def __init__(browser_self, hung=False):
                    super().__init__()
                    browser_self.hung = hung
                    browser_self.emergency_close_calls = 0
                    browser_self.visual_status = None

                def emergency_close(browser_self):
                    browser_self.emergency_close_calls += 1
                    release_startup.set()
                    return True

                def observe_lightweight(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/complete/"
                            "complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                    )

                def set_visual_status(browser_self, state, message=""):
                    browser_self.visual_status = (state, message)

            class Runtime:
                def __init__(runtime_self, browser):
                    runtime_self.browser = browser
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, job):
                    runtime_self.run_count += 1
                    job.state = JobState.REVIEW_REQUIRED
                    job.human_checkpoint = "Review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            browsers = []
            runtimes = []

            def factory(_job, startup_control):
                browser = Browser(hung=not browsers)
                browsers.append(browser)
                startup_control.publish_browser(browser)
                if browser.hung:
                    # Simulate Playwright/Chrome blocking after the exact
                    # private-profile browser has become known to the worker.
                    release_startup.wait(timeout=2)
                runtime = Runtime(browser)
                runtimes.append(runtime)
                return runtime

            factory._docflow_accepts_startup_control = True
            service = AgentService(
                AgentConfig(
                    data_dir=Path(directory),
                    browser_startup_timeout_seconds=0.03,
                ),
                runtime_factory=factory,
            )
            reviewed = self.reviewed_job(service)
            worker_suffix = reviewed["id"][-8:]

            started_at = time.monotonic()
            first = service.start_job(reviewed["id"])
            elapsed = time.monotonic() - started_at

            self.assertLess(elapsed, 0.8)
            self.assertEqual(first["state"], "waiting_human")
            self.assertTrue(first["continuous_run_requested"])
            self.assertTrue(first["automatic_retry_pending"])
            self.assertEqual(first["automatic_retry_kind"], "browser")
            self.assertEqual(first["execution_generation"], 2)
            self.assertEqual(len(browsers), 1)
            self.assertEqual(browsers[0].emergency_close_calls, 1)
            self.assertTrue(browsers[0].closed)
            self.assertTrue(any(
                event["kind"] == "browser_runtime_retry_scheduled"
                and event["detail"]["source"] == "runtime_start"
                and event["detail"]["errorType"]
                == "_RuntimeStartupTimeout"
                for event in first["events"]
            ))

            # No second API/start click: only expire the durable backoff and
            # let the already-armed watcher create a healthy replacement.
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            service.checkpoint_store.save(stored)
            for _ in range(400):
                current = service.get_job(reviewed["id"])
                with service._retired_runtime_lock:
                    retired = len(service._retired_runtime_workers)
                orphan_threads = [
                    thread
                    for thread in threading.enumerate()
                    if thread.name == f"agent-runtime-{worker_suffix}"
                    and thread.is_alive()
                ]
                if (
                    current["state"] == "review_required"
                    and retired == 0
                    and len(orphan_threads) == 1
                ):
                    # The one remaining worker is the healthy, intentionally
                    # retained Review runtime; the timed-out worker is gone.
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertFalse(current["continuous_run_requested"])
            self.assertFalse(current["automatic_retry_pending"])
            self.assertEqual(len(browsers), 2)
            self.assertEqual(len(runtimes), 2)
            self.assertEqual(runtimes[1].run_count, 1)
            self.assertEqual(retired, 0)
            self.assertEqual(len(orphan_threads), 1)
            self.assertIs(
                service._runtimes[reviewed["id"]]._runtime,
                runtimes[1],
            )
            service._release_runtime(reviewed["id"])

    def test_sync_replaces_stale_approved_fields_without_replacing_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            runtimes = []

            def factory(_job):
                runtime = self.FakeRuntime()
                runtimes.append(runtime)
                return runtime

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=factory,
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.completed_field_ids = ["personal.surname"]
            stored.control_normalized_values = {
                "personal.surname": "X" * 40,
            }
            service.checkpoint_store.save(stored)

            synchronized = service.sync_job(reviewed["id"], {
                "actor": "consultant-2",
                "autoNext": False,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "LI",
                    "confidence": 1.0,
                }],
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "LI",
                }],
            })

            self.assertEqual(synchronized["id"], reviewed["id"])
            self.assertEqual(synchronized["state"], "waiting_human")
            self.assertEqual(synchronized["fields"][0]["value"], "LI")
            self.assertFalse(synchronized["auto_next"])
            self.assertEqual(synchronized["completed_field_ids"], [])
            self.assertEqual(len(runtimes), 1)
            self.assertFalse(runtimes[0].browser.closed)
            self.assertEqual(
                service.checkpoint_store.load_job(
                    reviewed["id"]
                ).control_normalized_values,
                {},
            )

            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.completed_field_ids = ["personal.surname"]
            stored.control_normalized_values = {
                "personal.surname": "L",
            }
            service.checkpoint_store.save(stored)
            unchanged = service.sync_job(reviewed["id"], {
                "actor": "consultant-2",
                "autoNext": True,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "LI",
                    "confidence": 1.0,
                }],
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "LI",
                }],
            })
            self.assertEqual(
                unchanged["completed_field_ids"], ["personal.surname"]
            )
            self.assertTrue(unchanged["auto_next"])
            self.assertEqual(
                service.checkpoint_store.load_job(
                    reviewed["id"]
                ).control_normalized_values,
                {"personal.surname": "L"},
            )

    def test_sync_preserves_non_idempotent_pending_and_retry_state(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.execution_generation = 4
            stored.completed_field_ids = ["personal.surname"]
            stored.pending_action = ComputerAction(
                kind=ActionKind.CLICK,
                target_hint="Next: Personal 2",
                reason="Deterministic fixed CEAC Next control",
                id="action-pending-next",
            )
            stored.applied_action_ids = ["action-already-applied"]
            stored.automatic_retry_pending = True
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) + timedelta(seconds=20)
            ).isoformat()
            stored.automatic_retry_count = 3
            stored.automatic_retry_kind = "navigation_observation"
            stored.human_checkpoint = "Next 已派发，正在等待换页结果"
            service.checkpoint_store.save(stored)

            synchronized = service.sync_job(reviewed["id"], {
                "actor": "consultant-2",
                "autoNext": True,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "confidence": 1.0,
                }],
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "ZHANG",
                }],
            })

            self.assertEqual(synchronized["state"], "waiting_human")
            self.assertEqual(
                synchronized["pending_action"]["id"],
                "action-pending-next",
            )
            self.assertEqual(
                synchronized["applied_action_ids"],
                ["action-already-applied"],
            )
            self.assertTrue(synchronized["automatic_retry_pending"])
            self.assertEqual(
                synchronized["automatic_retry_kind"],
                "navigation_observation",
            )
            self.assertEqual(synchronized["automatic_retry_count"], 3)
            self.assertEqual(synchronized["execution_generation"], 5)
            self.assertIn("等待换页", synchronized["human_checkpoint"])

            changed = service.sync_job(reviewed["id"], {
                "actor": "consultant-2",
                "autoNext": True,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "LI",
                    "confidence": 1.0,
                }],
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "LI",
                }],
            })

            self.assertEqual(
                changed["pending_action"]["id"],
                "action-pending-next",
            )
            self.assertTrue(changed["automatic_retry_pending"])
            self.assertEqual(changed["execution_generation"], 6)
            self.assertEqual(changed["completed_field_ids"], [])
            service._release_runtime(reviewed["id"])

    def test_sync_invalidates_only_changed_pending_value_action(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            reviewed = self.reviewed_job(service)
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.execution_generation = 2
            stored.pending_action = ComputerAction(
                kind=ActionKind.TYPE,
                field_id="personal.surname",
                target_hint="Surnames",
                value="ZHANG",
                id="action-pending-value",
            )
            service.checkpoint_store.save(stored)

            synchronized = service.sync_job(reviewed["id"], {
                "actor": "consultant-2",
                "autoNext": True,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "LI",
                    "confidence": 1.0,
                }],
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "LI",
                }],
            })

            self.assertIsNone(synchronized["pending_action"])
            self.assertEqual(synchronized["execution_generation"], 3)
            self.assertTrue(any(
                event["kind"]
                == "pending_value_action_invalidated_by_sync"
                for event in synchronized["events"]
            ))

    def test_sync_same_value_changed_label_invalidates_dom_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            field_id = "ceac.personal1.001.personal.surname"
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            created = service.create_job({
                "startUrl": (
                    "https://ceac.state.gov/GenNIV/General/complete/"
                    "complete_personal.aspx?node=Personal1"
                ),
                "requiredFieldIds": [field_id],
                "fields": [{
                    "id": field_id,
                    "value": "ZHANG",
                    "label": (
                        "Surnames [control=text; "
                        "control_hints=APP_SURNAME_OLD]"
                    ),
                    "confidence": 0.9,
                }],
            })
            reviewed = service.review_job(created["id"], {
                "actor": "consultant",
                "decisions": [{
                    "fieldId": field_id,
                    "approved": True,
                    "value": "ZHANG",
                }],
            })
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.execution_generation = 3
            stored.completed_field_ids = [field_id]
            stored.pending_action = ComputerAction(
                kind=ActionKind.TYPE,
                field_id=field_id,
                target_hint="old-surname-binding",
                value="ZHANG",
                id="old-binding-action",
            )
            service.checkpoint_store.save(stored)

            synchronized = service.sync_job(reviewed["id"], {
                "actor": "consultant-2",
                "autoNext": True,
                "requiredFieldIds": [field_id],
                "fields": [{
                    "id": field_id,
                    "value": "ZHANG",
                    "label": (
                        "Surnames [control=text; "
                        "control_hints=APP_SURNAME_NEW]"
                    ),
                    "confidence": 0.9,
                }],
                "decisions": [{
                    "fieldId": field_id,
                    "approved": True,
                    "value": "ZHANG",
                }],
            })

            self.assertEqual(synchronized["execution_generation"], 4)
            self.assertEqual(synchronized["completed_field_ids"], [])
            self.assertIsNone(synchronized["pending_action"])
            self.assertEqual(
                synchronized["binding_refresh_field_ids"],
                [field_id],
            )

    def test_sync_evidence_change_fences_generation_but_keeps_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            created = service.create_job({
                "startUrl": "https://ceac.state.gov/GenNIV/Default.aspx",
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "label": "Surnames",
                    "confidence": 0.8,
                    "evidence": [{
                        "document_id": "passport-old",
                        "filename": "old.png",
                        "page": 1,
                        "excerpt": "ZHANG",
                        "method": "ocr",
                    }],
                }],
            })
            reviewed = service.review_job(created["id"], {
                "actor": "consultant",
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "ZHANG",
                }],
            })
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.completed_field_ids = ["personal.surname"]
            service.checkpoint_store.save(stored)

            synchronized = service.sync_job(reviewed["id"], {
                "actor": "consultant",
                "autoNext": True,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "label": "Surnames",
                    "confidence": 0.99,
                    "evidence": [{
                        "document_id": "passport-new",
                        "filename": "new.png",
                        "page": 1,
                        "excerpt": "ZHANG",
                        "method": "gemini-vision",
                    }],
                }],
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "ZHANG",
                }],
            })

            self.assertEqual(synchronized["execution_generation"], 1)
            self.assertEqual(
                synchronized["completed_field_ids"],
                ["personal.surname"],
            )
            self.assertEqual(synchronized["binding_refresh_field_ids"], [])

    def test_lease_revoked_between_pending_save_and_execute_blocks_side_effect(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            executed = []

            class Browser(self.FakeBrowser):
                def observe(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/complete/"
                            "complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                    )

                observe_lightweight = observe

                def plan_fields(browser_self, field_ids, *_args):
                    return ([
                        ComputerAction(
                            kind=ActionKind.TYPE,
                            field_id=field_id,
                            target_hint=field_id,
                        )
                        for field_id in field_ids
                    ], [])

                def execute(browser_self, action):
                    executed.append(action.id)

            class Model:
                def propose_action(self, *_args):
                    raise AssertionError("DOM planner should own this field")

            browser = Browser()
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: ComputerUseAgent(
                    Model(), browser
                ),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            original_save = service.checkpoint_store.save
            revoked = {"done": False}

            def revoke_after_pending_save(job):
                result = original_save(job)
                if (
                    job.pending_action is not None
                    and not revoked["done"]
                ):
                    revoked["done"] = True
                    service._revoke_execution_leases(job.id)
                return result

            service.checkpoint_store.save = revoke_after_pending_save
            result = service.start_job(reviewed["id"])

            self.assertTrue(revoked["done"])
            self.assertEqual(executed, [])
            self.assertIsNotNone(result["pending_action"])
            self.assertIn("-g1-", result["pending_action"]["id"])
            self.assertEqual(service._execution_leases, {})
            service._release_runtime(reviewed["id"])

    def test_sync_wakes_existing_page_change_watcher_without_dom_change(self):
        with tempfile.TemporaryDirectory() as directory:
            class Browser(self.FakeBrowser):
                def observe_lightweight(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/complete/"
                            "complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                    )

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class Runtime:
                def __init__(runtime_self):
                    runtime_self.browser = Browser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, job):
                    runtime_self.run_count += 1
                    job.state = JobState.REVIEW_REQUIRED
                    job.human_checkpoint = "Review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            runtime = Runtime()
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: runtime,
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.wait_kind = "manual_page_change"
            observation = runtime.browser.observe_lightweight()
            stored.wait_boundary_fingerprint = observation_fingerprint(
                stored,
                observation,
            )
            stored.human_checkpoint = "Waiting for a real page change"
            service.checkpoint_store.save(stored)
            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=True,
            )
            time.sleep(0.05)

            synchronized = service.sync_job(reviewed["id"], {
                "actor": "consultant",
                "autoNext": True,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "label": "Surnames [control=text]",
                    "confidence": 0.9,
                }],
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "ZHANG",
                }],
            })
            self.assertTrue(synchronized["sync_resume_pending"])

            for _ in range(300):
                current = service.get_job(reviewed["id"])
                if current["state"] == "review_required":
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertEqual(runtime.run_count, 1)
            service._release_runtime(reviewed["id"])

    def test_nonrecoverable_runtime_start_failure_is_terminal_not_fake_running(self):
        with tempfile.TemporaryDirectory() as directory:
            def broken_factory(_job):
                raise ValueError("invalid runtime construction")

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=broken_factory,
            )
            reviewed = self.reviewed_job(service)

            with self.assertRaises(ServiceError):
                service.start_job(reviewed["id"])

            current = service.get_job(reviewed["id"])
            self.assertEqual(current["state"], "failed")
            self.assertFalse(current["continuous_run_requested"])
            self.assertFalse(current["automatic_retry_pending"])
            self.assertFalse(current["execution_active"])
            self.assertTrue(any(
                event["kind"] == "runtime_terminal_failure"
                and event["detail"]["source"] == "runtime_start"
                for event in current["events"]
            ))

    def test_nonrecoverable_runtime_run_failure_is_terminal_not_fake_running(self):
        with tempfile.TemporaryDirectory() as directory:
            class BrokenRuntime(self.FakeRuntime):
                def run(runtime_self, _job):
                    raise ValueError("invalid runtime result")

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: BrokenRuntime(),
            )
            reviewed = self.reviewed_job(service)

            with self.assertRaises(ServiceError):
                service.start_job(reviewed["id"])

            current = service.get_job(reviewed["id"])
            self.assertEqual(current["state"], "failed")
            self.assertFalse(current["continuous_run_requested"])
            self.assertFalse(current["automatic_retry_pending"])
            self.assertFalse(current["execution_active"])
            self.assertTrue(any(
                event["kind"] == "runtime_terminal_failure"
                and event["detail"]["source"] == "runtime_value_error"
                for event in current["events"]
            ))
            service._release_runtime(reviewed["id"])

    def test_sync_after_service_restart_requires_browser_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))
            first_runtime = []
            service = AgentService(
                config,
                runtime_factory=lambda _job: (
                    first_runtime.append(self.FakeRuntime())
                    or first_runtime[-1]
                ),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])

            restarted_runtimes = []
            restarted = AgentService(
                config,
                runtime_factory=lambda _job: (
                    restarted_runtimes.append(self.FakeRuntime())
                    or restarted_runtimes[-1]
                ),
            )
            synchronized = restarted.sync_job(reviewed["id"], {
                "actor": "consultant-2",
                "autoNext": True,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "confidence": 1.0,
                }],
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "ZHANG",
                }],
            })
            self.assertEqual(synchronized["state"], "ready_for_form")
            self.assertEqual(restarted_runtimes, [])

            opened = restarted.open_job(reviewed["id"])
            self.assertEqual(opened["state"], "waiting_human")
            self.assertEqual(len(restarted_runtimes), 1)
            service._release_runtime(reviewed["id"])
            restarted._release_runtime(reviewed["id"])

    def test_service_restart_recovers_orphaned_filling_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))
            first = AgentService(config)
            reviewed = self.reviewed_job(first)
            stored = first.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.FILLING_FORM
            stored.continuous_run_requested = True
            stored.pending_action = ComputerAction(
                kind=ActionKind.TYPE,
                field_id="personal.surname",
                target_hint="Surnames",
            )
            first.checkpoint_store.save(stored)

            restarted = AgentService(config)
            recovered = restarted.get_job(reviewed["id"])

            self.assertEqual(recovered["state"], "ready_for_form")
            self.assertFalse(recovered["runtime_open"])
            self.assertTrue(recovered["continuous_run_requested"])
            self.assertEqual(
                recovered["pending_action"]["field_id"],
                "personal.surname",
            )
            self.assertTrue(any(
                event["kind"] == "orphaned_runtime_recovered"
                for event in recovered["events"]
            ))

    def test_startup_reopens_form_and_continues_without_any_api_trigger(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))
            first = AgentService(config)
            reviewed = self.reviewed_job(first)
            stored = first.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.FILLING_FORM
            stored.continuous_run_requested = True
            first.checkpoint_store.save(stored)

            class RestoredBrowser(self.FakeBrowser):
                def observe_lightweight(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/"
                            "complete/complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                    )

            class Plans:
                @staticmethod
                def match(observation):
                    return object() if "complete_personal" in observation.url else None

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class RestoredRuntime:
                def __init__(runtime_self):
                    runtime_self.browser = RestoredBrowser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, job):
                    runtime_self.run_count += 1
                    job.state = JobState.REVIEW_REQUIRED
                    job.human_checkpoint = "Review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            runtimes = []
            restarted = AgentService(
                config,
                runtime_factory=lambda _job: (
                    runtimes.append(RestoredRuntime()) or runtimes[-1]
                ),
            )
            recovered_ids = restarted.recover_durable_continuous_runs()
            self.assertEqual(recovered_ids, [reviewed["id"]])
            for _ in range(100):
                current = restarted.checkpoint_store.load_job(reviewed["id"])
                if current.state == JobState.REVIEW_REQUIRED:
                    break
                time.sleep(0.01)
            current = restarted.get_job(reviewed["id"])

            self.assertEqual(current["state"], "review_required")
            self.assertEqual(runtimes[0].run_count, 1)
            self.assertEqual(current["execution_generation"], 1)
            self.assertFalse(current["continuous_run_requested"])
            self.assertTrue(current["runtime_open"])
            self.assertTrue(any(
                event["kind"] == "continuous_run_recovery_armed"
                for event in current["events"]
            ))
            restarted._release_runtime(reviewed["id"])

    def test_future_provider_retry_is_deferred_without_second_runtime_call(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.automatic_retry_pending = True
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) + timedelta(seconds=60)
            ).isoformat()
            stored.automatic_retry_count = 2
            service.checkpoint_store.save(stored)

            with mock.patch.object(
                service,
                "_arm_continuous_resume",
            ) as arm:
                deferred = service.start_job(reviewed["id"])

            runtime = service._runtimes[reviewed["id"]]
            self.assertEqual(runtime.call(lambda item: item.run_count), 0)
            self.assertEqual(deferred["state"], "waiting_human")
            self.assertTrue(deferred["automatic_retry_pending"])
            self.assertEqual(deferred["automatic_retry_count"], 2)
            self.assertEqual(deferred["execution_generation"], 0)
            arm.assert_called_once_with(
                reviewed["id"],
                require_page_change=False,
            )
            service._release_runtime(reviewed["id"])

    def test_watcher_does_not_observe_or_start_before_retry_due_time(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))

            class CountingBrowser(self.FakeBrowser):
                def __init__(browser_self):
                    super().__init__()
                    browser_self.observe_count = 0

                def observe_lightweight(browser_self):
                    browser_self.observe_count += 1
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/"
                            "complete/complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                    )

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class Runtime:
                def __init__(runtime_self):
                    runtime_self.browser = CountingBrowser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, job):
                    runtime_self.run_count += 1
                    job.state = JobState.REVIEW_REQUIRED
                    job.human_checkpoint = "Review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            runtimes = []
            service = AgentService(
                config,
                runtime_factory=lambda _job: (
                    runtimes.append(Runtime()) or runtimes[-1]
                ),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.automatic_retry_pending = True
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) + timedelta(seconds=0.5)
            ).isoformat()
            stored.automatic_retry_count = 1
            service.checkpoint_store.save(stored)

            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=True,
            )
            time.sleep(0.1)
            runtime = runtimes[0]
            self.assertEqual(runtime.run_count, 0)
            self.assertEqual(runtime.browser.observe_count, 0)
            for _ in range(200):
                current = service.get_job(reviewed["id"])
                if current["state"] == "review_required":
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertEqual(runtime.run_count, 1)
            self.assertGreaterEqual(runtime.browser.observe_count, 1)
            for _ in range(100):
                with service._auto_resume_lock:
                    watcher_done = (
                        reviewed["id"] not in service._auto_resume_jobs
                    )
                with service._active_jobs_lock:
                    run_done = reviewed["id"] not in service._active_jobs
                if watcher_done and run_done:
                    break
                time.sleep(0.01)
            service._release_runtime(reviewed["id"])

    def test_expired_provider_retry_recovers_after_restart_on_same_page(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))
            first = AgentService(config)
            reviewed = self.reviewed_job(first)
            stored = first.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.automatic_retry_pending = True
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            stored.automatic_retry_count = 3
            stored.wait_boundary_fingerprint = "same-page-boundary"
            first.checkpoint_store.save(stored)

            class RetryBrowser(self.FakeBrowser):
                def observe_lightweight(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/"
                            "complete/complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                    )

                def set_visual_status(browser_self, state, message):
                    browser_self.visual_status = (state, message)

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class RetryRuntime:
                def __init__(runtime_self):
                    runtime_self.browser = RetryBrowser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, job):
                    runtime_self.run_count += 1
                    job.state = JobState.REVIEW_REQUIRED
                    job.human_checkpoint = "Review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            runtimes = []
            restarted = AgentService(
                config,
                runtime_factory=lambda _job: (
                    runtimes.append(RetryRuntime()) or runtimes[-1]
                ),
            )
            recovered = restarted.recover_durable_continuous_runs()

            self.assertEqual(recovered, [reviewed["id"]])
            for _ in range(200):
                current = restarted.get_job(reviewed["id"])
                if current["state"] == "review_required":
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertEqual(runtimes[0].run_count, 1)
            self.assertEqual(current["execution_generation"], 1)
            self.assertFalse(current["automatic_retry_pending"])
            self.assertEqual(current["automatic_retry_after"], "")
            self.assertEqual(current["automatic_retry_count"], 0)
            self.assertFalse(current["continuous_run_requested"])
            restarted._release_runtime(reviewed["id"])

    def test_browser_retry_rebuilds_after_agent_process_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))
            first = AgentService(config)
            reviewed = self.reviewed_job(first)
            stored = first.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.automatic_retry_pending = True
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            stored.automatic_retry_count = 2
            stored.automatic_retry_kind = "browser"
            stored.pending_action = ComputerAction(
                kind=ActionKind.TYPE,
                id="action-before-restart",
                field_id="personal.surname",
                target_hint="personal.surname",
                value="ZHANG",
            )
            first.checkpoint_store.save(stored)

            class Browser(self.FakeBrowser):
                def observe_lightweight(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/complete/"
                            "complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                        control_values={"personal.surname": "ZHANG"},
                    )

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class Runtime:
                def __init__(runtime_self):
                    runtime_self.browser = Browser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, job):
                    runtime_self.run_count += 1
                    self.assertEqual(
                        job.pending_action.id,
                        "action-before-restart",
                    )
                    job.pending_action = None
                    job.state = JobState.REVIEW_REQUIRED
                    job.human_checkpoint = "Review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            runtimes = []
            restarted = AgentService(
                config,
                runtime_factory=lambda _job: (
                    runtimes.append(Runtime()) or runtimes[-1]
                ),
            )

            recovered = restarted.recover_durable_continuous_runs()
            self.assertEqual(recovered, [reviewed["id"]])
            for _ in range(300):
                current = restarted.get_job(reviewed["id"])
                if current["state"] == "review_required":
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertEqual(len(runtimes), 1)
            self.assertEqual(runtimes[0].run_count, 1)
            self.assertEqual(current["execution_generation"], 1)
            self.assertFalse(current["automatic_retry_pending"])
            self.assertEqual(current["automatic_retry_kind"], "")
            restarted._release_runtime(reviewed["id"])

    def test_watcher_rearms_repeated_provider_retries_without_page_change(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))

            class SamePageBrowser(self.FakeBrowser):
                def observe_lightweight(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/"
                            "complete/complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                    )

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class RepeatedRetryRuntime:
                def __init__(runtime_self):
                    runtime_self.browser = SamePageBrowser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, job):
                    runtime_self.run_count += 1
                    if runtime_self.run_count <= 2:
                        job.state = JobState.WAITING_HUMAN
                        job.automatic_retry_pending = True
                        job.automatic_retry_after = (
                            datetime.now(timezone.utc)
                            - timedelta(milliseconds=1)
                        ).isoformat()
                        job.automatic_retry_count = runtime_self.run_count
                        job.human_checkpoint = "automatic provider retry"
                    else:
                        job.state = JobState.REVIEW_REQUIRED
                        job.human_checkpoint = "Review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            runtimes = []
            service = AgentService(
                config,
                runtime_factory=lambda _job: (
                    runtimes.append(RepeatedRetryRuntime()) or runtimes[-1]
                ),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])

            first = service.start_job(reviewed["id"])
            self.assertTrue(first["automatic_retry_pending"])
            for _ in range(300):
                current = service.get_job(reviewed["id"])
                if current["state"] == "review_required":
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertEqual(runtimes[0].run_count, 3)
            self.assertEqual(current["execution_generation"], 3)
            self.assertFalse(current["automatic_retry_pending"])
            self.assertFalse(current["continuous_run_requested"])
            service._release_runtime(reviewed["id"])

    def test_retry_watcher_turns_captcha_into_page_change_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))

            class CaptchaBrowser(self.FakeBrowser):
                def observe_lightweight(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/"
                            "complete/complete_personal.aspx?node=Personal1"
                        ),
                        title="Security check",
                        visible_text="Please complete CAPTCHA",
                        page_id="captcha",
                    )

                def set_visual_status(browser_self, state, message):
                    browser_self.visual_status = (state, message)

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class Decision:
                allowed = False
                reason = "Human checkpoint detected: captcha"

            class Policy:
                @staticmethod
                def inspect_page(_observation):
                    return Decision()

            class Runtime:
                def __init__(runtime_self):
                    runtime_self.browser = CaptchaBrowser()
                    runtime_self.page_plans = Plans()
                    runtime_self.policy = Policy()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, _job):
                    runtime_self.run_count += 1
                    raise AssertionError("CAPTCHA must not invoke Gemini")

            runtimes = []
            service = AgentService(
                config,
                runtime_factory=lambda _job: (
                    runtimes.append(Runtime()) or runtimes[-1]
                ),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.automatic_retry_pending = True
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            stored.automatic_retry_count = 2
            service.checkpoint_store.save(stored)

            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=False,
            )
            for _ in range(100):
                current = service.get_job(reviewed["id"])
                if any(
                    event["kind"]
                    == "automatic_retry_replaced_by_human_boundary"
                    for event in current["events"]
                ):
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "waiting_human")
            self.assertFalse(current["automatic_retry_pending"])
            self.assertTrue(current["continuous_run_requested"])
            self.assertEqual(runtimes[0].run_count, 0)
            self.assertIn("captcha", current["human_checkpoint"].lower())
            self.assertEqual(runtimes[0].browser.visual_status[0], "paused")
            service.cancel_job(reviewed["id"], {"actor": "test-cleanup"})
            for _ in range(100):
                with service._auto_resume_lock:
                    if reviewed["id"] not in service._auto_resume_jobs:
                        break
                time.sleep(0.01)

    def test_startup_recovers_all_intents_and_only_waits_at_durable_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))
            first = AgentService(config)
            immediate = self.reviewed_job(first)
            boundary = self.reviewed_job(first)
            for payload in (immediate, boundary):
                stored = first.checkpoint_store.load_job(payload["id"])
                stored.state = JobState.WAITING_HUMAN
                stored.continuous_run_requested = True
                if payload is boundary:
                    stored.wait_boundary_fingerprint = "durable-boundary"
                first.checkpoint_store.save(stored)

            restarted = AgentService(
                config,
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            with mock.patch.object(
                restarted,
                "_arm_continuous_resume",
            ) as arm:
                recovered = restarted.recover_durable_continuous_runs()

            self.assertEqual(
                recovered,
                sorted([immediate["id"], boundary["id"]]),
            )
            requirements = {
                call.args[0]: call.kwargs["require_page_change"]
                for call in arm.call_args_list
            }
            self.assertFalse(requirements[immediate["id"]])
            self.assertTrue(requirements[boundary["id"]])
            self.assertEqual(
                restarted.recover_durable_continuous_runs(),
                [],
            )

    def test_http_service_startup_arms_durable_recovery_before_serving(self):
        service = mock.MagicMock()
        server = mock.MagicMock()
        server.server_address = ("127.0.0.1", 4267)
        server.serve_forever.side_effect = KeyboardInterrupt()
        with mock.patch(
            "visa_agent.factory.build_service",
            return_value=service,
        ), mock.patch(
            "visa_agent.service.load_config",
            return_value=AgentConfig(),
        ), mock.patch(
            "visa_agent.service.ThreadingHTTPServer",
            return_value=server,
        ):
            run_server("127.0.0.1", 4267)

        service.recover_durable_continuous_runs.assert_called_once_with()
        service.shutdown.assert_called_once_with()
        server.serve_forever.assert_called_once_with()
        server.server_close.assert_called_once_with()

    def test_http_service_normal_serve_exit_uses_same_shutdown_path(self):
        service = mock.MagicMock()
        server = mock.MagicMock()
        server.server_address = ("127.0.0.1", 4267)
        with mock.patch(
            "visa_agent.factory.build_service",
            return_value=service,
        ), mock.patch(
            "visa_agent.service.load_config",
            return_value=AgentConfig(),
        ), mock.patch(
            "visa_agent.service.ThreadingHTTPServer",
            return_value=server,
        ):
            run_server("127.0.0.1", 4267)

        service.recover_durable_continuous_runs.assert_called_once_with()
        service.shutdown.assert_called_once_with()
        server.serve_forever.assert_called_once_with()
        server.server_close.assert_called_once_with()

    def test_sigterm_is_routed_through_keyboard_interrupt_finally_path(self):
        with self.assertRaises(KeyboardInterrupt):
            service_module._interrupt_server_on_sigterm(None, None)

    def test_auto_resume_uses_persisted_boundary_before_first_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))

            class ChangedBrowser(self.FakeBrowser):
                def observe_lightweight(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/"
                            "complete/complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="",
                        page_id="personal-1",
                        control_values={"personal.surname": "LI"},
                    )

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class Runtime:
                def __init__(runtime_self):
                    runtime_self.browser = ChangedBrowser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, job):
                    runtime_self.run_count += 1
                    job.state = JobState.REVIEW_REQUIRED
                    job.human_checkpoint = "Review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            runtimes = []
            service = AgentService(
                config,
                runtime_factory=lambda _job: (
                    runtimes.append(Runtime()) or runtimes[-1]
                ),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.wait_boundary_fingerprint = observation_fingerprint(
                stored,
                BrowserObservation(
                    url=(
                        "https://ceac.state.gov/GenNIV/General/"
                        "complete/complete_personal.aspx?node=Personal1"
                    ),
                    title="Personal Information 1",
                    visible_text="",
                    page_id="personal-1",
                    control_values={"personal.surname": "ZHANG"},
                ),
            )
            service.checkpoint_store.save(stored)

            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=True,
            )
            for _ in range(150):
                current = service.get_job(reviewed["id"])
                if current["state"] == "review_required":
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertEqual(runtimes[0].run_count, 1)
            self.assertFalse(current["continuous_run_requested"])
            service._release_runtime(reviewed["id"])

    def test_auto_resume_observes_terminal_without_starting_again(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))

            class ReviewBrowser(self.FakeBrowser):
                def observe_lightweight(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/"
                            "Review/ReviewReview.aspx?node=ReviewReview"
                        ),
                        title="Review Application",
                        visible_text="",
                        page_id="review",
                    )

                def set_visual_status(browser_self, state, message):
                    browser_self.visual_status = (state, message)

            class Plans:
                @staticmethod
                def match(_observation):
                    return None

                @staticmethod
                def terminal_reason(_observation):
                    return "最终签名和提交前停止"

            class Runtime:
                def __init__(runtime_self):
                    runtime_self.browser = ReviewBrowser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, _job):
                    runtime_self.run_count += 1
                    raise AssertionError("Review must not start another run")

            runtimes = []
            service = AgentService(
                config,
                runtime_factory=lambda _job: (
                    runtimes.append(Runtime()) or runtimes[-1]
                ),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.automatic_retry_pending = True
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            stored.automatic_retry_count = 2
            service.checkpoint_store.save(stored)

            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=True,
            )
            for _ in range(100):
                current = service.get_job(reviewed["id"])
                if current["state"] == "review_required":
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertEqual(runtimes[0].run_count, 0)
            self.assertFalse(current["continuous_run_requested"])
            self.assertFalse(current["automatic_retry_pending"])
            self.assertEqual(current["automatic_retry_after"], "")
            self.assertEqual(current["automatic_retry_count"], 0)
            self.assertEqual(
                runtimes[0].browser.visual_status[0],
                "paused",
            )
            service._release_runtime(reviewed["id"])

    def test_auto_resume_observation_failure_is_visible_and_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))

            class FlakyBrowser(self.FakeBrowser):
                def __init__(browser_self):
                    super().__init__()
                    browser_self.calls = 0

                def observe_lightweight(browser_self):
                    browser_self.calls += 1
                    if browser_self.calls <= 3:
                        raise RuntimeError("temporary browser disconnect")
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/"
                            "complete/complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="",
                        page_id="reconnected",
                    )

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class Runtime:
                def __init__(runtime_self):
                    runtime_self.browser = FlakyBrowser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, job):
                    runtime_self.run_count += 1
                    job.state = JobState.REVIEW_REQUIRED
                    job.human_checkpoint = "Review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            runtimes = []

            def factory(_job):
                runtime = Runtime()
                if runtimes:
                    # The replacement represents a newly-created Playwright
                    # connection and is healthy.
                    runtime.browser.calls = 3
                runtimes.append(runtime)
                return runtime

            service = AgentService(config, runtime_factory=factory)
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.wait_boundary_fingerprint = "old-boundary"
            service.checkpoint_store.save(stored)

            service._arm_continuous_resume(
                reviewed["id"],
                require_page_change=True,
            )
            for _ in range(500):
                current = service.get_job(reviewed["id"])
                if (
                    current["state"] == "review_required"
                    and not current["continuous_run_requested"]
                ):
                    break
                time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertFalse(current["continuous_run_requested"])
            self.assertEqual(runtimes[0].run_count, 0)
            self.assertEqual(runtimes[1].run_count, 1)
            self.assertTrue(any(
                event["kind"] == "browser_runtime_retry_scheduled"
                for event in current["events"]
            ))
            service._release_runtime(reviewed["id"])

    def test_service_restart_recovers_orphaned_waiting_human_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))
            runtimes = []
            first = AgentService(
                config,
                runtime_factory=lambda _job: (
                    runtimes.append(self.FakeRuntime()) or runtimes[-1]
                ),
            )
            reviewed = self.reviewed_job(first)
            first.open_job(reviewed["id"])
            self.assertTrue(first.get_job(reviewed["id"])["runtime_open"])

            restarted = AgentService(config, runtime_factory=lambda _job: None)
            recovered = restarted.get_job(reviewed["id"])

            self.assertEqual(recovered["state"], "waiting_human")
            self.assertEqual(
                recovered["wait_kind"],
                "manual_page_change",
            )
            self.assertIn(
                "manually retrieve",
                recovered["human_checkpoint"],
            )
            self.assertFalse(recovered["runtime_open"])
            self.assertFalse(any(
                event["kind"] == "orphaned_runtime_recovered"
                for event in recovered["events"]
            ))
            first._release_runtime(reviewed["id"])

    def test_completed_job_releases_runtime_and_reports_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            runtimes = []

            class CompletedRuntime(self.FakeRuntime):
                def run(runtime_self, job):
                    job.state = JobState.COMPLETED
                    runtime_self.checkpoint_store.save(job)
                    return job

            def factory(_job):
                runtime = CompletedRuntime()
                runtimes.append(runtime)
                return runtime

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=factory,
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])

            completed = service.start_job(reviewed["id"])
            observed = service.get_job(reviewed["id"])

            self.assertEqual(completed["state"], "completed")
            self.assertFalse(observed["runtime_open"])
            self.assertTrue(runtimes[0].browser.closed)
            self.assertTrue(
                runtimes[0].browser.profile_purge_requested
            )

    def test_third_party_blocked_runtime_cannot_leave_false_auto_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            class BlockedRuntime(self.FakeRuntime):
                def run(runtime_self, job):
                    job.state = JobState.BLOCKED
                    job.continuous_run_requested = True
                    job.automatic_retry_pending = True
                    job.automatic_retry_after = (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=30)
                    ).isoformat()
                    job.automatic_retry_count = 2
                    job.automatic_retry_kind = "provider"
                    runtime_self.checkpoint_store.save(job)
                    return job

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: BlockedRuntime(),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])

            blocked = service.start_job(reviewed["id"])
            current = service.get_job(reviewed["id"])

            self.assertEqual(blocked["state"], "blocked")
            self.assertFalse(blocked["continuous_run_requested"])
            self.assertFalse(blocked["automatic_retry_pending"])
            self.assertEqual(blocked["automatic_retry_kind"], "")
            self.assertFalse(current["continuous_run_requested"])
            service._release_runtime(reviewed["id"])

    def test_sync_and_start_share_atomic_per_job_lifecycle_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            run_started = threading.Event()
            release_run = threading.Event()
            captured_values = []

            class CapturingRuntime(self.FakeRuntime):
                def run(runtime_self, job):
                    captured_values.append(job.fields[0].value)
                    run_started.set()
                    release_run.wait(timeout=2)
                    job.state = JobState.WAITING_HUMAN
                    return job

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: CapturingRuntime(),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])

            original_load = service._load_job
            sync_reached_load = threading.Event()
            release_sync_load = threading.Event()

            def delayed_load(job_id):
                if threading.current_thread().name == "sync-request":
                    sync_reached_load.set()
                    release_sync_load.wait(timeout=2)
                return original_load(job_id)

            service._load_job = delayed_load
            outcomes = {}
            sync_payload = {
                "actor": "consultant-2",
                "autoNext": True,
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "LI",
                    "confidence": 1.0,
                }],
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "LI",
                }],
            }

            sync_thread = threading.Thread(
                name="sync-request",
                target=lambda: outcomes.setdefault(
                    "sync", service.sync_job(reviewed["id"], sync_payload)
                ),
            )
            start_thread = threading.Thread(
                name="start-request",
                target=lambda: outcomes.setdefault(
                    "start", service.start_job(reviewed["id"])
                ),
            )
            sync_thread.start()
            self.assertTrue(sync_reached_load.wait(timeout=1))
            start_thread.start()
            time.sleep(0.03)
            with service._active_jobs_lock:
                self.assertNotIn(reviewed["id"], service._active_jobs)
            release_sync_load.set()
            sync_thread.join(timeout=2)
            self.assertTrue(run_started.wait(timeout=1))
            release_run.set()
            start_thread.join(timeout=2)
            service._load_job = original_load

            self.assertFalse(sync_thread.is_alive())
            self.assertFalse(start_thread.is_alive())
            self.assertEqual(outcomes["sync"]["fields"][0]["value"], "LI")
            self.assertEqual(captured_values, ["LI"])
            service._release_runtime(reviewed["id"])

    def test_cancelled_checkpoint_wins_over_late_browser_save(self):
        with tempfile.TemporaryDirectory() as directory:
            run_started = threading.Event()
            release_late_save = threading.Event()
            runtimes = []

            class LateSavingRuntime(self.FakeRuntime):
                def run(runtime_self, job):
                    job.state = JobState.FILLING_FORM
                    runtime_self.checkpoint_store.save(job)
                    run_started.set()
                    release_late_save.wait(timeout=2)
                    job.state = JobState.WAITING_HUMAN
                    runtime_self.checkpoint_store.save(job)
                    return job

            def factory(_job):
                runtime = LateSavingRuntime()
                runtimes.append(runtime)
                return runtime

            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=factory,
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            outcomes = {}
            start_thread = threading.Thread(
                target=lambda: outcomes.setdefault(
                    "start", service.start_job(reviewed["id"])
                )
            )
            start_thread.start()
            self.assertTrue(run_started.wait(timeout=1))
            cancel_thread = threading.Thread(
                target=lambda: outcomes.setdefault(
                    "cancel",
                    service.cancel_job(reviewed["id"], {"actor": "test"}),
                )
            )
            cancel_thread.start()
            for _ in range(100):
                if (
                    service.checkpoint_store.load_job(reviewed["id"]).state
                    == JobState.CANCELLED
                ):
                    break
                time.sleep(0.01)
            release_late_save.set()
            cancel_thread.join(timeout=2)
            start_thread.join(timeout=2)

            stored = service.checkpoint_store.load_job(reviewed["id"])
            self.assertFalse(cancel_thread.is_alive())
            self.assertFalse(start_thread.is_alive())
            self.assertEqual(outcomes["cancel"]["state"], "cancelled")
            self.assertEqual(stored.state, JobState.CANCELLED)
            self.assertFalse(
                service.get_job(reviewed["id"])["runtime_open"]
            )
            self.assertTrue(runtimes[0].browser.closed)

    def test_terminal_jobs_cannot_restart(self):
        for state, wait_kind in (
            (JobState.REVIEW_REQUIRED, ""),
            (JobState.BLOCKED, ""),
            (JobState.FAILED, ""),
        ):
            with self.subTest(state=state.value, waitKind=wait_kind):
                with tempfile.TemporaryDirectory() as directory:
                    runtimes = []
                    service = AgentService(
                        AgentConfig(data_dir=Path(directory)),
                        runtime_factory=lambda _job: (
                            runtimes.append(self.FakeRuntime())
                            or runtimes[-1]
                        ),
                    )
                    reviewed = self.reviewed_job(service)
                    stored = service.checkpoint_store.load_job(
                        reviewed["id"]
                    )
                    stored.state = state
                    stored.wait_kind = wait_kind
                    stored.continuous_run_requested = False
                    stored.human_checkpoint = "preserve this boundary"
                    service.checkpoint_store.save(stored)

                    with self.assertRaises(ServiceError) as raised:
                        service.start_job(reviewed["id"])

                    self.assertEqual(raised.exception.status, 409)
                    self.assertEqual(runtimes, [])

                    opened = service.open_job(reviewed["id"])
                    self.assertEqual(opened["state"], state.value)
                    self.assertEqual(opened["wait_kind"], wait_kind)
                    self.assertEqual(
                        opened["human_checkpoint"],
                        "preserve this boundary",
                    )
                    with self.assertRaises(ServiceError) as raised_after_open:
                        service.start_job(reviewed["id"])
                    self.assertEqual(raised_after_open.exception.status, 409)
                    self.assertEqual(len(runtimes), 1)
                    service._release_runtime(reviewed["id"])

    def test_explicit_continue_reopens_manual_boundary_but_watcher_cannot(self):
        with tempfile.TemporaryDirectory() as directory:
            runtimes = []
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: (
                    runtimes.append(self.FakeRuntime())
                    or runtimes[-1]
                ),
            )
            reviewed = self.reviewed_job(service)
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.wait_kind = "manual_hard_boundary"
            stored.continuous_run_requested = False
            stored.human_checkpoint = "consultant must handle live CEAC page"
            service.checkpoint_store.save(stored)

            watcher_epoch = service._watcher_epoch(stored)
            with self.assertRaises(ServiceError) as watcher_error:
                service.start_job(
                    reviewed["id"],
                    expected_watcher_epoch=watcher_epoch,
                )
            self.assertEqual(watcher_error.exception.status, 409)
            self.assertEqual(runtimes, [])

            resumed = service.start_job(reviewed["id"])
            persisted = service.checkpoint_store.load_job(reviewed["id"])

            self.assertEqual(resumed["state"], "waiting_human")
            self.assertEqual(runtimes[0].run_count, 1)
            self.assertTrue(any(
                event.kind == "explicit_manual_boundary_reopened"
                for event in persisted.events
            ))
            service._release_runtime(reviewed["id"])

    def test_explicit_resume_reobserves_pending_next_hard_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            runtimes = []
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: (
                    runtimes.append(self.FakeRuntime())
                    or runtimes[-1]
                ),
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.wait_kind = "manual_hard_boundary"
            stored.continuous_run_requested = False
            stored.human_checkpoint = "late CEAC navigation"
            stored.pending_action = ComputerAction(
                kind=ActionKind.CLICK,
                target_hint="Next: Continue",
                reason="Deterministic fixed CEAC Next control",
                id="pending-next-reconciliation",
            )
            service.checkpoint_store.save(stored)

            resumed = service.start_job(reviewed["id"])
            persisted = service.checkpoint_store.load_job(reviewed["id"])

            self.assertEqual(resumed["state"], "waiting_human")
            self.assertEqual(runtimes[0].run_count, 1)
            self.assertEqual(
                persisted.pending_action.id,
                "pending-next-reconciliation",
            )
            self.assertTrue(any(
                event.kind == "hard_navigation_boundary_reopened"
                for event in persisted.events
            ))
            service._release_runtime(reviewed["id"])

    def test_explicit_resume_migrates_one_legacy_repeater_order_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            runtimes = []
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: (
                    runtimes.append(self.FakeRuntime())
                    or runtimes[-1]
                ),
            )
            reviewed = self.reviewed_job(service)
            stored = service.checkpoint_store.load_job(reviewed["id"])
            field_id = (
                "ceac.work_education3.additional.languages.ensure.2"
            )
            page_plan_id = "ceac-plan-work_education3"
            failure_key = f"{page_plan_id}::{field_id}"
            stored.state = JobState.WAITING_HUMAN
            stored.wait_kind = "manual_hard_boundary"
            stored.continuous_run_requested = False
            stored.current_page_plan_id = page_plan_id
            stored.human_checkpoint = (
                "Add Another 连续三次未增加表格行；V2 已停止继续点击并关闭"
                "自动唤醒，请检查当前第一行是否有未填或网页校验提示。"
            )
            stored.visual_failure_counts[failure_key] = 3
            stored.record(
                "repeater_growth_not_observed",
                "legacy ordering failure",
                fieldId=field_id,
                pagePlanId=page_plan_id,
                failureCount=3,
            )
            service.checkpoint_store.save(stored)

            resumed = service.start_job(reviewed["id"])
            persisted = service.checkpoint_store.load_job(reviewed["id"])

            self.assertEqual(resumed["state"], "waiting_human")
            self.assertEqual(runtimes[0].run_count, 1)
            self.assertNotIn(failure_key, persisted.visual_failure_counts)
            self.assertTrue(any(
                event.kind == "repeater_order_upgrade_reopened"
                for event in persisted.events
            ))
            service._release_runtime(reviewed["id"])

            persisted.state = JobState.WAITING_HUMAN
            persisted.wait_kind = "manual_hard_boundary"
            persisted.continuous_run_requested = False
            persisted.human_checkpoint = stored.human_checkpoint
            persisted.visual_failure_counts[failure_key] = 3
            persisted.record(
                "repeater_growth_not_observed",
                "legacy javascript-link dispatch failure",
                fieldId=field_id,
                pagePlanId=page_plan_id,
                failureCount=3,
            )
            service.checkpoint_store.save(persisted)

            resumed_dispatch = service.start_job(reviewed["id"])
            persisted_dispatch = service.checkpoint_store.load_job(
                reviewed["id"]
            )
            self.assertEqual(resumed_dispatch["state"], "waiting_human")
            self.assertEqual(runtimes[1].run_count, 1)
            self.assertNotIn(
                failure_key,
                persisted_dispatch.visual_failure_counts,
            )
            self.assertTrue(any(
                event.kind == "repeater_postback_upgrade_reopened"
                for event in persisted_dispatch.events
            ))
            service._release_runtime(reviewed["id"])

            persisted_dispatch.state = JobState.WAITING_HUMAN
            persisted_dispatch.wait_kind = "manual_hard_boundary"
            persisted_dispatch.continuous_run_requested = False
            persisted_dispatch.human_checkpoint = stored.human_checkpoint
            persisted_dispatch.visual_failure_counts[failure_key] = 3
            persisted_dispatch.record(
                "repeater_growth_not_observed",
                "post-dispatch-upgrade real failure",
                fieldId=field_id,
                pagePlanId=page_plan_id,
                failureCount=3,
            )
            service.checkpoint_store.save(persisted_dispatch)

            resumed_executor = service.start_job(reviewed["id"])
            persisted_executor = service.checkpoint_store.load_job(
                reviewed["id"]
            )
            self.assertEqual(resumed_executor["state"], "waiting_human")
            self.assertEqual(runtimes[2].run_count, 1)
            self.assertTrue(any(
                event.kind == "repeater_executor_upgrade_reopened"
                for event in persisted_executor.events
            ))
            service._release_runtime(reviewed["id"])

            persisted_executor.state = JobState.WAITING_HUMAN
            persisted_executor.wait_kind = "manual_hard_boundary"
            persisted_executor.continuous_run_requested = False
            persisted_executor.human_checkpoint = stored.human_checkpoint
            persisted_executor.visual_failure_counts[failure_key] = 3
            persisted_executor.record(
                "repeater_growth_not_observed",
                "post-executor-upgrade real failure",
                fieldId=field_id,
                pagePlanId=page_plan_id,
                failureCount=3,
            )
            service.checkpoint_store.save(persisted_executor)

            resumed_diagnostic = service.start_job(reviewed["id"])
            persisted_diagnostic = service.checkpoint_store.load_job(
                reviewed["id"]
            )
            self.assertEqual(resumed_diagnostic["state"], "waiting_human")
            self.assertEqual(runtimes[3].run_count, 1)
            self.assertTrue(any(
                event.kind == "repeater_diagnostic_upgrade_reopened"
                for event in persisted_diagnostic.events
            ))
            service._release_runtime(reviewed["id"])

            persisted_diagnostic.state = JobState.WAITING_HUMAN
            persisted_diagnostic.wait_kind = "manual_hard_boundary"
            persisted_diagnostic.continuous_run_requested = False
            persisted_diagnostic.human_checkpoint = stored.human_checkpoint
            persisted_diagnostic.visual_failure_counts[failure_key] = 3
            persisted_diagnostic.record(
                "repeater_growth_not_observed",
                "post-diagnostic real failure",
                fieldId=field_id,
                pagePlanId=page_plan_id,
                failureCount=3,
            )
            service.checkpoint_store.save(persisted_diagnostic)

            resumed_native_submit = service.start_job(reviewed["id"])
            persisted_native_submit = service.checkpoint_store.load_job(
                reviewed["id"]
            )
            self.assertEqual(
                resumed_native_submit["state"], "waiting_human"
            )
            self.assertEqual(runtimes[4].run_count, 1)
            self.assertTrue(any(
                event.kind == "repeater_native_submit_upgrade_reopened"
                for event in persisted_native_submit.events
            ))
            service._release_runtime(reviewed["id"])

            persisted_native_submit.state = JobState.WAITING_HUMAN
            persisted_native_submit.wait_kind = "manual_hard_boundary"
            persisted_native_submit.continuous_run_requested = False
            persisted_native_submit.human_checkpoint = (
                stored.human_checkpoint
            )
            persisted_native_submit.visual_failure_counts[failure_key] = 3
            persisted_native_submit.record(
                "repeater_growth_not_observed",
                "post-native-submit-upgrade real failure",
                fieldId=field_id,
                pagePlanId=page_plan_id,
                failureCount=3,
            )
            service.checkpoint_store.save(persisted_native_submit)

            resumed_explicit = service.start_job(reviewed["id"])
            persisted_explicit = service.checkpoint_store.load_job(
                reviewed["id"]
            )
            self.assertEqual(resumed_explicit["state"], "waiting_human")
            self.assertEqual(runtimes[5].run_count, 1)
            self.assertTrue(any(
                event.kind == "explicit_manual_boundary_reopened"
                for event in persisted_explicit.events
            ))
            service._release_runtime(reviewed["id"])

    def test_terminal_observation_closes_non_next_pending_without_applying(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            reviewed = self.reviewed_job(service)
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.pending_action = ComputerAction(
                kind=ActionKind.TYPE,
                field_id="personal.surname",
                value="ZHANG",
                id="pending-value-at-review",
            )
            service.checkpoint_store.save(stored)

            service._finish_auto_resume_at_terminal(
                reviewed["id"],
                "Review/Sign boundary",
            )
            current = service.get_job(reviewed["id"])

            self.assertEqual(current["state"], "review_required")
            self.assertIsNone(current["pending_action"])
            self.assertNotIn(
                "pending-value-at-review",
                current["applied_action_ids"],
            )
            self.assertFalse(current["continuous_run_requested"])

    def test_legacy_wait_kind_migration_preserves_manual_page_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory))
            )
            reviewed = self.reviewed_job(service)
            stored = service.checkpoint_store.load_job(reviewed["id"])
            payload = to_primitive(stored)
            payload["state"] = JobState.WAITING_HUMAN.value
            payload["continuous_run_requested"] = True
            payload["human_checkpoint"] = "Complete CAPTCHA in CEAC"
            payload["wait_boundary_fingerprint"] = "captcha-boundary"
            payload.pop("wait_kind", None)

            migrated = job_from_primitive(payload)

            self.assertEqual(migrated.wait_kind, "manual_page_change")
            self.assertEqual(
                migrated.human_checkpoint,
                "Complete CAPTCHA in CEAC",
            )

    @staticmethod
    def legacy_constraint_boundary(
        stored,
        *,
        error_type="RuntimeError",
        plan_source="deterministic-dom",
    ):
        checkpoint = (
            "网页控件声明的文本约束无法容纳当前值；系统在写入前"
            "已停止该动作，未产生网页截断或重复填写。"
        )
        stored.state = JobState.WAITING_HUMAN
        stored.wait_kind = "manual_hard_boundary"
        stored.continuous_run_requested = False
        stored.sync_resume_pending = False
        stored.human_checkpoint = checkpoint
        stored.current_page_plan_id = "ceac-plan-personal"
        stored.execution_generation = 7
        stored.pending_action = None
        stored.record(
            "continuous_run_armed",
            "Continuous Gemini execution is armed until Review/Sign",
            generation=7,
        )
        stored.record(
            "plan_proposed",
            "Computer-use runtime proposed a page action plan",
            actionCount=1,
            batched=False,
            source=plan_source,
        )
        stored.record(
            "control_constraint_unavailable",
            "The live CEAC control rejected its approved text contract "
            "before any DOM mutation",
            fieldId="personal.surname",
            pagePlanId="ceac-plan-personal",
            errorType=error_type,
        )
        stored.record("human_checkpoint", checkpoint)
        return checkpoint

    def test_legacy_binding_constraint_boundary_rearms_one_safe_replan(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory))
            )
            reviewed = self.reviewed_job(service)
            stored = service.checkpoint_store.load_job(reviewed["id"])
            self.legacy_constraint_boundary(stored)

            migrated = job_from_primitive(to_primitive(stored))

            self.assertEqual(migrated.state, JobState.READY_FOR_FORM)
            self.assertEqual(migrated.wait_kind, "runtime_recovery")
            self.assertTrue(migrated.continuous_run_requested)
            self.assertIsNone(migrated.human_checkpoint)
            self.assertIsNone(migrated.pending_action)
            self.assertIn(
                "personal.surname",
                migrated.binding_refresh_field_ids,
            )
            markers = [
                event
                for event in migrated.events
                if event.kind
                == "legacy_constraint_boundary_reclassified"
            ]
            self.assertEqual(len(markers), 1)
            self.assertEqual(
                markers[0].detail["retryScope"],
                "read_only_preflight",
            )

            # Once persisted, the migration marker makes the recovery
            # idempotent rather than adding a new retry on every load.
            round_tripped = job_from_primitive(to_primitive(migrated))
            self.assertEqual(len([
                event
                for event in round_tripped.events
                if event.kind
                == "legacy_constraint_boundary_reclassified"
            ]), 1)
            self.assertTrue(round_tripped.continuous_run_requested)

    def test_typed_value_constraint_remains_a_hard_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory))
            )
            reviewed = self.reviewed_job(service)
            stored = service.checkpoint_store.load_job(reviewed["id"])
            checkpoint = self.legacy_constraint_boundary(
                stored,
                error_type="ControlValueConstraintError",
            )

            preserved = job_from_primitive(to_primitive(stored))

            self.assertEqual(preserved.state, JobState.WAITING_HUMAN)
            self.assertEqual(
                preserved.wait_kind,
                "manual_hard_boundary",
            )
            self.assertFalse(preserved.continuous_run_requested)
            self.assertEqual(preserved.human_checkpoint, checkpoint)
            self.assertNotIn(
                "legacy_constraint_boundary_reclassified",
                {event.kind for event in preserved.events},
            )

    def test_legacy_constraint_recovery_requires_the_exact_event_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory))
            )
            reviewed = self.reviewed_job(service)
            stored = service.checkpoint_store.load_job(reviewed["id"])
            checkpoint = self.legacy_constraint_boundary(stored)
            stored.record(
                "dispatch_receipt_conflict",
                "A later hard consistency boundary must remain authoritative",
            )

            preserved = job_from_primitive(to_primitive(stored))

            self.assertEqual(preserved.state, JobState.WAITING_HUMAN)
            self.assertEqual(
                preserved.wait_kind,
                "manual_hard_boundary",
            )
            self.assertFalse(preserved.continuous_run_requested)
            self.assertEqual(preserved.human_checkpoint, checkpoint)

    def test_constraint_reclassification_marker_never_reopens_a_later_hard_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory))
            )
            reviewed = self.reviewed_job(service)
            stored = service.checkpoint_store.load_job(reviewed["id"])
            checkpoint = self.legacy_constraint_boundary(stored)
            migrated = job_from_primitive(to_primitive(stored))
            migrated.state = JobState.WAITING_HUMAN
            migrated.wait_kind = "manual_hard_boundary"
            migrated.continuous_run_requested = False
            migrated.human_checkpoint = checkpoint
            migrated.record(
                "plan_proposed",
                "Computer-use runtime proposed a page action plan",
                actionCount=1,
                batched=False,
                source="deterministic-dom",
            )
            migrated.record(
                "control_constraint_unavailable",
                "The live CEAC control rejected its approved text contract "
                "before any DOM mutation",
                fieldId="personal.surname",
                pagePlanId="ceac-plan-personal",
                errorType="RuntimeError",
            )
            migrated.record("human_checkpoint", checkpoint)

            preserved = job_from_primitive(to_primitive(migrated))

            self.assertEqual(preserved.state, JobState.WAITING_HUMAN)
            self.assertEqual(
                preserved.wait_kind,
                "manual_hard_boundary",
            )
            self.assertFalse(preserved.continuous_run_requested)
            self.assertEqual(len([
                event
                for event in preserved.events
                if event.kind
                == "legacy_constraint_boundary_reclassified"
            ]), 1)

    def test_saved_legacy_manual_entry_is_not_misclassified_as_hard_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory))
            )
            reviewed = self.reviewed_job(service)
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = False
            stored.wait_kind = "manual_hard_boundary"
            stored.human_checkpoint = (
                "Consultant must manually retrieve the already-created "
                "DS-160 application and enter a formal form page before "
                "starting Gemini"
            )
            stored.record(
                "browser_opened_for_manual_entry",
                "Browser opened without invoking the computer-use model",
            )

            migrated = job_from_primitive(to_primitive(stored))

            self.assertEqual(migrated.wait_kind, "manual_page_change")
            self.assertEqual(migrated.state, JobState.WAITING_HUMAN)

    def test_manual_entry_text_with_true_hard_provenance_stays_hard(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                AgentConfig(data_dir=Path(directory))
            )
            reviewed = self.reviewed_job(service)
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.wait_kind = "manual_hard_boundary"
            stored.human_checkpoint = (
                "Consultant must manually retrieve the already-created "
                "DS-160 application and enter a formal form page before "
                "starting Gemini"
            )
            stored.record(
                "browser_opened_for_manual_entry",
                "Browser opened without invoking the computer-use model",
            )
            stored.record(
                "human_checkpoint",
                "A true consistency boundary was recorded",
            )

            migrated = job_from_primitive(to_primitive(stored))

            self.assertEqual(
                migrated.wait_kind,
                "manual_hard_boundary",
            )

    def test_startup_disarms_legacy_failed_continuous_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig(data_dir=Path(directory))
            first = AgentService(config)
            reviewed = self.reviewed_job(first)
            stored = first.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.FAILED
            stored.continuous_run_requested = True
            stored.automatic_retry_pending = True
            stored.automatic_retry_kind = "provider"
            first.checkpoint_store.save(stored)

            restarted = AgentService(
                config,
                runtime_factory=lambda _job: self.FakeRuntime(),
            )
            recovered = restarted.recover_durable_continuous_runs()
            current = restarted.get_job(reviewed["id"])

            self.assertEqual(recovered, [])
            self.assertEqual(current["state"], "failed")
            self.assertFalse(current["continuous_run_requested"])
            self.assertFalse(current["automatic_retry_pending"])
            self.assertTrue(any(
                event["kind"] == "legacy_terminal_run_disarmed"
                for event in current["events"]
            ))

    def test_unexpected_watcher_exception_is_visible_and_self_heals(self):
        with tempfile.TemporaryDirectory() as directory:
            class Browser(self.FakeBrowser):
                def observe_lightweight(browser_self):
                    return BrowserObservation(
                        url=(
                            "https://ceac.state.gov/GenNIV/General/complete/"
                            "complete_personal.aspx?node=Personal1"
                        ),
                        title="Personal Information 1",
                        visible_text="Personal Information 1",
                        page_id="personal-1",
                    )

            class Plans:
                @staticmethod
                def match(_observation):
                    return object()

                @staticmethod
                def terminal_reason(_observation):
                    return ""

            class Runtime:
                def __init__(runtime_self):
                    runtime_self.browser = Browser()
                    runtime_self.page_plans = Plans()
                    runtime_self.checkpoint_store = None
                    runtime_self.run_count = 0

                def run(runtime_self, job):
                    runtime_self.run_count += 1
                    job.state = JobState.REVIEW_REQUIRED
                    job.human_checkpoint = "Review boundary"
                    runtime_self.checkpoint_store.save(job)
                    return job

            runtime = Runtime()
            service = AgentService(
                AgentConfig(data_dir=Path(directory)),
                runtime_factory=lambda _job: runtime,
            )
            reviewed = self.reviewed_job(service)
            service.open_job(reviewed["id"])
            stored = service.checkpoint_store.load_job(reviewed["id"])
            stored.state = JobState.WAITING_HUMAN
            stored.continuous_run_requested = True
            stored.wait_kind = "automatic_retry"
            stored.automatic_retry_pending = True
            stored.automatic_retry_after = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            stored.automatic_retry_kind = "provider"
            service.checkpoint_store.save(stored)
            original_delay = service._automatic_retry_delay_seconds
            calls = {"count": 0}

            def fail_once(job, now=None):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise RuntimeError("injected watcher bug")
                return original_delay(job, now=now)

            with mock.patch.object(
                service,
                "_automatic_retry_delay_seconds",
                side_effect=fail_once,
            ):
                service._arm_continuous_resume(
                    reviewed["id"],
                    require_page_change=False,
                )
                for _ in range(500):
                    current = service.get_job(reviewed["id"])
                    if current["state"] == "review_required":
                        break
                    time.sleep(0.01)

            self.assertEqual(current["state"], "review_required")
            self.assertEqual(runtime.run_count, 1)
            self.assertTrue(any(
                event["kind"] == "auto_resume_degraded"
                and event["detail"].get("reason")
                == "auto_resume_monitor_exception"
                for event in current["events"]
            ))
            service._release_runtime(reviewed["id"])

    def test_health_explicitly_reports_isolated_unconfigured_mode(self):
        health = AgentService(AgentConfig()).health()
        self.assertTrue(health["ok"])
        self.assertFalse(health["connectedToDocFlow"])
        self.assertFalse(health["modelConfigured"])
        self.assertFalse(health["ocrConfigured"])
        self.assertFalse(health["browserConfigured"])
        self.assertEqual(health["mode"], "isolated")

    def test_health_does_not_report_keyless_gemini_as_configured(self):
        config = AgentConfig(
            computer_use=ProviderConfig(
                provider="gemini",
                model="gemini-computer-use",
            ),
            browser=ProviderConfig(provider="playwright"),
        )

        health = AgentService(config).health()
        computer_use = health["providers"]["computerUse"]

        self.assertFalse(health["modelConfigured"])
        self.assertFalse(computer_use["configured"])
        self.assertTrue(computer_use["credentialRequired"])
        self.assertFalse(computer_use["credentialConfigured"])
        self.assertEqual(
            computer_use["configurationIssue"],
            "api_key_missing",
        )
        self.assertTrue(health["providers"]["browser"]["configured"])
        self.assertTrue(health["browserConfigured"])
        self.assertNotIn("api_key", computer_use)

    def test_health_reports_gemini_ready_only_when_key_is_present(self):
        secret = "must-never-appear-in-health"
        config = AgentConfig(
            computer_use=ProviderConfig(
                provider="google",
                model="gemini-computer-use",
                api_key=secret,
            ),
            browser=ProviderConfig(provider="playwright"),
        )

        health = AgentService(config).health()
        computer_use = health["providers"]["computerUse"]

        self.assertTrue(health["modelConfigured"])
        self.assertTrue(computer_use["configured"])
        self.assertTrue(computer_use["credentialRequired"])
        self.assertTrue(computer_use["credentialConfigured"])
        self.assertNotIn(secret, str(health))

    def test_recognition_endpoint_shape_is_docflow_compatible_but_not_connected(self):
        sample = (
            ROOT / "examples" / "sample_passport_mrz.txt"
        ).read_text(encoding="utf-8")
        result = AgentService(AgentConfig()).recognize_text({
            "filename": "passport.txt",
            "documentType": "passport",
            "ocrText": sample,
        })
        fields = {item["id"]: item for item in result["fields"]}
        self.assertEqual(fields["personal.surname"]["value"], "ERIKSSON")
        self.assertEqual(fields["passport.number"]["value"], "L898902C3")

    def test_agent_code_has_no_docflow_backend_import(self):
        for source_path in (ROOT / "visa_agent").glob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            self.assertNotIn("from backend", source, source_path.name)
            self.assertNotIn("import backend", source, source_path.name)
            self.assertNotIn("import server", source, source_path.name)

    def test_isolated_job_review_api_does_not_auto_confirm_input(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(AgentConfig(data_dir=Path(directory)))
            created = service.create_job({
                "startUrl": "https://ceac.state.gov/GenNIV/form",
                "requiredFieldIds": ["personal.surname"],
                "fields": [{
                    "id": "personal.surname",
                    "value": "ZHANG",
                    "confidence": 0.9,
                    "risk_level": "low",
                    "confirmed": True,
                }],
            })
            self.assertEqual(created["state"], "waiting_review")
            self.assertFalse(created["fields"][0]["confirmed"])
            self.assertEqual(created["fields"][0]["risk_level"], "high")
            reviewed = service.review_job(created["id"], {
                "actor": "consultant-1",
                "decisions": [{
                    "fieldId": "personal.surname",
                    "approved": True,
                    "value": "ZHANG",
                }],
            })
            self.assertEqual(reviewed["state"], "ready_for_form")
            self.assertEqual(
                reviewed["fields"][0]["confirmation"]["confirmed_by"],
                "consultant-1",
            )

    def test_translation_and_transliteration_provider_is_exposed(self):
        class LanguageProvider:
            def translate(self, text, source_language, target_language):
                return f"translated:{text}"

            def transliterate(self, text, source_language, target_script="Latn"):
                return f"latin:{text}"

        service = AgentService(
            AgentConfig(), translation_provider=LanguageProvider()
        )
        self.assertEqual(
            service.transform_text({"text": "北京", "mode": "translate"})["text"],
            "translated:北京",
        )
        self.assertEqual(
            service.transform_text({"text": "北京", "mode": "transliterate"})["text"],
            "latin:北京",
        )

    def test_document_endpoint_routes_pdf_to_document_parser(self):
        class Parser:
            def parse(self, content, filename, media_type):
                self.seen = (content, filename, media_type)
                return "Surname ZHANG"

        class MustNotRunOCR:
            def recognize(self, content, filename, media_type):
                raise AssertionError("PDF should use document parser")

        parser = Parser()
        service = AgentService(
            AgentConfig(),
            recognizer=DocumentRecognizer(
                MustNotRunOCR(),
                UnconfiguredExtractionModel(),
                document_parser=parser,
            ),
        )
        result = service.recognize_document({
            "fileBase64": base64.b64encode(b"%PDF").decode(),
            "filename": "passport.pdf",
            "mediaType": "application/pdf",
            "documentType": "other",
        })
        self.assertTrue(result["raw_text_available"])
        self.assertEqual(parser.seen[1], "passport.pdf")


if __name__ == "__main__":
    unittest.main()
