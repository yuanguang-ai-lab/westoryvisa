"""Standalone HTTP service; it never imports or calls DocFlow."""

import base64
import binascii
import json
import queue
import re
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .config import load_config
from .models import (
    ExecutionLeaseRevoked,
    JobState,
    extracted_field_from_primitive,
    observation_fingerprint,
    to_primitive,
)
from .orchestrator import AgentOrchestrator
from .page_plans import PagePlanRegistry
from .providers import (
    PlainTextOCRProvider,
    ProviderNotConfigured,
    UnconfiguredExtractionModel,
)
from .profile_storage import purge_private_profile_path
from .recognition import DocumentRecognizer
from .recovery import recovery_credentials_from_primitive
from .storage import CheckpointProtectionError, FileCheckpointStore


class ServiceError(RuntimeError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


TERMINAL_JOB_STATES = frozenset({
    JobState.COMPLETED,
    JobState.CANCELLED,
    JobState.REVIEW_REQUIRED,
    JobState.BLOCKED,
    JobState.FAILED,
})

REVIEW_RUNTIME_LEASE_MINUTES = 60


def _public_job_payload(job):
    """Serialize status without returning DS-160 retrieval credentials."""
    payload = to_primitive(job)
    credentials = getattr(job, "recovery_credentials", None)
    if credentials is not None:
        payload["recovery_credentials"] = credentials.public_summary()
    return payload


def _parse_job_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ensure_review_lease(job, now=None):
    """Backfill one fixed Review/Sign lease without sliding it on reads."""
    if not (
        job.final_submission_boundary_reached
        and job.state in {
            JobState.REVIEW_REQUIRED,
            JobState.WAITING_HUMAN,
        }
    ):
        return False
    if _parse_job_timestamp(job.review_lease_expires_at) is not None:
        return False
    now = now or datetime.now(timezone.utc)
    boundary_at = _parse_job_timestamp(job.updated_at) or now
    job.review_lease_expires_at = (
        boundary_at + timedelta(minutes=REVIEW_RUNTIME_LEASE_MINUTES)
    ).isoformat()
    job.record(
        "review_lease_started",
        "Review/Sign browser retention is bounded by a durable cleanup lease",
        reviewLeaseExpiresAt=job.review_lease_expires_at,
    )
    return True


def _review_lease_expired(job, now=None):
    if not (
        job.final_submission_boundary_reached
        and job.state in {
            JobState.REVIEW_REQUIRED,
            JobState.WAITING_HUMAN,
        }
    ):
        return False
    deadline = _parse_job_timestamp(job.review_lease_expires_at)
    if deadline is None:
        return False
    return deadline <= (now or datetime.now(timezone.utc))


class _RuntimeStartupControl:
    """Cross-thread cancellation and exact-browser teardown during startup.

    Playwright is created on the runtime worker because its synchronous API is
    thread-affine.  The service thread still needs one safe escape hatch while
    ``browser.start`` is blocked: the factory publishes the job-owned browser
    after its private profile is configured, allowing ``emergency_close`` to
    terminate only that exact Chrome process.  A stop event also makes a late
    factory result close itself instead of becoming an orphan runtime.
    """

    def __init__(self):
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._browser = None

    def publish_browser(self, browser):
        with self._lock:
            self._browser = browser

    @property
    def browser(self):
        with self._lock:
            return self._browser

    @property
    def stop_requested(self):
        return self.stop_event.is_set()

    def request_stop(self):
        self.stop_event.set()

    def emergency_close(self):
        browser = self.browser
        emergency_close = getattr(browser, "emergency_close", None)
        if not callable(emergency_close):
            return False
        try:
            return bool(emergency_close())
        except Exception:
            return False


class _RuntimeStartupTimeout(TimeoutError):
    """Carries the quarantined worker so AgentService never loses ownership."""

    def __init__(self, message, worker):
        super().__init__(message)
        self.worker = worker


class _RuntimeStartupFailure(RuntimeError):
    """Carries every failed startup worker until its teardown really exits."""

    def __init__(self, cause, worker):
        super().__init__(
            "Browser runtime factory failed during owned startup"
        )
        self.cause = cause
        self.worker = worker


class _RuntimeBusy(RuntimeError):
    """A non-blocking observation found a legitimate command in flight."""


class _RuntimeCommand:
    """One cancellable command reservation for the thread-affine runtime.

    Queue wait and browser execution are different phases.  A caller may
    safely cancel a command before it starts, but once the worker authorizes
    it, the execution timeout is the only deadline allowed to poison the
    runtime.
    """

    def __init__(self, function):
        self.function = function
        self.reply = queue.Queue(maxsize=1)
        self.started = threading.Event()
        self._lock = threading.Lock()
        self._cancelled = False

    def try_start(self):
        with self._lock:
            if self._cancelled:
                return False
            self.started.set()
            return True

    def cancel_before_start(self):
        with self._lock:
            if self.started.is_set():
                return False
            self._cancelled = True
            return True


class _ExecutionLease:
    """One non-reusable cancellation token for one durable generation."""

    def __init__(self, job_id, generation):
        self.job_id = str(job_id)
        self.generation = max(0, int(generation))
        self._revoked = threading.Event()
        # Authorization is linearized under this gate, but a potentially hung
        # Playwright/CDP call runs outside it.  Revocation must remain bounded:
        # cancel/timeout can fence all *new* mutations and emergency-close the
        # exact job browser without deadlocking behind the call that failed.
        self._side_effect_gate = threading.RLock()
        self._inflight_side_effects = 0

    def revoke(self):
        with self._side_effect_gate:
            self._revoked.set()

    @property
    def revoked(self):
        return self._revoked.is_set()

    def assert_current(self):
        with self._side_effect_gate:
            if self.revoked:
                raise ExecutionLeaseRevoked(
                    "Execution generation lease is no longer current"
                )
        return False

    def run_side_effect(self, callback, *args, **kwargs):
        """Authorize one mutation atomically; never block lease revocation."""
        with self._side_effect_gate:
            if self.revoked:
                raise ExecutionLeaseRevoked(
                    "Execution generation lease is no longer current"
                )
            self._inflight_side_effects += 1
        try:
            return callback(*args, **kwargs)
        finally:
            with self._side_effect_gate:
                self._inflight_side_effects = max(
                    0, self._inflight_side_effects - 1
                )

    @property
    def inflight_side_effects(self):
        with self._side_effect_gate:
            return self._inflight_side_effects


class _RuntimeWorker:
    """Keep a browser runtime on the one thread that created Playwright.

    ``ThreadingHTTPServer`` may serve ``open`` and ``resume`` on different
    request threads.  Playwright's synchronous API is thread-affine, so the
    browser must be created, observed, and controlled by one long-lived worker
    instead of whichever HTTP thread happened to receive the latest request.
    """

    CLOSE_TIMEOUT_SECONDS = 5
    EMERGENCY_CLOSE_TIMEOUT_SECONDS = 3

    def __init__(self, runtime_factory, job, startup_timeout=30):
        self._runtime_factory = runtime_factory
        self._job = job
        self._startup_control = _RuntimeStartupControl()
        self._commands = queue.Queue()
        self._ready = threading.Event()
        self._state_lock = threading.Lock()
        self._runtime = None
        self._startup_error = None
        self._poisoned = False
        self._busy = False
        self._pending_commands = 0
        self._stop_requested = False
        self._last_failure = ""
        self._thread = threading.Thread(
            target=self._serve,
            name=f"agent-runtime-{str(job.id)[-8:]}",
            daemon=True,
        )
        self._thread.start()
        startup_timeout = max(0.01, float(startup_timeout))
        if not self._ready.wait(timeout=startup_timeout):
            self.poison("startup_timeout")
            self._startup_control.request_stop()
            self._request_stop()
            # The browser factory publishes only the browser tied to this
            # job's private profile.  Its adapter independently verifies that
            # ownership before terminating Chrome.
            self._startup_control.emergency_close()
            self._thread.join(timeout=self.EMERGENCY_CLOSE_TIMEOUT_SECONDS)
            raise _RuntimeStartupTimeout(
                "Browser runtime did not start within "
                f"{startup_timeout:g} seconds",
                self,
            )
        if self._startup_error is not None:
            raise _RuntimeStartupFailure(
                self._startup_error,
                self,
            )

    def _serve(self):
        try:
            try:
                if getattr(
                    self._runtime_factory,
                    "_docflow_accepts_startup_control",
                    False,
                ):
                    self._runtime = self._runtime_factory(
                        self._job,
                        self._startup_control,
                    )
                else:
                    self._runtime = self._runtime_factory(self._job)
            except Exception as error:
                self._startup_error = error
                return
            finally:
                self._ready.set()
            if self._startup_control.stop_requested:
                return
            while True:
                command = self._commands.get()
                if command is None:
                    return
                with self._state_lock:
                    self._pending_commands = max(
                        0, self._pending_commands - 1
                    )
                    if not command.try_start():
                        continue
                    self._busy = True
                function = command.function
                reply = command.reply
                try:
                    succeeded = True
                    result = function(self._runtime)
                except Exception as error:
                    succeeded = False
                    result = error
                finally:
                    with self._state_lock:
                        self._busy = False
                reply.put((succeeded, result))
        finally:
            browser = (
                getattr(self._runtime, "browser", None)
                or self._startup_control.browser
            )
            close = getattr(browser, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _reserve_command(self, function, only_if_idle=False):
        command = _RuntimeCommand(function)
        with self._state_lock:
            if (
                not self._thread.is_alive()
                or self._poisoned
                or self._stop_requested
            ):
                raise RuntimeError(
                    "Browser runtime worker is no longer available"
                )
            if only_if_idle and (
                self._busy or self._pending_commands > 0
            ):
                raise _RuntimeBusy(
                    "Browser runtime worker is executing another command"
                )
            self._pending_commands += 1
            self._commands.put(command)
        return command

    def call(
        self,
        function,
        timeout=None,
        progress_event=None,
        queue_timeout=None,
        only_if_idle=False,
    ):
        command = self._reserve_command(
            function,
            only_if_idle=only_if_idle,
        )
        execution_timeout = (
            max(0.1, float(timeout))
            if timeout is not None
            else None
        )
        queued_deadline = (
            time.monotonic() + max(0.0, float(queue_timeout))
            if queue_timeout is not None
            else None
        )
        deadline = None
        while True:
            wait_seconds = 1.0
            if deadline is None and command.started.is_set():
                deadline = (
                    time.monotonic() + execution_timeout
                    if execution_timeout is not None
                    else None
                )
            if (
                deadline is None
                and queued_deadline is not None
                and time.monotonic() >= queued_deadline
            ):
                if command.cancel_before_start():
                    raise _RuntimeBusy(
                        "Browser runtime command did not start while idle"
                    )
                deadline = (
                    time.monotonic() + execution_timeout
                    if execution_timeout is not None
                    else None
                )
            if deadline is not None:
                if (
                    progress_event is not None
                    and progress_event.is_set()
                ):
                    progress_event.clear()
                    deadline = time.monotonic() + max(
                        0.1, float(timeout)
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.poison("command_timeout")
                    raise TimeoutError(
                        "Browser runtime command exceeded its deadline"
                    )
                wait_seconds = min(wait_seconds, remaining)
            elif queued_deadline is not None:
                wait_seconds = min(
                    wait_seconds,
                    max(0.01, queued_deadline - time.monotonic()),
                )
            elif execution_timeout is not None:
                # The execution deadline begins when the worker authorizes
                # the command, not when it was queued. Poll the started bit
                # closely enough that a short deadline remains meaningful.
                wait_seconds = min(wait_seconds, 0.01)
            try:
                succeeded, result = command.reply.get(
                    timeout=wait_seconds
                )
                break
            except queue.Empty:
                if not self._thread.is_alive():
                    self.poison("worker_stopped")
                    raise RuntimeError(
                        "Browser runtime worker stopped before returning a result"
                    )
        if not succeeded:
            raise result
        return result

    def try_call(self, function, timeout=None):
        """Execute only when no command is running or queued.

        Auto-resume observations use this path so their short read timeout can
        never sit behind, then poison, a valid long-running form execution.
        """
        return self.call(
            function,
            timeout=timeout,
            queue_timeout=0.1,
            only_if_idle=True,
        )

    def close(self, purge_profile=False):
        if purge_profile:
            # Playwright may already be poisoned, so enqueueing a worker call
            # is not a reliable way to publish the pure-Python purge intent.
            # Mark the exact job-owned browser directly before teardown; the
            # adapter performs validated deletion only after its own close.
            browser = (
                getattr(self._runtime, "browser", None)
                or self._startup_control.browser
            )
            mark_purge = getattr(browser, "purge_profile_on_close", None)
            if callable(mark_purge):
                try:
                    mark_purge()
                except Exception:
                    pass
        if self._thread.is_alive():
            self._startup_control.request_stop()
            self._request_stop()
            self._thread.join(timeout=self.CLOSE_TIMEOUT_SECONDS)
            if self._thread.is_alive():
                # Python cannot safely kill a thread blocked inside a native
                # browser call. The browser adapter may, however, terminate
                # the one Chrome process proven to own this job's private
                # profile. That releases both the blocked Playwright call and
                # the profile lock without touching any normal user browser.
                if not self._startup_control.emergency_close():
                    browser = getattr(self._runtime, "browser", None)
                    emergency_close = getattr(
                        browser,
                        "emergency_close",
                        None,
                    )
                    if callable(emergency_close):
                        try:
                            emergency_close()
                        except Exception:
                            pass
                self._thread.join(
                    timeout=self.EMERGENCY_CLOSE_TIMEOUT_SECONDS
                )
            if self._thread.is_alive():
                # Mark the abandoned daemon permanently unusable. Its queued
                # stop still closes Playwright if the native call later exits.
                self.poison("worker_close_timeout")

    def _request_stop(self):
        enqueue_stop = False
        with self._state_lock:
            if not self._stop_requested:
                self._stop_requested = True
                enqueue_stop = True
        if enqueue_stop:
            self._commands.put(None)

    def poison(self, reason=""):
        with self._state_lock:
            self._poisoned = True
            self._last_failure = str(reason or "")[:120]

    @property
    def is_alive(self):
        return self._thread.is_alive()

    @property
    def is_available(self):
        with self._state_lock:
            poisoned = self._poisoned
        return self._thread.is_alive() and not poisoned

    @property
    def is_busy(self):
        with self._state_lock:
            return self._busy or self._pending_commands > 0

    @property
    def last_failure(self):
        with self._state_lock:
            return self._last_failure


class _JobCheckpointStore:
    """Serialize workflow checkpoints with service lifecycle mutations.

    The browser loop saves after every verified action while HTTP cancellation
    and synchronization requests are served on different threads.  Routing the
    browser loop through the same per-job lock prevents two writers from using
    the checkpoint store's temporary file concurrently.  A cancellation is
    terminal for that execution, so a late in-memory browser save may not
    overwrite it.
    """

    def __init__(
        self,
        store,
        job_id,
        lifecycle_lock,
        execution_lease,
        progress_callback=None,
    ):
        self._store = store
        self._job_id = job_id
        self._lifecycle_lock = lifecycle_lock
        self._execution_lease = execution_lease
        self._progress_callback = (
            progress_callback
            if callable(progress_callback)
            else None
        )

    def save(self, job):
        self._execution_lease.assert_current()
        with self._lifecycle_lock:
            self._execution_lease.assert_current()
            _ensure_review_lease(job)
            if (
                job.state in TERMINAL_JOB_STATES
                or job.wait_kind == "manual_hard_boundary"
            ):
                # Review/Sign and non-retryable logic/provider failures are
                # hard one-click boundaries. Persist the stop atomically with
                # the state transition so a concurrent status poll can never
                # advertise a runnable terminal job.
                job.continuous_run_requested = False
                job.automatic_retry_pending = False
                job.automatic_retry_after = ""
                job.automatic_retry_count = 0
                job.automatic_retry_kind = ""
                job.automatic_retry_preserves_page_boundary = False
                if job.wait_kind == "manual_hard_boundary":
                    job.sync_resume_pending = False
            try:
                current = self._store.load_job(self._job_id)
            except FileNotFoundError:
                current = None
            expected_generation = self._execution_lease.generation
            if (
                int(job.execution_generation or 0) != expected_generation
                or (
                    current is not None
                    and int(current.execution_generation or 0)
                    != expected_generation
                )
            ):
                self._execution_lease.revoke()
                raise ExecutionLeaseRevoked(
                    "Checkpoint belongs to a stale execution generation"
                )
            current_terminal_or_hard = bool(
                current is not None
                and (
                    current.state in TERMINAL_JOB_STATES
                    or current.wait_kind == "manual_hard_boundary"
                )
            )
            same_authoritative_boundary = bool(
                current is not None
                and current.state == job.state
                and current.wait_kind == job.wait_kind
            )
            if current_terminal_or_hard and not same_authoritative_boundary:
                self._execution_lease.revoke()
                raise ExecutionLeaseRevoked(
                    "A terminal or hard-boundary checkpoint cannot be revived "
                    "by a late browser worker"
                )
            result = self._store.save(job)
            if self._progress_callback is not None:
                self._progress_callback()
            return result


class AgentService:
    # The workflow checkpoints before every side effect and after every
    # verification. Resetting this inactivity deadline on each save lets one
    # long multi-page run continue normally while still detecting a worker
    # stuck in a browser/model call.
    RUN_INACTIVITY_TIMEOUT_SECONDS = 75

    def __init__(
        self,
        config=None,
        checkpoint_store=None,
        runtime_factory=None,
        recognizer=None,
        translation_provider=None,
    ):
        self.config = config or load_config()
        self.recognizer = recognizer or DocumentRecognizer(
            PlainTextOCRProvider(), UnconfiguredExtractionModel()
        )
        self.runtime_factory = runtime_factory
        self.translation_provider = translation_provider
        self._runtimes = {}
        self._runtime_lock = threading.RLock()
        self._retiring_runtime_refs = {}
        self._retiring_runtime_purge_jobs = set()
        self._retired_runtime_workers = set()
        self._retired_runtime_lock = threading.Lock()
        self._runtime_startup_timeout_seconds = max(
            0.01,
            float(
                getattr(
                    self.config,
                    "browser_startup_timeout_seconds",
                    30.0,
                )
            ),
        )
        self._active_jobs = set()
        self._active_jobs_lock = threading.Lock()
        self._execution_leases = {}
        self._execution_leases_lock = threading.Lock()
        self._job_locks = {}
        self._job_locks_guard = threading.Lock()
        self._auto_resume_jobs = set()
        self._auto_resume_lock = threading.Lock()
        self._auto_resume_wake_events = {}
        self._auto_resume_stop_events = {}
        self._auto_resume_threads = {}
        self._auto_resume_thread_ready_events = {}
        # An arm request that races an already-active watcher must not be
        # dropped. False is the less restrictive request (resume without
        # requiring a page change), so concurrent requests combine with AND.
        self._auto_resume_pending_rearms = {}
        self._startup_recovery_lock = threading.Lock()
        self._startup_recovery_started = False
        # Process shutdown is a one-way lifecycle transition.  The event is
        # checked at every process-local resource creation boundary, while the
        # lock/complete pair makes concurrent shutdown callers share one exact
        # teardown instead of closing the same runtime twice.
        self._shutdown_requested = threading.Event()
        self._shutdown_complete = threading.Event()
        self._shutdown_lock = threading.Lock()
        self._shutdown_started = False
        self._shutdown_report = {}
        self.storage_error = ""
        if checkpoint_store is not None:
            self.checkpoint_store = checkpoint_store
        else:
            try:
                self.checkpoint_store = FileCheckpointStore(
                    self.config.data_dir,
                    encryption_key=self.config.checkpoint_encryption_key,
                    allow_plaintext=self.config.allow_plaintext_checkpoints,
                )
            except CheckpointProtectionError as error:
                self.checkpoint_store = None
                self.storage_error = str(error)

    def _assert_process_lifecycle_open(self):
        if self._shutdown_requested.is_set():
            raise ServiceError(
                "Agent Core is shutting down; no new browser work is accepted",
                503,
            )

    def _shutdown_lifecycle_snapshot(self):
        """Return process-local ownership counts without touching a job."""
        with self._runtime_lock:
            runtimes = set(self._runtimes.values())
            for refs in self._retiring_runtime_refs.values():
                runtimes.update(refs)
        with self._retired_runtime_lock:
            runtimes.update(self._retired_runtime_workers)
        with self._auto_resume_lock:
            watcher_threads = set(self._auto_resume_threads.values())
            watcher_jobs = set(self._auto_resume_jobs)
        with self._execution_leases_lock:
            leases = list(self._execution_leases.values())
        with self._active_jobs_lock:
            active_jobs = set(self._active_jobs)
        return {
            "runtime_count": sum(
                1 for runtime in runtimes if runtime.is_alive
            ),
            "runtime_reference_count": len(runtimes),
            "watcher_count": sum(
                1 for thread in watcher_threads if thread.is_alive()
            ),
            "watcher_reference_count": len(watcher_jobs),
            "lease_count": len(leases),
            "active_job_count": len(active_jobs),
        }

    def shutdown(self, timeout=20.0):
        """Quiesce every process-local browser lifecycle without profile GC.

        Durable checkpoints intentionally remain untouched.  In particular,
        ``continuous_run_requested`` survives a normal process restart so the
        next service instance can reconstruct its watcher and reuse the same
        private browser profile.  Only process-local execution authority is
        revoked here.
        """
        timeout = max(0.1, float(timeout))
        owner = False
        with self._shutdown_lock:
            if not self._shutdown_started:
                self._shutdown_started = True
                self._shutdown_requested.set()
                owner = True
        if not owner:
            self._shutdown_complete.wait(timeout=timeout)
            with self._shutdown_lock:
                return dict(
                    self._shutdown_report
                    or self._shutdown_lifecycle_snapshot()
                )

        deadline = time.monotonic() + timeout

        def remaining():
            return max(0.0, deadline - time.monotonic())

        try:
            # Stop/rearm is process-local.  Do not call
            # _disarm_and_signal_auto_resume: it deliberately clears the
            # durable one-click intent and is correct for an explicit job
            # release, not for a process restart.
            with self._auto_resume_lock:
                self._auto_resume_pending_rearms.clear()
                watcher_ready_events = list(
                    self._auto_resume_thread_ready_events.values()
                )
                watcher_stop_events = list(
                    self._auto_resume_stop_events.values()
                )
                watcher_wake_events = list(
                    self._auto_resume_wake_events.values()
                )
            for event in watcher_stop_events:
                event.set()
            for event in watcher_wake_events:
                event.set()

            # Revoke first.  A runtime currently blocked in a native call may
            # still unwind later, but it can no longer authorize a checkpoint
            # write or browser side effect after shutdown begins.
            with self._execution_leases_lock:
                leases = list(self._execution_leases.values())
            for lease in leases:
                lease.revoke()

            # Atomically detach every current runtime.  Watchers and request
            # threads therefore cannot rediscover it while shutdown closes it.
            # Existing retiring workers are included so no daemon reference is
            # forgotten merely because teardown started before SIGTERM.
            with self._runtime_lock:
                runtimes = set(self._runtimes.values())
                self._runtimes.clear()
                for refs in self._retiring_runtime_refs.values():
                    runtimes.update(refs)
                for runtime in runtimes:
                    self._mark_runtime_retiring(
                        runtime,
                        purge_profile=False,
                    )
            with self._retired_runtime_lock:
                runtimes.update(self._retired_runtime_workers)

            # Close workers in parallel so several browser profiles cannot
            # multiply the process shutdown deadline.  Passing False is the
            # non-negotiable graceful-restart contract: profile data survives.
            close_threads = []

            def close_runtime(runtime):
                try:
                    runtime.close(purge_profile=False)
                finally:
                    if runtime.is_alive:
                        self._track_retired_runtime_worker(runtime)
                    else:
                        self._finalize_runtime_retirement(runtime)

            for runtime in runtimes:
                thread = threading.Thread(
                    target=close_runtime,
                    args=(runtime,),
                    name=(
                        "agent-shutdown-runtime-"
                        f"{self._runtime_job_key(runtime)[-8:]}"
                    ),
                    daemon=True,
                )
                close_threads.append(thread)
                thread.start()

            # An arm call can have reserved its maps but not yet published its
            # thread.  Its ready event linearizes that race before we snapshot
            # and join the final watcher set.
            for event in watcher_ready_events:
                event.wait(timeout=remaining())
            with self._auto_resume_lock:
                watcher_threads = set(
                    self._auto_resume_threads.values()
                )
            for thread in watcher_threads:
                if thread is threading.current_thread():
                    continue
                thread.join(timeout=remaining())
            for thread in close_threads:
                thread.join(timeout=remaining())

            # No new runtime can pass the shutdown gate.  A second sweep closes
            # the narrow constructor race in which startup began before the
            # event but published its worker after the first detach.
            with self._runtime_lock:
                late_runtimes = set(self._runtimes.values())
                self._runtimes.clear()
                for runtime in late_runtimes:
                    self._mark_runtime_retiring(
                        runtime,
                        purge_profile=False,
                    )
            for runtime in late_runtimes:
                runtime.close(purge_profile=False)
                if runtime.is_alive:
                    self._track_retired_runtime_worker(runtime)
                else:
                    self._finalize_runtime_retirement(runtime)

            # Runtime closure releases synchronous start_job callers.  Wait
            # for their finally blocks and any retirement reapers to discard
            # the last strong references, bounded by the shared deadline.
            while remaining() > 0:
                snapshot = self._shutdown_lifecycle_snapshot()
                if (
                    snapshot["runtime_count"] == 0
                    and snapshot["runtime_reference_count"] == 0
                    and snapshot["watcher_count"] == 0
                    and snapshot["watcher_reference_count"] == 0
                    and snapshot["active_job_count"] == 0
                ):
                    break
                time.sleep(min(0.02, remaining()))

            with self._execution_leases_lock:
                # Every entry is revoked and no replacement can be created.
                # Clear the registry even if a late Python frame still holds a
                # revoked token; that frame has no remaining authority.
                self._execution_leases.clear()
            with self._auto_resume_lock:
                dead_keys = {
                    key
                    for key, thread in self._auto_resume_threads.items()
                    if not thread.is_alive()
                }
                for key in dead_keys:
                    self._auto_resume_threads.pop(key, None)
                    self._auto_resume_stop_events.pop(key, None)
                    self._auto_resume_thread_ready_events.pop(key, None)
                    self._auto_resume_jobs.discard(key)
                if not self._auto_resume_threads:
                    self._auto_resume_jobs.clear()
                    self._auto_resume_stop_events.clear()
                    self._auto_resume_thread_ready_events.clear()
                    self._auto_resume_wake_events.clear()
                    self._auto_resume_pending_rearms.clear()

            report = self._shutdown_lifecycle_snapshot()
            report["complete"] = not any(report.values())
            with self._shutdown_lock:
                self._shutdown_report = dict(report)
            return report
        finally:
            self._shutdown_complete.set()

    def recover_durable_continuous_runs(self):
        """Re-arm every persisted one-click run after an Agent process restart.

        Browser workers and watcher threads are intentionally process-local,
        while ``continuous_run_requested`` is durable.  Reconstructing the
        former from the latter is therefore a service-startup responsibility,
        not something that may depend on a later DocFlow GET or another user
        click.
        """
        if self._shutdown_requested.is_set():
            return []
        with self._startup_recovery_lock:
            if self._shutdown_requested.is_set():
                return []
            if self._startup_recovery_started:
                return []
            self._startup_recovery_started = True
        if self.checkpoint_store is None:
            return []
        list_job_ids = getattr(self.checkpoint_store, "list_job_ids", None)
        if not callable(list_job_ids):
            return []

        recoverable_states = {
            JobState.READY_FOR_FORM,
            JobState.FILLING_FORM,
            JobState.WAITING_HUMAN,
        }
        recovered = []
        try:
            durable_job_ids = list_job_ids()
        except (OSError, ValueError):
            return []
        for job_id in durable_job_ids:
            require_page_change = False
            try:
                with self._job_lifecycle_lock(job_id):
                    # Read the pre-normalized legacy shape so startup can
                    # publish why an old armed terminal/hard checkpoint was
                    # disarmed. A normal service load intentionally hides the
                    # invalid runnable flag immediately.
                    job = self._require_store().load_job(job_id)
                    legacy_terminal_armed = bool(
                        job.state in {
                            JobState.BLOCKED,
                            JobState.FAILED,
                        }
                        and job.continuous_run_requested
                    )
                    legacy_hard_armed = bool(
                        job.wait_kind == "manual_hard_boundary"
                        and job.continuous_run_requested
                    )
                    self._normalize_lifecycle_invariants(job)
                    if (
                        job.final_submission_boundary_reached
                        and job.state in {
                            JobState.REVIEW_REQUIRED,
                            JobState.WAITING_HUMAN,
                        }
                    ):
                        lease_changed = _ensure_review_lease(job)
                        if _review_lease_expired(job):
                            self._expire_review_lease(
                                job,
                                source="service_startup",
                            )
                            self._purge_orphaned_terminal_profile(job_id)
                        elif lease_changed:
                            self._require_store().save(job)
                        # Review is deliberately not runnable.  Its browser
                        # remains available only until the fixed review lease;
                        # startup never arms a new execution for it.
                        continue
                    if job.state in {
                        JobState.COMPLETED,
                        JobState.CANCELLED,
                    }:
                        self._purge_orphaned_terminal_profile(job_id)
                        continue
                    if self.runtime_factory is None:
                        continue
                    if legacy_terminal_armed:
                        # Compatibility cleanup for legacy checkpoints that
                        # accidentally persisted a terminal state with the
                        # one-click flag still armed.
                        job.continuous_run_requested = False
                        job.wait_kind = ""
                        job.sync_resume_pending = False
                        self._clear_automatic_retry_state(job)
                        job.record(
                            "legacy_terminal_run_disarmed",
                            "A legacy terminal checkpoint was prevented from "
                            "restarting during Agent startup",
                            state=job.state.value,
                        )
                        self._require_store().save(job)
                        continue
                    if legacy_hard_armed:
                        job.record(
                            "hard_boundary_preserved_on_restart",
                            "Agent restart preserved the hard manual boundary "
                            "and did not arm browser execution",
                        )
                        self._require_store().save(job)
                        continue
                    if (
                        not job.continuous_run_requested
                        or job.state not in recoverable_states
                    ):
                        continue
                    previous_state = job.state
                    require_page_change = (
                        previous_state == JobState.WAITING_HUMAN
                        and not job.automatic_retry_pending
                        and not job.sync_resume_pending
                        and job.wait_kind in {
                            "manual_page_change",
                            "manual_hard_boundary",
                        }
                    )
                    if (
                        previous_state == JobState.FILLING_FORM
                        or (
                            previous_state == JobState.WAITING_HUMAN
                            and job.wait_kind == "runtime_recovery"
                        )
                    ):
                        job.state = JobState.READY_FOR_FORM
                        job.wait_kind = "runtime_recovery"
                        job.human_checkpoint = None
                        job.record(
                            "orphaned_runtime_recovered",
                            "The previous browser worker was lost during an "
                            "Agent restart; the durable continuous-run intent "
                            "will recreate it automatically",
                            previousState=previous_state.value,
                            continuousRunRequested=True,
                            recoverySource="service_startup",
                        )
                    job.record(
                        "continuous_run_recovery_armed",
                        "Agent startup recovered the durable one-click run; "
                        "the browser will reconnect without another user action",
                        previousState=previous_state.value,
                        requirePageChange=require_page_change,
                    )
                    self._require_store().save(job)
                recovered.append(job_id)
                self._arm_continuous_resume(
                    job_id,
                    require_page_change=require_page_change,
                )
            except (ServiceError, OSError, ValueError):
                # One damaged/expired checkpoint must not prevent the service
                # or other applicants' durable runs from recovering.
                continue
        return recovered

    def _expire_review_lease(self, job, source):
        """Turn an elapsed Review lease into the existing purgeable terminal."""
        self._revoke_execution_leases(job.id)
        self._discard_terminal_pending_action(job, "review_lease_expired")
        job.state = JobState.CANCELLED
        job.continuous_run_requested = False
        job.wait_kind = ""
        job.sync_resume_pending = False
        self._clear_automatic_retry_state(job)
        job.human_checkpoint = (
            "Review/Sign retention lease expired; the private browser and "
            "profile were closed"
        )
        job.record(
            "review_lease_expired",
            job.human_checkpoint,
            reviewLeaseExpiresAt=str(job.review_lease_expires_at or ""),
            source=str(source or ""),
        )
        self._require_store().save(job)
        return job

    def _purge_orphaned_terminal_profile(self, job_id):
        """GC one exact terminal profile after a process-crash window."""
        candidate = str(job_id or "")
        if not re.fullmatch(r"agent-job-[A-Za-z0-9-]+", candidate):
            return False
        if self._job_has_retired_runtime(candidate):
            # The exact old worker still owns teardown and may still hold its
            # profile lock. Its durable purge intent must finish first.
            return False
        profile_root = (
            self.config.data_dir / "browser-profiles"
        )
        return purge_private_profile_path(
            profile_root / candidate,
            required_parent=profile_root,
        )

    def health(self):
        providers = {
            name: self.config.provider_public_summary(name, settings)
            for name, settings in self.config.providers.items()
        }
        return {
            "ok": True,
            "service": "docflow-standalone-agent",
            "version": "0.5.0",
            "connectedToDocFlow": self.config.integration_mode != "isolated",
            "modelConfigured": self.config.model_configured,
            "ocrConfigured": self.config.ocr_configured,
            "browserConfigured": self.config.browser_configured,
            "providers": providers,
            "checkpointStoreReady": self.checkpoint_store is not None,
            "checkpointProtection": (
                self.checkpoint_store.protection_mode
                if self.checkpoint_store else "unavailable"
            ),
            "storageWarning": self.storage_error,
            "mode": self.config.integration_mode,
            "computerUseExecution": (
                self.config.computer_use_execution
                or "provider-runtime"
            ),
        }

    def recognize_text(self, payload):
        text = str(payload.get("ocrText") or "")
        filename = str(payload.get("filename") or "document.txt")
        document_type = str(payload.get("documentType") or "passport")
        result = self.recognizer.recognize(
            text.encode("utf-8"),
            filename,
            "text/plain",
            document_type,
        )
        return self._recognition_payload(result)

    def recognize_document(self, payload):
        encoded = payload.get("fileBase64")
        if not isinstance(encoded, str) or not encoded.strip():
            raise ServiceError("fileBase64 is required")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ServiceError("fileBase64 is invalid") from error
        if not content:
            raise ServiceError("document is empty")
        if len(content) > 25 * 1024 * 1024:
            raise ServiceError("document exceeds the 25 MB limit")
        filename = str(payload.get("filename") or "document.bin")
        if len(filename) > 255 or "/" in filename or "\\" in filename:
            raise ServiceError("filename is invalid")
        media_type = str(
            payload.get("mediaType") or "application/octet-stream"
        )
        document_type = str(payload.get("documentType") or "unknown")
        result = self.recognizer.recognize(
            content,
            filename,
            media_type,
            document_type,
        )
        return self._recognition_payload(result)

    def _recognition_payload(self, result):
        payload = to_primitive(result)
        payload["providers"] = {
            name: self.config.provider_public_summary(name, settings)
            for name, settings in self.config.providers.items()
            if name in {
                "documentParser", "ocr", "ocrFallback",
                "extraction", "review",
            }
        }
        stages = payload.get("stages") or {}
        text_stage = stages.get("text") or {}
        if text_stage.get("provider") == "document-parser":
            text_stage["provider"] = (
                self.config.document_parser.provider or "document-parser"
            )
            text_stage["model"] = self.config.document_parser.model
        elif str(text_stage.get("provider") or "").startswith("ocr"):
            text_stage["provider"] = self.config.ocr.provider or "ocr"
            text_stage["model"] = self.config.ocr.model
        extraction_stage = stages.get("extraction") or {}
        if extraction_stage:
            extraction_stage["provider"] = (
                self.config.extraction.provider or "model"
            )
            extraction_stage["model"] = self.config.extraction.model
        review_stage = stages.get("review") or {}
        if review_stage:
            review_stage["provider"] = self.config.review.provider or "model"
            review_stage["model"] = self.config.review.model
        return payload

    def transform_text(self, payload):
        if self.translation_provider is None:
            raise ServiceError("Translation/transliteration provider is not configured", 409)
        text = str(payload.get("text") or "")
        if not text.strip():
            raise ServiceError("text is required")
        if len(text) > 20000:
            raise ServiceError("text is too large")
        source_language = str(payload.get("sourceLanguage") or "auto")
        mode = str(payload.get("mode") or "translate").strip().lower()
        try:
            if mode == "translate":
                target_language = str(payload.get("targetLanguage") or "en")
                result = self.translation_provider.translate(
                    text, source_language, target_language
                )
            elif mode == "transliterate":
                target_script = str(payload.get("targetScript") or "Latn")
                transliterate = getattr(
                    self.translation_provider, "transliterate", None
                )
                if not callable(transliterate):
                    raise ServiceError(
                        "Configured language provider does not support transliteration",
                        409,
                    )
                result = transliterate(text, source_language, target_script)
            else:
                raise ServiceError("mode must be translate or transliterate")
        except ServiceError:
            raise
        except ProviderNotConfigured as error:
            raise ServiceError(str(error), 409) from error
        except Exception as error:
            raise ServiceError(
                f"Language provider failed: {type(error).__name__}", 502
            ) from error
        if not isinstance(result, str) or not result.strip():
            raise ServiceError("Language provider returned an unusable result", 502)
        return {"mode": mode, "text": result}

    def create_job(self, payload):
        store = self._require_store()
        start_url = str(payload.get("startUrl") or "")
        if not start_url:
            raise ServiceError("startUrl is required")
        raw_fields = payload.get("fields")
        if not isinstance(raw_fields, list) or not raw_fields:
            raise ServiceError("fields must be a non-empty list")
        required_field_ids = payload.get("requiredFieldIds")
        if not isinstance(required_field_ids, list) or not required_field_ids:
            raise ServiceError("requiredFieldIds must be a non-empty list")
        if any(not isinstance(item, dict) for item in raw_fields):
            raise ServiceError("Each field must be an object")
        try:
            fields = [
                extracted_field_from_primitive(item)
                for item in raw_fields
            ]
        except (TypeError, ValueError) as error:
            raise ServiceError(f"Invalid field payload: {error}") from error
        try:
            job = AgentOrchestrator(
                checkpoint_store=store
            ).create_review_job(
                fields,
                start_url,
                required_field_ids=[
                    str(item) for item in required_field_ids
                ],
            )
        except ValueError as error:
            raise ServiceError(str(error)) from error
        raw_recovery_credentials = payload.get("recoveryCredentials")
        if raw_recovery_credentials is not None:
            if getattr(store, "protector", None) is None:
                raise ServiceError(
                    "Approved DS-160 recovery credentials require an "
                    "encrypted Agent checkpoint store",
                    409,
                )
            try:
                job.recovery_credentials = (
                    recovery_credentials_from_primitive(
                        raw_recovery_credentials,
                        require_approval=True,
                    )
                )
            except ValueError as error:
                raise ServiceError(str(error)) from error
        job.provider_versions = {
            name: ":".join(filter(None, (
                settings.provider, settings.model, settings.version
            )))
            for name, settings in self.config.providers.items()
            if settings.configured
        }
        job.auto_next = payload.get("autoNext", True) is True
        store.save(job)
        return _public_job_payload(job)

    def get_job(self, job_id):
        # Status polling is an observation, not a lifecycle mutation.  Browser
        # startup is intentionally serialized with review/start/cancel under
        # the per-job lifecycle lock and may legitimately take tens of
        # seconds.  Waiting behind that lock made a healthy startup or restart
        # look exactly like a frozen Agent Core to DocFlow.  Checkpoint writes
        # replace one complete file atomically, so a read-only snapshot is safe
        # while a lifecycle request is in flight: it observes either the last
        # committed state or the next committed state, never a partial one.
        # The following normal poll will perform any lease/recovery mutation
        # once the lifecycle owner has finished.
        lifecycle_lock = self._job_lifecycle_lock(job_id)
        if not lifecycle_lock.acquire(blocking=False):
            return self._readonly_job_status(job_id, lifecycle_busy=True)
        arm_continuous_resume = False
        arm_require_page_change = False
        release_terminal_runtime = False
        purge_terminal_profile = False
        try:
            job = self._load_job(job_id)
            if (
                job.final_submission_boundary_reached
                and job.state in {
                    JobState.REVIEW_REQUIRED,
                    JobState.WAITING_HUMAN,
                }
            ):
                lease_changed = _ensure_review_lease(job)
                if _review_lease_expired(job):
                    job = self._expire_review_lease(
                        job,
                        source="status_read",
                    )
                elif lease_changed:
                    self._require_store().save(job)
            runtime_observation = self._runtime_is_open(
                job_id,
                purge_if_stale=job.state in {
                    JobState.COMPLETED,
                    JobState.CANCELLED,
                },
                nonblocking=True,
            )
            runtime_transitioning = runtime_observation is None
            runtime_open = bool(runtime_observation)
            if (
                not runtime_transitioning
                and job.state in {JobState.COMPLETED, JobState.CANCELLED}
            ):
                # A terminal provider job must not retain a browser worker.
                # This also cleans up a stale runtime left by an older Agent
                # version before reporting the authoritative runtime truth.
                if runtime_open:
                    release_terminal_runtime = True
                    runtime_open = False
                purge_terminal_profile = True
            elif (
                not runtime_transitioning
                and job.state in {
                    JobState.FILLING_FORM,
                    JobState.WAITING_HUMAN,
                }
                and not runtime_open
            ):
                with self._active_jobs_lock:
                    active = job_id in self._active_jobs
                if active:
                    with self._active_jobs_lock:
                        self._active_jobs.discard(job_id)
                    self._revoke_execution_leases(job_id)
                if (
                    job.automatic_retry_pending
                    and job.continuous_run_requested
                ):
                    # A provider backoff is itself a durable execution
                    # state.  Keep it visible and let the watcher recreate
                    # Chrome at the due time instead of rewriting it as a
                    # manual-ready checkpoint.
                    arm_continuous_resume = True
                elif (
                    job.state == JobState.WAITING_HUMAN
                    and job.wait_kind in {
                        "manual_page_change",
                        "manual_hard_boundary",
                    }
                ):
                    # Preserve the durable reason.  A missing in-memory
                    # browser does not turn CAPTCHA/manual handling into a
                    # ready form. Manual page-change waits recreate the
                    # browser but only resume after a real DOM transition.
                    if job.wait_kind == "manual_page_change":
                        arm_continuous_resume = bool(
                            job.continuous_run_requested
                        )
                        arm_require_page_change = True
                    else:
                        job.continuous_run_requested = False
                        self._require_store().save(job)
                else:
                    previous_state = job.state.value
                    # Process restarts can leave an encrypted checkpoint in
                    # either execution-bound state even though the in-memory
                    # Playwright worker no longer exists. Reporting the stale
                    # state makes DocFlow monitor a browser that cannot be
                    # resumed instead of reopening it.
                    job.state = JobState.READY_FOR_FORM
                    job.wait_kind = "runtime_recovery"
                    job.human_checkpoint = None
                    job.record(
                        "orphaned_runtime_recovered",
                        "The previous browser worker was lost during an Agent "
                        "restart; the reviewed job is ready to reopen with its "
                        "durable continuous-run intent",
                        previousState=previous_state,
                        continuousRunRequested=job.continuous_run_requested,
                    )
                    self._require_store().save(job)
                    arm_continuous_resume = bool(
                        job.continuous_run_requested
                    )
            payload = _public_job_payload(job)
            payload["runtime_open"] = runtime_open
            payload["runtime_transitioning"] = runtime_transitioning
        finally:
            lifecycle_lock.release()
        if release_terminal_runtime:
            self._release_runtime(job_id, purge_profile=True)
        if purge_terminal_profile:
            self._purge_orphaned_terminal_profile(job_id)
        if (
            not arm_continuous_resume
            and job.continuous_run_requested
            and job.state in {
                JobState.READY_FOR_FORM,
                JobState.WAITING_HUMAN,
                JobState.FILLING_FORM,
            }
            and job.wait_kind != "manual_hard_boundary"
        ):
            with self._active_jobs_lock:
                active = job_id in self._active_jobs
            with self._auto_resume_lock:
                watcher_armed = job_id in self._auto_resume_jobs
            if not active and not watcher_armed:
                arm_continuous_resume = True
                arm_require_page_change = bool(
                    job.state == JobState.WAITING_HUMAN
                    and job.wait_kind == "manual_page_change"
                    and not job.sync_resume_pending
                )
        if arm_continuous_resume:
            self._arm_continuous_resume(
                job_id,
                require_page_change=arm_require_page_change,
            )
        # These are process-local liveness facts, intentionally kept out of
        # the durable job model.  DocFlow can distinguish a real execution or
        # armed watcher from a stale ``filling_form`` checkpoint after either
        # process restarts, without mutating the Agent lifecycle.
        with self._active_jobs_lock:
            payload["execution_active"] = job_id in self._active_jobs
        with self._auto_resume_lock:
            payload["auto_resume_watcher_armed"] = (
                job_id in self._auto_resume_jobs
            )
        return payload

    def _readonly_job_status(self, job_id, lifecycle_busy=False):
        """Return one committed status snapshot without waiting on writers.

        FileCheckpointStore publishes via atomic replacement.  This read-only
        path deliberately performs no lease expiry, orphan recovery, runtime
        teardown, or watcher arm.  Those lifecycle effects remain serialized
        in the next ordinary ``get_job`` call.
        """
        job = self._load_job(job_id)
        runtime_observation = self._runtime_is_open(
            job_id,
            purge_if_stale=False,
            nonblocking=True,
        )
        payload = _public_job_payload(job)
        payload["runtime_open"] = bool(runtime_observation)
        payload["runtime_transitioning"] = bool(
            lifecycle_busy or runtime_observation is None
        )
        with self._active_jobs_lock:
            payload["execution_active"] = job_id in self._active_jobs
        with self._auto_resume_lock:
            payload["auto_resume_watcher_armed"] = (
                job_id in self._auto_resume_jobs
            )
        return payload

    def review_job(self, job_id, payload):
        with self._job_lifecycle_lock(job_id):
            with self._active_jobs_lock:
                if job_id in self._active_jobs:
                    raise ServiceError(
                        "Gemini Computer Use 正在运行，不能在填写中重新复核",
                        409,
                    )
            actor = str(payload.get("actor") or "").strip()
            decisions = payload.get("decisions")
            if not isinstance(decisions, list):
                raise ServiceError("decisions must be a list")
            current = self._load_job(job_id)
            pristine_review = bool(
                current.state in {
                    JobState.WAITING_REVIEW,
                    JobState.REVIEW_REQUIRED,
                }
                and not current.completed_field_ids
                and not current.applied_action_ids
                and current.pending_action is None
                and not current.current_page_plan_id
                and int(current.action_index or 0) == 0
                and int(current.step_count or 0) == 0
                and int(current.execution_generation or 0) == 0
                and current.wait_kind != "manual_hard_boundary"
            )
            if not pristine_review:
                raise ServiceError(
                    "Review is limited to the initial pre-execution field "
                    "checkpoint; running jobs must use synchronized reviewed "
                    "snapshots and cannot clear a hard browser boundary",
                    409,
                )
            try:
                job = AgentOrchestrator(
                    checkpoint_store=self._require_store()
                ).apply_human_review(
                    current, decisions, actor
                )
            except ValueError as error:
                raise ServiceError(str(error)) from error
        return _public_job_payload(job)

    @staticmethod
    def _field_binding_semantics(field):
        """Stable approved semantics that can change DOM binding/verification."""
        confirmation = getattr(field, "confirmation", None)
        risk = getattr(field, "risk_level", "")
        risk_value = getattr(risk, "value", risk)
        return (
            str(getattr(field, "value", "") or ""),
            str(getattr(field, "label", "") or ""),
            str(risk_value or ""),
            bool(getattr(field, "confirmed", False)),
            str(
                getattr(confirmation, "confirmed_value", "")
                if confirmation is not None
                else ""
            ),
        )

    @staticmethod
    def _field_snapshot_semantics(field):
        """Full stable field payload used to fence older worker checkpoints."""
        confirmation = getattr(field, "confirmation", None)
        risk = getattr(field, "risk_level", "")
        risk_value = getattr(risk, "value", risk)
        evidence = tuple(
            (
                str(getattr(item, "document_id", "") or ""),
                str(getattr(item, "filename", "") or ""),
                int(getattr(item, "page", 0) or 0),
                str(getattr(item, "excerpt", "") or ""),
                str(getattr(item, "method", "") or ""),
            )
            for item in getattr(field, "evidence", ()) or ()
        )
        confirmation_semantics = (
            (
                str(getattr(confirmation, "confirmed_by", "") or ""),
                str(getattr(confirmation, "source", "") or ""),
                str(getattr(confirmation, "original_value", "") or ""),
                str(getattr(confirmation, "confirmed_value", "") or ""),
                str(getattr(confirmation, "reason", "") or ""),
            )
            if confirmation is not None
            else ()
        )
        return (
            str(getattr(field, "value", "") or ""),
            str(getattr(field, "label", "") or ""),
            float(getattr(field, "confidence", 0.0) or 0.0),
            str(risk_value or ""),
            bool(getattr(field, "confirmed", False)),
            evidence,
            tuple(str(item) for item in getattr(field, "alternatives", ()) or ()),
            confirmation_semantics,
        )

    @staticmethod
    def _canonical_legacy_page_plan_id(page_plan_id, registry=None):
        """Map one persisted broad alias to its exact physical-page plan."""
        candidate = str(page_plan_id or "")
        if not candidate:
            return ""
        registry = registry or PagePlanRegistry.default()
        exact_ids = tuple(
            registry.LEGACY_PLAN_EQUIVALENTS.get(candidate) or ()
        )
        if len(exact_ids) == 1:
            return str(exact_ids[0])
        return candidate

    @classmethod
    def _completed_page_provenance(cls, job):
        """Recover verified owning pages, including pre-upgrade checkpoints."""
        registry = PagePlanRegistry.default()

        def canonical_owner(field_id, page_plan_id):
            # Field ownership is code-owned and unique for every supported
            # coarse/dynamic ID.  Legacy broad provenance is migration input,
            # never a durable output after recovery.
            return (
                registry.canonical_owner_for_field(field_id)
                or cls._canonical_legacy_page_plan_id(
                    page_plan_id,
                    registry,
                )
            )

        completed = set(job.completed_field_ids or ())
        provenance = {
            str(field_id): canonical_owner(
                str(field_id),
                page_plan_id,
            )
            for field_id, page_plan_id in dict(
                job.completed_field_page_plan_by_id or {}
            ).items()
            if str(field_id) in completed and str(page_plan_id or "")
        }
        started = {}
        for event in job.events or ():
            detail = dict(getattr(event, "detail", {}) or {})
            action_id = str(detail.get("actionId") or "")
            if event.kind == "action_started" and action_id:
                started[action_id] = (
                    str(detail.get("fieldId") or ""),
                    str(detail.get("pagePlanId") or ""),
                )
                continue
            if event.kind != "action_verified":
                continue
            field_id = str(detail.get("fieldId") or "")
            page_plan_id = ""
            if action_id in started:
                started_field_id, page_plan_id = started[action_id]
                field_id = field_id or started_field_id
            if (
                field_id in completed
                and page_plan_id
            ):
                provenance[field_id] = canonical_owner(
                    field_id,
                    page_plan_id,
                )
        return provenance

    @classmethod
    def _visited_page_plans(cls, job):
        """Recover durable route history from both new and legacy events."""
        registry = PagePlanRegistry.default()
        visited = []

        def remember(value):
            page_plan_id = cls._canonical_legacy_page_plan_id(
                value,
                registry,
            )
            if page_plan_id and page_plan_id not in visited:
                visited.append(page_plan_id)

        for page_plan_id in job.visited_page_plan_ids or ():
            remember(page_plan_id)
        for event in job.events or ():
            detail = dict(getattr(event, "detail", {}) or {})
            remember(detail.get("pagePlanId"))
            remember(detail.get("fromPagePlanId"))
            remember(detail.get("toPagePlanId"))
        remember(job.current_page_plan_id)
        return visited

    def sync_job(self, job_id, payload):
        """Atomically replace a reviewed field snapshot without replacing Chrome.

        Reusing a browser session is required for an already-retrieved DS-160,
        but reusing its original field checkpoint after the consultant edits
        DocFlow would fill stale values.  This endpoint refreshes the encrypted
        provider checkpoint while preserving only completions whose approved
        value is unchanged.
        """
        should_wake_resume = False
        with self._job_lifecycle_lock(job_id):
            with self._active_jobs_lock:
                if job_id in self._active_jobs:
                    raise ServiceError(
                        "Gemini Computer Use 正在运行，不能在填写中替换字段快照",
                        409,
                    )
            job = self._load_job(job_id)
            if job.state in {JobState.COMPLETED, JobState.CANCELLED}:
                raise ServiceError(
                    f"Cannot synchronize a {job.state.value} job",
                    409,
                )
            raw_fields = payload.get("fields")
            required_field_ids = payload.get("requiredFieldIds")
            decisions = payload.get("decisions")
            actor = str(payload.get("actor") or "").strip()
            if not isinstance(raw_fields, list) or not raw_fields:
                raise ServiceError("fields must be a non-empty list")
            if any(not isinstance(item, dict) for item in raw_fields):
                raise ServiceError("Each field must be an object")
            if not isinstance(required_field_ids, list) or not required_field_ids:
                raise ServiceError("requiredFieldIds must be a non-empty list")
            if not isinstance(decisions, list):
                raise ServiceError("decisions must be a list")
            try:
                fields = [
                    extracted_field_from_primitive(item)
                    for item in raw_fields
                ]
                draft = AgentOrchestrator().create_review_job(
                    fields,
                    job.start_url,
                    required_field_ids=[
                        str(item) for item in required_field_ids
                    ],
                )
                reviewed = AgentOrchestrator().apply_human_review(
                    draft,
                    decisions,
                    actor,
                )
            except (TypeError, ValueError) as error:
                raise ServiceError(
                    f"Invalid synchronized field payload: {error}"
                ) from error
            if reviewed.state != JobState.READY_FOR_FORM:
                raise ServiceError(
                    "Synchronized fields did not pass human review",
                    409,
                )

            old_confirmed = {
                field.id: self._field_binding_semantics(field)
                for field in job.fields
                if field.confirmed
            }
            old_field_snapshot = {
                field.id: self._field_snapshot_semantics(field)
                for field in job.fields
            }
            old_required_field_ids = list(job.required_field_ids)
            old_validation_errors = list(job.validation_errors)
            old_auto_next = bool(job.auto_next)
            old_state = job.state
            old_final_submission_boundary = bool(
                job.final_submission_boundary_reached
            )
            old_human_checkpoint = job.human_checkpoint
            old_completed_field_ids = list(job.completed_field_ids)
            page_registry = PagePlanRegistry.default()
            canonical_current_page_plan_id = (
                self._canonical_legacy_page_plan_id(
                    job.current_page_plan_id,
                    page_registry,
                )
            )
            if (
                canonical_current_page_plan_id
                != str(job.current_page_plan_id or "")
            ):
                previous_page_plan_id = str(
                    job.current_page_plan_id or ""
                )
                job.current_page_plan_id = (
                    canonical_current_page_plan_id
                )
                job.record(
                    "current_page_plan_migrated",
                    "The persisted broad page alias was migrated to its "
                    "exact physical CEAC page plan",
                    previousPagePlanId=previous_page_plan_id,
                    pagePlanId=canonical_current_page_plan_id,
                )
            completed_page_provenance = (
                self._completed_page_provenance(job)
            )
            if completed_page_provenance != dict(
                job.completed_field_page_plan_by_id or {}
            ):
                job.completed_field_page_plan_by_id = dict(
                    completed_page_provenance
                )
                job.record(
                    "completed_page_provenance_migrated",
                    "Verified field ownership was recovered from durable "
                    "action events written by an earlier Agent version",
                    fieldCount=len(completed_page_provenance),
                )
            visited_page_plan_ids = self._visited_page_plans(job)
            if visited_page_plan_ids != list(
                job.visited_page_plan_ids or ()
            ):
                job.visited_page_plan_ids = list(visited_page_plan_ids)
                job.record(
                    "visited_page_plans_migrated",
                    "Visited DS-160 page ownership was recovered from durable "
                    "events written by an earlier Agent version",
                    pagePlanIds=visited_page_plan_ids,
                )
            old_wait_kind = str(job.wait_kind or "")
            new_confirmed = {
                field.id: self._field_binding_semantics(field)
                for field in reviewed.fields
                if field.confirmed
            }
            new_field_snapshot = {
                field.id: self._field_snapshot_semantics(field)
                for field in reviewed.fields
            }
            unchanged_ids = {
                field_id
                for field_id, semantics in new_confirmed.items()
                if old_confirmed.get(field_id) == semantics
            }
            changed_binding_ids = sorted(
                field_id
                for field_id in set(old_confirmed).union(new_confirmed)
                if field_id not in unchanged_ids
            )
            changed_completed_ids = [
                field_id
                for field_id in old_completed_field_ids
                if field_id not in unchanged_ids
            ]
            newly_confirmed_ids = sorted(
                set(new_confirmed).difference(old_confirmed)
            )
            newly_required_ids = sorted(
                set(reviewed.required_field_ids).difference(
                    old_required_field_ids
                )
            )
            job.control_normalized_values = {
                field_id: value
                for field_id, value in dict(
                    job.control_normalized_values or {}
                ).items()
                if field_id in unchanged_ids
            }
            job.fields = reviewed.fields
            job.required_field_ids = reviewed.required_field_ids
            job.validation_errors = reviewed.validation_errors
            synchronized_field_ids = set(new_field_snapshot)
            previous_visual_failure_counts = dict(
                job.visual_failure_counts or {}
            )
            job.visual_failure_counts = {
                key: count
                for key, count in previous_visual_failure_counts.items()
                if str(key).partition("::")[2]
                in synchronized_field_ids
            }
            if (
                job.visual_failure_counts
                != previous_visual_failure_counts
            ):
                removed_budget_field_ids = sorted({
                    str(key).partition("::")[2]
                    for key in previous_visual_failure_counts
                    if str(key).partition("::")[2]
                    not in synchronized_field_ids
                })
                job.record(
                    "visual_failure_budgets_pruned_by_sync",
                    "Durable visual repair budgets for fields removed from "
                    "the synchronized reviewed snapshot were deleted",
                    fieldIds=removed_budget_field_ids,
                    removedCount=(
                        len(previous_visual_failure_counts)
                        - len(job.visual_failure_counts)
                    ),
                )
            job.completed_field_ids = [
                field_id
                for field_id in job.completed_field_ids
                if field_id in unchanged_ids
            ]
            job.inapplicable_field_ids = [
                field_id
                for field_id in job.inapplicable_field_ids
                if field_id in unchanged_ids
            ]
            job.binding_refresh_field_ids = list(dict.fromkeys((
                *job.binding_refresh_field_ids,
                *changed_binding_ids,
            )))
            pending = job.pending_action
            pending_kind = str(
                getattr(getattr(pending, "kind", ""), "value", "")
                or getattr(pending, "kind", "")
                or ""
            ).strip().lower()
            pending_value_changed = bool(
                pending is not None
                and pending_kind in {"type", "select"}
                and pending.field_id not in unchanged_ids
            )
            if pending_value_changed:
                # Value writes are idempotent and may be safely replanned from
                # the new approved snapshot. Non-idempotent actions (especially
                # Next and repeater clicks) must survive every field sync until
                # the live browser proves whether they were dispatched/applied.
                job.pending_action = None
                job.record(
                    "pending_value_action_invalidated_by_sync",
                    "A pending value action referenced a changed approved value "
                    "and will be rebound from the synchronized snapshot",
                    actionId=pending.id,
                    fieldId=pending.field_id,
                )
            pending_is_next = bool(
                pending is not None
                and pending_kind == "click"
                and (
                    str(pending.reason or "")
                    == "Deterministic fixed CEAC Next control"
                    or str(pending.target_hint or "")
                    .strip()
                    .lower()
                    .startswith("next")
                )
            )
            origin_page_plan_id = str(
                job.current_page_plan_id or ""
            )
            if pending_is_next and not origin_page_plan_id:
                for event in reversed(job.events):
                    if (
                        event.kind == "page_navigation_started"
                        and str(event.detail.get("actionId") or "")
                        == str(pending.id)
                    ):
                        origin_page_plan_id = str(
                            event.detail.get("fromPagePlanId") or ""
                        )
                        if origin_page_plan_id:
                            break
            origin_page_plan_id = self._canonical_legacy_page_plan_id(
                origin_page_plan_id,
                page_registry,
            )
            plans_by_id = {
                str(plan.id): plan for plan in page_registry.plans
            }
            origin_plan = plans_by_id.get(origin_page_plan_id)

            persisted_target_by_field = dict(
                job.sync_reconciliation_page_plan_by_field or {}
            )
            target_by_field = {
                str(field_id): (
                    page_registry.canonical_owner_for_field(field_id)
                    or self._canonical_legacy_page_plan_id(
                        page_plan_id,
                        page_registry,
                    )
                )
                for field_id, page_plan_id
                in persisted_target_by_field.items()
                if str(field_id)
            }
            persisted_legacy_target = str(
                job.sync_reconciliation_page_plan_id or ""
            )
            legacy_target = str(
                self._canonical_legacy_page_plan_id(
                    persisted_legacy_target,
                    page_registry,
                )
            )
            if (
                target_by_field != persisted_target_by_field
                or legacy_target != persisted_legacy_target
            ):
                job.sync_reconciliation_page_plan_by_field = dict(
                    target_by_field
                )
                job.sync_reconciliation_page_plan_id = legacy_target
                job.record(
                    "sync_reconciliation_targets_migrated",
                    "Legacy broad reconciliation targets were migrated to "
                    "exact physical CEAC page plans",
                    targetPagePlanIds=target_by_field,
                    pagePlanId=legacy_target,
                )
            visited_set = set(visited_page_plan_ids)

            def owning_page_plan_id(field_id):
                canonical_owner = (
                    page_registry.canonical_owner_for_field(field_id)
                )
                existing_target = str(
                    target_by_field.get(field_id) or ""
                )
                if existing_target:
                    return canonical_owner or existing_target
                verified_target = str(
                    completed_page_provenance.get(field_id) or ""
                )
                if verified_target:
                    return canonical_owner or verified_target
                if (
                    pending_is_next
                    and origin_plan is not None
                    and origin_plan.allows_field(field_id)
                ):
                    return canonical_owner or origin_page_plan_id
                if canonical_owner:
                    return canonical_owner
                matching = [
                    str(plan.id)
                    for plan in page_registry.plans
                    if plan.allows_field(field_id)
                ]
                if origin_page_plan_id in matching:
                    return origin_page_plan_id
                visited_matches = [
                    page_plan_id
                    for page_plan_id in visited_page_plan_ids
                    if (
                        page_plan_id in matching
                        or (
                            plans_by_id.get(page_plan_id) is not None
                            and plans_by_id[
                                page_plan_id
                            ].allows_field(field_id)
                        )
                    )
                ]
                if visited_matches:
                    # Multiple IDs can be aliases for the same physical page
                    # across Agent upgrades.  Route history is ordered, so the
                    # most recent durable visit is the best ownership proof.
                    return visited_matches[-1]
                if len(matching) == 1:
                    return matching[0]
                return legacy_target

            def owner_was_visited(field_id, owner):
                if owner in visited_set:
                    return True
                return any(
                    page_registry.equivalent_for_field(
                        owner,
                        visited_page_plan_id,
                        field_id,
                    )
                    for visited_page_plan_id in visited_page_plan_ids
                )

            # Every changed value that was previously verified is dirty even
            # when the browser has already advanced several pages. Newly
            # confirmed/required values are also dirty when their owning page
            # is already in the durable route history; otherwise a later sync
            # on page B would silently skip a new field from page A.
            reconciliation_ids = set(changed_completed_ids)
            current_material_ids = (
                {
                    *changed_binding_ids,
                    *newly_confirmed_ids,
                    *newly_required_ids,
                }
                & set(new_confirmed)
            )
            for field_id in current_material_ids:
                owner = owning_page_plan_id(field_id)
                if (
                    owner_was_visited(field_id, owner)
                    or (
                        pending_is_next
                        and (
                            not origin_page_plan_id
                            or owner == origin_page_plan_id
                        )
                    )
                ):
                    reconciliation_ids.add(field_id)
            if reconciliation_ids:
                previous_reconciliation = list(
                    job.sync_reconciliation_field_ids
                )
                job.sync_reconciliation_field_ids = list(dict.fromkeys((
                    *previous_reconciliation,
                    *sorted(reconciliation_ids),
                )))
                for field_id in job.sync_reconciliation_field_ids:
                    target_by_field[field_id] = owning_page_plan_id(
                        field_id
                    )
                job.sync_reconciliation_page_plan_by_field = target_by_field
                distinct_targets = {
                    str(target_by_field.get(field_id) or "")
                    for field_id in job.sync_reconciliation_field_ids
                }
                job.sync_reconciliation_page_plan_id = (
                    next(iter(distinct_targets))
                    if len(distinct_targets) == 1
                    else ""
                )
                job.record(
                    (
                        "sync_deferred_across_pending_navigation"
                        if pending_is_next
                        else "sync_cross_page_reconciliation_required"
                    ),
                    "Synchronized fields that may differ from values already "
                    "written in CEAC must be reverified on their owning pages "
                    "before navigation can continue",
                    fieldIds=sorted(reconciliation_ids),
                    pagePlanId=job.current_page_plan_id,
                    targetPagePlanIds=target_by_field,
                    pendingActionId=(
                        str(pending.id) if pending_is_next else ""
                    ),
                )
            job.auto_next = payload.get("autoNext", job.auto_next) is True
            snapshot_changed = bool(
                old_field_snapshot != new_field_snapshot
                or old_required_field_ids != list(job.required_field_ids)
                or old_validation_errors != list(job.validation_errors)
                or old_auto_next != bool(job.auto_next)
            )
            if snapshot_changed:
                # A browser worker retired after a timeout can return late even
                # though it is no longer present in _active_jobs. Advance the
                # durable generation so its stale checkpoint cannot overwrite
                # this newly-approved field snapshot.
                self._revoke_execution_leases(job_id)
                job.execution_generation = max(
                    0, int(job.execution_generation or 0)
                ) + 1
                job.sync_resume_pending = bool(
                    job.continuous_run_requested
                )
                should_wake_resume = job.sync_resume_pending
            with self._runtime_lock:
                runtime = self._runtimes.get(job_id)
                browser_still_open = bool(
                    runtime is not None and runtime.is_available
                )
            durable_wait = bool(
                job.automatic_retry_pending
                or job.pending_action is not None
            )
            job.state = (
                JobState.WAITING_HUMAN
                if browser_still_open or durable_wait
                else JobState.READY_FOR_FORM
            )
            job.human_checkpoint = (
                old_human_checkpoint
                if durable_wait and old_human_checkpoint
                else (
                    "A previously prepared browser action is still awaiting "
                    "live-page verification; it was not discarded or repeated"
                    if job.pending_action is not None
                    else "DocFlow approved field snapshot synchronized; resume "
                    "the same browser session"
                )
                if browser_still_open or durable_wait
                else None
            )
            if job.automatic_retry_pending:
                job.wait_kind = (
                    old_wait_kind
                    if old_wait_kind in {
                        "automatic_retry",
                        "runtime_recovery",
                    }
                    else "automatic_retry"
                )
            elif job.pending_action is not None:
                job.wait_kind = (
                    "manual_hard_boundary"
                    if old_wait_kind == "manual_hard_boundary"
                    and pending_is_next
                    else "automatic_retry"
                )
            elif job.sync_resume_pending:
                job.wait_kind = "runtime_recovery"
            elif (
                old_wait_kind == "manual_hard_boundary"
                and snapshot_changed
            ):
                # A field-consistency hard boundary with no non-idempotent
                # pending action may be reopened only by an explicit reviewed
                # snapshot change.  Receipt-conflict boundaries above retain
                # the hard wait because their pending Next remains unresolved.
                job.state = JobState.READY_FOR_FORM
                job.wait_kind = ""
            elif job.state == JobState.READY_FOR_FORM:
                job.wait_kind = ""
            preserve_hard_boundary = bool(
                old_wait_kind == "manual_hard_boundary"
                and (
                    not snapshot_changed
                    or pending_is_next
                )
            )
            if preserve_hard_boundary:
                job.state = JobState.WAITING_HUMAN
                job.wait_kind = "manual_hard_boundary"
                job.human_checkpoint = old_human_checkpoint
                job.continuous_run_requested = False
                job.sync_resume_pending = False
                should_wake_resume = False
                self._clear_automatic_retry_state(job)
            preserve_terminal_boundary = bool(
                old_state in TERMINAL_JOB_STATES
                and (
                    not snapshot_changed
                    or old_final_submission_boundary
                )
            )
            if preserve_terminal_boundary:
                job.state = old_state
                job.final_submission_boundary_reached = (
                    old_final_submission_boundary
                )
                job.wait_kind = old_wait_kind
                job.human_checkpoint = old_human_checkpoint
                job.continuous_run_requested = False
                job.sync_resume_pending = False
                should_wake_resume = False
                self._clear_automatic_retry_state(job)
            job.record(
                "approved_fields_synchronized",
                "Approved field snapshot synchronized without replacing browser",
                fieldCount=len(job.fields),
                retainedCompletedFieldCount=len(job.completed_field_ids),
                autoNext=job.auto_next,
                snapshotChanged=snapshot_changed,
                executionGeneration=job.execution_generation,
                pendingActionPreserved=job.pending_action is not None,
                automaticRetryPreserved=job.automatic_retry_pending,
            )
            self._require_store().save(job)
        if should_wake_resume:
            self._wake_continuous_resume(job_id)
            self._arm_continuous_resume(
                job_id,
                require_page_change=False,
            )
        return _public_job_payload(job)

    def cancel_job(self, job_id, payload):
        with self._job_lifecycle_lock(job_id):
            with self._active_jobs_lock:
                self._revoke_execution_leases(job_id)
            try:
                job = AgentOrchestrator(
                    checkpoint_store=self._require_store()
                ).cancel(
                    self._load_job(job_id),
                    actor=str(payload.get("actor") or "api"),
                )
            except ValueError as error:
                raise ServiceError(str(error), 409) from error
            job.continuous_run_requested = False
            job.wait_kind = ""
            job.sync_resume_pending = False
            self._clear_automatic_retry_state(job)
            self._require_store().save(job)
        self._release_runtime(job_id, purge_profile=True)
        return _public_job_payload(job)

    def open_job(self, job_id):
        """Open one browser for manual DS-160 retrieval without running Gemini."""
        self._assert_process_lifecycle_open()
        if self.runtime_factory is None:
            raise ServiceError(
                "Computer-use runtime is not configured in this isolated build",
                409,
            )
        with self._job_lifecycle_lock(job_id):
            with self._active_jobs_lock:
                if job_id in self._active_jobs:
                    raise ServiceError(
                        "Gemini Computer Use 正在运行，不能重复打开浏览器",
                        409,
                    )
            job = self._load_job(job_id)
            if job.state not in {
                JobState.READY_FOR_FORM,
                JobState.WAITING_HUMAN,
                JobState.REVIEW_REQUIRED,
                JobState.BLOCKED,
                JobState.FAILED,
            }:
                raise ServiceError(
                    f"Job browser cannot open from state: {job.state.value}",
                    409,
                )
            try:
                self._ensure_auto_resume_runtime(job_id, job)
            except (RuntimeError, ValueError, ProviderNotConfigured) as error:
                raise ServiceError(str(error), 409) from error
            except Exception as error:
                # Provider libraries such as Playwright expose their own
                # ``Error`` base class. Letting it escape produced only
                # "Agent Core internal error: Error" and left the user with
                # no safe retry guidance, even though startup teardown had
                # already closed the exact job-owned browser.
                raise ServiceError(
                    "专用浏览器启动失败（"
                    f"{type(error).__name__}）；已安全清理，请重试。",
                    409,
                ) from error
            state_before_open = job.state
            preserve_boundary = bool(
                job.wait_kind == "manual_hard_boundary"
                or
                state_before_open in {
                    JobState.REVIEW_REQUIRED,
                    JobState.BLOCKED,
                    JobState.FAILED,
                }
                or (
                    state_before_open == JobState.WAITING_HUMAN
                    and job.wait_kind in {
                        "manual_page_change",
                        "manual_hard_boundary",
                    }
                )
            )
            if (
                not preserve_boundary
                and not (
                job.automatic_retry_pending
                and job.continuous_run_requested
                )
            ):
                job.state = JobState.WAITING_HUMAN
                job.wait_kind = "manual_page_change"
                job.human_checkpoint = (
                    "Consultant must manually retrieve the already-created "
                    "DS-160 application and enter a formal form page before "
                    "starting Gemini"
                )
            job.record(
                "browser_opened_for_manual_entry",
                "Browser opened without invoking the computer-use model",
                autoResumeArmed=job.continuous_run_requested,
                statePreserved=preserve_boundary,
                preservedState=(
                    state_before_open.value if preserve_boundary else ""
                ),
                preservedWaitKind=(
                    str(job.wait_kind or "") if preserve_boundary else ""
                ),
            )
            self._require_store().save(job)
        should_arm_resume = bool(
            job.continuous_run_requested
            and job.state in {
                JobState.READY_FOR_FORM,
                JobState.WAITING_HUMAN,
                JobState.FILLING_FORM,
            }
            and job.wait_kind != "manual_hard_boundary"
        )
        if should_arm_resume:
            self._arm_continuous_resume(
                job_id,
                require_page_change=bool(
                    job.state == JobState.WAITING_HUMAN
                    and job.wait_kind == "manual_page_change"
                    and not job.automatic_retry_pending
                    and not job.sync_resume_pending
                ),
            )
        return _public_job_payload(job)

    @staticmethod
    def _reopen_legacy_repeater_order_boundary(job):
        """Migrate one checkpoint paused by the pre-fix repeater ordering.

        Before the page-batch ordering fix, a visual plan could execute Add
        Another before the remaining required fields on the same CEAC page.
        CEAC rejected that whole-page-validating postback three times and the
        durable safety budget correctly created a hard boundary. Let one
        explicit resume clear only that exact exhausted repeater field after
        the fixed runtime is installed. The migration event is durable, so a
        genuine post-fix failure can never be reopened into another loop.
        """
        order_upgrade_reopened = any(
            event.kind == "repeater_order_upgrade_reopened"
            for event in list(job.events or ())
        )
        dispatch_upgrade_reopened = any(
            event.kind == "repeater_postback_upgrade_reopened"
            for event in list(job.events or ())
        )
        executor_upgrade_reopened = any(
            event.kind == "repeater_executor_upgrade_reopened"
            for event in list(job.events or ())
        )
        diagnostic_upgrade_reopened = any(
            event.kind == "repeater_diagnostic_upgrade_reopened"
            for event in list(job.events or ())
        )
        native_submit_upgrade_reopened = any(
            event.kind == "repeater_native_submit_upgrade_reopened"
            for event in list(job.events or ())
        )
        if native_submit_upgrade_reopened:
            return False
        failure_event = next((
            event
            for event in reversed(list(job.events or ()))
            if event.kind == "repeater_growth_not_observed"
        ), None)
        if failure_event is None:
            return False
        detail = dict(failure_event.detail or {})
        field_id = str(detail.get("fieldId") or "")
        page_plan_id = str(detail.get("pagePlanId") or "")
        failure_count = int(detail.get("failureCount") or 0)
        if not (
            field_id
            and ".ensure." in field_id.casefold()
            and page_plan_id
            and page_plan_id == str(job.current_page_plan_id or "")
            and failure_count >= 3
            and str(job.human_checkpoint or "").startswith(
                "Add Another 连续三次未增加表格行"
            )
        ):
            return False
        failure_key = f"{page_plan_id}::{field_id}"
        if int(job.visual_failure_counts.get(failure_key, 0) or 0) < 3:
            return False
        job.visual_failure_counts.pop(failure_key, None)
        job.wait_kind = ""
        job.human_checkpoint = None
        event_kind = (
            "repeater_native_submit_upgrade_reopened"
            if diagnostic_upgrade_reopened
            else "repeater_diagnostic_upgrade_reopened"
            if executor_upgrade_reopened
            else "repeater_executor_upgrade_reopened"
            if dispatch_upgrade_reopened
            else "repeater_postback_upgrade_reopened"
            if order_upgrade_reopened
            else "repeater_order_upgrade_reopened"
        )
        job.record(
            event_kind,
            (
                "One legacy Add Another hard boundary was reopened after "
                "V2 added a request-proven native WebForms submit fallback"
                if diagnostic_upgrade_reopened
                else "One legacy Add Another hard boundary was reopened for one "
                "non-sensitive dispatch diagnostic"
                if executor_upgrade_reopened
                else "One legacy Add Another hard boundary was reopened after "
                "the repeater executor adopted exact ASP.NET dispatch"
                if dispatch_upgrade_reopened
                else "One legacy Add Another hard boundary was reopened "
                "after the exact ASP.NET postback dispatch fix"
                if order_upgrade_reopened
                else "One legacy Add Another hard boundary was reopened "
                "after the whole-page validation ordering fix"
            ),
            fieldId=field_id,
            pagePlanId=page_plan_id,
            clearedFailureCount=failure_count,
        )
        return True

    @staticmethod
    def _reopen_legacy_travel_duration_boundary(job):
        """Reopen one hard stop created by the old duration truncation bug.

        The generic maxlength preflight used to persist ``7 D`` for Travel's
        composite ``7 DAY`` field.  The page-wide audit then compared that
        stale representation with the visible amount/unit pair until the
        durable loop guard correctly stopped the job.  After installing the
        composite reader, allow one explicit resume of only that proven legacy
        checkpoint.  The durable migration event prevents a genuine later
        mismatch from being reopened repeatedly.
        """
        reopened_event = "travel_duration_composite_upgrade_reopened"
        if any(
            event.kind == reopened_event
            for event in list(job.events or ())
        ):
            return False
        failure_event = next((
            event
            for event in reversed(list(job.events or ()))
            if event.kind == "page_revalidation_stalled"
        ), None)
        if failure_event is None:
            return False
        detail = dict(failure_event.detail or {})
        field_ids = [
            str(field_id or "")
            for field_id in detail.get("fieldIds", ()) or ()
        ]
        field_id = next((
            candidate
            for candidate in field_ids
            if candidate.casefold().endswith("travel.stayduration")
        ), "")
        page_plan_id = str(detail.get("pagePlanId") or "")
        if not (
            field_id
            and len(field_ids) == 1
            and page_plan_id == "ceac-plan-travel"
            and page_plan_id == str(job.current_page_plan_id or "")
            and bool(detail.get("durable"))
            and str(job.human_checkpoint or "").startswith(
                "本页有字段在自动修复后仍与网页实际值不一致"
            )
        ):
            return False
        normalized_event = next((
            event
            for event in reversed(list(job.events or ()))
            if (
                event.kind == "control_value_normalized"
                and str((event.detail or {}).get("fieldId") or "")
                == field_id
            )
        ), None)
        if normalized_event is None:
            return False
        normalized_detail = dict(normalized_event.detail or {})
        if not (
            int(normalized_detail.get("originalLength") or 0)
            > int(normalized_detail.get("effectiveLength") or 0)
            and int(normalized_detail.get("maxLength") or 0) == 3
        ):
            return False
        job.control_normalized_values.pop(field_id, None)
        job.wait_kind = ""
        job.human_checkpoint = None
        job.record(
            reopened_event,
            "One legacy Travel duration hard boundary was reopened after "
            "composite maxlength handling and live readback were fixed",
            fieldId=field_id,
            pagePlanId=page_plan_id,
            clearedNormalizedValue=True,
        )
        return True

    @staticmethod
    def _reopen_legacy_address_phone_postal_boundary(job):
        """Migrate one D/N/A stop created before exact checkbox readback.

        The Address/Phone home-postal checkbox can be reset by a later CEAC
        postback. Older V2 runs retained its verified completion when the
        marker disappeared, and the first readback fix could consume the
        durable repair budget before stale validation summaries were accepted
        through exact native checked-state proof. Reopen only that fully
        evidenced historical checkpoint and only once.
        """
        reopened_event = (
            "address_phone_postal_dna_trusted_click_upgrade_reopened"
        )
        if any(
            event.kind == reopened_event
            for event in list(job.events or ())
        ):
            return False
        field_id = "ceac.address_phone.contact.homepostalcode"
        page_plan_id = "ceac-plan-address_phone"
        stalled = next((
            event
            for event in reversed(list(job.events or ()))
            if event.kind == "page_revalidation_stalled"
        ), None)
        audited = next((
            event
            for event in reversed(list(job.events or ()))
            if (
                event.kind
                == "v2_address_phone_postal_dna_revalidated"
                and field_id in {
                    str(item)
                    for item in (event.detail or {}).get(
                        "resetFieldIds", ()
                    ) or ()
                }
            )
        ), None)
        if stalled is None or audited is None:
            return False
        detail = dict(stalled.detail or {})
        if not (
            list(detail.get("fieldIds") or ()) == [field_id]
            and str(detail.get("pagePlanId") or "") == page_plan_id
            and str(job.current_page_plan_id or "") == page_plan_id
            and bool(detail.get("durable"))
            and str(job.human_checkpoint or "").startswith(
                "本页有字段在自动修复后仍与网页实际值不一致"
            )
        ):
            return False
        job.completed_field_ids = [
            item for item in job.completed_field_ids
            if str(item) != field_id
        ]
        job.inapplicable_field_ids = [
            item for item in job.inapplicable_field_ids
            if str(item) != field_id
        ]
        job.wait_kind = ""
        job.human_checkpoint = None
        job.record(
            reopened_event,
            "One legacy Address/Phone postal D/N/A boundary was reopened "
            "after exact native checkbox readback was installed",
            fieldId=field_id,
            pagePlanId=page_plan_id,
        )
        return True

    def start_job(self, job_id, expected_watcher_epoch=None):
        self._assert_process_lifecycle_open()
        if self.runtime_factory is None:
            raise ServiceError(
                "Computer-use runtime is not configured in this isolated build",
                409,
            )
        lifecycle_lock = self._job_lifecycle_lock(job_id)
        deferred_retry = None
        runtime_setup_error = None
        execution_lease = None
        with lifecycle_lock:
            self._assert_process_lifecycle_open()
            with self._active_jobs_lock:
                if job_id in self._active_jobs:
                    raise ServiceError(
                        "Gemini Computer Use 已在持续运行，无需再次点击",
                        409,
                    )
            job = self._load_job(job_id)
            if (
                expected_watcher_epoch is not None
                and not self._watcher_epoch_matches(
                    job,
                    expected_watcher_epoch,
                )
            ):
                raise ServiceError(
                    "Auto-resume watcher observation is stale",
                    409,
                )
            if job.state not in {
                JobState.READY_FOR_FORM,
                JobState.WAITING_HUMAN,
                JobState.FILLING_FORM,
            }:
                raise ServiceError(
                    f"Job cannot start from terminal state: {job.state.value}",
                    409,
                )
            if job.wait_kind == "manual_hard_boundary":
                explicit_user_resume = expected_watcher_epoch is None
                legacy_boundary_reopened = (
                    self._reopen_legacy_repeater_order_boundary(job)
                    or self._reopen_legacy_travel_duration_boundary(job)
                    or self._reopen_legacy_address_phone_postal_boundary(job)
                )
                pending = job.pending_action
                pending_kind = str(
                    getattr(getattr(pending, "kind", ""), "value", "")
                    or getattr(pending, "kind", "")
                    or ""
                ).strip().lower()
                pending_is_next = bool(
                    pending is not None
                    and pending_kind == "click"
                    and (
                        str(pending.reason or "")
                        == "Deterministic fixed CEAC Next control"
                        or str(pending.target_hint or "")
                        .strip()
                        .lower()
                        .startswith("next")
                    )
                )
                if (
                    not explicit_user_resume
                    and not pending_is_next
                    and not legacy_boundary_reopened
                ):
                    raise ServiceError(
                        "Job is waiting at a hard manual boundary and "
                        "cannot be reopened by an automatic watcher",
                        409,
                    )
                if (
                    explicit_user_resume
                    and not pending_is_next
                    and not legacy_boundary_reopened
                ):
                    # A hard boundary must stay durable across process restarts
                    # and watcher observations, but it is not a terminal job.
                    # The consultant's explicit Continue Gemini click is the
                    # authority to re-observe the same live browser after they
                    # have handled the visible CEAC page.  Do not clear any
                    # completed fields or pending action receipts.
                    previous_checkpoint = str(
                        job.human_checkpoint or ""
                    )[:400]
                    job.wait_kind = ""
                    job.human_checkpoint = None
                    job.sync_resume_pending = False
                    self._clear_automatic_retry_state(job)
                    job.record(
                        "explicit_manual_boundary_reopened",
                        "An explicit Continue Gemini request reopened the "
                        "same browser checkpoint for fresh observation",
                        previousCheckpoint=previous_checkpoint,
                        pendingActionPreserved=job.pending_action is not None,
                    )
                # An explicit user resume may safely re-observe a dispatched
                # Next receipt. The workflow always resolves pending actions
                # before planning, so this cannot click Next again: it either
                # recognizes the late CEAC route change or returns to a
                # read-only navigation wait on the same source page.
                if pending_is_next:
                    job.wait_kind = ""
                    job.human_checkpoint = None
                    job.record(
                        "hard_navigation_boundary_reopened",
                        "Explicit resume reopened a pending Next boundary for "
                        "read-only route reconciliation",
                        actionId=str(pending.id or ""),
                        pendingActionPreserved=True,
                    )
            job.continuous_run_requested = True
            if (
                job.automatic_retry_pending
                and self._automatic_retry_delay_seconds(job) > 0
            ):
                # Duplicate/lost HTTP responses must not bypass a provider,
                # browser, or progress backoff. The same durable run intent
                # remains armed and the watcher owns the due-time restart.
                self._require_store().save(job)
                deferred_retry = job
            else:
                with self._active_jobs_lock:
                    self._active_jobs.add(job_id)
            try:
                if deferred_retry is not None:
                    runtime = None
                    run_checkpoint_store = None
                else:
                    execution_lease = self._create_execution_lease(job)
                    job.sync_resume_pending = False
                    job.wait_kind = ""
                    job.record(
                        "continuous_run_armed",
                        "Continuous Gemini execution is armed until Review/Sign",
                        generation=job.execution_generation,
                    )
                    self._require_store().save(job)
                    runtime = self._ensure_auto_resume_runtime(
                        job_id,
                        job,
                    )
                    checkpoint_store = self._require_store()
                    run_progress = threading.Event()
                    run_checkpoint_store = _JobCheckpointStore(
                        checkpoint_store,
                        job_id,
                        lifecycle_lock,
                        execution_lease,
                        progress_callback=run_progress.set,
                    )
            except Exception as error:
                if execution_lease is not None:
                    # Factory/startup failed before a runtime could own and
                    # eventually reap this generation.  Revoke and remove the
                    # orphan immediately so the lease registry cannot grow
                    # across repeated startup failures.
                    execution_lease.revoke()
                    self._forget_execution_lease(execution_lease)
                if deferred_retry is None:
                    with self._active_jobs_lock:
                        self._active_jobs.discard(job_id)
                if (
                    isinstance(error, ServiceError)
                    and error.status == 503
                    and self._shutdown_requested.is_set()
                ):
                    # Graceful process shutdown is not a job failure.  Keep
                    # the durable one-click intent for startup recovery and
                    # publish no false terminal checkpoint.
                    raise
                if self._is_recoverable_runtime_error(error):
                    runtime_setup_error = error
                else:
                    self._record_terminal_runtime_failure(
                        job_id,
                        error,
                        source="runtime_start",
                    )
                    if isinstance(error, ServiceError):
                        raise
                    if isinstance(error, ProviderNotConfigured):
                        raise ServiceError(str(error), 409) from error
                    raise ServiceError(
                        "Computer-use runtime could not be initialized",
                        500,
                    ) from error
        if runtime_setup_error is not None:
            recovered = self._schedule_browser_runtime_retry(
                job_id,
                reason=(
                    "专用浏览器本轮未能启动；系统已保存任务并将自动"
                    "重建，无需再次点击运行。"
                ),
                error=runtime_setup_error,
                source="runtime_start",
                allow_active_generation=True,
            )
            self._arm_continuous_resume(
                job_id,
                require_page_change=False,
            )
            return _public_job_payload(recovered)
        if deferred_retry is not None:
            self._arm_continuous_resume(
                job_id,
                require_page_change=False,
            )
            return _public_job_payload(deferred_retry)
        runtime_failure = None
        runtime_result_ready = False
        try:
            def run_on_runtime_thread(computer_use_agent):
                # The browser loop owns its own checkpoint hook.  Wire the
                # service store here so every verified action survives a later
                # pause/resume instead of reloading the original empty job.
                computer_use_agent.checkpoint_store = run_checkpoint_store
                computer_use_agent.cancellation_check = (
                    execution_lease.assert_current
                )
                computer_use_agent.side_effect_executor = (
                    execution_lease.run_side_effect
                )
                return AgentOrchestrator(
                    checkpoint_store=run_checkpoint_store,
                    computer_use_agent=computer_use_agent,
                ).run_form(job)

            runtime._execution_lease = execution_lease
            completed = runtime.call(
                run_on_runtime_thread,
                timeout=self.RUN_INACTIVITY_TIMEOUT_SECONDS,
                progress_event=run_progress,
            )
            runtime_result_ready = True
        except ExecutionLeaseRevoked:
            # Cancellation, sync, or runtime retirement already published the
            # authoritative newer checkpoint.  The old worker must unwind
            # silently instead of converting that state into a failure/retry.
            completed = self._load_job(job_id)
            runtime_result_ready = True
        except ServiceError as error:
            self._record_terminal_runtime_failure(
                job_id,
                error,
                source="runtime_service_error",
            )
            raise
        except ProviderNotConfigured as error:
            self._record_terminal_runtime_failure(
                job_id,
                error,
                source="runtime_provider_configuration",
            )
            raise ServiceError(str(error), 409) from error
        except (RuntimeError, TimeoutError) as error:
            if not self._is_recoverable_runtime_error(error):
                self._record_terminal_runtime_failure(
                    job_id,
                    error,
                    source="runtime_nonrecoverable",
                )
                raise ServiceError(str(error), 409) from error
            runtime.poison(type(error).__name__)
            runtime_failure = error
            completed = self._schedule_browser_runtime_retry(
                job_id,
                reason=(
                    "浏览器运行时本轮失去响应；系统已保存当前动作并将"
                    "自动重建专用浏览器，无需再次点击。"
                ),
                error=error,
                source="runtime_command",
                allow_active_generation=True,
            )
            runtime_result_ready = True
        except ValueError as error:
            self._record_terminal_runtime_failure(
                job_id,
                error,
                source="runtime_value_error",
            )
            raise ServiceError(str(error), 409) from error
        except Exception as error:
            if self._is_recoverable_runtime_error(error):
                runtime.poison(type(error).__name__)
                runtime_failure = error
                completed = self._schedule_browser_runtime_retry(
                    job_id,
                    reason=(
                        "浏览器页面或连接已失效；系统已保存当前动作并将"
                        "自动重建专用浏览器，无需再次点击。"
                    ),
                    error=error,
                    source="runtime_exception",
                    allow_active_generation=True,
                )
                runtime_result_ready = True
            else:
                self._record_terminal_runtime_failure(
                    job_id,
                    error,
                    source="runtime_unexpected",
                )
                raise ServiceError(
                    f"Computer-use runtime failed: {type(error).__name__}",
                    500,
                ) from error
        finally:
            if not runtime_result_ready:
                with self._active_jobs_lock:
                    self._active_jobs.discard(job_id)
                if execution_lease is not None:
                    self._forget_execution_lease(execution_lease)

        completed = self._commit_runtime_result(
            job_id,
            completed,
            execution_lease,
        )
        if execution_lease is not None and not runtime.is_busy:
            self._forget_execution_lease(execution_lease)

        # Keep the exact browser/application session for resumable waits and
        # Review/Sign. Completed/cancelled jobs purge their private profile.
        if completed.state in {JobState.COMPLETED, JobState.CANCELLED}:
            self._release_runtime(job_id, purge_profile=True)
        elif (
            runtime_failure is not None
            or (
                completed.state == JobState.WAITING_HUMAN
                and completed.automatic_retry_pending
                and completed.automatic_retry_kind == "browser"
            )
        ):
            self._release_runtime(
                job_id,
                purge_profile=False,
                preserve_continuous_run=True,
            )
        if (
            completed.state == JobState.WAITING_HUMAN
            and completed.continuous_run_requested
            and completed.wait_kind != "manual_hard_boundary"
        ):
            self._arm_continuous_resume(
                job_id,
                require_page_change=bool(
                    not completed.automatic_retry_pending
                    and completed.wait_kind == "manual_page_change"
                ),
            )
        return _public_job_payload(completed)

    @staticmethod
    def _automatic_retry_delay_seconds(job, now=None):
        if not job.automatic_retry_pending:
            return 0.0
        raw = str(job.automatic_retry_after or "").strip()
        if not raw:
            return 0.0
        try:
            retry_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return 0.0
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - current).total_seconds())

    @staticmethod
    def _watcher_epoch(job):
        """CAS token for one watcher observation and its resulting mutation."""
        return (
            max(0, int(job.execution_generation or 0)),
            str(getattr(job.state, "value", job.state) or ""),
            str(job.wait_kind or ""),
            bool(job.continuous_run_requested),
            str(job.automatic_retry_after or ""),
            bool(job.automatic_retry_pending),
            str(job.automatic_retry_kind or ""),
            bool(job.sync_resume_pending),
            bool(job.final_submission_boundary_reached),
        )

    @classmethod
    def _watcher_epoch_matches(cls, job, expected_epoch):
        return (
            expected_epoch is None
            or cls._watcher_epoch(job) == tuple(expected_epoch)
        )

    @staticmethod
    def _clear_automatic_retry_state(job):
        job.automatic_retry_pending = False
        job.automatic_retry_after = ""
        job.automatic_retry_count = 0
        job.automatic_retry_kind = ""
        job.automatic_retry_preserves_page_boundary = False

    @staticmethod
    def _discard_terminal_pending_action(job, terminal_kind):
        pending = job.pending_action
        if pending is None:
            return
        job.pending_action = None
        job.record(
            "terminal_pending_action_discarded",
            "A pending browser action was closed at a terminal lifecycle "
            "boundary and was not claimed as applied",
            actionId=str(getattr(pending, "id", "") or ""),
            terminalKind=str(terminal_kind or ""),
        )

    def _commit_runtime_result(
        self,
        job_id,
        returned_job,
        execution_lease,
    ):
        """CAS, normalize, and persist one run result before clearing active.

        Cancellation is allowed to race an in-flight browser call; sync/review
        are not.  The durable current checkpoint therefore wins whenever the
        run lease was revoked, its generation changed, or a terminal/hard
        boundary was already published.  This closes the historical window in
        which a stale returned object could resurrect a cancelled job.
        """
        lifecycle_lock = self._job_lifecycle_lock(job_id)
        try:
            with lifecycle_lock:
                current = self._load_job(job_id)
                expected_generation = (
                    int(execution_lease.generation)
                    if execution_lease is not None
                    else int(current.execution_generation or 0)
                )
                current_is_authoritative = bool(
                    execution_lease is None
                    or execution_lease.revoked
                    or int(current.execution_generation or 0)
                    != expected_generation
                    or current.state in TERMINAL_JOB_STATES
                    or current.wait_kind == "manual_hard_boundary"
                )
                returned_generation_matches = bool(
                    returned_job is not None
                    and int(returned_job.execution_generation or 0)
                    == expected_generation
                )
                if current_is_authoritative or not returned_generation_matches:
                    committed = current
                else:
                    # Workflow exits save their own checkpoint.  Prefer that
                    # durable copy by default, while still supporting a custom
                    # runtime that returns an unsaved terminal/wait boundary.
                    committed = current
                    if (
                        returned_job.state != current.state
                        and (
                            returned_job.state in TERMINAL_JOB_STATES
                            or returned_job.state == JobState.WAITING_HUMAN
                        )
                    ):
                        committed = returned_job

                if committed.state in TERMINAL_JOB_STATES:
                    self._discard_terminal_pending_action(
                        committed,
                        committed.state.value,
                    )
                    committed.continuous_run_requested = False
                    committed.wait_kind = ""
                    committed.sync_resume_pending = False
                    self._clear_automatic_retry_state(committed)
                elif committed.wait_kind == "manual_hard_boundary":
                    committed.state = JobState.WAITING_HUMAN
                    committed.continuous_run_requested = False
                    committed.sync_resume_pending = False
                    self._clear_automatic_retry_state(committed)
                elif (
                    committed.state == JobState.WAITING_HUMAN
                    and committed.automatic_retry_pending
                    and committed.automatic_retry_kind == "browser"
                    and execution_lease is not None
                    and not execution_lease.revoked
                    and int(committed.execution_generation or 0)
                    == expected_generation
                ):
                    # Fence the failed browser generation atomically with its
                    # retry checkpoint.  The replacement cannot reuse its
                    # profile until the retiring-runtime tombstone clears.
                    self._revoke_execution_leases(job_id)
                    committed.execution_generation = (
                        expected_generation + 1
                    )
                    committed.record(
                        "execution_generation_retired",
                        "The previous browser worker generation was fenced "
                        "before runtime recovery",
                        reason="workflow_browser_retry",
                        generation=committed.execution_generation,
                    )
                _ensure_review_lease(committed)
                self._require_store().save(committed)
                return committed
        finally:
            with self._active_jobs_lock:
                self._active_jobs.discard(job_id)

    def _record_terminal_runtime_failure(self, job_id, error, source):
        """Atomically disarm a generation that cannot recover automatically.

        A raised runtime exception used to leave the last persisted checkpoint
        in ``filling_form`` with its continuous intent still set. Backends then
        attached a read-only monitor to an execution thread that no longer
        existed, creating a permanent fake-running UI. Every non-recoverable
        exit now publishes one authoritative terminal checkpoint before the HTTP
        error escapes.
        """
        with self._job_lifecycle_lock(job_id):
            try:
                job = self._load_job(job_id)
            except ServiceError:
                return None
            if job.state in TERMINAL_JOB_STATES:
                return job
            if (
                job.state == JobState.WAITING_HUMAN
                and job.wait_kind == "manual_hard_boundary"
            ):
                job.continuous_run_requested = False
                job.sync_resume_pending = False
                self._clear_automatic_retry_state(job)
                self._require_store().save(job)
                return job
            self._revoke_execution_leases(job_id)
            self._discard_terminal_pending_action(job, "runtime_failure")
            job.state = JobState.FAILED
            job.continuous_run_requested = False
            job.wait_kind = ""
            job.sync_resume_pending = False
            self._clear_automatic_retry_state(job)
            job.human_checkpoint = (
                "Gemini 执行遇到不可自动恢复的运行时错误；任务已明确停止，"
                "不会继续显示为运行中。"
            )
            job.record(
                "runtime_terminal_failure",
                job.human_checkpoint,
                source=str(source or "")[:80],
                errorType=type(error).__name__,
            )
            self._require_store().save(job)
            return job

    @staticmethod
    def _is_recoverable_runtime_error(error):
        if isinstance(error, TimeoutError):
            return True
        name = type(error).__name__.casefold()
        module = type(error).__module__.casefold()
        message = str(error or "").casefold()
        if "playwright" in module or any(
            token in name
            for token in (
                "targetclosed",
                "browserclosed",
                "pageclosed",
                "connectionclosed",
            )
        ):
            return True
        return any(
            token in message
            for token in (
                "browser runtime worker",
                "browser runtime command",
                "target page, context or browser has been closed",
                "target closed",
                "page closed",
                "browser closed",
                "browser has been closed",
                "connection closed",
                "cdp",
                "websocket",
                "pipe closed",
                "failed to launch browser",
                "failed to launch chromium",
                "browser launch",
                "profile is already in use",
                "singletonlock",
            )
        )

    def _schedule_browser_runtime_retry(
        self,
        job_id,
        reason,
        error,
        source,
        expected_epoch=None,
        allow_active_generation=False,
        return_status=False,
    ):
        """Persist a browser-only retry that survives process restarts."""
        def result(job, mutated):
            if return_status:
                return job, bool(mutated)
            return job

        with self._job_lifecycle_lock(job_id):
            job = self._load_job(job_id)
            if job.state in TERMINAL_JOB_STATES:
                return result(job, False)
            if not self._watcher_epoch_matches(job, expected_epoch):
                return result(job, False)
            with self._active_jobs_lock:
                active = job_id in self._active_jobs
            if active and not allow_active_generation:
                return result(job, False)
            if (
                job.state == JobState.WAITING_HUMAN
                and job.wait_kind == "manual_hard_boundary"
            ):
                job.continuous_run_requested = False
                job.sync_resume_pending = False
                self._clear_automatic_retry_state(job)
                self._require_store().save(job)
                return result(job, False)
            preserve_manual_page_boundary = bool(
                job.state == JobState.WAITING_HUMAN
                and job.wait_kind == "manual_page_change"
            )
            preserved_checkpoint = str(job.human_checkpoint or "")
            previous_kind = str(job.automatic_retry_kind or "")
            retry_count = (
                max(0, int(job.automatic_retry_count or 0))
                if previous_kind == "browser"
                else 0
            ) + 1
            retry_delay = min(
                30,
                2 * (2 ** min(max(0, retry_count - 1), 4)),
            )
            retry_after = (
                datetime.now(timezone.utc)
                + timedelta(seconds=retry_delay)
            ).isoformat()
            # Fence the failed worker immediately.  Waiting until the next
            # start would leave a timeout window in which its late return could
            # prepare and execute another browser action.
            self._revoke_execution_leases(job_id)
            job.execution_generation = max(
                0, int(job.execution_generation or 0)
            ) + 1
            job.continuous_run_requested = True
            job.state = JobState.WAITING_HUMAN
            job.wait_kind = (
                "manual_page_change"
                if preserve_manual_page_boundary
                else "runtime_recovery"
            )
            job.automatic_retry_pending = True
            job.automatic_retry_after = retry_after
            job.automatic_retry_count = retry_count
            job.automatic_retry_kind = "browser"
            job.automatic_retry_preserves_page_boundary = (
                preserve_manual_page_boundary
            )
            job.human_checkpoint = (
                preserved_checkpoint
                if preserve_manual_page_boundary and preserved_checkpoint
                else str(reason or "")
            )
            job.record(
                "browser_runtime_retry_scheduled",
                job.human_checkpoint,
                retryCount=retry_count,
                retryDelaySeconds=retry_delay,
                retryAfter=retry_after,
                source=str(source or "")[:80],
                errorType=type(error).__name__,
                pendingActionPreserved=job.pending_action is not None,
                runtimeResetRequired=True,
                manualPageBoundaryPreserved=preserve_manual_page_boundary,
                generation=job.execution_generation,
            )
            self._require_store().save(job)
            return result(job, True)

    @staticmethod
    def _runtime_job_key(runtime):
        return str(getattr(getattr(runtime, "_job", None), "id", "") or "")

    def _mark_runtime_retiring(self, runtime, purge_profile=False):
        if runtime is None:
            return ""
        job_key = self._runtime_job_key(runtime)
        if not job_key:
            return ""
        with self._runtime_lock:
            self._retiring_runtime_refs.setdefault(
                job_key, set()
            ).add(runtime)
            if purge_profile:
                self._retiring_runtime_purge_jobs.add(job_key)
        return job_key

    @staticmethod
    def _request_runtime_profile_purge(runtime):
        browser = (
            getattr(getattr(runtime, "_runtime", None), "browser", None)
            or getattr(
                getattr(runtime, "_startup_control", None),
                "browser",
                None,
            )
        )
        mark_purge = getattr(browser, "purge_profile_on_close", None)
        if callable(mark_purge):
            try:
                mark_purge()
            except Exception:
                pass

    def _finalize_runtime_retirement(self, runtime):
        job_key = self._runtime_job_key(runtime)
        lease = getattr(runtime, "_execution_lease", None)
        if lease is not None:
            self._forget_execution_lease(lease)
        if not job_key:
            return
        with self._runtime_lock:
            refs = self._retiring_runtime_refs.get(job_key)
            if refs is not None:
                refs.discard(runtime)
                if not refs:
                    self._retiring_runtime_refs.pop(job_key, None)
                    self._retiring_runtime_purge_jobs.discard(job_key)

    def _runtime_is_open(
        self,
        job_id,
        purge_if_stale=False,
        nonblocking=False,
    ):
        """Return whether this service can still use the job's browser worker."""
        stale = None
        acquired = self._runtime_lock.acquire(blocking=not nonblocking)
        if not acquired:
            # Runtime creation currently holds this lock while Playwright owns
            # its bounded startup handshake.  A status request must not join
            # that potentially 30-second wait. ``None`` is an explicit
            # transition/unknown result; callers must not treat it as proof of
            # an orphaned runtime.
            return None
        try:
            runtime = self._runtimes.get(job_id)
            runtime_open = bool(
                runtime is not None and runtime.is_available
            )
            if runtime is not None and not runtime_open:
                self._runtimes.pop(job_id, None)
                stale = runtime
                self._mark_runtime_retiring(
                    stale,
                    purge_profile=purge_if_stale,
                )
        finally:
            self._runtime_lock.release()
        if stale is not None:
            # Status polling must never spend the browser's full close timeout.
            # The tombstone above blocks a same-profile replacement while the
            # exact worker is retired asynchronously.
            self._retire_runtime_worker(
                stale,
                purge_profile=purge_if_stale,
                asynchronous=True,
            )
        return runtime_open

    def _retire_runtime_worker(
        self,
        runtime,
        purge_profile=False,
        asynchronous=False,
    ):
        """Close, retain, and eventually reap one exact browser worker."""
        if runtime is None:
            return
        job_key = self._mark_runtime_retiring(
            runtime,
            purge_profile=purge_profile,
        )
        lease = getattr(runtime, "_execution_lease", None)
        if lease is not None:
            lease.revoke()
        if (
            purge_profile
            or (
                job_key
                and job_key in self._retiring_runtime_purge_jobs
            )
        ):
            self._request_runtime_profile_purge(runtime)

        def close_and_reap():
            with self._runtime_lock:
                must_purge = bool(
                    purge_profile
                    or (
                        job_key
                        and job_key in self._retiring_runtime_purge_jobs
                    )
                )
            runtime.close(purge_profile=must_purge)
            if runtime.is_alive:
                self._track_retired_runtime_worker(runtime)
            else:
                self._finalize_runtime_retirement(runtime)

        if asynchronous:
            threading.Thread(
                target=close_and_reap,
                name=f"agent-runtime-retire-{job_key[-8:]}",
                daemon=True,
            ).start()
        else:
            close_and_reap()

    def _disarm_and_signal_auto_resume(self, job_id):
        """Fence durable intent and wake the one process-local watcher."""
        with self._job_lifecycle_lock(job_id):
            try:
                job = self._load_job(job_id)
            except ServiceError:
                job = None
            if job is not None:
                changed = bool(
                    job.continuous_run_requested
                    or job.sync_resume_pending
                    or job.automatic_retry_pending
                )
                self._revoke_execution_leases(job_id)
                job.continuous_run_requested = False
                job.sync_resume_pending = False
                self._clear_automatic_retry_state(job)
                if changed:
                    job.record(
                        "runtime_release_disarmed",
                        "An explicit runtime release disarmed durable "
                        "auto-resume before browser teardown",
                    )
                    self._require_store().save(job)
        with self._auto_resume_lock:
            self._auto_resume_pending_rearms.pop(str(job_id), None)
            stop_event = self._auto_resume_stop_events.get(str(job_id))
            ready_event = self._auto_resume_thread_ready_events.get(
                str(job_id)
            )
            wake_event = self._auto_resume_wake_events.get(str(job_id))
            if stop_event is not None:
                stop_event.set()
            if wake_event is not None:
                wake_event.set()
        return ready_event

    def _wait_for_auto_resume_watcher_exit(
        self,
        job_id,
        ready_event=None,
    ):
        """Join the exact watcher; release never returns beside a writer."""
        if ready_event is not None:
            ready_event.wait()
        with self._auto_resume_lock:
            thread = self._auto_resume_threads.get(str(job_id))
        if (
            thread is not None
            and thread is not threading.current_thread()
        ):
            thread.join()

    def _release_runtime(
        self,
        job_id,
        purge_profile=False,
        preserve_continuous_run=False,
    ):
        ready_event = None
        if not preserve_continuous_run:
            ready_event = self._disarm_and_signal_auto_resume(job_id)

        def retire_current_runtime():
            retiring = []
            with self._runtime_lock:
                runtime = self._runtimes.pop(job_id, None)
                if runtime is not None:
                    self._mark_runtime_retiring(
                        runtime,
                        purge_profile=purge_profile,
                    )
                elif purge_profile:
                    job_key = str(job_id)
                    retiring = list(
                        self._retiring_runtime_refs.get(job_key) or ()
                    )
                    if retiring:
                        self._retiring_runtime_purge_jobs.add(job_key)
            for item in retiring:
                self._request_runtime_profile_purge(item)
            self._retire_runtime_worker(
                runtime,
                purge_profile=purge_profile,
            )

        # Closing the browser first interrupts a watcher blocked in a live
        # observation. Joining then proves no checkpoint writer survives this
        # explicit release. A second sweep closes a runtime created in the
        # narrow ensure-vs-disarm race before the watcher observed its stop.
        retire_current_runtime()
        if not preserve_continuous_run:
            self._wait_for_auto_resume_watcher_exit(
                job_id,
                ready_event=ready_event,
            )
            retire_current_runtime()

    def _ensure_auto_resume_runtime(self, job_id, job):
        """Return or recreate the job-owned persistent browser runtime."""
        self._assert_process_lifecycle_open()
        stale = None
        with self._runtime_lock:
            self._assert_process_lifecycle_open()
            if self._retiring_runtime_refs.get(str(job_id)):
                raise RuntimeError(
                    "Browser runtime worker for this job is still retiring"
                )
            runtime = self._runtimes.get(job_id)
            if runtime is not None and not runtime.is_available:
                self._runtimes.pop(job_id, None)
                stale = runtime
                self._mark_runtime_retiring(stale, purge_profile=False)
                runtime = None
        if stale is not None:
            self._retire_runtime_worker(
                stale,
                purge_profile=False,
                asynchronous=True,
            )
            raise RuntimeError(
                "Browser runtime worker for this job is still retiring"
            )
        created_during_shutdown = None
        with self._runtime_lock:
            self._assert_process_lifecycle_open()
            runtime = self._runtimes.get(job_id)
            if runtime is None:
                try:
                    runtime = _RuntimeWorker(
                        self.runtime_factory,
                        job,
                        startup_timeout=(
                            self._runtime_startup_timeout_seconds
                        ),
                    )
                except _RuntimeStartupTimeout as error:
                    self._track_retired_runtime_worker(error.worker)
                    raise
                except _RuntimeStartupFailure as error:
                    self._track_retired_runtime_worker(error.worker)
                    raise error.cause from error
                if self._shutdown_requested.is_set():
                    created_during_shutdown = runtime
                    self._mark_runtime_retiring(
                        runtime,
                        purge_profile=False,
                    )
                else:
                    self._runtimes[job_id] = runtime
            if created_during_shutdown is None:
                return runtime
        # Startup began before the shutdown event but completed after it.  It
        # never becomes visible to a request/watcher and its profile is kept.
        created_during_shutdown.close(purge_profile=False)
        if created_during_shutdown.is_alive:
            self._track_retired_runtime_worker(created_during_shutdown)
        else:
            self._finalize_runtime_retirement(
                created_during_shutdown
            )
        self._assert_process_lifecycle_open()

    def _job_has_retired_runtime(self, job_id):
        with self._runtime_lock:
            return bool(
                self._retiring_runtime_refs.get(str(job_id))
            )

    def _track_retired_runtime_worker(self, runtime):
        """Retain and reap a timed-out startup worker until it really exits.

        A Python thread cannot be force-killed safely.  Startup cancellation
        therefore closes the exact job-owned Chrome process, queues a stop
        sentinel, and keeps the worker strongly owned here until its factory
        unwinds.  It can never become an untracked daemon that later acquires
        the same private profile behind the replacement runtime.
        """
        if runtime is None:
            return
        self._mark_runtime_retiring(runtime, purge_profile=False)
        if not runtime.is_alive:
            self._finalize_runtime_retirement(runtime)
            return
        with self._retired_runtime_lock:
            if runtime in self._retired_runtime_workers:
                return
            self._retired_runtime_workers.add(runtime)

        def reap():
            try:
                while runtime.is_alive:
                    runtime._thread.join(timeout=0.5)
            finally:
                with self._retired_runtime_lock:
                    self._retired_runtime_workers.discard(runtime)
                self._finalize_runtime_retirement(runtime)

        threading.Thread(
            target=reap,
            name=f"agent-runtime-reaper-{str(runtime._job.id)[-8:]}",
            daemon=True,
        ).start()

    def _record_auto_resume_degraded(
        self,
        job_id,
        reason,
        expected_epoch=None,
        **detail,
    ):
        """Publish a durable, visible retry state instead of dying silently."""
        with self._job_lifecycle_lock(job_id):
            try:
                job = self._load_job(job_id)
            except ServiceError:
                return None
            if not self._watcher_epoch_matches(job, expected_epoch):
                return None
            with self._active_jobs_lock:
                if job_id in self._active_jobs:
                    return None
            if (
                not job.continuous_run_requested
                or job.state in {
                    JobState.COMPLETED,
                    JobState.CANCELLED,
                    JobState.REVIEW_REQUIRED,
                    JobState.BLOCKED,
                    JobState.FAILED,
                }
            ):
                return None
            if job.wait_kind == "manual_hard_boundary":
                job.continuous_run_requested = False
                job.sync_resume_pending = False
                self._clear_automatic_retry_state(job)
                job.record(
                    "hard_boundary_monitor_exit",
                    "The auto-resume monitor stopped without changing or "
                    "crossing the hard manual boundary",
                    reason=str(reason or "")[:240],
                    **detail,
                )
                self._require_store().save(job)
                return job
            if job.wait_kind == "manual_page_change":
                # A monitor exception does not convert a user/page boundary
                # into an immediate runtime retry.  Preserve both the visible
                # reason and the requirement for an actual page transition.
                job.record(
                    "auto_resume_degraded",
                    "Auto-resume monitoring will reconnect while preserving "
                    "the existing manual page-change boundary",
                    reason=str(reason or "")[:240],
                    waitKind=job.wait_kind,
                    **detail,
                )
                self._require_store().save(job)
                return job
            job.state = JobState.WAITING_HUMAN
            job.wait_kind = "runtime_recovery"
            job.human_checkpoint = (
                "Gemini 自动续跑监控暂时中断，系统正在自动重连专用"
                "浏览器；无需再次点击运行。"
            )
            job.record(
                "auto_resume_degraded",
                job.human_checkpoint,
                reason=str(reason or "")[:240],
                **detail,
            )
            self._require_store().save(job)
            return job

    def _finish_auto_resume_at_terminal(
        self,
        job_id,
        reason,
        expected_epoch=None,
    ):
        """Make an observed Review/Sign page authoritative without another run."""
        with self._job_lifecycle_lock(job_id):
            try:
                job = self._load_job(job_id)
            except ServiceError:
                return False
            if not self._watcher_epoch_matches(job, expected_epoch):
                return False
            with self._active_jobs_lock:
                if job_id in self._active_jobs:
                    return False
            if job.state in TERMINAL_JOB_STATES:
                return False
            # Fence any timed-out generation before an observation-only
            # watcher publishes the authoritative Review/Sign boundary.
            self._revoke_execution_leases(job_id)
            job.execution_generation = max(
                0, int(job.execution_generation or 0)
            ) + 1
            pending = job.pending_action
            if pending is not None:
                try:
                    is_next = (
                        str(pending.kind.value) == "click"
                        and (
                            str(pending.reason or "")
                            == "Deterministic fixed CEAC Next control"
                            or str(pending.target_hint or "")
                            .strip()
                            .lower()
                            .startswith("next")
                        )
                    )
                except AttributeError:
                    is_next = False
                if is_next:
                    if pending.id not in job.applied_action_ids:
                        job.applied_action_ids.append(pending.id)
                else:
                    job.record(
                        "terminal_pending_action_discarded",
                        "A non-navigation pending action was closed at Review/"
                        "Sign without being claimed as applied",
                        actionId=pending.id,
                    )
                job.pending_action = None
            job.state = JobState.REVIEW_REQUIRED
            job.final_submission_boundary_reached = True
            job.continuous_run_requested = False
            job.wait_kind = ""
            job.sync_resume_pending = False
            job.sync_reconciliation_field_ids = []
            job.sync_reconciliation_page_plan_id = ""
            job.sync_reconciliation_page_plan_by_field = {}
            self._clear_automatic_retry_state(job)
            job.human_checkpoint = str(reason or "Review/Sign boundary")
            job.record(
                "auto_resume_terminal_observed",
                "Review/Sign was observed while the continuous-run watcher "
                "was armed; no second start or click was issued",
            )
            _ensure_review_lease(job)
            self._require_store().save(job)
            return True

    def _convert_retry_to_human_boundary(
        self,
        job_id,
        reason,
        fingerprint="",
        expected_epoch=None,
    ):
        """Replace any automatic retry with a real page-change boundary."""
        with self._job_lifecycle_lock(job_id):
            try:
                job = self._load_job(job_id)
            except ServiceError:
                return False
            if not self._watcher_epoch_matches(job, expected_epoch):
                return False
            with self._active_jobs_lock:
                if job_id in self._active_jobs:
                    return False
            if not job.automatic_retry_pending:
                return False
            self._revoke_execution_leases(job_id)
            job.execution_generation = max(
                0, int(job.execution_generation or 0)
            ) + 1
            self._clear_automatic_retry_state(job)
            job.state = JobState.WAITING_HUMAN
            job.wait_kind = "manual_page_change"
            job.human_checkpoint = str(
                reason
                or "The browser reached a page that requires human handling"
            )
            if fingerprint:
                job.wait_boundary_fingerprint = str(fingerprint)
            job.record(
                "automatic_retry_replaced_by_human_boundary",
                job.human_checkpoint,
            )
            self._require_store().save(job)
            return True

    def _arm_continuous_resume(self, job_id, require_page_change):
        """Resume one durable run intent when an approved CEAC page is ready.

        The watcher only observes the job-owned browser.  It never clicks and
        never starts a second execution while the service reports an active
        run.  After a hard/manual boundary it also requires an observable page
        change, preventing an immediate loop on the same unresolved control.
        """
        if self._shutdown_requested.is_set():
            return
        job_key = str(job_id)
        requested_page_change = bool(require_page_change)
        with self._auto_resume_lock:
            if self._shutdown_requested.is_set():
                return
            wake_event = self._auto_resume_wake_events.setdefault(
                job_key, threading.Event()
            )
            if job_key in self._auto_resume_jobs:
                previous_request = self._auto_resume_pending_rearms.get(
                    job_key
                )
                self._auto_resume_pending_rearms[job_key] = (
                    requested_page_change
                    if previous_request is None
                    else bool(previous_request)
                    and requested_page_change
                )
                # The active watcher either consumes this relaxed arm request
                # on its next loop or hands it to a successor during teardown.
                wake_event.set()
                return
            queued_request = self._auto_resume_pending_rearms.pop(
                job_key,
                None,
            )
            monitor_require_page_change = (
                requested_page_change
                if queued_request is None
                else requested_page_change and bool(queued_request)
            )
            stop_event = threading.Event()
            thread_ready_event = threading.Event()
            self._auto_resume_jobs.add(job_key)
            self._auto_resume_stop_events[job_key] = stop_event
            self._auto_resume_thread_ready_events[
                job_key
            ] = thread_ready_event

        def monitor():
            baseline = None
            page_change_required = monitor_require_page_change
            watcher_epoch = None
            rearm_after_run = False
            rearm_delay = 0.0
            rearm_require_page_change = True
            observation_failures = 0
            unknown_retry_signature = None
            unknown_retry_observations = 0

            def wait_for_change(seconds):
                if stop_event.is_set():
                    return
                wake_event.wait(timeout=max(0.0, float(seconds)))
                wake_event.clear()

            try:
                while True:
                    with self._auto_resume_lock:
                        queued_request = (
                            self._auto_resume_pending_rearms.pop(
                                job_key,
                                None,
                            )
                        )
                    if queued_request is not None:
                        page_change_required = bool(
                            page_change_required
                            and bool(queued_request)
                        )
                    if stop_event.is_set():
                        break
                    try:
                        job = self._load_job(job_id)
                    except ServiceError:
                        break
                    # A quiesce can disarm the job while this watcher is
                    # blocked reading its checkpoint. Never recreate a
                    # browser from that pre-disarm snapshot.
                    if stop_event.is_set():
                        break
                    if (
                        not job.continuous_run_requested
                        or job.state in {
                            JobState.COMPLETED,
                            JobState.CANCELLED,
                            JobState.REVIEW_REQUIRED,
                            JobState.BLOCKED,
                            JobState.FAILED,
                        }
                        or job.wait_kind == "manual_hard_boundary"
                    ):
                        break
                    with self._active_jobs_lock:
                        if job_id in self._active_jobs:
                            break
                    watcher_epoch = self._watcher_epoch(job)
                    if job.automatic_retry_pending:
                        retry_delay = self._automatic_retry_delay_seconds(job)
                        if retry_delay > 0:
                            # Every automatic retry is time-driven. Do not
                            # touch or re-observe the form before its durable
                            # due time; this also prevents a lost HTTP response
                            # from bypassing browser/provider rate limits.
                            wait_for_change(min(1.0, retry_delay))
                            continue
                    try:
                        runtime = self._ensure_auto_resume_runtime(
                            job_id,
                            job,
                        )
                    except ServiceError:
                        # The durable job/checkpoint disappeared (for example
                        # cancellation or service shutdown) while this daemon
                        # watcher was waking. There is nothing left to retry.
                        break
                    except Exception as error:
                        _, retry_scheduled = (
                            self._schedule_browser_runtime_retry(
                                job_id,
                                reason=(
                                    "专用浏览器本轮未能重新打开；系统将按"
                                    "退避计划继续自动重试，无需再次点击。"
                                ),
                                error=error,
                                source="persistent_runtime_reopen",
                                expected_epoch=watcher_epoch,
                                return_status=True,
                            )
                        )
                        rearm_after_run = True
                        rearm_require_page_change = False
                        if not retry_scheduled:
                            rearm_delay = 0.1
                        break
                    if stop_event.is_set():
                        break

                    def inspect(computer_use_agent):
                        browser = computer_use_agent.browser
                        observe = getattr(
                            browser, "observe_lightweight", None
                        )
                        observation = (
                            observe()
                            if callable(observe)
                            else browser.observe()
                        )
                        terminal_reason = (
                            computer_use_agent.page_plans.terminal_reason(
                                observation
                            )
                        )
                        approved = bool(
                            computer_use_agent.page_plans.match(observation)
                        )
                        manual_reason = ""
                        policy = getattr(computer_use_agent, "policy", None)
                        inspect_page = getattr(policy, "inspect_page", None)
                        if callable(inspect_page):
                            decision = inspect_page(observation)
                            if not decision.allowed:
                                manual_reason = str(decision.reason or "")
                        fingerprint = observation_fingerprint(
                            job,
                            observation,
                        )
                        signature = (
                            str(observation.page_id or ""),
                            str(observation.url or ""),
                            str(observation.title or ""),
                            tuple(observation.errors or ()),
                            tuple(sorted(
                                dict(observation.control_values or {}).items()
                            )),
                            fingerprint,
                        )
                        set_status = getattr(
                            browser,
                            "set_visual_status",
                            None,
                        )
                        if terminal_reason:
                            if callable(set_status):
                                set_status("paused", terminal_reason)
                        elif manual_reason and job.automatic_retry_pending:
                            if callable(set_status):
                                set_status("paused", manual_reason)
                        elif job.automatic_retry_pending:
                            if callable(set_status):
                                set_status(
                                    "thinking",
                                    job.human_checkpoint
                                    or "Gemini 服务暂时无响应，系统将自动重试",
                                )
                        return (
                            approved,
                            terminal_reason,
                            manual_reason,
                            signature,
                            fingerprint,
                            str(observation.url or ""),
                        )

                    try:
                        (
                            approved,
                            terminal_reason,
                            manual_reason,
                            signature,
                            fingerprint,
                            current_url,
                        ) = runtime.try_call(inspect, timeout=12)
                    except _RuntimeBusy:
                        # The form executor won the single-flight slot after
                        # the lifecycle check.  A read-only watcher must not
                        # queue behind, time out, or poison that valid run.
                        if stop_event.is_set():
                            break
                        wait_for_change(0.25)
                        continue
                    except Exception as error:
                        if stop_event.is_set():
                            break
                        observation_failures += 1
                        if observation_failures >= 3:
                            runtime.poison("browser_observation_failed")
                            _, retry_scheduled = (
                                self._schedule_browser_runtime_retry(
                                    job_id,
                                    reason=(
                                        "浏览器状态连续读取失败；系统已保留"
                                        "当前动作并将自动重建专用浏览器。"
                                    ),
                                    error=error,
                                    source="auto_resume_observation",
                                    expected_epoch=watcher_epoch,
                                    return_status=True,
                                )
                            )
                            if retry_scheduled:
                                self._release_runtime(
                                    job_id,
                                    purge_profile=False,
                                    preserve_continuous_run=True,
                                )
                            rearm_after_run = True
                            rearm_require_page_change = False
                            if not retry_scheduled:
                                rearm_delay = 0.1
                            break
                        wait_for_change(1.0)
                        continue
                    if stop_event.is_set():
                        break
                    observation_failures = 0
                    if terminal_reason:
                        terminal_committed = (
                            self._finish_auto_resume_at_terminal(
                            job_id,
                            terminal_reason,
                            expected_epoch=watcher_epoch,
                            )
                        )
                        rearm_after_run = not terminal_committed
                        if rearm_after_run:
                            rearm_delay = 0.1
                        break
                    if (
                        job.automatic_retry_pending
                        and manual_reason
                    ):
                        converted = self._convert_retry_to_human_boundary(
                            job_id,
                            manual_reason,
                            fingerprint=fingerprint,
                            expected_epoch=watcher_epoch,
                        )
                        rearm_after_run = True
                        rearm_require_page_change = True
                        if not converted:
                            rearm_delay = 0.1
                        break
                    if manual_reason:
                        # Policy/manual overlays are never crossed merely
                        # because the underlying route still matches a page
                        # plan.  Keep observing; the job-scoped visible-text
                        # digest in signature makes an overlay/CAPTCHA removal
                        # an observable page change.
                        baseline = signature
                        wait_for_change(1.0)
                        continue
                    if job.automatic_retry_pending and not approved:
                        # A navigation/browser retry can briefly expose an
                        # interstitial loading DOM. Do not mistake one sample
                        # for a new boundary, but also never spin forever on a
                        # stable page outside the code-owned CEAC registry.
                        if signature == unknown_retry_signature:
                            unknown_retry_observations += 1
                        else:
                            unknown_retry_signature = signature
                            unknown_retry_observations = 1
                        if unknown_retry_observations >= 3:
                            unknown_reason = (
                                "当前页面连续无法匹配任何已批准的 DS-160 "
                                "页面计划；Gemini 已安全暂停，请恢复到正式"
                                "申请页面后系统会自动继续。"
                            )
                            try:
                                def show_unknown_boundary(
                                    computer_use_agent,
                                ):
                                    set_status = getattr(
                                        computer_use_agent.browser,
                                        "set_visual_status",
                                        None,
                                    )
                                    if callable(set_status):
                                        set_status(
                                            "paused",
                                            unknown_reason,
                                        )

                                runtime.try_call(
                                    show_unknown_boundary,
                                    timeout=4,
                                )
                            except Exception:
                                pass
                            converted = (
                                self._convert_retry_to_human_boundary(
                                job_id,
                                unknown_reason,
                                fingerprint=fingerprint,
                                expected_epoch=watcher_epoch,
                                )
                            )
                            rearm_after_run = True
                            rearm_require_page_change = True
                            if not converted:
                                rearm_delay = 0.1
                            break
                        wait_for_change(1.0)
                        continue
                    unknown_retry_signature = None
                    unknown_retry_observations = 0
                    retry_pending = bool(job.automatic_retry_pending)
                    manual_page_boundary = bool(
                        job.wait_kind == "manual_page_change"
                        and (
                            not retry_pending
                            or job.automatic_retry_preserves_page_boundary
                        )
                    )
                    changed = False
                    if baseline is None:
                        baseline = signature
                        durable = str(
                            job.wait_boundary_fingerprint or ""
                        )
                        if durable:
                            changed = fingerprint != durable
                        elif job.last_safe_url:
                            changed = current_url != job.last_safe_url
                        if (
                            (
                                page_change_required
                                or manual_page_boundary
                            )
                            and (
                                manual_page_boundary
                                or not retry_pending
                            )
                        ):
                            if not changed and not job.sync_resume_pending:
                                wait_for_change(1.0)
                                continue
                    else:
                        changed = signature != baseline
                    if approved and (
                        (retry_pending and not manual_page_boundary)
                        or job.sync_resume_pending
                        or (
                            not page_change_required
                            and not manual_page_boundary
                        )
                        or changed
                    ):
                        try:
                            result = self.start_job(
                                job_id,
                                expected_watcher_epoch=watcher_epoch,
                            )
                        except ServiceError as error:
                            if stop_event.is_set():
                                break
                            with self._active_jobs_lock:
                                already_active = (
                                    job_id in self._active_jobs
                                )
                            if error.status == 409 and already_active:
                                break
                            try:
                                latest = self._load_job(job_id)
                            except ServiceError:
                                break
                            if (
                                error.status == 409
                                and not self._watcher_epoch_matches(
                                    latest,
                                    watcher_epoch,
                                )
                            ):
                                if (
                                    latest.continuous_run_requested
                                    and latest.state
                                    not in TERMINAL_JOB_STATES
                                    and latest.wait_kind
                                    != "manual_hard_boundary"
                                ):
                                    baseline = None
                                    continue
                                break
                            degraded = self._record_auto_resume_degraded(
                                job_id,
                                "automatic_start_failed",
                                expected_epoch=watcher_epoch,
                                status=error.status,
                                errorType=type(error).__name__,
                            )
                            rearm_after_run = bool(
                                degraded is None
                                or (
                                    degraded.continuous_run_requested
                                    and degraded.wait_kind
                                    != "manual_hard_boundary"
                                )
                            )
                            rearm_delay = 2.0
                            rearm_require_page_change = bool(
                                degraded is not None
                                and degraded.wait_kind
                                == "manual_page_change"
                                and not degraded.automatic_retry_pending
                                and not degraded.sync_resume_pending
                            )
                            break
                        rearm_after_run = (
                            result.get("state")
                            == JobState.WAITING_HUMAN.value
                            and bool(result.get(
                                "continuous_run_requested"
                            ))
                        )
                        rearm_require_page_change = not bool(
                            result.get("automatic_retry_pending")
                        )
                        break
                    wait_for_change(1.0)
            except Exception as error:
                # A watcher is a recovery mechanism, not a single-shot daemon.
                # Any unexpected monitor bug must become visible and re-arm
                # itself; otherwise an open browser plus a dead watcher looks
                # indistinguishable from a silently frozen run.
                if not stop_event.is_set():
                    degraded = self._record_auto_resume_degraded(
                        job_id,
                        "auto_resume_monitor_exception",
                        expected_epoch=watcher_epoch,
                        errorType=type(error).__name__,
                    )
                    rearm_after_run = bool(
                        degraded is None
                        or (
                            degraded.continuous_run_requested
                            and degraded.wait_kind
                            != "manual_hard_boundary"
                        )
                    )
                    rearm_delay = 1.0
                    rearm_require_page_change = bool(
                        degraded is not None
                        and degraded.wait_kind == "manual_page_change"
                        and not degraded.automatic_retry_pending
                        and not degraded.sync_resume_pending
                    )
            finally:
                queued_rearm = None
                with self._auto_resume_lock:
                    current_thread = threading.current_thread()
                    if (
                        self._auto_resume_threads.get(job_key)
                        is current_thread
                    ):
                        queued_rearm = (
                            self._auto_resume_pending_rearms.pop(
                                job_key,
                                None,
                            )
                        )
                        self._auto_resume_jobs.discard(job_key)
                        self._auto_resume_threads.pop(job_key, None)
                        self._auto_resume_stop_events.pop(
                            job_key,
                            None,
                        )
                        self._auto_resume_thread_ready_events.pop(
                            job_key,
                            None,
                        )
                if stop_event.is_set():
                    rearm_after_run = False
                    queued_rearm = None
                elif queued_rearm is not None:
                    if rearm_after_run:
                        rearm_require_page_change = bool(
                            rearm_require_page_change
                            and bool(queued_rearm)
                        )
                    else:
                        rearm_after_run = True
                        rearm_require_page_change = bool(queued_rearm)
            if rearm_after_run:
                if rearm_delay:
                    wait_for_change(rearm_delay)
                if stop_event.is_set():
                    rearm_after_run = False
                try:
                    latest = self._load_job(job_id)
                    with self._active_jobs_lock:
                        latest_is_active = job_id in self._active_jobs
                    if (
                        latest_is_active
                        or not latest.continuous_run_requested
                        or latest.state in {
                            JobState.COMPLETED,
                            JobState.CANCELLED,
                            JobState.REVIEW_REQUIRED,
                            JobState.BLOCKED,
                            JobState.FAILED,
                        }
                        or latest.wait_kind == "manual_hard_boundary"
                    ):
                        rearm_after_run = False
                    if latest.automatic_retry_pending:
                        rearm_require_page_change = False
                except ServiceError:
                    return
                if rearm_after_run and not stop_event.is_set():
                    self._arm_continuous_resume(
                        job_id,
                        require_page_change=rearm_require_page_change,
                    )
            if not rearm_after_run:
                with self._auto_resume_lock:
                    if (
                        job_key not in self._auto_resume_jobs
                        and self._auto_resume_wake_events.get(
                            job_key
                        ) is wake_event
                    ):
                        self._auto_resume_wake_events.pop(
                            job_key,
                            None,
                        )

        watcher_thread = threading.Thread(
            target=monitor,
            name=f"agent-auto-resume-{job_key[-8:]}",
            daemon=True,
        )
        with self._auto_resume_lock:
            self._auto_resume_threads[job_key] = watcher_thread
            try:
                watcher_thread.start()
            except Exception:
                if (
                    self._auto_resume_threads.get(job_key)
                    is watcher_thread
                ):
                    self._auto_resume_threads.pop(job_key, None)
                    self._auto_resume_jobs.discard(job_key)
                    self._auto_resume_stop_events.pop(job_key, None)
                    self._auto_resume_thread_ready_events.pop(
                        job_key,
                        None,
                    )
                    self._auto_resume_pending_rearms.pop(
                        job_key,
                        None,
                    )
                    if (
                        self._auto_resume_wake_events.get(job_key)
                        is wake_event
                    ):
                        self._auto_resume_wake_events.pop(
                            job_key,
                            None,
                        )
                raise
            finally:
                # Quiesce may already be waiting for publication. It must
                # never return before this arm attempt has either started or
                # failed deterministically.
                thread_ready_event.set()

    def _job_lifecycle_lock(self, job_id):
        with self._job_locks_guard:
            return self._job_locks.setdefault(
                str(job_id), threading.RLock()
            )

    def _revoke_execution_leases(self, job_id):
        """Revoke every worker generation for this job without reusing events."""
        job_key = str(job_id)
        with self._execution_leases_lock:
            leases = [
                lease
                for (stored_job_id, _generation), lease
                in self._execution_leases.items()
                if stored_job_id == job_key
            ]
        for lease in leases:
            lease.revoke()

    def _create_execution_lease(self, job):
        """Advance the durable generation and return its fresh one-shot lease."""
        self._assert_process_lifecycle_open()
        self._revoke_execution_leases(job.id)
        job.execution_generation = max(
            0, int(job.execution_generation or 0)
        ) + 1
        lease = _ExecutionLease(job.id, job.execution_generation)
        with self._execution_leases_lock:
            if self._shutdown_requested.is_set():
                lease.revoke()
                raise ServiceError(
                    "Agent Core is shutting down; no execution lease was created",
                    503,
                )
            self._execution_leases[
                (str(job.id), int(job.execution_generation))
            ] = lease
        return lease

    def _forget_execution_lease(self, lease):
        if lease is None:
            return
        key = (str(lease.job_id), int(lease.generation))
        with self._execution_leases_lock:
            if self._execution_leases.get(key) is lease:
                self._execution_leases.pop(key, None)

    def _retire_execution_generation(self, job_id, reason):
        """Fence a timed-out/retired worker before any replacement can start."""
        with self._job_lifecycle_lock(job_id):
            job = self._load_job(job_id)
            self._revoke_execution_leases(job_id)
            job.execution_generation = max(
                0, int(job.execution_generation or 0)
            ) + 1
            job.record(
                "execution_generation_retired",
                "The previous browser worker generation was fenced before "
                "runtime recovery",
                reason=str(reason or "")[:120],
                generation=job.execution_generation,
            )
            self._require_store().save(job)
            return job

    def _wake_continuous_resume(self, job_id):
        with self._auto_resume_lock:
            wake_event = self._auto_resume_wake_events.setdefault(
                str(job_id), threading.Event()
            )
            wake_event.set()

    def _normalize_lifecycle_invariants(self, job):
        """Apply non-bypassable terminal/hard-boundary state invariants."""
        changed = False
        if (
            job.state == JobState.REVIEW_REQUIRED
            and not job.final_submission_boundary_reached
        ):
            boundary_text = " ".join([
                str(job.human_checkpoint or ""),
                *[
                    str(getattr(event, "message", "") or "")
                    for event in job.events or ()
                    if event.kind in {
                        "review_required",
                        "auto_resume_terminal_observed",
                    }
                ],
            ]).casefold()
            if (
                "review/sign" in boundary_text
                or "final submit" in boundary_text
                or "最终签名" in boundary_text
                or any(
                    event.kind == "auto_resume_terminal_observed"
                    for event in job.events or ()
                )
            ):
                job.final_submission_boundary_reached = True
                changed = True
        if job.state in TERMINAL_JOB_STATES:
            if job.continuous_run_requested or job.sync_resume_pending:
                changed = True
            job.continuous_run_requested = False
            job.sync_resume_pending = False
            if job.automatic_retry_pending:
                changed = True
            self._clear_automatic_retry_state(job)
        if job.wait_kind == "manual_hard_boundary":
            if job.state in {
                JobState.READY_FOR_FORM,
                JobState.FILLING_FORM,
            }:
                job.state = JobState.WAITING_HUMAN
                changed = True
            if job.continuous_run_requested or job.sync_resume_pending:
                changed = True
            job.continuous_run_requested = False
            job.sync_resume_pending = False
            if job.automatic_retry_pending:
                changed = True
            self._clear_automatic_retry_state(job)
            if not job.human_checkpoint:
                job.human_checkpoint = (
                    "Gemini is stopped at a hard consistency boundary"
                )
                changed = True
        return changed

    def _load_job(self, job_id):
        try:
            job = self._require_store().load_job(job_id)
            self._normalize_lifecycle_invariants(job)
            return job
        except FileNotFoundError as error:
            raise ServiceError("Job not found", 404) from error
        except (ValueError, CheckpointProtectionError) as error:
            raise ServiceError(str(error), 400) from error

    def _require_store(self):
        if self.checkpoint_store is None:
            raise ServiceError(
                self.storage_error or "Checkpoint store is unavailable", 503
            )
        return self.checkpoint_store


class Handler(BaseHTTPRequestHandler):
    service = AgentService()
    JOB_PATH = re.compile(
        r"^/v1/jobs/(?P<job_id>agent-job-[A-Za-z0-9-]+)"
        r"(?:/(?P<action>review|sync|open|start|resume|cancel))?$"
    )

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            return self.json_response(self.service.health())
        matched = self.JOB_PATH.fullmatch(path)
        if matched and not matched.group("action"):
            return self._call(
                self.service.get_job, matched.group("job_id")
            )
        return self.json_response({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/v1/recognize-text":
            return self._call(self.service.recognize_text, self.read_json())
        if path == "/v1/recognize-document":
            return self._call(
                self.service.recognize_document,
                self.read_json(max_bytes=36 * 1024 * 1024),
            )
        if path == "/v1/transform-text":
            return self._call(self.service.transform_text, self.read_json())
        if path == "/v1/jobs":
            return self._call(self.service.create_job, self.read_json(), status=201)
        matched = self.JOB_PATH.fullmatch(path)
        if matched:
            job_id = matched.group("job_id")
            action = matched.group("action")
            if action == "review":
                return self._call(
                    self.service.review_job, job_id, self.read_json()
                )
            if action == "sync":
                return self._call(
                    self.service.sync_job, job_id, self.read_json()
                )
            if action == "open":
                return self._call(self.service.open_job, job_id)
            if action in {"start", "resume"}:
                return self._call(self.service.start_job, job_id)
            if action == "cancel":
                return self._call(
                    self.service.cancel_job, job_id, self.read_json()
                )
        return self.json_response({"error": "Not found"}, 404)

    def _call(self, function, *args, status=200):
        try:
            return self.json_response(function(*args), status)
        except ServiceError as error:
            return self.json_response({"error": str(error)}, error.status)
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as error:
            return self.json_response({"error": str(error)}, 400)
        except Exception as error:
            return self.json_response(
                {
                    "error": (
                        "Agent Core internal error: "
                        f"{type(error).__name__}"
                    )
                },
                500,
            )

    def read_json(self, max_bytes=2 * 1024 * 1024):
        length = int(self.headers.get("Content-Length", "0"))
        if length > max_bytes:
            raise ValueError("Request is too large")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw)

    def json_response(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header(
                "Content-Type", "application/json; charset=utf-8"
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The launcher deliberately uses short, no-proxy health probes.
            # If a probe times out while a concurrent startup recovery is
            # finishing, the client socket can close before this tiny response
            # is written.  That is not an Agent crash and must not print a
            # misleading request-thread traceback in the user-facing terminal.
            return

    def log_message(self, format, *args):
        # Avoid accidentally writing request paths containing identifiers.
        return


def _interrupt_server_on_sigterm(_signum, _frame):
    """Route SIGTERM through the same finally path as Ctrl-C."""
    raise KeyboardInterrupt


def run_server(host=None, port=None):
    from .factory import build_service

    config = load_config()
    Handler.service = build_service(config)
    server = ThreadingHTTPServer(
        (host or config.host, port or config.port), Handler
    )
    previous_sigterm_handler = None
    install_sigterm_handler = bool(
        threading.current_thread() is threading.main_thread()
        and hasattr(signal, "SIGTERM")
    )
    if install_sigterm_handler:
        previous_sigterm_handler = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _interrupt_server_on_sigterm)
    try:
        Handler.service.recover_durable_continuous_runs()
        print(
            "Standalone agent API: "
            f"http://{server.server_address[0]}:{server.server_port}"
        )
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            Handler.service.shutdown()
        finally:
            try:
                server.server_close()
            finally:
                if install_sigterm_handler:
                    signal.signal(
                        signal.SIGTERM,
                        previous_sigterm_handler,
                    )


if __name__ == "__main__":
    selected_port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_server(port=selected_port)
