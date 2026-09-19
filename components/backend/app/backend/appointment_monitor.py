"""Local appointment-opportunity monitoring framework.

This module is intentionally a read-only orchestration seam.  It models five
independent monitoring workers and a coordinator, but it does not authenticate
to a visa scheduling website, call private endpoints, solve challenges, select
slots, or submit bookings.  A future approved browser integration can provide a
``SlotProvider`` implementation while retaining the same state and human-
confirmation boundary.

The default provider used by tests and the demo is an in-memory simulator.  It
lets the rest of the application be developed without starting five browsers
or touching live appointment inventory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple


LOGGER = logging.getLogger("docflow.appointment_monitor")

SUPPORTED_LOCATIONS: Tuple[str, ...] = (
    "BEIJING",
    "SHANGHAI",
    "GUANGZHOU",
    "WUHAN",
    "SHENYANG",
)
LOCATION_LABELS: Mapping[str, str] = {
    "BEIJING": "北京",
    "SHANGHAI": "上海",
    "GUANGZHOU": "广州",
    "WUHAN": "武汉",
    "SHENYANG": "沈阳",
}
LOCATION_ALIASES: Mapping[str, str] = {
    "北京": "BEIJING",
    "北京使馆": "BEIJING",
    "上海": "SHANGHAI",
    "上海领馆": "SHANGHAI",
    "广州": "GUANGZHOU",
    "广州领馆": "GUANGZHOU",
    "武汉": "WUHAN",
    "武汉领馆": "WUHAN",
    "沈阳": "SHENYANG",
    "沈阳领馆": "SHENYANG",
}
WORKER_IDS: Tuple[str, ...] = tuple(
    f"appointment-monitor-{location.lower()}" for location in SUPPORTED_LOCATIONS
)
MIN_INTERVAL_SECONDS = 30
ALLOWED_BOOKING_MODES = frozenset({"notify_only", "human_confirm"})


class AppointmentMonitorError(ValueError):
    """Raised when a monitor configuration or provider response is invalid."""


class SlotProvider(Protocol):
    """Read-only source of currently visible appointment slots."""

    def check(self, config: "MonitorConfig") -> Sequence["AppointmentSlot"]:
        ...


@dataclass(frozen=True)
class AppointmentSlot:
    """A normalized slot observation with no credentials or applicant PII."""

    location: str
    appointment_date: date
    appointment_time: str
    appointment_type: str = "interview"
    slot_id: str = ""
    observed_at: str = ""

    def __post_init__(self) -> None:
        raw_location = str(self.location or "").strip()
        location = LOCATION_ALIASES.get(raw_location, raw_location.upper())
        if location not in SUPPORTED_LOCATIONS:
            raise AppointmentMonitorError(f"不支持的预约地点: {self.location}")
        if not isinstance(self.appointment_date, date):
            raise AppointmentMonitorError("预约日期必须是 datetime.date")
        if not str(self.appointment_time or "").strip():
            raise AppointmentMonitorError("预约时间不能为空")
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "appointment_time", str(self.appointment_time).strip())
        object.__setattr__(self, "appointment_type", str(self.appointment_type or "interview").strip())
        if not self.slot_id:
            digest = hashlib.sha256(
                f"{location}|{self.appointment_date.isoformat()}|"
                f"{self.appointment_time}|{self.appointment_type}".encode("utf-8")
            ).hexdigest()[:24]
            object.__setattr__(self, "slot_id", f"slot-{digest}")

    def as_dict(self) -> Dict[str, str]:
        return {
            "slotId": self.slot_id,
            "location": self.location,
            "locationLabel": LOCATION_LABELS[self.location],
            "appointmentDate": self.appointment_date.isoformat(),
            "appointmentTime": self.appointment_time,
            "appointmentType": self.appointment_type,
            "observedAt": self.observed_at,
        }


@dataclass(frozen=True)
class MonitorConfig:
    """Configuration for one applicant/location monitoring task."""

    monitor_id: str
    applicant_id: str
    location: str
    interval_seconds: int = 300
    earliest_date: Optional[date] = None
    latest_date: Optional[date] = None
    baseline_date: Optional[date] = None
    enabled: bool = True
    booking_mode: str = "human_confirm"

    def __post_init__(self) -> None:
        monitor_id = str(self.monitor_id or "").strip()
        applicant_id = str(self.applicant_id or "").strip()
        location = str(self.location or "").strip().upper()
        if not monitor_id:
            raise AppointmentMonitorError("monitor_id 不能为空")
        if not applicant_id:
            raise AppointmentMonitorError("applicant_id 不能为空")
        if location not in SUPPORTED_LOCATIONS:
            raise AppointmentMonitorError(f"不支持的预约地点: {self.location}")
        if int(self.interval_seconds) < MIN_INTERVAL_SECONDS:
            raise AppointmentMonitorError(
                f"查询间隔不能小于 {MIN_INTERVAL_SECONDS} 秒"
            )
        if self.earliest_date and self.latest_date and self.earliest_date > self.latest_date:
            raise AppointmentMonitorError("日期范围的起始日期不能晚于结束日期")
        if self.booking_mode not in ALLOWED_BOOKING_MODES:
            raise AppointmentMonitorError(
                "booking_mode 只能是 notify_only 或 human_confirm"
            )
        object.__setattr__(self, "monitor_id", monitor_id)
        object.__setattr__(self, "applicant_id", applicant_id)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "interval_seconds", int(self.interval_seconds))

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "MonitorConfig":
        def parse_date(value: object) -> Optional[date]:
            if value in (None, ""):
                return None
            try:
                return date.fromisoformat(str(value))
            except ValueError as error:
                raise AppointmentMonitorError(f"日期格式无效: {value}") from error

        return cls(
            monitor_id=str(payload.get("monitorId") or payload.get("monitor_id") or ""),
            applicant_id=str(payload.get("applicantId") or payload.get("applicant_id") or ""),
            location=str(payload.get("location") or ""),
            interval_seconds=int(payload.get("intervalSeconds") or payload.get("interval_seconds") or 300),
            earliest_date=parse_date(payload.get("earliestDate") or payload.get("earliest_date")),
            latest_date=parse_date(payload.get("latestDate") or payload.get("latest_date")),
            baseline_date=parse_date(payload.get("baselineDate") or payload.get("baseline_date")),
            enabled=bool(payload.get("enabled", True)),
            booking_mode=str(payload.get("bookingMode") or payload.get("booking_mode") or "human_confirm"),
        )


@dataclass(frozen=True)
class MonitorEvent:
    """A safe event for the coordinator/notification layer."""

    event_type: str
    monitor_id: str
    location: str
    created_at: str
    candidates: Tuple[AppointmentSlot, ...] = ()
    message: str = ""
    error: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "eventType": self.event_type,
            "monitorId": self.monitor_id,
            "location": self.location,
            "createdAt": self.created_at,
            "candidates": [slot.as_dict() for slot in self.candidates],
            "message": self.message,
            "error": self.error,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sort_slots(slots: Iterable[AppointmentSlot]) -> List[AppointmentSlot]:
    return sorted(
        slots,
        key=lambda slot: (slot.appointment_date, slot.appointment_time, slot.slot_id),
    )


class AppointmentMonitorWorker:
    """One deterministic, read-only monitor worker."""

    def __init__(
        self,
        config: MonitorConfig,
        provider: SlotProvider,
        on_event: Optional[Callable[[MonitorEvent], None]] = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.on_event = on_event
        self.state = "disabled" if not config.enabled else "watching"
        self.last_checked_at = ""
        self.last_error = ""
        self.last_slots: Tuple[AppointmentSlot, ...] = ()
        self.pending_candidates: Tuple[AppointmentSlot, ...] = ()
        self._notified_slot_ids = set()
        self._lock = threading.RLock()

    def _emit(self, event: MonitorEvent) -> MonitorEvent:
        if self.on_event:
            self.on_event(event)
        return event

    def _accepted(self, slot: AppointmentSlot) -> bool:
        if slot.location != self.config.location:
            return False
        if self.config.earliest_date and slot.appointment_date < self.config.earliest_date:
            return False
        if self.config.latest_date and slot.appointment_date > self.config.latest_date:
            return False
        if self.config.baseline_date and slot.appointment_date >= self.config.baseline_date:
            return False
        return True

    def poll_once(self, now: Optional[str] = None) -> List[MonitorEvent]:
        """Read one snapshot and return any newly actionable events."""
        with self._lock:
            if not self.config.enabled:
                self.state = "disabled"
                return []
            self.state = "checking"
            self.last_checked_at = now or _now_iso()
            self.last_error = ""
            try:
                observed = tuple(_sort_slots(self.provider.check(self.config)))
                if any(slot.location != self.config.location for slot in observed):
                    raise AppointmentMonitorError("数据源返回了与 Worker 地点不一致的名额")
            except Exception as error:
                self.state = "error"
                self.last_error = f"{type(error).__name__}: {error}"
                return [self._emit(MonitorEvent(
                    event_type="error",
                    monitor_id=self.config.monitor_id,
                    location=self.config.location,
                    created_at=self.last_checked_at,
                    message="预约页面读取失败，等待人工检查",
                    error=self.last_error,
                ))]

            previous_ids = {slot.slot_id for slot in self.last_slots}
            current_ids = {slot.slot_id for slot in observed}
            # A slot that disappears and later reappears should notify again.
            self._notified_slot_ids.intersection_update(current_ids)
            new_slots = [slot for slot in observed if slot.slot_id not in previous_ids]
            candidates = [
                slot for slot in observed
                if self._accepted(slot) and slot.slot_id not in self._notified_slot_ids
            ]
            self.last_slots = observed
            events: List[MonitorEvent] = []
            pending_ids = {slot.slot_id for slot in self.pending_candidates}
            still_pending = [slot for slot in observed if slot.slot_id in pending_ids]
            if candidates:
                self._notified_slot_ids.update(slot.slot_id for slot in candidates)
                self.pending_candidates = tuple(candidates)
                self.state = "awaiting_confirmation"
                events.append(self._emit(MonitorEvent(
                    event_type="new_opportunity",
                    monitor_id=self.config.monitor_id,
                    location=self.config.location,
                    created_at=self.last_checked_at,
                    candidates=tuple(candidates),
                    message="发现符合条件的预约机会，等待人工确认",
                )))
            elif still_pending:
                self.pending_candidates = tuple(still_pending)
                self.state = "awaiting_confirmation"
            else:
                self.pending_candidates = ()
                self.state = "watching"
            if new_slots and not candidates:
                events.append(self._emit(MonitorEvent(
                    event_type="snapshot_changed",
                    monitor_id=self.config.monitor_id,
                    location=self.config.location,
                    created_at=self.last_checked_at,
                    candidates=tuple(new_slots),
                    message="预约页面出现新的可见名额，但不符合当前筛选条件",
                )))
            return events

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return {
                "monitorId": self.config.monitor_id,
                "applicantId": self.config.applicant_id,
                "location": self.config.location,
                "locationLabel": LOCATION_LABELS[self.config.location],
                "state": self.state,
                "lastCheckedAt": self.last_checked_at,
                "lastError": self.last_error,
                "visibleSlots": [slot.as_dict() for slot in self.last_slots],
                "pendingCandidates": [slot.as_dict() for slot in self.pending_candidates],
                "bookingMode": self.config.booking_mode,
                "safety": {
                    "readOnly": True,
                    "credentials": "manual_only",
                    "captcha": "manual_only",
                    "slotSelection": "manual_only",
                    "finalBooking": "manual_only",
                },
            }


class AppointmentMonitorCoordinator:
    """Owns a bounded set of workers and a single serialized polling loop."""

    def __init__(
        self,
        workers: Optional[Iterable[AppointmentMonitorWorker]] = None,
        on_event: Optional[Callable[[MonitorEvent], None]] = None,
    ) -> None:
        self._workers: Dict[str, AppointmentMonitorWorker] = {}
        self._on_event = on_event
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        for worker in workers or ():
            self.add_worker(worker)

    @property
    def workers(self) -> Mapping[str, AppointmentMonitorWorker]:
        with self._lock:
            return dict(self._workers)

    def add_worker(self, worker: AppointmentMonitorWorker) -> None:
        with self._lock:
            monitor_id = worker.config.monitor_id
            if monitor_id in self._workers:
                raise AppointmentMonitorError(f"重复的 monitor_id: {monitor_id}")
            if len(self._workers) >= len(SUPPORTED_LOCATIONS):
                raise AppointmentMonitorError("本地框架最多配置 5 个监控 Worker")
            if self._on_event:
                worker.on_event = self._on_event
            self._workers[monitor_id] = worker

    def remove_worker(self, monitor_id: str) -> None:
        with self._lock:
            self._workers.pop(monitor_id, None)

    def poll_once_all(self, now: Optional[str] = None) -> List[MonitorEvent]:
        """Poll workers sequentially to keep local resource use bounded."""
        with self._lock:
            workers = list(self._workers.values())
        events: List[MonitorEvent] = []
        for worker in workers:
            events.extend(worker.poll_once(now=now))
        return events

    def snapshots(self) -> List[Dict[str, object]]:
        with self._lock:
            return [worker.snapshot() for worker in self._workers.values()]

    def start(self) -> None:
        """Start one serialized local loop; no browsers are created here."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="appointment-monitor-coordinator",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            self.poll_once_all()
            with self._lock:
                intervals = [
                    worker.config.interval_seconds
                    for worker in self._workers.values()
                    if worker.config.enabled
                ]
            wait_seconds = max(1, min(intervals or [MIN_INTERVAL_SECONDS]))
            elapsed = time.monotonic() - started
            self._stop_event.wait(max(0.0, wait_seconds - elapsed))


class MockAppointmentProvider:
    """Deterministic in-memory provider for local tests and demos."""

    def __init__(self, sequences: Optional[Mapping[str, Sequence[Sequence[AppointmentSlot]]]] = None) -> None:
        self._sequences: Dict[str, List[List[AppointmentSlot]]] = {
            key: [list(snapshot) for snapshot in snapshots]
            for key, snapshots in (sequences or {}).items()
        }
        self._last: Dict[str, List[AppointmentSlot]] = {}
        self._lock = threading.RLock()

    def set_sequence(self, monitor_id: str, snapshots: Sequence[Sequence[AppointmentSlot]]) -> None:
        with self._lock:
            self._sequences[monitor_id] = [list(snapshot) for snapshot in snapshots]
            self._last.pop(monitor_id, None)

    def set_slots(self, monitor_id: str, slots: Sequence[AppointmentSlot]) -> None:
        with self._lock:
            self._sequences[monitor_id] = [list(slots)]
            self._last[monitor_id] = list(slots)

    def check(self, config: MonitorConfig) -> Sequence[AppointmentSlot]:
        with self._lock:
            queue = self._sequences.get(config.monitor_id) or []
            if queue:
                current = queue.pop(0)
                self._last[config.monitor_id] = list(current)
                return list(current)
            return list(self._last.get(config.monitor_id, []))


def build_five_location_configs(
    applicants: Sequence[str],
    *,
    interval_seconds: int = 300,
    baseline_dates: Optional[Mapping[str, date]] = None,
) -> List[MonitorConfig]:
    """Build the five local framework slots without starting browsers.

    ``applicants`` must contain one applicant id per location.  This explicit
    mapping prevents accidental cross-applicant data reuse in later adapters.
    """
    if len(applicants) != len(SUPPORTED_LOCATIONS):
        raise AppointmentMonitorError("五地区框架需要恰好 5 个申请人标识")
    baseline_dates = baseline_dates or {}
    return [
        MonitorConfig(
            monitor_id=WORKER_IDS[index],
            applicant_id=str(applicants[index]),
            location=location,
            interval_seconds=interval_seconds,
            baseline_date=baseline_dates.get(location),
        )
        for index, location in enumerate(SUPPORTED_LOCATIONS)
    ]


def demo_payload() -> Dict[str, object]:
    """Run one local simulated cycle and return JSON-serializable output."""
    provider = MockAppointmentProvider()
    configs = build_five_location_configs(
        [f"demo-applicant-{index + 1}" for index in range(5)],
        interval_seconds=MIN_INTERVAL_SECONDS,
        baseline_dates={location: date(2027, 12, 31) for location in SUPPORTED_LOCATIONS},
    )
    for config in configs:
        provider.set_slots(config.monitor_id, [])
    provider.set_slots(
        WORKER_IDS[2],
        [AppointmentSlot("GUANGZHOU", date(2027, 5, 12), "09:30")],
    )
    events: List[MonitorEvent] = []
    coordinator = AppointmentMonitorCoordinator(
        [AppointmentMonitorWorker(config, provider) for config in configs],
        on_event=events.append,
    )
    coordinator.poll_once_all(now="2026-08-27T00:00:00+00:00")
    return {
        "workers": coordinator.snapshots(),
        "events": [event.as_dict() for event in events],
        "safety": {
            "liveBrowser": False,
            "liveProvider": False,
            "finalBooking": "manual_only",
        },
    }


if __name__ == "__main__":
    print(json.dumps(demo_payload(), ensure_ascii=False, indent=2))
