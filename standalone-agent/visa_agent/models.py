"""Provider-neutral domain models used by every Agent stage."""

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .recovery import (
    RecoveryCredentials,
    recovery_credentials_from_primitive,
)


MAX_VISUAL_FAILURE_COUNT = 3
MAX_VISUAL_FAILURE_ENTRIES = 512


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class ExecutionLeaseRevoked(RuntimeError):
    """The durable job has advanced beyond the worker's execution generation."""


class NextDispatchReceiptUnavailable(RuntimeError):
    """A non-idempotent Next click cannot be durably receipted."""


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    SENSITIVE = "sensitive"


class ActionKind(str, Enum):
    NAVIGATE = "navigate"
    TYPE = "type"
    SELECT = "select"
    CLICK = "click"
    PRESS_KEY = "press_key"
    SCROLL = "scroll"
    WAIT = "wait"
    COMPLETE = "complete"
    PAUSE = "pause"


class JobState(str, Enum):
    CREATED = "created"
    PARSING_DOCUMENTS = "parsing_documents"
    EXTRACTING_FIELDS = "extracting_fields"
    VALIDATING = "validating"
    WAITING_REVIEW = "waiting_review"
    READY_FOR_FORM = "ready_for_form"
    FILLING_FORM = "filling_form"
    WAITING_HUMAN = "waiting_human"
    REVIEW_REQUIRED = "review_required"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"

    # Kept as a source-compatible alias for callers of the 0.1 prototype.
    RUNNING = "filling_form"


@dataclass(frozen=True)
class Evidence:
    document_id: str
    filename: str
    page: int
    excerpt: str
    method: str


@dataclass(frozen=True)
class FieldConfirmation:
    confirmed_by: str
    confirmed_at: str
    source: str = "human-review"
    original_value: str = ""
    confirmed_value: str = ""
    reason: str = ""


@dataclass
class ExtractedField:
    id: str
    value: str
    label: str = ""
    confidence: float = 0.0
    risk_level: RiskLevel = RiskLevel.MEDIUM
    confirmed: bool = False
    evidence: List[Evidence] = field(default_factory=list)
    alternatives: List[str] = field(default_factory=list)
    confirmation: Optional[FieldConfirmation] = None

    def confirm(
        self,
        value,
        confirmed_by,
        source="human-review",
        reason="",
        confirmed_at=None,
    ):
        """Record an auditable human decision without losing the extracted value."""
        original_value = self.value
        self.value = str(value)
        self.confirmed = True
        self.confirmation = FieldConfirmation(
            confirmed_by=str(confirmed_by),
            confirmed_at=confirmed_at or now_iso(),
            source=str(source),
            original_value=original_value,
            confirmed_value=self.value,
            reason=str(reason),
        )

    def unconfirm(self):
        self.confirmed = False
        self.confirmation = None


@dataclass
class RecognitionResult:
    document_id: str
    filename: str
    document_type: str
    fields: List[ExtractedField] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    raw_text_available: bool = False
    raw_text: str = ""
    stages: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrowserObservation:
    url: str
    title: str
    visible_text: str
    screenshot_ref: str = ""
    page_id: str = ""
    control_values: Dict[str, str] = field(default_factory=dict)
    # Count of non-hidden controls inside the live document's form.  This is
    # structural evidence only: values and selectors never leave the browser.
    # CEAC can render a "Session Timed Out" document while retaining the old
    # ``/General/complete/`` URL, so route identity alone is not sufficient to
    # prove that a restored tab is still an actionable DS-160 form.
    form_control_count: int = 0
    # Monotonic dynamic-section counts keyed by the reviewed repeater field.
    # They let a restarted worker prove whether an "ensure N records" action
    # finished, partially advanced, or never ran without relying on an
    # in-memory click acknowledgement.
    repeater_counts: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    acknowledged_action_ids: List[str] = field(default_factory=list)
    # A deterministic Next click installs a synchronous browser-side receipt
    # before the click is dispatched.  The scope identifies the exact isolated
    # browser/profile ledger.  Absence is authoritative only when both the
    # observation and the persisted action name the same scope.
    dispatched_action_ids: List[str] = field(default_factory=list)
    dispatch_receipt_scope: str = ""
    dispatch_receipts_authoritative: bool = False
    dispatch_receipt_conflict: bool = False
    # Exact document scroll geometry lets deterministic verification reject a
    # model scroll that hit the page edge. Without this, an acknowledged
    # wheel event looked like progress even when the viewport never moved,
    # allowing Gemini to spend minutes repeating the same no-op action.
    scroll_x: int = 0
    scroll_y: int = 0
    scroll_height: int = 0
    viewport_height: int = 0


def observation_fingerprint(job, observation):
    """Hash a resume boundary without storing plaintext control values."""
    job_id = str(getattr(job, "id", "") or "")
    payload = {
        "job": job_id,
        "page": str(observation.page_id or ""),
        "url": str(observation.url or ""),
        "title": str(observation.title or ""),
        # Page overlays and CAPTCHA/manual boundaries can disappear without
        # changing the URL, title, errors, or form controls.  Include only a
        # job-scoped digest so the watcher sees that transition without ever
        # persisting visible page text.
        "visibleTextDigest": hashlib.sha256(
            (
                job_id
                + "\0"
                + str(getattr(observation, "visible_text", "") or "")
            ).encode("utf-8")
        ).hexdigest(),
        "errors": sorted(str(item) for item in observation.errors or ()),
        "dispatchScope": str(observation.dispatch_receipt_scope or ""),
        "dispatchConflict": bool(observation.dispatch_receipt_conflict),
        "dispatchedActions": sorted(
            str(item) for item in observation.dispatched_action_ids or ()
        ),
        "controls": sorted(
            (
                str(field_id),
                hashlib.sha256(
                    (job_id + "\0" + str(value)).encode("utf-8")
                ).hexdigest(),
            )
            for field_id, value in dict(
                observation.control_values or {}
            ).items()
        ),
        "formControlCount": max(
            0,
            int(getattr(observation, "form_control_count", 0) or 0),
        ),
        "scroll": (
            max(0, int(getattr(observation, "scroll_x", 0) or 0)),
            max(0, int(getattr(observation, "scroll_y", 0) or 0)),
            max(0, int(getattr(observation, "scroll_height", 0) or 0)),
            max(0, int(getattr(observation, "viewport_height", 0) or 0)),
        ),
        "repeaters": sorted(
            (
                str(field_id),
                max(0, int(count or 0)),
            )
            for field_id, count in dict(
                observation.repeater_counts or {}
            ).items()
        ),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ComputerAction:
    kind: ActionKind
    field_id: str = ""
    target_hint: str = ""
    value: str = ""
    reason: str = ""
    coordinate_x: Optional[int] = None
    coordinate_y: Optional[int] = None
    scroll_direction: str = ""
    scroll_amount: int = 0
    # System-owned dispatch protocol metadata.  Model output never controls
    # these fields.  They distinguish a prepared checkpoint from a Next click
    # that actually reached the page's click event.
    dispatch_receipt_required: bool = False
    dispatch_receipt_scope: str = ""
    # Assigned by the workflow, never by the visual model.  Persisted pending
    # actions retain their original generation across recovery, while newly
    # planned actions can never collide with an older worker's identifiers.
    execution_generation: int = 0
    id: str = field(default_factory=lambda: f"action-{uuid4().hex}")


@dataclass
class AgentEvent:
    kind: str
    message: str
    created_at: str = field(default_factory=now_iso)
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentJob:
    fields: List[ExtractedField]
    start_url: str
    id: str = field(default_factory=lambda: f"agent-job-{uuid4().hex}")
    auto_next: bool = True
    state: JobState = JobState.CREATED
    completed_field_ids: List[str] = field(default_factory=list)
    # Confirmed fields can be conditionally absent from the live CEAC DOM
    # after reviewed branch answers are applied. They are not falsely marked
    # complete; instead the browser reclassifies this list on every observation
    # and terminal completeness excludes only fields still proven inapplicable.
    inapplicable_field_ids: List[str] = field(default_factory=list)
    # Durable provenance for each deterministically verified value.  Sync
    # reconciliation must use the page that actually owned the control, not
    # reverse-engineer ownership from overlapping legacy/dynamic allowlists.
    completed_field_page_plan_by_id: Dict[str, str] = field(
        default_factory=dict
    )
    # The live CEAC control can declare a stricter maxlength than DocFlow's
    # source field.  Store the deterministic, control-compatible value
    # separately so a restart or stale-value audit never tries to restore the
    # longer source value and enter an endless truncate/verify loop.
    control_normalized_values: Dict[str, str] = field(default_factory=dict)
    required_field_ids: List[str] = field(default_factory=list)
    applied_action_ids: List[str] = field(default_factory=list)
    pending_action: Optional[ComputerAction] = None
    events: List[AgentEvent] = field(default_factory=list)
    human_checkpoint: Optional[str] = None
    final_submission_boundary_reached: bool = False
    # Review/Sign keeps the exact browser/profile alive only for a bounded
    # human-review window.  This durable deadline lets service restart GC
    # finish teardown even when the backend process that observed Review died
    # before it could cancel the provider.
    review_lease_expires_at: str = ""
    current_page_plan_id: str = ""
    visited_page_plan_ids: List[str] = field(default_factory=list)
    page_plan_version: str = ""
    provider_versions: Dict[str, str] = field(default_factory=dict)
    validation_errors: List[str] = field(default_factory=list)
    step_count: int = 0
    action_index: int = 0
    # Once the consultant starts Gemini, this durable intent survives an
    # Agent/service restart.  A recovered browser can therefore resume as soon
    # as the existing CEAC application is visible, without requiring a second
    # click in DocFlow.
    continuous_run_requested: bool = False
    # Authentication-like DS-160 retrieval data is deliberately isolated
    # from ordinary questionnaire fields.  It is written only to the
    # encrypted provider checkpoint and never sent to the visual model.  A
    # profile is present only after the whole snapshot was explicitly
    # approved; partial values are never derived or guessed by the Agent.
    recovery_credentials: Optional[RecoveryCredentials] = None
    # Durable retrieval-stage telemetry contains no credential values.  It
    # lets service/watcher recovery distinguish a CAPTCHA/manual boundary
    # from the automatic Retrieve-existing-application state machine.
    recovery_stage: str = ""
    recovery_transition_count: int = 0
    execution_generation: int = 0
    # Durable classification for WAITING_HUMAN.  It prevents process restart
    # recovery from erasing CAPTCHA/manual-boundary reasons or treating every
    # wait as an orphaned runtime.
    wait_kind: str = ""
    # A field sync is an observable resume trigger even when the CEAC DOM has
    # not changed.  This flag survives an Agent restart and is consumed only
    # when a new execution generation is actually started.
    sync_resume_pending: bool = False
    # If a sync changes a value already verified on page A while a persisted
    # Next may already have moved to page B, the new value must not be claimed
    # as applied.  These fields retain that consistency boundary until page A
    # can be safely refilled or the mismatch is surfaced explicitly.
    sync_reconciliation_field_ids: List[str] = field(default_factory=list)
    sync_reconciliation_page_plan_id: str = ""
    # Field-level targets make reconciliation valid across more than one
    # already-visited page.  The legacy single-page member remains readable
    # for old encrypted checkpoints and is used as a migration fallback.
    sync_reconciliation_page_plan_by_field: Dict[str, str] = field(
        default_factory=dict
    )
    binding_refresh_field_ids: List[str] = field(default_factory=list)
    # Durable visual repair budget, keyed by ``pagePlanId::fieldId``.  A
    # workflow invocation is deliberately short-lived (the service watcher
    # starts a new one after every automatic backoff), so an in-memory retry
    # counter cannot prevent the same bad screenshot binding from invoking the
    # model forever.  Counts survive checkpoint/service recovery and are
    # cleared only after that exact field passes deterministic verification.
    visual_failure_counts: Dict[str, int] = field(default_factory=dict)
    last_safe_url: str = ""
    # SHA-256 of the last observed page identity, validation markers, and
    # control state.  It contains no plaintext field values, but lets the
    # auto-resume watcher detect a page/control change that happened before
    # its thread took the first live sample.
    wait_boundary_fingerprint: str = ""
    # A transient provider outage is not a human checkpoint.  These durable
    # fields let the service resume the same one-click run after a bounded
    # backoff, including after an Agent process restart, without requiring a
    # page change or a second click from the consultant.
    automatic_retry_pending: bool = False
    automatic_retry_after: str = ""
    automatic_retry_count: int = 0
    # ``provider`` retries keep the current browser runtime and repeat only
    # the side-effect-free model planning call. ``browser`` retries retire the
    # current Playwright worker, preserve its private profile and reconstruct
    # the runtime before resolving any persisted pending action from live DOM.
    automatic_retry_kind: str = ""
    # Browser reconstruction can occur while the job is already waiting for
    # an actual human/page transition.  This explicit bit prevents a due retry
    # from being mistaken for permission to cross that older boundary.
    automatic_retry_preserves_page_boundary: bool = False
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def confirmed_field_map(self):
        confirmed = {
            item.id: item for item in self.fields if item.confirmed
        }
        for field_id, value in dict(
            self.control_normalized_values or {}
        ).items():
            if field_id in confirmed:
                confirmed[field_id] = replace(
                    confirmed[field_id],
                    value=str(value),
                )
        return confirmed

    def record(self, kind, message, **detail):
        self.events.append(AgentEvent(kind=kind, message=message, detail=detail))
        self.updated_at = now_iso()


def to_primitive(value):
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    return value


def _enum(enum_type, value, default):
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return default


def evidence_from_primitive(payload):
    return Evidence(
        document_id=str(payload.get("document_id") or ""),
        filename=str(payload.get("filename") or ""),
        page=int(payload.get("page") or 1),
        excerpt=str(payload.get("excerpt") or ""),
        method=str(payload.get("method") or ""),
    )


def extracted_field_from_primitive(payload):
    confirmation_payload = payload.get("confirmation")
    confirmation = None
    if isinstance(confirmation_payload, dict):
        confirmation = FieldConfirmation(
            confirmed_by=str(confirmation_payload.get("confirmed_by") or ""),
            confirmed_at=str(confirmation_payload.get("confirmed_at") or ""),
            source=str(confirmation_payload.get("source") or "human-review"),
            original_value=str(confirmation_payload.get("original_value") or ""),
            confirmed_value=str(confirmation_payload.get("confirmed_value") or ""),
            reason=str(confirmation_payload.get("reason") or ""),
        )
    return ExtractedField(
        id=str(payload.get("id") or ""),
        value=str(payload.get("value") or ""),
        label=str(payload.get("label") or ""),
        confidence=float(payload.get("confidence") or 0.0),
        risk_level=_enum(
            RiskLevel, payload.get("risk_level"), RiskLevel.MEDIUM
        ),
        confirmed=bool(payload.get("confirmed")),
        evidence=[
            evidence_from_primitive(item)
            for item in payload.get("evidence") or []
            if isinstance(item, dict)
        ],
        alternatives=[
            str(item) for item in payload.get("alternatives") or []
        ],
        confirmation=confirmation,
    )


def action_from_primitive(payload):
    if not isinstance(payload, dict):
        return None
    return ComputerAction(
        kind=_enum(ActionKind, payload.get("kind"), ActionKind.PAUSE),
        field_id=str(payload.get("field_id") or ""),
        target_hint=str(payload.get("target_hint") or ""),
        value=str(payload.get("value") or ""),
        reason=str(payload.get("reason") or ""),
        coordinate_x=_optional_int(payload.get("coordinate_x")),
        coordinate_y=_optional_int(payload.get("coordinate_y")),
        scroll_direction=str(payload.get("scroll_direction") or ""),
        scroll_amount=int(payload.get("scroll_amount") or 0),
        dispatch_receipt_required=bool(
            payload.get("dispatch_receipt_required")
        ),
        dispatch_receipt_scope=str(
            payload.get("dispatch_receipt_scope") or ""
        ),
        execution_generation=max(
            0, int(payload.get("execution_generation") or 0)
        ),
        id=str(payload.get("id") or f"action-{uuid4().hex}"),
    )


def _optional_int(value):
    if value is None or value == "":
        return None
    return int(value)


def _visual_failure_counts_from_primitive(payload):
    """Bound and validate an untrusted/corrupt checkpoint repair map."""
    if not isinstance(payload, dict):
        return {}
    result = {}
    for raw_key, raw_count in payload.items():
        if len(result) >= MAX_VISUAL_FAILURE_ENTRIES:
            break
        key = str(raw_key or "").strip()
        # Both identities are required and the separator is unambiguous.  IDs
        # are generated internally, but a damaged encrypted checkpoint must not
        # be able to create an unbounded or control-character-bearing map.
        if (
            len(key) > 512
            or key.count("::") != 1
            or any(ord(character) < 32 for character in key)
        ):
            continue
        page_plan_id, field_id = key.split("::", 1)
        if not page_plan_id.strip() or not field_id.strip():
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError, OverflowError):
            continue
        if count <= 0:
            continue
        result[key] = min(MAX_VISUAL_FAILURE_COUNT, count)
    return result


def job_from_primitive(payload):
    """Load both current checkpoints and 0.1 prototype checkpoints."""
    events = []
    for item in payload.get("events") or []:
        if not isinstance(item, dict):
            continue
        events.append(AgentEvent(
            kind=str(item.get("kind") or ""),
            message=str(item.get("message") or ""),
            created_at=str(item.get("created_at") or now_iso()),
            detail=dict(item.get("detail") or {}),
        ))
    raw_state = payload.get("state")
    if raw_state == "running":
        raw_state = JobState.FILLING_FORM.value
    state = _enum(JobState, raw_state, JobState.CREATED)
    automatic_retry_pending = bool(
        payload.get("automatic_retry_pending")
    )
    wait_kind = str(payload.get("wait_kind") or "").strip()
    if not wait_kind and state == JobState.WAITING_HUMAN:
        # Compatibility migration for checkpoints written before wait_kind was
        # introduced.  A durable continuous wait is conservatively treated as
        # a real page-change boundary; it must never be cleared merely because
        # the Agent process restarted.
        if automatic_retry_pending:
            wait_kind = "automatic_retry"
        elif bool(payload.get("continuous_run_requested")):
            wait_kind = (
                "manual_page_change"
                if (
                    payload.get("wait_boundary_fingerprint")
                    or payload.get("last_safe_url")
                )
                else "runtime_recovery"
            )
        else:
            wait_kind = "manual_hard_boundary"
    elif not wait_kind and state == JobState.FILLING_FORM:
        wait_kind = "runtime_recovery"
    # Early one-click builds wrote the manual CEAC retrieval checkpoint before
    # ``wait_kind`` existed.  Once a newer build loaded and saved that shape,
    # the conservative fallback above could become a persisted
    # ``manual_hard_boundary`` even though no consistency failure had ever
    # occurred.  That made the same reviewed job impossible to start after an
    # Agent/browser restart.  Recover only the provenance-specific manual
    # entry checkpoint; every real hard boundary records ``human_checkpoint``
    # (and often a more specific conflict/review event) and remains untouched.
    checkpoint_text = str(payload.get("human_checkpoint") or "").casefold()
    event_kinds = {
        str(getattr(event, "kind", "") or "")
        for event in events
    }
    legacy_manual_entry = bool(
        state == JobState.WAITING_HUMAN
        and wait_kind == "manual_hard_boundary"
        and (
            "manually retrieve the already-created ds-160 application"
            in checkpoint_text
            or (
                "人工恢复" in checkpoint_text
                and "正式表格" in checkpoint_text
            )
        )
        and "browser_opened_for_manual_entry" in event_kinds
        and not event_kinds.intersection({
            "human_checkpoint",
            "dispatch_receipt_conflict",
            "review_incomplete",
            "review_required",
            "blocked",
            "failed",
        })
    )
    if legacy_manual_entry:
        wait_kind = "manual_page_change"

    # Builds which predated typed control-preflight failures collapsed two
    # materially different conditions into the same RuntimeError:
    #
    # * a transiently detached/missing semantic DOM binding; and
    # * a genuine control-value constraint which cannot accept any text.
    #
    # Both were persisted as a hard human boundary even though the first case
    # happened before any DOM mutation and is safe to re-plan.  The old event
    # schema did not retain enough detail to distinguish the two after a
    # restart, so recover exactly one read-only re-plan for only the known old
    # deterministic-DOM event tail.  Newer typed preflight code will either
    # rebind the control or reassert a genuine value constraint before browser
    # mutation.  The durable marker makes that retry one-shot: a real hard
    # boundary produced by the new attempt can never be reopened repeatedly.
    legacy_constraint_checkpoint = (
        "网页控件声明的文本约束无法容纳当前值；系统在写入前"
        "已停止该动作，未产生网页截断或重复填写。"
    )
    migration_marker = "legacy_constraint_boundary_reclassified"
    legacy_constraint_recovery = False
    legacy_constraint_field_id = ""
    legacy_constraint_page_plan_id = ""
    current_generation = max(
        0, int(payload.get("execution_generation") or 0)
    )
    if (
        state == JobState.WAITING_HUMAN
        and wait_kind == "manual_hard_boundary"
        and not bool(payload.get("continuous_run_requested"))
        and not bool(payload.get("final_submission_boundary_reached"))
        and (
            payload.get("pending_action") is None
            or payload.get("pending_action") == ""
        )
        and str(payload.get("human_checkpoint") or "")
        == legacy_constraint_checkpoint
        and migration_marker not in event_kinds
        and len(events) >= 4
    ):
        plan_event = events[-3]
        constraint_event = events[-2]
        checkpoint_event = events[-1]
        legacy_constraint_field_id = str(
            constraint_event.detail.get("fieldId") or ""
        ).strip()
        legacy_constraint_page_plan_id = str(
            constraint_event.detail.get("pagePlanId") or ""
        ).strip()
        current_page_plan_id = str(
            payload.get("current_page_plan_id") or ""
        ).strip()
        latest_arm = next((
            event
            for event in reversed(events[:-3])
            if event.kind == "continuous_run_armed"
        ), None)
        approved_field_ids = {
            str(item.get("id") or "")
            for item in payload.get("fields") or ()
            if isinstance(item, dict) and bool(item.get("confirmed"))
        }
        legacy_constraint_recovery = bool(
            plan_event.kind == "plan_proposed"
            and str(plan_event.detail.get("source") or "") in {
                "deterministic-dom",
                "deterministic-dom-visual",
            }
            and constraint_event.kind
            == "control_constraint_unavailable"
            and constraint_event.message == (
                "The live CEAC control rejected its approved text "
                "contract before any DOM mutation"
            )
            and str(
                constraint_event.detail.get("errorType") or ""
            ) == "RuntimeError"
            and bool(legacy_constraint_field_id)
            and legacy_constraint_field_id in approved_field_ids
            and bool(legacy_constraint_page_plan_id)
            and (
                not current_page_plan_id
                or legacy_constraint_page_plan_id == current_page_plan_id
            )
            and checkpoint_event.kind == "human_checkpoint"
            and checkpoint_event.message == legacy_constraint_checkpoint
            and latest_arm is not None
            and max(
                0,
                int(latest_arm.detail.get("generation") or 0),
            ) == current_generation
        )
    continuous_run_requested = bool(
        payload.get("continuous_run_requested")
    )
    human_checkpoint = payload.get("human_checkpoint")
    binding_refresh_field_ids = [
        str(item)
        for item in payload.get("binding_refresh_field_ids") or []
    ]
    if legacy_constraint_recovery:
        state = JobState.READY_FOR_FORM
        wait_kind = "runtime_recovery"
        continuous_run_requested = True
        human_checkpoint = None
        binding_refresh_field_ids = list(dict.fromkeys((
            *binding_refresh_field_ids,
            legacy_constraint_field_id,
        )))
        events.append(AgentEvent(
            kind=migration_marker,
            message=(
                "A legacy pre-mutation control-preflight boundary was "
                "reclassified for one automatic semantic rebind attempt"
            ),
            detail={
                "fieldId": legacy_constraint_field_id,
                "pagePlanId": legacy_constraint_page_plan_id,
                "legacyErrorType": "RuntimeError",
                "generation": current_generation,
                "retryScope": "read_only_preflight",
            },
        ))
    return AgentJob(
        fields=[
            extracted_field_from_primitive(item)
            for item in payload.get("fields") or []
            if isinstance(item, dict)
        ],
        start_url=str(payload.get("start_url") or ""),
        id=str(payload.get("id") or f"agent-job-{uuid4().hex}"),
        auto_next=bool(payload.get("auto_next", True)),
        state=state,
        completed_field_ids=[
            str(item) for item in payload.get("completed_field_ids") or []
        ],
        inapplicable_field_ids=[
            str(item)
            for item in payload.get("inapplicable_field_ids") or []
        ],
        completed_field_page_plan_by_id={
            str(field_id): str(page_plan_id or "")
            for field_id, page_plan_id in dict(
                payload.get("completed_field_page_plan_by_id") or {}
            ).items()
            if str(field_id)
        },
        control_normalized_values={
            str(field_id): str(value)
            for field_id, value in dict(
                payload.get("control_normalized_values") or {}
            ).items()
            if str(field_id)
        },
        required_field_ids=[
            str(item) for item in payload.get("required_field_ids") or []
        ],
        applied_action_ids=[
            str(item) for item in payload.get("applied_action_ids") or []
        ],
        pending_action=action_from_primitive(payload.get("pending_action")),
        events=events,
        human_checkpoint=human_checkpoint,
        final_submission_boundary_reached=bool(
            payload.get("final_submission_boundary_reached")
        ),
        review_lease_expires_at=str(
            payload.get("review_lease_expires_at") or ""
        ),
        current_page_plan_id=str(payload.get("current_page_plan_id") or ""),
        visited_page_plan_ids=[
            str(item)
            for item in payload.get("visited_page_plan_ids") or []
            if str(item)
        ],
        page_plan_version=str(payload.get("page_plan_version") or ""),
        provider_versions=dict(payload.get("provider_versions") or {}),
        validation_errors=[
            str(item) for item in payload.get("validation_errors") or []
        ],
        step_count=int(payload.get("step_count") or 0),
        action_index=int(payload.get("action_index") or 0),
        continuous_run_requested=continuous_run_requested,
        recovery_credentials=recovery_credentials_from_primitive(
            payload.get("recovery_credentials"),
            require_approval=True,
        ),
        recovery_stage=str(payload.get("recovery_stage") or ""),
        recovery_transition_count=max(
            0, min(20, int(payload.get("recovery_transition_count") or 0))
        ),
        execution_generation=int(
            payload.get("execution_generation") or 0
        ),
        wait_kind=wait_kind,
        sync_resume_pending=bool(
            payload.get("sync_resume_pending")
        ),
        sync_reconciliation_field_ids=[
            str(item)
            for item in payload.get("sync_reconciliation_field_ids") or []
        ],
        sync_reconciliation_page_plan_id=str(
            payload.get("sync_reconciliation_page_plan_id") or ""
        ),
        sync_reconciliation_page_plan_by_field={
            str(field_id): str(page_plan_id or "")
            for field_id, page_plan_id in dict(
                payload.get("sync_reconciliation_page_plan_by_field") or {}
            ).items()
            if str(field_id)
        },
        binding_refresh_field_ids=binding_refresh_field_ids,
        visual_failure_counts=_visual_failure_counts_from_primitive(
            payload.get("visual_failure_counts")
        ),
        last_safe_url=str(payload.get("last_safe_url") or ""),
        wait_boundary_fingerprint=str(
            payload.get("wait_boundary_fingerprint") or ""
        ),
        automatic_retry_pending=automatic_retry_pending,
        automatic_retry_after=str(
            payload.get("automatic_retry_after") or ""
        ),
        automatic_retry_count=max(
            0, int(payload.get("automatic_retry_count") or 0)
        ),
        automatic_retry_kind=str(
            payload.get("automatic_retry_kind") or ""
        ),
        automatic_retry_preserves_page_boundary=bool(
            payload.get("automatic_retry_preserves_page_boundary")
        ),
        created_at=str(payload.get("created_at") or now_iso()),
        updated_at=str(payload.get("updated_at") or now_iso()),
    )
