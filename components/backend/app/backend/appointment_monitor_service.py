"""Main-process service that wires appointment monitor workers to persistence."""

from datetime import datetime, timedelta, timezone

from .appointment_monitor import (
    AppointmentMonitorCoordinator,
    AppointmentMonitorWorker,
    MonitorConfig,
)
from . import appointment_monitor_store as store


class AppointmentMonitorService:
    """Bounded local service; browser adapters can be supplied later."""

    def __init__(self, connect, *, organization_id, provider, on_event=None):
        self.connect = connect
        self.organization_id = organization_id
        self.provider = provider
        self.on_event = on_event
        self.coordinator = AppointmentMonitorCoordinator(on_event=self._handle_event)
        self._task_ids = {}

    def load_tasks(self, user_id=None):
        tasks = store.list_tasks(self.connect, organization_id=self.organization_id)
        for task in tasks:
            if user_id is not None and str(task["user_id"]) != str(user_id):
                continue
            if task["id"] in self._task_ids:
                continue
            config = MonitorConfig.from_dict({
                "monitorId": task["worker_id"],
                "applicantId": task["applicant_id"],
                "location": task["location"],
                "intervalSeconds": task["interval_seconds"],
                "earliestDate": task["earliest_date"],
                "latestDate": task["latest_date"],
                "baselineDate": task["baseline_date"],
                "bookingMode": task["booking_mode"],
            })
            worker = AppointmentMonitorWorker(config, self.provider)
            self.coordinator.add_worker(worker)
            self._task_ids[task["id"]] = worker
        return tasks

    def create_task(self, *, user_id, case_id, config, provider_kind="mock"):
        task = store.create_task(
            self.connect,
            organization_id=self.organization_id,
            user_id=user_id,
            case_id=case_id,
            config=config,
            provider_kind=provider_kind,
        )
        worker = AppointmentMonitorWorker(config, self.provider)
        self.coordinator.add_worker(worker)
        self._task_ids[task["id"]] = worker
        return task

    def poll_once_all(self, now=None):
        events = self.coordinator.poll_once_all(now=now)
        for task_id, worker in self._task_ids.items():
            snapshot = worker.snapshot()
            checked_at = snapshot["lastCheckedAt"] or now
            interval = worker.config.interval_seconds
            next_check = None
            if checked_at:
                try:
                    next_check = (
                        datetime.fromisoformat(checked_at)
                        + timedelta(seconds=interval)
                    ).isoformat()
                except ValueError:
                    next_check = None
            store.update_runtime(
                self.connect,
                task_id=task_id,
                organization_id=self.organization_id,
                state=snapshot["state"],
                checked_at=checked_at,
                next_check_at=next_check,
                last_error=snapshot["lastError"],
            )
            store.record_snapshot(
                self.connect,
                task_id=task_id,
                organization_id=self.organization_id,
                slots=worker.last_slots,
                observed_at=checked_at,
            )
        return events

    def _handle_event(self, event):
        task_id = next(
            (task_id for task_id, worker in self._task_ids.items()
             if worker.config.monitor_id == event.monitor_id),
            None,
        )
        if task_id:
            store.record_event(
                self.connect,
                task_id=task_id,
                organization_id=self.organization_id,
                event=event,
            )
        if self.on_event:
            self.on_event(event)

    def snapshots(self):
        return self.coordinator.snapshots()

    def start(self):
        self.coordinator.start()

    def stop(self):
        self.coordinator.stop()
