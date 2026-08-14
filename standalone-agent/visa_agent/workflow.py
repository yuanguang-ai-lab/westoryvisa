"""Recoverable computer-use loop with system-owned permissions and verification."""

import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

from .adapters import (
    ControlBindingUnavailable,
    ControlValueConstraintError,
    ProviderRequestError,
)
from .models import (
    ActionKind,
    ComputerAction,
    ExecutionLeaseRevoked,
    JobState,
    MAX_VISUAL_FAILURE_COUNT,
    NextDispatchReceiptUnavailable,
    observation_fingerprint,
)
from .page_plans import PagePlanRegistry
from .providers import ProviderNotConfigured
from .safety import VisaFormSafetyPolicy
from .verification import DeterministicActionVerifier, VerificationResult


class ComputerUseAgent:
    VISUAL_FIELD_FAILURE_LIMIT = MAX_VISUAL_FAILURE_COUNT

    def __init__(
        self,
        model,
        browser,
        policy=None,
        checkpoint_store=None,
        max_steps=400,
        page_plans=None,
        verifier=None,
        use_model_verification=True,
        action_reviewer=None,
        execution_mode="hybrid",
        cancellation_check=None,
        side_effect_executor=None,
    ):
        self.model = model
        self.browser = browser
        self.policy = policy or VisaFormSafetyPolicy()
        self.checkpoint_store = checkpoint_store
        self.max_steps = max_steps
        self.page_plans = page_plans or PagePlanRegistry.default()
        self.verifier = verifier or DeterministicActionVerifier()
        self.use_model_verification = use_model_verification
        self.action_reviewer = action_reviewer
        self.execution_mode = str(execution_mode or "hybrid").strip().lower()
        self.cancellation_check = (
            cancellation_check if callable(cancellation_check) else None
        )
        self.side_effect_executor = (
            side_effect_executor
            if callable(side_effect_executor)
            else None
        )

    def run(self, job):
        if job.state in {JobState.COMPLETED, JobState.CANCELLED}:
            return job
        fields = job.confirmed_field_map()
        if not fields:
            return self._block(job, "No human-confirmed fields are available")
        if job.validation_errors:
            return self._review_required(
                job, "Job has unresolved validation errors"
            )
        job.state = JobState.FILLING_FORM
        job.human_checkpoint = None
        job.wait_kind = ""
        job.sync_resume_pending = False
        job.page_plan_version = self.page_plans.version
        job.record("started", "Computer-use job started", fieldCount=len(fields))
        self._save(job)
        if self._cancel_requested():
            return self._cancel(job)
        refresh_bindings = list(dict.fromkeys(
            str(item)
            for item in job.binding_refresh_field_ids or ()
            if str(item)
        ))
        for field_id in refresh_bindings:
            self._invalidate_browser_field(field_id)
        if refresh_bindings:
            job.binding_refresh_field_ids = []
            job.record(
                "synchronized_bindings_refreshed",
                "Cached DOM bindings affected by synchronized field semantics "
                "were invalidated before planning",
                fieldIds=refresh_bindings,
            )
            self._save(job)
            if self._cancel_requested():
                return self._cancel(job)
        run_step_count = 0
        navigation_count = 0
        revalidation_repairs = {}
        execution_replans = {}
        constraint_binding_replans = {}
        force_model_repair_fields = set()
        last_page_identity = ""
        page_completed_high_water = -1
        no_progress_cycles = 0

        while run_step_count < self.max_steps:
            if self._cancel_requested():
                return self._cancel(job)
            self._visual_status(
                "observing",
                "正在读取当前页面",
            )
            observation = self._observe_with_retries(
                job,
                purpose="page",
                attempts=3,
            )
            if observation is None:
                if self._browser_retry_is_pending(job):
                    return job
                return self._fail(
                    job,
                    "Browser observation failed after automatic retries",
                )
            terminal_reason = self.page_plans.terminal_reason(observation)
            if terminal_reason:
                # If the process restarted immediately after clicking the last
                # safe Next button, reaching Review/Sign is authoritative
                # evidence that the pending navigation succeeded.  Clear that
                # pending action so a later status refresh can never repeat it.
                pending = job.pending_action
                if pending is not None:
                    if self._is_next_action(pending):
                        self._mark_applied(job, pending)
                        job.record(
                            "page_navigation_recovered",
                            "Pending Next action reached the terminal review boundary",
                            actionId=pending.id,
                            toUrl=observation.url,
                        )
                    else:
                        job.pending_action = None
                        job.record(
                            "terminal_pending_action_discarded",
                            "A non-navigation pending action was closed at the "
                            "authoritative Review/Sign boundary without being "
                            "claimed as applied",
                            actionId=pending.id,
                        )
                if job.sync_reconciliation_field_ids:
                    terminal_reason = (
                        f"{terminal_reason}；同步资料中有上一页字段在页面前进后"
                        "才发生变化，系统未将这些新值伪报为已填写。"
                    )
                job.sync_resume_pending = False
                job.sync_reconciliation_field_ids = []
                job.sync_reconciliation_page_plan_id = ""
                job.sync_reconciliation_page_plan_by_field = {}
                job.final_submission_boundary_reached = True
                missing_required = self._missing_required_field_ids(job)
                if missing_required:
                    return self._review_incomplete(
                        job,
                        (
                            f"{terminal_reason}；系统在最终核对边界发现仍有 "
                            f"{len(missing_required)} 个必填字段未完成："
                            + ", ".join(missing_required)
                        ),
                        missing_required,
                    )
                return self._review_required(job, terminal_reason)
            page_decision = self.policy.inspect_page(observation)
            if not page_decision.allowed:
                return self._wait_human(job, page_decision.reason)
            page_plan = self.page_plans.match(observation)
            if page_plan is None:
                return self._wait_human(
                    job, "No approved page plan matches the current CEAC page"
                )
            if page_plan.id not in job.visited_page_plan_ids:
                job.visited_page_plan_ids.append(page_plan.id)
                job.record(
                    "page_plan_visited",
                    "The live browser entered a code-owned DS-160 page plan",
                    pagePlanId=page_plan.id,
                )
            page_identity = (
                str(observation.page_id or "").strip()
                or str(observation.url or "").strip()
                or page_plan.id
            )
            completed_now = len(job.completed_field_ids)
            if page_identity != last_page_identity:
                last_page_identity = page_identity
                page_completed_high_water = completed_now
                no_progress_cycles = 0
            elif completed_now > page_completed_high_water:
                page_completed_high_water = completed_now
                no_progress_cycles = 0
            else:
                no_progress_cycles += 1
            if no_progress_cycles >= 12:
                job.record(
                    (
                        "no_progress_loop_yielded"
                        if job.continuous_run_requested
                        else "no_progress_loop_stopped"
                    ),
                    "The page loop made no new verified-field or navigation "
                    "progress for twelve observations",
                    pagePlanId=page_plan.id,
                    completedFields=completed_now,
                )
                if job.continuous_run_requested:
                    return self._schedule_progress_retry(
                        job,
                        observation,
                        kind="progress_stall",
                        message=(
                            "当前页面连续 12 轮没有新增已验证字段或页面切换；"
                            "系统已暂停空转并将在退避后自动重新观测，无需再次点击。"
                        ),
                        event_kind="no_progress_retry_scheduled",
                        base_delay=3,
                    )
                return self._wait_human(
                    job,
                    "当前页面连续 12 轮没有新增已验证字段或页面切换；"
                    "Gemini 已停止空转，请检查网页是否有未显示的校验提示。",
                )

            pending_result = self._resolve_pending(
                job,
                observation,
                current_page_plan_id=page_plan.id,
            )
            if pending_result is not None:
                if pending_result:
                    self._clear_progress_retry(job)
                    job.current_page_plan_id = page_plan.id
                    job.last_safe_url = str(
                        observation.url or job.last_safe_url
                    )
                    self._save(job)
                    continue
                if (
                    job.state == JobState.WAITING_HUMAN
                    and job.wait_kind == "manual_hard_boundary"
                ):
                    # Pending repeater recovery can prove that a previously
                    # dispatched Add Another never increased the live row
                    # count.  Once its durable budget is exhausted,
                    # _resolve_pending has already established the strongest
                    # possible boundary.  Do not overwrite that checkpoint
                    # with the generic uncertain-action message below.
                    return job
                # A real route transition is stronger evidence than either
                # process-local dispatch ledger.  _resolve_pending gets the
                # first opportunity to recover that transition; only a job
                # still on the source page may stop on divergent ledgers.
                if (
                    self._is_next_action(job.pending_action)
                    and observation.dispatch_receipt_conflict
                ):
                    return self._wait_for_dispatch_receipt_consistency(
                        job,
                        job.pending_action,
                    )
                if (
                    self._is_next_action(job.pending_action)
                    and observation.errors
                ):
                    pending = job.pending_action
                    job.pending_action = None
                    job.record(
                        "page_navigation_failed",
                        "A persisted Next action retained the page with "
                        "visible CEAC validation errors",
                        actionId=pending.id,
                        fromPagePlanId=job.current_page_plan_id,
                        errorCount=len(observation.errors),
                    )
                    return self._wait_human(
                        job,
                        "Next 已执行，但网页显示字段校验错误；Gemini 已保留"
                        "进度并停止重复点击，请处理网页明确标出的错误。",
                        wait_kind="manual_hard_boundary",
                    )
                if (
                    self._is_next_action(job.pending_action)
                    and job.continuous_run_requested
                ):
                    return self._schedule_progress_retry(
                        job,
                        observation,
                        kind="navigation_observation",
                        message=(
                            "Next 已执行，但 CEAC 尚未给出可验证的页面切换结果；"
                            "系统只会继续观测，不会再次点击 Next。"
                        ),
                        event_kind="page_navigation_retry_scheduled",
                        base_delay=2,
                    )
                if self._browser_retry_is_pending(job):
                    # ``_resolve_pending`` may have exhausted an action-scoped
                    # observation and scheduled reconstruction of the private
                    # browser.  Do not overwrite that stronger automatic retry
                    # with a generic human checkpoint.
                    return job
                return self._wait_human(
                    job,
                    "Pending action outcome is uncertain; it will not be repeated automatically",
                )
            reconciliation, reconciliation_wait_kind = (
                self._sync_reconciliation_boundary(
                job,
                page_plan.id,
                )
            )
            if reconciliation:
                return self._wait_human(
                    job,
                    reconciliation,
                    wait_kind=reconciliation_wait_kind,
                )
            job.current_page_plan_id = page_plan.id
            job.last_safe_url = str(observation.url or job.last_safe_url)

            page_field_ids = sorted(
                field_id
                for field_id in fields
                if page_plan.allows_field(field_id)
            )
            field_labels = dict(page_plan.field_labels)
            control_hints = {
                field_id: tuple(hints or ())
                for field_id, hints in dict(
                    page_plan.control_hints or {}
                ).items()
            }
            for field_id in page_field_ids:
                raw_approved_label = str(fields[field_id].label or "").strip()
                approved_label = raw_approved_label.split(
                    "[control=", 1
                )[0].strip()
                if not approved_label:
                    continue
                existing = tuple(field_labels.get(field_id) or ())
                if approved_label not in existing:
                    field_labels[field_id] = (*existing, approved_label)
                    existing = field_labels[field_id]
                # Keep the system-owned control descriptor available to the
                # deterministic browser planner.  It distinguishes text
                # inputs from radios/checkboxes without exposing value choice
                # to Gemini or relying on screenshots.
                if (
                    "[control=" in raw_approved_label
                    and raw_approved_label not in existing
                ):
                    field_labels[field_id] = (
                        *existing,
                        raw_approved_label,
                    )
                for label_term in self._descriptor_terms(
                    raw_approved_label, "label_terms"
                ):
                    existing = tuple(field_labels.get(field_id) or ())
                    if label_term not in existing:
                        field_labels[field_id] = (*existing, label_term)
                descriptor_hints = self._descriptor_terms(
                    raw_approved_label, "control_hints"
                )
                if descriptor_hints:
                    existing_hints = tuple(
                        control_hints.get(field_id) or ()
                    )
                    control_hints[field_id] = tuple(dict.fromkeys(
                        (*existing_hints, *descriptor_hints)
                    ))
            visual_loop = self.execution_mode in {
                "visual", "native-visual", "codex-like"
            }
            # A verified branch controller can be reset by a later ASP.NET
            # partial postback while its dependent fields are still pending.
            # Waiting until *all* pending fields disappear before auditing
            # completed values creates a deadlock: the reset controller hides
            # those dependants, so the normal final-page audit is never reached.
            # V2 supplies a narrow, read-only controller audit for this exact
            # case; V1 and browsers without the hook retain their old behavior.
            audit_branch_controllers = getattr(
                self,
                "_stale_completed_branch_controller_fields",
                None,
            )
            if callable(audit_branch_controllers):
                try:
                    stale_controllers = list(dict.fromkeys(
                        str(field_id)
                        for field_id in audit_branch_controllers(
                            job,
                            fields,
                            page_field_ids,
                            field_labels,
                            control_hints,
                        ) or ()
                        if str(field_id) in set(page_field_ids)
                        and str(field_id) in set(job.completed_field_ids)
                    ))
                except Exception as error:
                    stale_controllers = []
                    job.record(
                        "branch_controller_audit_unavailable",
                        "A completed branch controller could not be audited; "
                        "its verified state was retained",
                        pagePlanId=page_plan.id,
                        errorType=type(error).__name__,
                    )
                if stale_controllers:
                    repair_limit = max(1, int(getattr(
                        self,
                        "BRANCH_CONTROLLER_REOPEN_LIMIT",
                        2,
                    ) or 2))
                    exhausted_controllers = []
                    for field_id in stale_controllers:
                        field_repair_limit = repair_limit
                        limit_for_field = getattr(
                            self,
                            "_branch_controller_repair_limit",
                            None,
                        )
                        if callable(limit_for_field):
                            try:
                                field_repair_limit = max(
                                    1,
                                    int(limit_for_field(
                                        field_id,
                                        repair_limit,
                                    )),
                                )
                            except Exception:
                                field_repair_limit = repair_limit
                        prior_repairs = sum(
                            1
                            for event in job.events or ()
                            if event.kind
                            == "stale_branch_controller_reopened"
                            and str(event.detail.get("pagePlanId") or "")
                            == str(page_plan.id or "")
                            and field_id in {
                                str(item)
                                for item in event.detail.get(
                                    "fieldIds", ()
                                ) or ()
                            }
                        )
                        if prior_repairs >= field_repair_limit:
                            exhausted_controllers.append(field_id)
                    if exhausted_controllers:
                        job.record(
                            "branch_controller_repair_exhausted",
                            "A CEAC branch controller kept reverting after "
                            "the bounded automatic repair budget",
                            pagePlanId=page_plan.id,
                            fieldIds=exhausted_controllers,
                            repairLimit=repair_limit,
                        )
                        return self._wait_human(
                            job,
                            "CEAC 连续重置同一个分支下拉框，已停止"
                            f"自动读写（上限 {repair_limit} 次）。页面保持"
                            "不动，不会再自动恢复或调用 Gemini；请检查"
                            "当前下拉框的 CEAC 服务器状态。",
                            wait_kind="manual_hard_boundary",
                        )
                    reopened = set(stale_controllers)
                    job.completed_field_ids = [
                        field_id
                        for field_id in job.completed_field_ids
                        if field_id not in reopened
                    ]
                    job.inapplicable_field_ids = sorted(
                        set(job.inapplicable_field_ids or ()).difference(
                            reopened
                        )
                    )
                    job.record(
                        "stale_branch_controller_reopened",
                        "A later CEAC postback reset a verified branch "
                        "controller; it was reopened before planning hidden "
                        "dependent fields",
                        pagePlanId=page_plan.id,
                        fieldIds=stale_controllers,
                    )
                    self._save(job)
            candidate_page_fields = [
                field_id for field_id in page_field_ids
                if field_id not in job.completed_field_ids
            ]
            classify_presence = getattr(
                self.browser, "classify_field_presence", None
            )
            if (
                self._should_classify_field_presence(visual_loop)
                and candidate_page_fields
                and callable(classify_presence)
            ):
                try:
                    presence = classify_presence(
                        candidate_page_fields,
                        field_labels,
                        control_hints,
                    )
                    absent = {
                        str(field_id)
                        for field_id in dict(presence or {}).get(
                            "absent", ()
                        )
                        if str(field_id) in set(candidate_page_fields)
                    }
                    previous_inapplicable = set(
                        job.inapplicable_field_ids or ()
                    )
                    # Reclassify every field owned by this physical page on
                    # every observation. A later branch postback that reveals
                    # a previously absent control automatically removes it.
                    next_inapplicable = (
                        previous_inapplicable.difference(page_field_ids)
                        | absent
                    )
                    if next_inapplicable != previous_inapplicable:
                        job.inapplicable_field_ids = sorted(
                            next_inapplicable
                        )
                        job.record(
                            "conditional_field_scope_updated",
                            "Live rendered DOM scope was updated before Gemini "
                            "planning; absent conditional fields were not sent "
                            "to the model or falsely marked complete",
                            pagePlanId=page_plan.id,
                            inapplicableFieldIds=sorted(absent),
                            inapplicableFieldCount=len(absent),
                        )
                        self._save(job)
                except Exception as error:
                    job.record(
                        "conditional_field_scope_unavailable",
                        "The browser could not prove conditional field "
                        "presence; every reviewed field remained pending",
                        pagePlanId=page_plan.id,
                        errorType=type(error).__name__,
                    )
            pending_page_fields = [
                field_id
                for field_id in candidate_page_fields
                if field_id not in set(job.inapplicable_field_ids or ())
            ]
            exhausted_visual_fields = {
                field_id
                for field_id in pending_page_fields
                if self._visual_failure_count(
                    job,
                    page_plan.id,
                    field_id,
                ) >= self.VISUAL_FIELD_FAILURE_LIMIT
            }
            # Reaching the durable budget permanently disables the ephemeral
            # "force Gemini" marker for this page/field.  The next attempt is
            # always a cheap semantic DOM rebind, including after a watcher or
            # service restart.
            force_model_repair_fields.difference_update(
                exhausted_visual_fields
            )
            actions = []
            unresolved = list(pending_page_fields)
            plan_source = ""
            local_planner = getattr(self.browser, "plan_fields", None)
            model_pending_page_fields = [
                field_id
                for field_id in pending_page_fields
                if field_id not in exhausted_visual_fields
            ]
            # Visual execution is screenshot-led, not a differently animated
            # deterministic form filler. Every fresh physical page (and every
            # same-URL postback generation with remaining approved fields)
            # must first receive one Gemini page-level batch. DOM planning is
            # retained only for non-visual execution and for exact fields that
            # have spent their durable visual repair budget.
            visual_batch_required = bool(
                visual_loop and model_pending_page_fields
            )
            if visual_loop:
                local_pending_page_fields = [
                    field_id
                    for field_id in pending_page_fields
                    if field_id in exhausted_visual_fields
                ]
            else:
                local_pending_page_fields = [
                    field_id
                    for field_id in pending_page_fields
                    if (
                        field_id not in force_model_repair_fields
                        or field_id in exhausted_visual_fields
                    )
                ]
            if callable(local_planner) and local_pending_page_fields:
                # In visual mode this list contains only fields whose durable
                # Gemini repair budget is exhausted.  Resolve them immediately
                # instead of waiting for every other model-owned field on the
                # page to finish first.  Otherwise one exhausted radio could
                # remain visibly blank while unrelated provider retries keep
                # the cursor parked for minutes.
                try:
                    actions, unresolved = local_planner(
                        local_pending_page_fields,
                        field_labels,
                        control_hints,
                    )
                    choice_ids = [
                        field_id for field_id in unresolved
                        if "[control=yes_no" in str(
                            fields[field_id].label or ""
                        ).lower()
                    ]
                    choice_planner = getattr(
                        self.browser, "plan_choice_fields", None
                    )
                    if choice_ids and callable(choice_planner):
                        choice_actions, choice_unresolved = choice_planner(
                            choice_ids,
                            field_labels,
                            control_hints,
                        )
                        actions.extend(choice_actions)
                        unresolved = [
                            field_id for field_id in unresolved
                            if field_id not in set(choice_ids)
                            or field_id in set(choice_unresolved)
                        ]
                except Exception as error:
                    job.record(
                        "local_plan_unavailable",
                        "Deterministic DOM planning was unavailable; "
                        "falling back to the computer-use model",
                        errorType=type(error).__name__,
                    )
                    actions = []
                    unresolved = list(local_pending_page_fields)
                if actions:
                    plan_source = (
                        "deterministic-dom-visual"
                        if visual_loop
                        else "deterministic-dom"
                    )

            propose_actions = getattr(self.model, "propose_actions", None)
            batched_plan = bool(actions) or callable(propose_actions)
            if not pending_page_fields and not actions:
                stale_page_fields, inconclusive_page_fields = (
                    self._stale_completed_page_fields(
                        job,
                        fields,
                        page_field_ids,
                        field_labels,
                        control_hints,
                        local_planner,
                    )
                )
                if inconclusive_page_fields:
                    # A selector that cannot be reconstructed after a CEAC
                    # partial postback is not evidence that the value was
                    # erased. The value action already passed exact DOM
                    # verification when it was applied. Reopening such fields
                    # created the historical infinite-refill loop, so retain
                    # their verified completion and let CEAC's own Next
                    # validation be the final page-level guard.
                    job.record(
                        "page_revalidation_inconclusive",
                        "Some completed controls could not be re-resolved; "
                        "their previously verified completion was retained",
                        fieldIds=inconclusive_page_fields,
                    )
                if stale_page_fields:
                    local_exhausted = [
                        field_id
                        for field_id in stale_page_fields
                        if revalidation_repairs.get(
                            (page_plan.id, field_id), 0
                        ) >= 1
                    ]
                    durable_exhausted = [
                        field_id
                        for field_id in stale_page_fields
                        if self._durable_revalidation_failure_count(
                            job,
                            page_plan.id,
                            field_id,
                        ) >= max(1, self.VISUAL_FIELD_FAILURE_LIMIT - 1)
                    ]
                    exhausted = list(dict.fromkeys([
                        *local_exhausted,
                        *durable_exhausted,
                    ]))
                    if exhausted:
                        field_summary = self._field_label_summary(
                            fields,
                            exhausted,
                        )
                        job.record(
                            "page_revalidation_stalled",
                            "A field still differed after the bounded "
                            "deterministic repairs; the refill loop was "
                            "stopped across continuous-run resumes",
                            fieldIds=exhausted,
                            pagePlanId=page_plan.id,
                            durable=True,
                        )
                        return self._wait_human(
                            job,
                            "本页有字段在自动修复后仍与网页实际值不一致；"
                            "Gemini 已阻止重复重填。"
                            + (
                                f"请重点检查：{field_summary}。"
                                if field_summary else ""
                            )
                            + "请检查网页字段限制或选项。",
                            wait_kind="manual_hard_boundary",
                        )
                    for field_id in stale_page_fields:
                        key = (page_plan.id, field_id)
                        revalidation_repairs[key] = (
                            revalidation_repairs.get(key, 0) + 1
                        )
                    stale = set(stale_page_fields)
                    job.completed_field_ids = [
                        field_id for field_id in job.completed_field_ids
                        if field_id not in stale
                    ]
                    job.record(
                        "page_revalidation_failed",
                        "Completed field values no longer match the live page",
                        fieldIds=stale_page_fields,
                        pagePlanId=page_plan.id,
                    )
                    self._save(job)
                    continue
                if not page_plan.allow_next:
                    return self._wait_human(
                        job,
                        "本页仍有未授权或需人工处理的内容；"
                            "Gemini 已暂停，请处理后手动进入下一页再继续任务。",
                    )
                if not job.auto_next:
                    job.record(
                        "auto_next_disabled",
                        "Current page verified; automatic Next is disabled "
                        "for this reviewed job",
                        pagePlanId=page_plan.id,
                        currentUrl=observation.url,
                    )
                    self._save(job)
                    return self._wait_human(
                        job,
                        "本页字段已全部填写并校验通过；当前任务未授权自动 "
                        "Next，请手动进入下一页后继续。",
                    )
                plan_next = getattr(self.browser, "plan_next", None)
                if callable(plan_next):
                    try:
                        next_action = plan_next()
                    except NextDispatchReceiptUnavailable as error:
                        job.record(
                            "next_dispatch_receipt_unavailable",
                            "The fixed CEAC Next control was not activated because "
                            "its browser-side dispatch receipt was unavailable",
                            reason=str(error)[:500],
                        )
                        return self._wait_human(
                            job,
                            "本页已填写完成，但浏览器的 Next 派发回执无法形成"
                            "一致、可持久验证的记录；Gemini 未点击 Next，并已在"
                            "安全边界暂停，绝不会无回执派发或重复点击。",
                            wait_kind="manual_hard_boundary",
                        )
                    except Exception as error:
                        job.record(
                            "next_plan_unavailable",
                            "Could not resolve the fixed CEAC Next control",
                            errorType=type(error).__name__,
                        )
                        next_action = None
                    if next_action is not None:
                        actions = [next_action]
                        plan_source = "deterministic-next"
                        batched_plan = True
                if not actions:
                    # Offline/demo drivers do not implement CEAC page
                    # navigation.  Preserve their local completion contract,
                    # while production drivers with plan_next must continue
                    # until the explicit Review/Sign boundary above.
                    required = (
                        set(job.required_field_ids) or set(fields)
                    ).difference(job.inapplicable_field_ids)
                    if (
                        not callable(plan_next)
                        and required
                        and required.issubset(job.completed_field_ids)
                    ):
                        return self._complete_if_allowed(
                            job,
                            observation,
                            page_plan,
                            fields,
                            allow_terminal_completion=True,
                        )
                    return self._wait_human(
                        job,
                        "本页已填写完成，但未能安全定位 Next；"
                        "请手动进入下一页后继续任务。",
                    )
            if not actions:
                if (
                    pending_page_fields
                    and not model_pending_page_fields
                ):
                    return self._yield_exhausted_visual_rebind(
                        job,
                        observation,
                        page_plan.id,
                        pending_page_fields,
                        fields,
                    )
                fallback_diagnostics = getattr(
                    self.browser,
                    "model_fallback_diagnostics",
                    None,
                )
                if callable(fallback_diagnostics):
                    try:
                        diagnostics = dict(
                            fallback_diagnostics(
                                model_pending_page_fields
                            ) or {}
                        )
                    except Exception:
                        diagnostics = {}
                    if diagnostics:
                        job.record(
                            "semantic_fallback_diagnostics",
                            "Read-only live control diagnostics were captured "
                            "before model fallback",
                            pagePlanId=page_plan.id,
                            fieldIds=model_pending_page_fields,
                            **diagnostics,
                        )
                block_model_fallback = getattr(
                    self.browser,
                    "model_fallback_block_reason",
                    None,
                )
                if callable(block_model_fallback):
                    try:
                        fallback_block_reason = str(
                            block_model_fallback(
                                model_pending_page_fields
                            )
                            or ""
                        ).strip()
                    except Exception:
                        fallback_block_reason = ""
                    if fallback_block_reason:
                        job.record(
                            "model_fallback_blocked_by_required_branch",
                            "A required semantic branch remained absent after "
                            "its bounded controller repair; visual guessing "
                            "was prohibited",
                            fieldIds=model_pending_page_fields,
                            pagePlanId=page_plan.id,
                        )
                        return self._wait_human(
                            job,
                            fallback_block_reason,
                            wait_kind="manual_hard_boundary",
                        )
                set_page_context = getattr(
                    self.model, "set_page_context", None
                )
                if callable(set_page_context):
                    try:
                        set_page_context({
                            field_id: {
                                "label": fields[field_id].label,
                            }
                            for field_id in model_pending_page_fields
                        })
                    except Exception as error:
                        job.record(
                            "model_page_context_unavailable",
                            "Optional Gemini page context could not be updated; "
                            "the current screenshot remains authoritative",
                            errorType=type(error).__name__,
                        )
                plan_source = (
                    "model-visual-batch"
                    if visual_loop and callable(propose_actions)
                    else "model-visual-single"
                    if visual_loop
                    else "model-batch"
                    if callable(propose_actions)
                    else "model-single"
                )
                self._visual_status(
                    "thinking",
                    "正在一次规划本页可见字段",
                )
                job.record(
                    "model_planning_started",
                    "Waiting for one Gemini page-level visual plan",
                    pendingFieldCount=len(model_pending_page_fields),
                    pagePlanId=page_plan.id,
                )
                self._save(job)
                planning_started = time.monotonic()
                try:
                    actions = self._propose_actions_with_retries(
                        job,
                        observation,
                        {
                            field_id: fields[field_id]
                            for field_id in model_pending_page_fields
                        },
                        model_pending_page_fields,
                        propose_actions,
                        attempts=3,
                    )
                except ProviderNotConfigured as error:
                    return self._block(job, str(error))
                except ProviderRequestError as error:
                    if self._is_retryable_provider_exhaustion(error):
                        if job.continuous_run_requested:
                            return self._schedule_automatic_retry(
                                job,
                                observation,
                                error,
                            )
                        return self._wait_human(
                            job,
                            "Gemini 请求暂时中断；网页和已完成字段已保留，"
                            "请稍后点击“继续 Gemini”重试。",
                            wait_kind="manual_hard_boundary",
                        )
                    reason_code = str(
                        getattr(error, "reason_code", "") or ""
                    )
                    status_code = getattr(error, "status_code", None)
                    job.record(
                        "provider_request_rejected",
                        "Gemini rejected the planning request; the live "
                        "browser checkpoint was retained",
                        reasonCode=reason_code or "request_rejected",
                        statusCode=status_code,
                        errorType=type(error).__name__,
                    )
                    return self._wait_human(
                        job,
                        self._provider_rejection_checkpoint(reason_code),
                        wait_kind="manual_hard_boundary",
                    )
                except Exception as error:
                    if self._is_retryable_provider_exhaustion(error):
                        if job.continuous_run_requested:
                            return self._schedule_automatic_retry(
                                job,
                                observation,
                                error,
                            )
                        # A transient provider outage must not turn a safe,
                        # resumable checkpoint into a terminal FAILED job.
                        # One-shot runs remain opt-in: preserve the page and
                        # progress, then let the operator click Continue when
                        # the provider is reachable again.
                        return self._wait_human(
                            job,
                            "Gemini 请求暂时中断；网页和已完成字段已保留，"
                            "请稍后点击“继续 Gemini”重试。",
                            wait_kind="manual_hard_boundary",
                        )
                    return self._fail(
                        job,
                        "Computer-use model failed",
                        type(error).__name__,
                    )
                job.record(
                    "model_planning_finished",
                    "Gemini page-level visual plan returned",
                    durationMs=max(
                        0, int((time.monotonic() - planning_started) * 1000)
                    ),
                    actionCount=(
                        len(actions)
                        if isinstance(actions, (list, tuple))
                        else 0
                    ),
                    pagePlanId=page_plan.id,
                )
                if isinstance(actions, (list, tuple)):
                    rejected_exhausted = sorted({
                        str(action.field_id)
                        for action in actions
                        if (
                            getattr(action, "field_id", "")
                            and str(action.field_id)
                            in exhausted_visual_fields
                        )
                    })
                    if rejected_exhausted:
                        actions = [
                            action
                            for action in actions
                            if (
                                not getattr(action, "field_id", "")
                                or str(action.field_id)
                                not in exhausted_visual_fields
                            )
                        ]
                        job.record(
                            "exhausted_visual_model_actions_rejected",
                            "Model actions for fields whose durable visual "
                            "repair budget was exhausted were rejected before "
                            "any browser mutation",
                            fieldIds=rejected_exhausted,
                            pagePlanId=page_plan.id,
                        )
                self._save(job)

            if (
                not isinstance(actions, (list, tuple))
                or not actions
            ):
                job.record(
                    "invalid_model_plan",
                    "Gemini returned no executable field action; the browser "
                    "was not mutated",
                    pagePlanId=page_plan.id,
                    responseType=type(actions).__name__,
                )
                if job.continuous_run_requested:
                    return self._schedule_progress_retry(
                        job,
                        observation,
                        kind="progress_stall",
                        message=(
                            "Gemini 本轮没有返回可执行字段；系统将在退避后"
                            "自动重新截图规划，无需再次点击。"
                        ),
                        event_kind="invalid_model_plan_retry_scheduled",
                        base_delay=1,
                    )
                return self._block(
                    job,
                    "Computer-use model returned an invalid page action batch",
                )
            # A returned model/DOM plan proves that provider/browser recovery
            # succeeded, but it is not page progress by itself. Keep progress
            # and navigation retry counters until a value write or page
            # transition is actually verified, otherwise repeated no-op plans
            # would reset their backoff into a tight loop.
            if job.automatic_retry_kind in {"", "provider", "browser"}:
                self._clear_automatic_retry(job, record_event=True)
            # CEAC repeater links (for example Work/Education 3 languages)
            # run whole-page ASP.NET validation before adding a row.  A
            # screenshot plan may legitimately list the visible first record,
            # then Add Another, then the remaining Yes/No controls in visual
            # order.  Executing that order makes CEAC reject the structural
            # postback even though the first record itself is valid.  Preserve
            # the model's order for ordinary fields, but make every reviewed
            # ensure-N action the final mutation in the current page batch.
            # The newly revealed record is filled by the next fresh plan.
            ordered_actions = self._defer_repeater_actions(actions, fields)
            if list(ordered_actions) != list(actions):
                job.record(
                    "repeater_actions_deferred",
                    "Repeater structure actions were deferred until all "
                    "currently visible page values are filled and verified",
                    fieldIds=[
                        str(action.field_id)
                        for action in ordered_actions
                        if self._is_repeater_field_action(action, fields)
                    ],
                    pagePlanId=page_plan.id,
                )
                actions = ordered_actions
            if len(actions) > 20:
                if plan_source.startswith("deterministic-"):
                    job.record(
                        "deterministic_batch_chunked",
                        "A large deterministic page plan was split into a "
                        "bounded execution chunk",
                        originalActionCount=len(actions),
                        chunkActionCount=20,
                    )
                    actions = list(actions[:20])
                else:
                    return self._block(
                        job,
                        "Computer-use model returned an invalid page action batch",
                    )
            job.record(
                "plan_proposed",
                "Computer-use runtime proposed a page action plan",
                actionCount=len(actions),
                batched=batched_plan,
                source=plan_source,
            )
            current_observation = observation
            for action_number, action in enumerate(actions, start=1):
                if self._cancel_requested():
                    return self._cancel(job)
                if run_step_count >= self.max_steps:
                    return self._yield_step_budget(
                        job,
                        current_observation,
                    )
                run_step_count += 1
                job.step_count += 1
                action_error = self._validate_proposed_action(action)
                if action_error:
                    return self._block(job, action_error)
                if action.kind == ActionKind.COMPLETE:
                    completion = self._complete_if_allowed(
                        job, current_observation, page_plan, fields
                    )
                    if completion is not None:
                        return completion
                    # COMPLETE is advisory on a formal data-entry page. Once
                    # its fields are verified, the next loop uses the
                    # deterministic fixed Next control.
                    break
                if action.kind == ActionKind.PAUSE:
                    pause_reason = (
                        action.reason or "Model requested human review"
                    )
                    if (
                        job.continuous_run_requested
                        and not self._pause_requires_human(pause_reason)
                    ):
                        job.record(
                            "recoverable_model_pause",
                            "A non-boundary model/schema pause was converted "
                            "to a bounded automatic replan; the browser was not "
                            "mutated",
                            reason=str(pause_reason)[:500],
                            pagePlanId=page_plan.id,
                        )
                        return self._schedule_progress_retry(
                            job,
                            current_observation,
                            kind="progress_stall",
                            message=(
                                "Gemini 本轮没有返回可执行的字段计划；系统将在"
                                "退避后自动重新截图规划，无需再次点击。"
                            ),
                            event_kind="model_plan_retry_scheduled",
                            base_delay=1,
                        )
                    return self._wait_human(
                        job, pause_reason
                    )

                action_decision = self.policy.inspect_action(
                    action, fields, page_plan
                )
                system_owned_next = bool(
                    action.kind == ActionKind.CLICK
                    and not action.field_id
                    and self._is_next_action(action)
                    and plan_source == "deterministic-next"
                    and not pending_page_fields
                )
                if (
                    system_owned_next
                    and getattr(
                        self.browser,
                        "requires_next_dispatch_receipt",
                        False,
                    )
                    and not (
                        action.dispatch_receipt_required
                        and str(
                            action.dispatch_receipt_scope or ""
                        ).strip()
                    )
                ):
                    return self._wait_human(
                        job,
                        "系统已定位 Next，但浏览器未建立可持久验证的派发"
                        "回执；为避免崩溃后重复点击，本次未执行。",
                        wait_kind="manual_hard_boundary",
                    )
                if (
                    action.kind == ActionKind.NAVIGATE
                    or (
                        action.kind == ActionKind.CLICK
                        and not action.field_id
                        and not system_owned_next
                    )
                ):
                    job.record(
                        "model_navigation_rejected",
                        "A model-authored page navigation/control click was "
                        "rejected before any browser side effect; only the "
                        "system-owned Next gate may change DS-160 pages",
                        action=action.kind.value,
                        target=str(action.target_hint or "")[:200],
                        pagePlanId=page_plan.id,
                        pendingFieldCount=len(pending_page_fields),
                    )
                    if job.continuous_run_requested:
                        return self._schedule_progress_retry(
                            job,
                            current_observation,
                            kind="progress_stall",
                            message=(
                                "Gemini 返回了不受信任的跳页动作；系统已在执行前"
                                "拒绝，并会自动重新规划本页，网页没有被操作。"
                            ),
                            event_kind="model_navigation_retry_scheduled",
                            base_delay=1,
                        )
                    if action.kind == ActionKind.NAVIGATE:
                        return self._wait_human(
                            job,
                            "Model-authored form navigation was rejected before execution",
                        )
                    return self._block(
                        job,
                        "Model-authored page-control click was rejected before execution",
                    )
                if system_owned_next and (
                    len(actions) != 1
                    or plan_source != "deterministic-next"
                ):
                    return self._block(
                        job,
                        "System-owned Next was mixed with another page action",
                    )
                if not action_decision.allowed and not system_owned_next:
                    if action_decision.requires_human:
                        return self._wait_human(
                            job, action_decision.reason
                        )
                    return self._block(job, action_decision.reason)

                if action.kind in {ActionKind.TYPE, ActionKind.SELECT}:
                    # The model chooses only a field. The approved value always
                    # comes from the confirmed record, never from model output.
                    action.value = fields[action.field_id].value
                    if len(action.value) > 2048:
                        return self._block(
                            job,
                            "Approved field value exceeds browser action limit",
                        )

                # A page-level model plan can take tens of seconds.  During
                # that interval a consultant may manually press Next, or CEAC
                # may finish a delayed postback.  Never execute an action that
                # was planned for a page plan which no longer owns the live
                # document.  This check happens before semantic binding and
                # before any browser mutation, so a stale Work 1 Job Title can
                # never be typed into Work 2 Course of Study.
                lightweight_observer = (
                    getattr(
                        self.browser, "observe_route_lightweight", None
                    )
                    or getattr(
                        self.browser, "observe_lightweight", None
                    )
                )
                try:
                    live_before_execution = (
                        lightweight_observer()
                        if callable(lightweight_observer)
                        else current_observation
                    )
                except Exception as error:
                    if action.field_id:
                        self._invalidate_browser_field(action.field_id)
                    job.record(
                        "preexecution_page_check_unavailable",
                        "The live page could not be rechecked immediately "
                        "before execution; the planned action was discarded "
                        "without mutating the browser",
                        fieldId=action.field_id,
                        pagePlanId=page_plan.id,
                        errorType=type(error).__name__,
                    )
                    self._save(job)
                    break
                live_page_plan = self.page_plans.match(
                    live_before_execution
                )
                if (
                    live_page_plan is None
                    or live_page_plan.id != page_plan.id
                ):
                    if action.field_id:
                        self._invalidate_browser_field(action.field_id)
                    clear_page_state = getattr(
                        self.browser, "clear_page_state", None
                    )
                    if callable(clear_page_state):
                        try:
                            clear_page_state()
                        except Exception:
                            pass
                    job.record(
                        "stale_page_action_discarded",
                        "The live document changed page plans after planning; "
                        "the old action was discarded before browser mutation",
                        fieldId=action.field_id,
                        plannedPagePlanId=page_plan.id,
                        livePagePlanId=(
                            live_page_plan.id if live_page_plan else ""
                        ),
                        plannedUrl=current_observation.url,
                        liveUrl=live_before_execution.url,
                    )
                    current_observation = live_before_execution
                    self._save(job)
                    break
                current_observation = live_before_execution

                if (
                    action.field_id
                    and action.kind in {
                        ActionKind.TYPE,
                        ActionKind.SELECT,
                        ActionKind.CLICK,
                    }
                    and action.coordinate_x is not None
                ):
                    bind_visual = getattr(
                        self.browser,
                        "bind_visual_field",
                        None,
                    )
                    if callable(bind_visual):
                        binding_error_type = ""
                        try:
                            semantically_bound = bool(bind_visual(
                                action,
                                field_labels.get(action.field_id) or (),
                                control_hints.get(action.field_id) or (),
                            ))
                        except Exception as error:
                            semantically_bound = False
                            binding_error_type = type(error).__name__
                        if not semantically_bound:
                            previous_failures, failure_count = (
                                self._record_visual_failure(
                                    job,
                                    page_plan.id,
                                    action.field_id,
                                    failure_kind="binding",
                                )
                            )
                            if (
                                failure_count
                                < self.VISUAL_FIELD_FAILURE_LIMIT
                            ):
                                force_model_repair_fields.add(
                                    action.field_id
                                )
                            else:
                                force_model_repair_fields.discard(
                                    action.field_id
                                )
                            self._invalidate_browser_field(action.field_id)
                            job.record(
                                "visual_field_binding_rejected",
                                "The visual coordinate did not uniquely match "
                                "the system-owned field descriptor; no DOM "
                                "control was mutated",
                                fieldId=action.field_id,
                                pagePlanId=page_plan.id,
                                attempt=failure_count,
                                errorType=binding_error_type,
                            )
                            report_result = getattr(
                                self.model,
                                "record_action_result",
                                None,
                            )
                            if callable(report_result):
                                try:
                                    report_result(
                                        action,
                                        current_observation,
                                        current_observation,
                                        verified=False,
                                        error=(
                                            "Coordinate control identity does "
                                            "not match the approved field"
                                        ),
                                    )
                                except Exception:
                                    pass
                            self._save(job)
                            if (
                                previous_failures
                                >= self.VISUAL_FIELD_FAILURE_LIMIT
                            ):
                                return self._yield_exhausted_visual_rebind(
                                    job,
                                    current_observation,
                                    page_plan.id,
                                    [action.field_id],
                                    fields,
                                )
                            # On the transition to three failures, break out to
                            # the outer observation loop.  It will perform one
                            # fresh deterministic semantic DOM rebind before
                            # deciding whether a cheap watcher backoff is needed.
                            break

                constrain_value = getattr(
                    self.browser,
                    "constrain_action_value",
                    None,
                )
                if (
                    action.kind == ActionKind.TYPE
                    and action.field_id
                    and callable(constrain_value)
                ):
                    try:
                        constraint = constrain_value(action)
                    except ControlBindingUnavailable as error:
                        repair_key = (page_plan.id, action.field_id)
                        constraint_binding_replans[repair_key] = (
                            constraint_binding_replans.get(repair_key, 0) + 1
                        )
                        attempt = constraint_binding_replans[repair_key]
                        self._invalidate_browser_field(action.field_id)
                        job.record(
                            "control_binding_replan_scheduled",
                            "The semantic DOM binding disappeared before text "
                            "constraint preflight; the stale selector was "
                            "discarded for a fresh page observation and no DOM "
                            "control was mutated",
                            fieldId=action.field_id,
                            pagePlanId=page_plan.id,
                            attempt=attempt,
                            errorType=type(error).__name__,
                        )
                        self._save(job)
                        if (
                            attempt >= 3
                            and job.continuous_run_requested
                        ):
                            return self._schedule_progress_retry(
                                job,
                                current_observation,
                                kind="progress_stall",
                                message=(
                                    "网页局部刷新连续使当前字段的语义绑定失效；"
                                    "系统已丢弃旧绑定且未写入网页，将换新页面"
                                    "快照自动重新定位"
                                ),
                                event_kind=(
                                    "control_binding_retry_scheduled"
                                ),
                                base_delay=1,
                            )
                        # Re-observe the live document and let the deterministic
                        # planner establish a new semantic marker.  Reusing the
                        # action or its old selector would race CEAC's partial
                        # postback and risks writing the wrong control.
                        break
                    except ControlValueConstraintError as error:
                        job.record(
                            "control_value_constraint_rejected",
                            "The live, semantically bound CEAC control cannot "
                            "accept the approved text value; no DOM control "
                            "was mutated",
                            fieldId=action.field_id,
                            pagePlanId=page_plan.id,
                            errorType=type(error).__name__,
                        )
                        return self._wait_human(
                            job,
                            "网页控件声明的文本约束无法容纳当前值；系统在写入前"
                            "已停止该动作，未产生网页截断或重复填写。",
                            wait_kind="manual_hard_boundary",
                        )
                    except Exception as error:
                        # An untyped adapter/runtime failure is not proof that
                        # the approved value violates maxlength.  Treat it as
                        # unavailable preflight and recover without mutation;
                        # production Playwright paths classify this more
                        # precisely with the two exceptions above.
                        self._invalidate_browser_field(action.field_id)
                        job.record(
                            "control_constraint_inspection_retry_scheduled",
                            "Control constraint inspection was unavailable; "
                            "the field binding was discarded before any DOM "
                            "mutation",
                            fieldId=action.field_id,
                            pagePlanId=page_plan.id,
                            errorType=type(error).__name__,
                        )
                        self._save(job)
                        if job.continuous_run_requested:
                            return self._schedule_progress_retry(
                                job,
                                current_observation,
                                kind="progress_stall",
                                message=(
                                    "网页控件约束暂时无法读取；系统未写入网页，"
                                    "将自动重新观察并定位字段"
                                ),
                                event_kind=(
                                    "control_constraint_inspection_retry_scheduled"
                                ),
                                base_delay=1,
                            )
                        break
                    constraint_binding_replans.pop(
                        (page_plan.id, action.field_id),
                        None,
                    )
                    if constraint:
                        normalized_value = str(action.value or "")
                        job.control_normalized_values[
                            action.field_id
                        ] = normalized_value
                        fields[action.field_id] = replace(
                            fields[action.field_id],
                            value=normalized_value,
                        )
                        job.record(
                            "control_value_normalized",
                            "Approved text was normalized to the live CEAC "
                            "maxlength before browser mutation",
                            pagePlanId=page_plan.id,
                            **dict(constraint),
                        )

                # Idempotency identifiers are system-owned, not model-supplied.
                action.execution_generation = max(
                    0, int(job.execution_generation or 0)
                )
                action.id = (
                    f"action-{job.id[len('agent-job-'):]}"
                    f"-g{action.execution_generation}"
                    f"-{job.action_index + 1}"
                )
                if action.id in job.applied_action_ids:
                    job.record(
                        "duplicate_action_ignored",
                        "Previously applied action was not executed again",
                        actionId=action.id,
                    )
                    self._save(job)
                    continue

                before = current_observation
                preserve_refreshed_batch = False
                refresh_declared = self._refresh_after_change(
                    action, fields
                )
                # Descriptor metadata means "this control may branch", not
                # proof that this particular approved option actually caused
                # an ASP.NET postback.  Keep it as the conservative fallback
                # for browsers without a DOM-generation detector; production
                # Playwright replaces it with observed evidence below.
                refresh_after_change = refresh_declared
                is_next_navigation = (
                    plan_source == "deterministic-next"
                    and self._is_next_action(action)
                )
                job.pending_action = action
                job.action_index += 1
                job.record(
                    "action_started",
                    "Approved action is ready for browser execution",
                    actionId=action.id,
                    actionIndex=job.action_index,
                    action=action.kind.value,
                    fieldId=action.field_id,
                    pagePlanId=page_plan.id,
                )
                if is_next_navigation:
                    job.record(
                        "page_navigation_started",
                        "Verified page fields are complete; activating CEAC Next",
                        actionId=action.id,
                        fromPagePlanId=page_plan.id,
                        fromUrl=before.url,
                    )
                self._visual_status(
                    "navigating" if is_next_navigation else "working",
                    (
                        f"正在填写本页 {action_number}/{len(actions)}"
                        if action.field_id
                        else "正在进入下一页"
                    ),
                )
                self._save(job)
                # This generation check is intentionally adjacent to the DOM
                # side effect.  A sync/timeout can revoke the lease after the
                # durable pending checkpoint save but before execute(); the
                # old worker must stop in that exact race window.
                if self._cancel_requested():
                    return self._cancel(job)
                try:
                    if callable(self.side_effect_executor):
                        self.side_effect_executor(
                            self.browser.execute,
                            action,
                        )
                    else:
                        self.browser.execute(action)
                except Exception as error:
                    job.record(
                        "browser_execution_interrupted",
                        "Browser action raised before its outcome was known; "
                        "the live page will be checked before any retry",
                        actionId=action.id,
                        fieldId=action.field_id,
                        errorType=type(error).__name__,
                        errorSummary=str(error)[:240],
                    )
                    recovered = self._observe_with_retries(
                        job,
                        purpose="execution-recovery",
                        action=action,
                        attempts=3,
                    )
                    if (
                        recovered is None
                        and self._browser_retry_is_pending(job)
                    ):
                        # The action may already have reached the browser.
                        # Preserve its durable token and let the reconstructed
                        # runtime prove the live outcome before doing anything
                        # else.  This is the central no-double-action invariant.
                        return job
                    if recovered is not None:
                        recovered_result = (
                            self._verify_next_navigation(
                                action, before, recovered
                            )
                            if is_next_navigation
                            else self.verifier.verify_current(
                                action, recovered
                            )
                        )
                        recovered_result = (
                            self._apply_browser_action_postcondition(
                                action,
                                recovered_result,
                            )
                        )
                        if recovered_result.verified:
                            self._mark_applied(job, action)
                            current_observation = recovered
                            job.record(
                                "browser_execution_recovered",
                                "The interrupted action was proven from the "
                                "current DOM and was not repeated",
                                actionId=action.id,
                                fieldId=action.field_id,
                            )
                            if is_next_navigation:
                                navigation_count += 1
                                job.record(
                                    "page_navigation_verified",
                                    "CEAC advanced after an interrupted browser "
                                    "call; the live page proved that Next had "
                                    "already succeeded",
                                    actionId=action.id,
                                    fromPagePlanId=page_plan.id,
                                    fromUrl=before.url,
                                    toUrl=recovered.url,
                                    navigationCount=navigation_count,
                                )
                                self._visual_status(
                                    "observing",
                                    "已进入下一页，正在读取新页面",
                                )
                                if navigation_count >= 40:
                                    self._save(job)
                                    return self._wait_human(
                                        job,
                                        "连续导航页面数超过 DS-160 预期范围，"
                                        "Gemini 已停止以避免页面循环。",
                                    )
                                clear_page_state = getattr(
                                    self.browser,
                                    "clear_page_state",
                                    None,
                                )
                                if callable(clear_page_state):
                                    try:
                                        clear_page_state()
                                    except Exception as cleanup_error:
                                        job.record(
                                            "page_state_cleanup_unavailable",
                                            "Previous-page browser selectors "
                                            "could not be cleared after "
                                            "recovered navigation",
                                            errorType=type(
                                                cleanup_error
                                            ).__name__,
                                        )
                            self._save(job)
                            continue
                        if (
                            is_next_navigation
                            and recovered.dispatch_receipt_conflict
                        ):
                            return self._wait_for_dispatch_receipt_consistency(
                                job,
                                action,
                            )
                        if (
                            is_next_navigation
                            and self._pending_next_authoritatively_not_dispatched(
                                action,
                                recovered,
                            )
                        ):
                            job.pending_action = None
                            job.record(
                                "interrupted_next_not_dispatched",
                                "The matching browser dispatch ledger proved "
                                "that the failed Next call never reached the "
                                "page; the fixed control will be safely rebound",
                                actionId=action.id,
                                receiptScope=action.dispatch_receipt_scope,
                            )
                            self._save(job)
                            current_observation = recovered
                            break
                        if (
                            is_next_navigation
                            and not recovered.errors
                            and job.continuous_run_requested
                            and self._pending_next_authoritatively_dispatched(
                                action,
                                recovered,
                            )
                        ):
                            return self._schedule_progress_retry(
                                job,
                                recovered,
                                kind="navigation_observation",
                                message=(
                                    "Next 已派发，但执行调用在页面切换确认前中断；"
                                    "系统将只继续观测，绝不会重复点击 Next。"
                                ),
                                event_kind=(
                                    "interrupted_next_observation_retry_scheduled"
                                ),
                                base_delay=2,
                            )
                    retry_safety = getattr(
                        self.browser,
                        "interrupted_action_retry_safe",
                        None,
                    )
                    if callable(retry_safety):
                        try:
                            retry_is_safe = bool(retry_safety(action, error))
                        except Exception:
                            retry_is_safe = False
                        if not retry_is_safe:
                            job.pending_action = None
                            self._save(job)
                            return self._wait_human(
                                job,
                                "控件回传已经启动，但最终响应仍不确定；"
                                "系统已恢复可人工操作的页面并停止，"
                                "不会重复操作同一个动态控件。",
                                wait_kind="manual_hard_boundary",
                            )
                    if (
                        not is_next_navigation
                        and action.kind in {
                            ActionKind.TYPE,
                            ActionKind.SELECT,
                        }
                        and action.field_id
                    ):
                        repair_key = (page_plan.id, action.field_id)
                        execution_replans[repair_key] = (
                            execution_replans.get(repair_key, 0) + 1
                        )
                        self._invalidate_browser_field(action.field_id)
                        job.pending_action = None
                        if (
                            self._visual_failure_count(
                                job,
                                page_plan.id,
                                action.field_id,
                            ) < self.VISUAL_FIELD_FAILURE_LIMIT
                        ):
                            force_model_repair_fields.add(action.field_id)
                        else:
                            force_model_repair_fields.discard(action.field_id)
                        job.record(
                            "browser_execution_replanned",
                            "An idempotent value action could not be proven; "
                            "its stale binding was discarded for a fresh visual "
                            "repair instead of repeating the old locator",
                            actionId=action.id,
                            fieldId=action.field_id,
                            attempt=execution_replans[repair_key],
                        )
                        self._save(job)
                        if execution_replans[repair_key] <= 3:
                            break
                    return self._wait_human(
                        job,
                        "浏览器动作中断，系统已自动复核但仍无法安全确认结果；"
                        "为避免重复点击，Gemini 已保留进度并明确暂停。",
                    )
                detected_dynamic_refresh = False
                refresh_detection_available = False
                if (
                    not is_next_navigation
                    and action.field_id
                    and action.kind in {
                        ActionKind.TYPE,
                        ActionKind.SELECT,
                        ActionKind.CLICK,
                    }
                ):
                    detect_refresh = getattr(
                        self.browser, "dynamic_refresh_detected", None
                    )
                    if callable(detect_refresh):
                        refresh_detection_available = True
                        try:
                            detected_dynamic_refresh = bool(
                                detect_refresh(action)
                            )
                        except Exception as error:
                            refresh_detection_available = False
                            job.record(
                                "dynamic_refresh_detection_unavailable",
                                "The browser could not inspect DOM generation "
                                "after the field action",
                                fieldId=action.field_id,
                                errorType=type(error).__name__,
                            )
                if refresh_detection_available:
                    # Preserve the rest of Gemini's already-approved page
                    # batch when the live document proves that no postback or
                    # marked-control replacement occurred.  This avoids a
                    # redundant provider request after branch options such as
                    # "No" that leave the CEAC DOM unchanged.
                    refresh_after_change = detected_dynamic_refresh
                    if refresh_declared and not detected_dynamic_refresh:
                        job.record(
                            "declared_dynamic_refresh_not_observed",
                            "The branch-capable control did not replace the "
                            "live DOM; the remaining verified Gemini page "
                            "batch was preserved",
                            actionId=action.id,
                            fieldId=action.field_id,
                            pagePlanId=page_plan.id,
                            refreshEvidence=dict(getattr(
                                self.browser,
                                "_last_dynamic_refresh_evidence",
                                {},
                            ) or {}),
                            controlPostback=dict(getattr(
                                self.browser,
                                "_last_control_postback_diagnostic",
                                {},
                            ) or {}),
                        )
                if detected_dynamic_refresh and not refresh_declared:
                    refresh_after_change = True
                    job.record(
                        "dynamic_refresh_auto_detected",
                        "A document or marked-control replacement was "
                        "detected without descriptor metadata",
                        actionId=action.id,
                        fieldId=action.field_id,
                        pagePlanId=page_plan.id,
                    )
                if refresh_after_change:
                    self._visual_status(
                        "observing",
                        "网页正在刷新动态字段，完成后会自动继续",
                    )
                    settle = getattr(
                        self.browser, "settle_after_dynamic_refresh", None
                    )
                    if callable(settle):
                        try:
                            settle(
                                action.field_id,
                                field_labels.get(action.field_id) or (),
                                control_hints.get(action.field_id) or (),
                            )
                        except Exception as error:
                            job.record(
                                "dynamic_refresh_settle_unavailable",
                                "The browser could not deterministically "
                                "rebind the changed control after a postback",
                                fieldId=action.field_id,
                                errorType=type(error).__name__,
                            )
                after = self._observe_with_retries(
                    job,
                    purpose="post-action",
                    action=action if batched_plan else None,
                    attempts=3,
                )
                if after is None:
                    if self._browser_retry_is_pending(job):
                        return job
                    return self._fail(
                        job,
                        "Post-action observation failed after automatic retries",
                    )
                deterministic = self.verifier.verify(
                    action, before, after
                )
                deterministic = self._apply_browser_action_postcondition(
                    action,
                    deterministic,
                )
                if is_next_navigation:
                    deterministic = self._verify_next_navigation(
                        action,
                        before,
                        after,
                    )
                    if (
                        not deterministic.verified
                        and not after.errors
                    ):
                        after, deterministic = (
                            self._await_navigation_outcome(
                                job,
                                action,
                                before,
                                after,
                            )
                        )
                        if self._browser_retry_is_pending(job):
                            return job
                if not deterministic.verified:
                    if self._browser_postcondition_requires_hard_boundary(
                        action
                    ):
                        return self._wait_human(
                            job,
                            deterministic.reason
                            or "浏览器依赖控件未出现，任务已停止且可继续运行。",
                            wait_kind="manual_hard_boundary",
                        )
                    if is_next_navigation:
                        if after.dispatch_receipt_conflict:
                            return self._wait_for_dispatch_receipt_consistency(
                                job,
                                action,
                            )
                        repair_fields, has_unscoped_error = (
                            self._field_ids_from_errors(
                                after.errors,
                                set(page_field_ids),
                            )
                        )
                        if repair_fields and not has_unscoped_error:
                            repair_set = set(repair_fields)
                            job.inapplicable_field_ids = [
                                field_id
                                for field_id in job.inapplicable_field_ids
                                if field_id not in repair_set
                            ]
                            job.completed_field_ids = [
                                field_id
                                for field_id in job.completed_field_ids
                                if field_id not in repair_set
                            ]
                            for field_id in repair_fields:
                                self._invalidate_browser_field(field_id)
                                # CEAC proved that the prior binding/value did
                                # not satisfy this exact field.  Bypass the
                                # same deterministic locator once and require
                                # a fresh Gemini screenshot plan; successful
                                # verification clears this flag below.
                                if (
                                    self._visual_failure_count(
                                        job,
                                        page_plan.id,
                                        field_id,
                                    ) < self.VISUAL_FIELD_FAILURE_LIMIT
                                ):
                                    force_model_repair_fields.add(field_id)
                                else:
                                    force_model_repair_fields.discard(field_id)
                            job.pending_action = None
                            job.record(
                                "page_validation_repair_started",
                                "CEAC mapped the retained-page validation "
                                "errors to approved fields; those fields were "
                                "reopened for automatic repair",
                                fieldIds=repair_fields,
                                errorCount=len(after.errors),
                            )
                            self._save(job)
                            break
                        if (
                            not after.errors
                            and job.continuous_run_requested
                        ):
                            # A slow ASP.NET postback can outlive the bounded
                            # in-run observation window. The physical click is
                            # already persisted as pending; yield to the durable
                            # watcher and observe again later without ever
                            # issuing a second click.
                            job.pending_action = action
                            return self._schedule_progress_retry(
                                job,
                                after,
                                kind="navigation_observation",
                                message=(
                                    "Next 已执行，但 CEAC 尚未完成页面切换；"
                                    "系统将在退避后继续观测，绝不会重复点击 Next。"
                                ),
                                event_kind="page_navigation_retry_scheduled",
                                base_delay=2,
                            )
                        # A fixed Next click is never sent back to Gemini for
                        # blind visual retries.  One click is enough: a retained
                        # page with explicit CEAC validation errors is a real
                        # human boundary, not another click opportunity.
                        job.pending_action = None
                        validation_summary = self._safe_validation_summary(
                            after.errors
                        )
                        job.record(
                            "page_navigation_failed",
                            "CEAC Next did not advance to a different page",
                            actionId=action.id,
                            fromPagePlanId=page_plan.id,
                            reason=deterministic.reason,
                            errorCount=len(after.errors),
                            validationSummary=validation_summary,
                        )
                        return self._wait_human(
                            job,
                            "本页字段已填写，但点击 Next 后网页没有进入下一页"
                            "（"
                            f"{deterministic.reason or '页面未确认导航结果'}"
                            "）。Gemini 已停止重复点击，请检查网页提示。"
                            + (
                                f" 网页提示：{validation_summary}"
                                if validation_summary else ""
                            ),
                            wait_kind=(
                                "manual_hard_boundary"
                                if after.errors
                                else ""
                            ),
                        )
                    if self._is_repeater_action(action):
                        # Add Another is not an idempotent value write.  It is
                        # successful only when the browser proves that the
                        # requested record count increased.  Persist one
                        # page/field budget even in semantic-first mode so a
                        # CEAC validation rejection, stale postback, restart,
                        # or watcher resume can never create an endless click
                        # and flash loop.
                        _previous_failures, failure_count = (
                            self._record_visual_failure(
                                job,
                                page_plan.id,
                                action.field_id,
                                failure_kind="repeater_growth",
                            )
                        )
                        if action.id not in job.applied_action_ids:
                            job.applied_action_ids.append(action.id)
                        self._invalidate_browser_field(action.field_id)
                        job.pending_action = None
                        job.record(
                            "repeater_growth_not_observed",
                            "The Add Another action did not increase the live "
                            "record count and consumed its durable retry "
                            "budget",
                            actionId=action.id,
                            fieldId=action.field_id,
                            pagePlanId=page_plan.id,
                            failureCount=failure_count,
                            limit=self.VISUAL_FIELD_FAILURE_LIMIT,
                            reason=deterministic.reason,
                            dispatchDiagnostic=(
                                self.browser.repeater_dispatch_diagnostic()
                                if callable(getattr(
                                    self.browser,
                                    "repeater_dispatch_diagnostic",
                                    None,
                                ))
                                else {}
                            ),
                        )
                        self._save(job)
                        if (
                            failure_count
                            >= self.VISUAL_FIELD_FAILURE_LIMIT
                        ):
                            return self._wait_human(
                                job,
                                "Add Another 连续三次未增加表格行；"
                                "V2 已停止继续点击并关闭自动唤醒，"
                                "请检查当前第一行是否有未填或网页"
                                "校验提示。",
                                wait_kind="manual_hard_boundary",
                            )
                        if job.continuous_run_requested:
                            return self._schedule_progress_retry(
                                job,
                                after,
                                kind="progress_stall",
                                message=(
                                    "Add Another 未观察到新增表格行，"
                                    "系统已丢弃旧绑定并将从实际行数"
                                    "重新核对。"
                                ),
                                event_kind=(
                                    "repeater_growth_retry_scheduled"
                                ),
                                base_delay=1,
                            )
                        return self._wait_human(
                            job,
                            "Add Another 未观察到新增表格行，"
                            "Gemini 已停止重复点击。",
                            wait_kind="manual_hard_boundary",
                        )
                    if action.kind == ActionKind.SCROLL:
                        # A wheel event at the document edge is not progress.
                        # Consume its action id so crash recovery never replays
                        # it, then yield through the durable watcher instead of
                        # spending another provider call in the same hot loop.
                        if action.id not in job.applied_action_ids:
                            job.applied_action_ids.append(action.id)
                        job.pending_action = None
                        report_result = getattr(
                            self.model, "record_action_result", None
                        )
                        if callable(report_result):
                            try:
                                report_result(
                                    action,
                                    before,
                                    after,
                                    verified=False,
                                    error=deterministic.reason,
                                )
                            except Exception as error:
                                job.record(
                                    "model_feedback_unavailable",
                                    "The rejected no-op scroll could not be "
                                    "mirrored to Gemini",
                                    errorType=type(error).__name__,
                                )
                        job.record(
                            "no_op_scroll_rejected",
                            "A Gemini scroll reached the document edge without "
                            "moving the viewport; the action was not counted as "
                            "page progress",
                            actionId=action.id,
                            direction=action.scroll_direction,
                            pagePlanId=page_plan.id,
                            scrollY=after.scroll_y,
                            documentHeight=after.scroll_height,
                            viewportHeight=after.viewport_height,
                        )
                        self._save(job)
                        if job.continuous_run_requested:
                            return self._schedule_progress_retry(
                                job,
                                after,
                                kind="progress_stall",
                                message=(
                                    "Gemini 的滚动没有移动页面，系统已阻止重复"
                                    "空转并重新核对本页实际可见字段。"
                                ),
                                event_kind=(
                                    "no_op_scroll_retry_scheduled"
                                ),
                                base_delay=1,
                            )
                        return self._wait_human(
                            job,
                            "Gemini 的滚动没有移动页面，已明确暂停。",
                        )
                    report_result = getattr(
                        self.model, "record_action_result", None
                    )
                    if visual_loop and action.field_id:
                        previous_failures, failure_count = (
                            self._record_visual_failure(
                                job,
                                page_plan.id,
                                action.field_id,
                                failure_kind="verification",
                            )
                        )
                        if callable(report_result):
                            try:
                                report_result(
                                    action,
                                    before,
                                    after,
                                    verified=False,
                                    error=deterministic.reason,
                                )
                            except Exception as error:
                                job.record(
                                    "model_feedback_unavailable",
                                    "Gemini correction feedback could not be "
                                    "recorded; the stale field binding was still "
                                    "discarded for a fresh semantic rebind",
                                    fieldId=action.field_id,
                                    errorType=type(error).__name__,
                                )
                        if action.id not in job.applied_action_ids:
                            job.applied_action_ids.append(action.id)
                        self._invalidate_browser_field(action.field_id)
                        if (
                            failure_count
                            < self.VISUAL_FIELD_FAILURE_LIMIT
                        ):
                            force_model_repair_fields.add(action.field_id)
                        else:
                            force_model_repair_fields.discard(action.field_id)
                        job.pending_action = None
                        job.record(
                            "action_correction_requested",
                            "The failed browser value verification was "
                            "checkpointed before a fresh field rebind",
                            actionId=action.id,
                            fieldId=action.field_id,
                            pagePlanId=page_plan.id,
                            failureCount=failure_count,
                            reason=deterministic.reason,
                        )
                        self._save(job)
                        current_observation = after
                        if (
                            previous_failures
                            >= self.VISUAL_FIELD_FAILURE_LIMIT
                        ):
                            return self._yield_exhausted_visual_rebind(
                                job,
                                after,
                                page_plan.id,
                                [action.field_id],
                                fields,
                            )
                        # A newly exhausted field gets one deterministic DOM
                        # rebind in the next outer loop.  Lower counts retain
                        # the existing one-shot Gemini repair behavior.
                        break
                    return self._wait_human(
                        job,
                        deterministic.reason
                        or "Deterministic verification failed for "
                        f"{action.field_id or action.target_hint}",
                    )

                # Exact DOM value verification is authoritative for a batch.
                # Avoid one secondary model call per field, which would erase
                # the latency benefit of page-level planning.
                # The independent reviewer only receives structured browser
                # state (URL/title/control values), not the screenshots that
                # the visual model used. It can independently review value
                # changes and navigation, but it cannot meaningfully judge a
                # focus-only click, scroll, or safe key press. Deterministic
                # acknowledgement is authoritative for those UI-only actions.
                independently_reviewable = action.kind in {
                    ActionKind.TYPE,
                    ActionKind.SELECT,
                    ActionKind.NAVIGATE,
                }
                if (
                    self.use_model_verification
                    and not batched_plan
                    and independently_reviewable
                ):
                    try:
                        reviewer = self.action_reviewer or self.model
                        review_action = getattr(
                            reviewer, "review_action", None
                        )
                        if callable(review_action):
                            model_verified = review_action(
                                action, before, after
                            )
                        else:
                            model_verified = reviewer.verify_action(
                                action, before, after
                            )
                    except ProviderNotConfigured:
                        model_verified = True
                    except Exception as error:
                        job.record(
                            "secondary_verification_unavailable",
                            "Deterministic verification passed; secondary "
                            "review was unavailable",
                            errorType=type(error).__name__,
                        )
                        model_verified = True
                    if not model_verified:
                        return self._wait_human(
                            job,
                            "Secondary model review rejected action "
                            f"{action.id}",
                        )
                report_result = getattr(
                    self.model, "record_action_result", None
                )
                if visual_loop and callable(report_result):
                    try:
                        report_result(
                            action,
                            before,
                            after,
                            verified=True,
                        )
                    except Exception as error:
                        job.record(
                            "model_feedback_unavailable",
                            "The verified browser result could not be mirrored "
                            "to Gemini; deterministic DOM verification remains "
                            "authoritative",
                            fieldId=action.field_id,
                            errorType=type(error).__name__,
                        )
                self._mark_applied(job, action)
                self._clear_visual_failure_after_verified_value(
                    job,
                    page_plan.id,
                    action,
                )
                if action.field_id:
                    force_model_repair_fields.discard(action.field_id)
                if refresh_after_change:
                    after_page_plan = self.page_plans.match(after)
                    preserve_refreshed_batch = bool(
                        plan_source == "model-visual-batch"
                        and action_number < len(actions)
                        and after_page_plan is not None
                        and after_page_plan.id == page_plan.id
                    )
                    if preserve_refreshed_batch:
                        job.record(
                            "dynamic_refresh_batch_preserved",
                            "A branch-changing control was verified after its "
                            "postback; remaining Gemini-approved field actions "
                            "will be rebound semantically against the replacement "
                            "DOM instead of requesting another screenshot plan",
                            actionId=action.id,
                            fieldId=action.field_id,
                            pagePlanId=page_plan.id,
                            remainingActionCount=(
                                len(actions) - action_number
                            ),
                            controlPostback=dict(getattr(
                                self.browser,
                                "_last_control_postback_diagnostic",
                                {},
                            ) or {}),
                        )
                    else:
                        job.record(
                            "dynamic_refresh_replanned",
                            "A branch-changing control was verified after its "
                            "postback; no same-page Gemini action remained safe "
                            "to rebind, so a fresh page plan will be requested",
                            actionId=action.id,
                            fieldId=action.field_id,
                            pagePlanId=page_plan.id,
                            controlPostback=dict(getattr(
                                self.browser,
                                "_last_control_postback_diagnostic",
                                {},
                            ) or {}),
                        )
                if is_next_navigation:
                    navigation_count += 1
                    job.record(
                        "page_navigation_verified",
                        "CEAC advanced to a different page",
                        actionId=action.id,
                        fromPagePlanId=page_plan.id,
                        fromUrl=before.url,
                        toUrl=after.url,
                        navigationCount=navigation_count,
                    )
                    self._visual_status(
                        "observing",
                        "已进入下一页，正在读取新页面",
                    )
                    if navigation_count >= 40:
                        return self._wait_human(
                            job,
                            "连续导航页面数超过 DS-160 预期范围，"
                            "Gemini 已停止以避免页面循环。",
                        )
                    clear_page_state = getattr(
                        self.browser, "clear_page_state", None
                    )
                    if callable(clear_page_state):
                        try:
                            clear_page_state()
                        except Exception as error:
                            job.record(
                                "page_state_cleanup_unavailable",
                                "Previous-page browser selectors could not "
                                "be cleared after verified navigation",
                                errorType=type(error).__name__,
                            )
                self._save(job)
                current_observation = after
                if refresh_after_change and not preserve_refreshed_batch:
                    # A CEAC branch selection can replace the whole form or an
                    # UpdatePanel while keeping the same URL. Continue only
                    # when a same-page visual batch still has approved actions:
                    # the production browser prunes detached selectors during
                    # observation and bind_visual_field proves each remaining
                    # field against the replacement DOM before mutation.
                    break

        return self._yield_step_budget(job, None)

    def _propose_actions_with_retries(
        self,
        job,
        observation,
        fields,
        page_field_ids,
        propose_actions,
        attempts=3,
    ):
        """Retry transient Gemini planning without touching the live form."""
        attempts = max(1, min(int(attempts or 1), 3))
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                if callable(propose_actions):
                    return propose_actions(
                        observation,
                        sorted(fields),
                        list(job.completed_field_ids),
                        page_field_ids,
                    )
                return [self.model.propose_action(
                    observation,
                    sorted(fields),
                    list(job.completed_field_ids),
                )]
            except ProviderNotConfigured:
                raise
            except Exception as error:
                last_error = error
                if (
                    getattr(error, "provider_retry_exhausted", False)
                    or getattr(error, "retryable", None) is False
                ):
                    raise
                job.record(
                    "model_planning_retry",
                    "Gemini planning failed before any browser action; the "
                    "same screenshot context will be retried automatically",
                    attempt=attempt,
                    maxAttempts=attempts,
                    errorType=type(error).__name__,
                )
                self._save(job)
                if attempt < attempts:
                    self._visual_status(
                        "thinking",
                        "Gemini 规划暂时中断，正在自动重试；网页尚未被操作",
                    )
                    time.sleep(0.15 * attempt)
        raise last_error

    def _observe_with_retries(
        self,
        job,
        purpose,
        action=None,
        attempts=3,
    ):
        """Bound transient Playwright observations without repeating actions."""
        attempts = max(1, min(int(attempts or 1), 5))
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                observe_action = getattr(
                    self.browser, "observe_action", None
                )
                lightweight = getattr(
                    self.browser, "observe_lightweight", None
                )
                if action is not None and callable(observe_action):
                    observation = observe_action(action)
                elif action is not None and callable(lightweight):
                    observation = lightweight()
                else:
                    observation = self.browser.observe()
                job.wait_boundary_fingerprint = (
                    observation_fingerprint(job, observation)
                )
                return observation
            except Exception as error:
                last_error = error
                job.record(
                    "browser_observation_retry",
                    "A transient browser observation failed and will be "
                    "retried without repeating the preceding action",
                    purpose=str(purpose or "")[:80],
                    attempt=attempt,
                    maxAttempts=attempts,
                    errorType=type(error).__name__,
                )
                self._save(job)
                if attempt < attempts:
                    self._visual_status(
                        "observing",
                        "网页状态读取暂时中断，正在自动重连",
                    )
                    time.sleep(0.15 * attempt)
        if last_error is not None:
            job.record(
                "browser_observation_exhausted",
                "Automatic browser observation retries were exhausted",
                purpose=str(purpose or "")[:80],
                errorType=type(last_error).__name__,
            )
            if job.continuous_run_requested:
                self._schedule_browser_retry(
                    job,
                    purpose=purpose,
                    error=last_error,
                )
            else:
                self._save(job)
        return None

    def _await_navigation_outcome(
        self,
        job,
        action,
        before,
        latest,
        timeout_seconds=None,
    ):
        """Observe a slow CEAC postback; never issue a second Next click."""
        if timeout_seconds is None:
            timeout_seconds = getattr(
                self.browser,
                "navigation_outcome_timeout_seconds",
                0.5,
            )
        wait_started = time.monotonic()
        deadline = wait_started + max(
            1.0, min(float(timeout_seconds), 30.0)
        )
        pause = 0.25
        observation_count = 0
        current = latest
        verification = self._verify_next_navigation(
            action, before, current
        )
        if not verification.verified and not current.errors:
            self._visual_status(
                "navigating",
                "Next 已点击，正在等待 CEAC 完成页面切换",
            )
        while (
            not verification.verified
            and not current.errors
            and time.monotonic() < deadline
        ):
            # Refresh the host lease throughout a slow ASP.NET postback. This is
            # a real progress heartbeat, not a second click, and keeps the
            # cursor/status visible while the page is legitimately waiting.
            self._visual_status(
                "navigating",
                "Next 已点击，正在等待 CEAC 完成页面切换",
            )
            time.sleep(pause)
            pause = min(1.5, pause * 1.6)
            observed = self._observe_with_retries(
                job,
                purpose="navigation-outcome",
                action=action,
                attempts=2,
            )
            if observed is None:
                if self._browser_retry_is_pending(job):
                    # Observation exhaustion means the Playwright worker, not
                    # the ASP.NET postback, must be reconstructed. Return to
                    # the caller immediately so it cannot overwrite this
                    # stronger recovery with a same-runtime navigation yield.
                    return current, verification
                continue
            observation_count += 1
            current = observed
            verification = self._verify_next_navigation(
                action, before, current
            )
        if verification.verified:
            job.record(
                "slow_navigation_recovered",
                "A slow CEAC postback was verified without clicking Next again",
                actionId=action.id,
                toUrl=current.url,
                durationMs=max(
                    0, int((time.monotonic() - wait_started) * 1000)
                ),
                observationCount=observation_count,
            )
        return current, verification

    @staticmethod
    def _field_ids_from_errors(errors, allowed_field_ids):
        allowed = set(allowed_field_ids or ())
        matched = []
        has_unscoped = False
        for error in errors or ():
            value = str(error or "")
            marker = re.search(
                r"\[field_id=([A-Za-z0-9_.-]{1,200})\]",
                value,
            )
            if marker and marker.group(1) in allowed:
                if marker.group(1) not in matched:
                    matched.append(marker.group(1))
            else:
                has_unscoped = True
        return matched, has_unscoped

    @staticmethod
    def _field_label_summary(fields, field_ids):
        labels = []
        for field_id in field_ids or ():
            field = (fields or {}).get(field_id)
            label = str(getattr(field, "label", "") or "")
            label = re.sub(r"\s*\[control=.*$", "", label).strip()
            safe = label or str(field_id or "")
            safe = re.sub(r"\s+", " ", safe)[:100]
            if safe and safe not in labels:
                labels.append(safe)
            if len(labels) >= 3:
                break
        return "、".join(labels)

    @staticmethod
    def _safe_validation_summary(errors):
        """Keep useful CEAC wording while redacting likely submitted values."""
        summaries = []
        for raw_error in errors or ():
            value = re.sub(
                r"\[field_id=[A-Za-z0-9_.-]{1,200}\]",
                "",
                str(raw_error or ""),
            )
            value = re.sub(
                r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                "[已隐藏邮箱]",
                value,
            )
            value = re.sub(r"\b\d{5,}\b", "[已隐藏号码]", value)
            value = re.sub(r"\s+", " ", value).strip(" .;：:，,")[:180]
            if value and value not in summaries:
                summaries.append(value)
            if len(summaries) >= 2:
                break
        return "；".join(summaries)

    def _invalidate_browser_field(self, field_id):
        if not field_id:
            return
        invalidate = getattr(
            self.browser, "invalidate_field_binding", None
        )
        if callable(invalidate):
            try:
                invalidate(field_id)
            except Exception:
                pass

    @staticmethod
    def _descriptor_terms(raw_label, name):
        matched = re.search(
            rf"(?:\[|;\s*){re.escape(str(name))}=([^;\]]*)",
            str(raw_label or ""),
            flags=re.IGNORECASE,
        )
        if not matched:
            return ()
        return tuple(
            item.strip()
            for item in matched.group(1).split("|")
            if item.strip()
        )[:6]

    @classmethod
    def _refresh_after_change(cls, action, fields):
        if action.kind not in {
            ActionKind.TYPE,
            ActionKind.SELECT,
            ActionKind.CLICK,
        }:
            return False
        field = fields.get(action.field_id)
        return bool(
            field
            and re.search(
                r"(?:\[|;\s*)refresh_after_change=true(?:;|\])",
                str(field.label or ""),
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _validate_proposed_action(action):
        if not isinstance(action, ComputerAction):
            return "Computer-use model returned an invalid action object"
        try:
            action.kind = ActionKind(action.kind)
        except (TypeError, ValueError):
            return "Computer-use model returned an unknown action kind"
        limits = {
            "field_id": 200,
            "target_hint": 500,
            "value": 2048,
            "reason": 1000,
            "dispatch_receipt_scope": 200,
        }
        for attribute, limit in limits.items():
            value = getattr(action, attribute, "")
            if not isinstance(value, str):
                return f"Computer-use action {attribute} must be text"
            if len(value) > limit:
                return f"Computer-use action {attribute} is too long"
        if not isinstance(action.dispatch_receipt_required, bool):
            return "Computer-use dispatch receipt flag must be boolean"
        coordinates = (action.coordinate_x, action.coordinate_y)
        if any(value is not None for value in coordinates):
            if not all(
                isinstance(value, int) and not isinstance(value, bool)
                and 0 <= value <= 999
                for value in coordinates
            ):
                return "Computer-use coordinates must both be integers from 0 to 999"
        if action.kind == ActionKind.SCROLL:
            if action.scroll_direction not in {"up", "down", "left", "right"}:
                return "Computer-use scroll direction is invalid"
            if (
                isinstance(action.scroll_amount, bool)
                or not isinstance(action.scroll_amount, int)
                or not 1 <= action.scroll_amount <= 2000
            ):
                return "Computer-use scroll amount is invalid"
        return ""

    @staticmethod
    def _pause_requires_human(reason):
        """Separate real page boundaries from recoverable model parse output."""
        value = str(reason or "")
        return any(re.search(pattern, value, re.IGNORECASE) for pattern in (
            r"\bcaptcha\b",
            r"\b(?:login|log in|sign in)\b",
            r"\b(?:password|credential|one[- ]time code|verification code)\b",
            r"\b(?:electronic signature|sign and submit|final submit)\b",
            r"\b(?:payment|visa fee)\b",
            r"\bprompt injection\b",
            r"\buntrusted\b",
            r"\bsafety decision:\s*block\b",
            r"\b(?:start|create|retrieve)\b.{0,40}\bapplication\b",
        ))

    def _resolve_pending(
        self,
        job,
        observation,
        current_page_plan_id="",
    ):
        if job.pending_action is None:
            return None
        action = job.pending_action
        if action.id in job.applied_action_ids:
            job.pending_action = None
            return True
        if self._is_next_action(action):
            # A persisted Next click may be recovered only from actual page
            # advancement.  Browser acknowledgement alone is insufficient:
            # CEAC can acknowledge the click while retaining the same page
            # because a validation error blocked navigation.
            transition_proven = self._navigation_transition_proven(
                job.last_safe_url,
                observation.url,
                before_page_plan_id=job.current_page_plan_id,
                after_page_plan_id=current_page_plan_id,
            )
            if transition_proven:
                self._mark_applied(job, action)
                clear_page_state = getattr(
                    self.browser,
                    "clear_page_state",
                    None,
                )
                if callable(clear_page_state):
                    try:
                        clear_page_state()
                    except Exception as error:
                        job.record(
                            "page_state_cleanup_unavailable",
                            "Previous-page browser selectors could not be "
                            "cleared after recovered navigation",
                            errorType=type(error).__name__,
                        )
                job.record(
                    "page_navigation_recovered",
                    "Pending Next action was recovered on the following page",
                    actionId=action.id,
                    fromPagePlanId=job.current_page_plan_id,
                    toPagePlanId=current_page_plan_id,
                    toUrl=observation.url,
                )
                return True
            if self._pending_next_authoritatively_not_dispatched(
                action,
                observation,
            ):
                # The checkpoint was written before the browser side effect.
                # The matching, authoritative browser ledger proves that this
                # action token never reached a click event, so replanning the
                # fixed Next control is safe.  A missing/mismatched ledger is
                # deliberately treated as uncertain and is never re-clicked.
                job.pending_action = None
                if (
                    job.sync_reconciliation_field_ids
                    and not job.sync_reconciliation_page_plan_id
                ):
                    # Authoritative non-dispatch proves that this live page is
                    # still the source page, even if a legacy checkpoint lacked
                    # current_page_plan_id.
                    job.sync_reconciliation_page_plan_id = str(
                        current_page_plan_id or ""
                    )
                job.record(
                    "pending_next_not_dispatched",
                    "The browser dispatch ledger proved that the prepared Next "
                    "action never reached the page; the fixed control may be "
                    "safely planned again",
                    actionId=action.id,
                    receiptScope=action.dispatch_receipt_scope,
                )
                return True
            return False
        if (
            action.kind == ActionKind.CLICK
            and str(action.reason or "").startswith(
                "Deterministic repeater ensure "
            )
        ):
            # A repeater can comprise several clicks.  A process may stop after
            # any one of them, before the in-memory acknowledgement is appended.
            # Re-read the live record count from the persisted action descriptor
            # before deciding whether the action completed or must be replanned.
            scoped = self._observe_with_retries(
                job,
                purpose="pending-repeater-recovery",
                action=action,
                attempts=3,
            )
            if scoped is None:
                return False
            observation = scoped
        current = self._apply_browser_action_postcondition(
            action,
            self.verifier.verify_current(action, observation),
        )
        if not current.verified:
            repeater_count = self.verifier._repeater_count(
                action,
                observation,
            )
            if repeater_count is not None:
                actual, expected = repeater_count
                if actual < expected:
                    self._invalidate_browser_field(action.field_id)
                    job.pending_action = None
                    _previous_failures, failure_count = (
                        self._record_visual_failure(
                            job,
                            (
                                current_page_plan_id
                                or job.current_page_plan_id
                            ),
                            action.field_id,
                            failure_kind="pending_repeater_growth",
                        )
                    )
                    job.record(
                        "pending_repeater_replanned",
                        "The restored page proved the dynamic section was only "
                        "partially advanced; a new monotonic ensure action will "
                        "continue from the live count without replaying earlier "
                        "clicks",
                        actionId=action.id,
                        fieldId=action.field_id,
                        liveCount=actual,
                        expectedCount=expected,
                        failureCount=failure_count,
                        limit=self.VISUAL_FIELD_FAILURE_LIMIT,
                    )
                    if (
                        failure_count
                        >= self.VISUAL_FIELD_FAILURE_LIMIT
                    ):
                        self._wait_human(
                            job,
                            "Add Another 连续三次未增加表格行；"
                            "V2 已停止继续点击并关闭自动唤醒。",
                            wait_kind="manual_hard_boundary",
                        )
                        return False
                    return True
            if (
                action.kind in {ActionKind.TYPE, ActionKind.SELECT}
                and action.field_id
            ):
                # Value writes are idempotent.  If a restart happened before
                # the write, the live DOM proves it is absent; discard only the
                # stale binding/action token and let the normal planner bind the
                # current control.  This is categorically different from Next
                # or repeater clicks, whose outcome must never be guessed.
                self._invalidate_browser_field(action.field_id)
                job.pending_action = None
                job.record(
                    "pending_value_action_replanned",
                    "A persisted value action was not present in the restored "
                    "DOM and will be safely rebound",
                    actionId=action.id,
                    fieldId=action.field_id,
                )
                return True
            return False
        self._mark_applied(job, action)
        job.record(
            "pending_action_recovered",
            "Pending action was verified after resume without repeating it",
            actionId=action.id,
        )
        return True

    def _sync_reconciliation_boundary(self, job, current_page_plan_id):
        """Keep synchronized values honest across every already-visited page."""
        field_ids = list(dict.fromkeys(
            str(item)
            for item in job.sync_reconciliation_field_ids or ()
            if str(item)
        ))
        if not field_ids:
            return "", ""
        # Resolve the persisted Next first.  Its dispatch receipt/route proof is
        # the only authority on whether the browser is still on page A.
        if self._is_next_action(job.pending_action):
            return "", ""
        current = str(current_page_plan_id or "").strip()
        confirmed = set(job.confirmed_field_map())
        missing = sorted(set(field_ids).difference(confirmed))
        if missing:
            return (
                "DocFlow 已删除或取消确认本页先前写入的字段；系统不能保留 "
                "CEAC 中的旧值后继续 Next。请重新确认或明确替换这些字段："
                + ", ".join(missing),
                "manual_hard_boundary",
            )

        legacy_target = str(
            job.sync_reconciliation_page_plan_id or ""
        ).strip()
        targets = {
            str(field_id): str(page_plan_id or "").strip()
            for field_id, page_plan_id in dict(
                job.sync_reconciliation_page_plan_by_field or {}
            ).items()
            if str(field_id)
        }
        for field_id in field_ids:
            targets.setdefault(field_id, legacy_target)
        unknown = sorted(
            field_id for field_id in field_ids
            if not targets.get(field_id)
        )
        if unknown:
            return (
                "同步字段的来源页标识缺失，来自无法证明所属页面的旧检查点；"
                "系统不会在未知"
                "页面保留 CEAC 旧值后继续。请重新确认这些字段："
                + ", ".join(unknown),
                "manual_hard_boundary",
            )

        current_plan = next(
            (
                plan for plan in self.page_plans.plans
                if str(plan.id) == current
            ),
            None,
        )
        def targets_current_page(field_id):
            target = targets.get(field_id)
            return bool(
                target == current
                or self.page_plans.equivalent_for_field(
                    target,
                    current,
                    field_id,
                )
            )

        current_fields = [
            field_id for field_id in field_ids
            if targets_current_page(field_id)
        ]
        unsupported = sorted(
            field_id for field_id in current_fields
            if current_plan is None
            or not current_plan.allows_field(field_id)
        )
        if unsupported:
            return (
                "同步字段与其代码所有的 DS-160 页面计划不一致；系统已阻止 "
                "Next，避免把字段填到错误控件："
                + ", ".join(unsupported),
                "manual_hard_boundary",
            )

        completed = set(job.completed_field_ids)
        reconciled_here = [
            field_id for field_id in current_fields
            if field_id in completed
        ]
        if reconciled_here:
            reconciled = set(reconciled_here)
            job.sync_reconciliation_field_ids = [
                field_id for field_id in field_ids
                if field_id not in reconciled
            ]
            for field_id in reconciled:
                targets.pop(field_id, None)
            job.sync_reconciliation_page_plan_by_field = targets
            job.record(
                "synchronized_fields_reconciled",
                "Changed synchronized fields were reverified on their owning "
                "page before navigation continued",
                fieldIds=reconciled_here,
                pagePlanId=current,
            )
            remaining_targets = {
                targets.get(field_id, "")
                for field_id in job.sync_reconciliation_field_ids
            }
            job.sync_reconciliation_page_plan_id = (
                next(iter(remaining_targets))
                if len(remaining_targets) == 1
                else ""
            )
            self._save(job)
            field_ids = list(job.sync_reconciliation_field_ids)
            if not field_ids:
                job.sync_reconciliation_page_plan_id = ""
                job.sync_reconciliation_page_plan_by_field = {}
                self._save(job)
                return "", ""

        # If none of the remaining dirty fields belongs to this live page,
        # Next must not proceed.  The watcher resumes only after the operator
        # returns to one of the explicitly named owning pages.
        if not any(
            targets_current_page(field_id)
            for field_id in field_ids
        ):
            target_pages = sorted({
                targets.get(field_id, "") for field_id in field_ids
                if targets.get(field_id, "")
            })
            return (
                "DocFlow 更新了已写入其他 DS-160 页面中的字段；当前页不能"
                "证明这些新值已应用，也没有把同步后的新值伪报为已应用。"
                "请返回页面 "
                + ", ".join(target_pages)
                + "，页面变化后 Gemini 会自动重填并继续。",
                "manual_page_change",
            )
        return "", ""

    @staticmethod
    def _is_next_action(action):
        if action is None:
            return False
        try:
            kind = ActionKind(action.kind)
        except (TypeError, ValueError):
            return False
        if kind != ActionKind.CLICK:
            return False
        return bool(
            str(action.reason or "")
            == "Deterministic fixed CEAC Next control"
            or str(action.target_hint or "").strip().lower().startswith("next")
        )

    @staticmethod
    def _is_repeater_action(action):
        if action is None:
            return False
        try:
            kind = ActionKind(action.kind)
        except (TypeError, ValueError):
            return False
        return bool(
            kind == ActionKind.CLICK
            and str(action.reason or "").startswith(
                "Deterministic repeater ensure "
            )
        )

    @staticmethod
    def _is_repeater_field_action(action, fields):
        """Return whether the approved field owns an ensure-repeater action.

        Model-authored visual actions do not yet carry the deterministic
        repeater reason used by ``_is_repeater_action``; that reason is added
        only during semantic binding immediately before execution.  Ordering
        therefore has to use the system-owned field descriptor instead of
        trusting the model reason or coordinates.
        """
        field_id = str(getattr(action, "field_id", "") or "")
        approved = dict(fields or {}).get(field_id)
        return bool(
            approved
            and re.search(
                r"(?:\[|;\s*)control=ensure_repeater(?:;|\])",
                str(getattr(approved, "label", "") or ""),
                flags=re.IGNORECASE,
            )
        )

    @classmethod
    def _defer_repeater_actions(cls, actions, fields):
        """Keep stable value order while placing structural postbacks last."""
        ordinary = []
        repeaters = []
        for action in list(actions or ()):
            target = (
                repeaters
                if cls._is_repeater_field_action(action, fields)
                else ordinary
            )
            target.append(action)
        return [*ordinary, *repeaters]

    def _apply_browser_action_postcondition(self, action, verification):
        """Combine value verification with a browser-owned dependent guard.

        Most fields are complete when their exact live value matches.  A small
        number of legacy WebForms controllers additionally own required child
        controls.  Browser adapters can expose that stronger postcondition
        without teaching the generic verifier about site-specific DOM.
        """
        if not verification.verified:
            return verification
        hook = getattr(self.browser, "action_postcondition", None)
        if not callable(hook):
            return verification
        try:
            outcome = hook(action)
        except Exception as error:
            return VerificationResult(
                False,
                "Browser-dependent action postcondition could not be read "
                f"({type(error).__name__})",
            )
        if isinstance(outcome, tuple):
            passed = bool(outcome[0]) if outcome else False
            reason = str(outcome[1] or "") if len(outcome) > 1 else ""
        else:
            passed = bool(outcome)
            reason = ""
        if passed:
            return verification
        return VerificationResult(
            False,
            reason or "Browser-dependent action postcondition was not met",
        )

    def _browser_postcondition_requires_hard_boundary(self, action):
        hook = getattr(
            self.browser,
            "action_postcondition_requires_hard_boundary",
            None,
        )
        if not callable(hook):
            return False
        try:
            return bool(hook(action))
        except Exception:
            return False

    def _verify_next_navigation(self, action, before, after):
        """Require a real page transition after the deterministic Next click."""
        if after.errors:
            return VerificationResult(False, "网页报告了字段校验错误")
        if self.page_plans.terminal_reason(after):
            return VerificationResult(True)
        before_plan = self.page_plans.match(before)
        after_plan = self.page_plans.match(after)
        if self._navigation_transition_proven(
            before.url,
            after.url,
            before_page_plan_id=(
                before_plan.id if before_plan is not None else ""
            ),
            after_page_plan_id=(
                after_plan.id if after_plan is not None else ""
            ),
        ):
            return VerificationResult(True)
        return VerificationResult(
            False,
            "Next 点击已执行，但 CEAC 路由、node 和页面计划均未变化",
        )

    @staticmethod
    def _canonical_ceac_route(raw_url):
        """Return the code-owned CEAC page identity, ignoring query noise."""
        try:
            parsed = urlsplit(str(raw_url or ""))
        except (TypeError, ValueError):
            return ()
        if (
            parsed.scheme.casefold() != "https"
            or (parsed.hostname or "").casefold() != "ceac.state.gov"
        ):
            return ()
        path = re.sub(r"/+", "/", str(parsed.path or "")).rstrip("/")
        path = (path or "/").casefold()
        node = ""
        for key, value in parse_qsl(
            parsed.query,
            keep_blank_values=True,
        ):
            if str(key).casefold() == "node":
                node = str(value or "").strip().casefold()
                break
        return ("ceac.state.gov", path, node)

    @classmethod
    def _navigation_transition_proven(
        cls,
        before_url,
        after_url,
        *,
        before_page_plan_id="",
        after_page_plan_id="",
    ):
        """Prove navigation without trusting mutable titles or headings."""
        before_plan = str(before_page_plan_id or "").strip()
        after_plan = str(after_page_plan_id or "").strip()
        before_route = cls._canonical_ceac_route(before_url)
        after_route = cls._canonical_ceac_route(after_url)
        if before_route and after_route:
            # Canonical route/node is stronger than a registry match.  The same
            # URL can select a different overlapping legacy plan when a tooltip
            # changes the title, which is display noise rather than navigation.
            return before_route != after_route
        return bool(
            before_plan
            and after_plan
            and before_plan != after_plan
        )

    @staticmethod
    def _pending_next_authoritatively_not_dispatched(action, observation):
        scope = str(action.dispatch_receipt_scope or "").strip()
        observed_scope = str(
            observation.dispatch_receipt_scope or ""
        ).strip()
        if (
            not action.dispatch_receipt_required
            or not scope
            or observation.dispatch_receipt_conflict
            or not observation.dispatch_receipts_authoritative
            or observed_scope != scope
        ):
            return False
        dispatched = {
            str(item)
            for item in observation.dispatched_action_ids or ()
        }
        return str(action.id) not in dispatched

    @staticmethod
    def _pending_next_authoritatively_dispatched(action, observation):
        scope = str(action.dispatch_receipt_scope or "").strip()
        observed_scope = str(
            observation.dispatch_receipt_scope or ""
        ).strip()
        if (
            not action.dispatch_receipt_required
            or not scope
            or observation.dispatch_receipt_conflict
            or not observation.dispatch_receipts_authoritative
            or observed_scope != scope
        ):
            return False
        return str(action.id) in {
            str(item)
            for item in observation.dispatched_action_ids or ()
        }

    def _wait_for_dispatch_receipt_consistency(self, job, action):
        job.record(
            "dispatch_receipt_conflict",
            "Browser session and durable Next-dispatch ledgers disagree; "
            "neither copy was treated as authoritative and no Next click was "
            "repeated",
            actionId=str(getattr(action, "id", "") or ""),
            receiptScope=str(
                getattr(action, "dispatch_receipt_scope", "") or ""
            ),
        )
        return self._wait_human(
            job,
            "浏览器会话与持久化的 Next 派发回执发生冲突；系统未采用"
            "任一份记录，也未重复点击 Next。Gemini 已在一致性边界明确"
            "暂停，需恢复同一浏览器会话后再继续。",
            wait_kind="manual_hard_boundary",
        )

    @staticmethod
    def _visual_failure_key(page_plan_id, field_id):
        return f"{str(page_plan_id or '').strip()}::{str(field_id or '').strip()}"

    @classmethod
    def _visual_failure_count(cls, job, page_plan_id, field_id):
        key = cls._visual_failure_key(page_plan_id, field_id)
        try:
            return max(
                0,
                int((getattr(job, "visual_failure_counts", {}) or {}).get(
                    key,
                    0,
                )),
            )
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _record_visual_failure(
        cls,
        job,
        page_plan_id,
        field_id,
        *,
        failure_kind,
    ):
        """Increment one durable page/field budget and cap it at the limit."""
        key = cls._visual_failure_key(page_plan_id, field_id)
        counts = dict(getattr(job, "visual_failure_counts", {}) or {})
        previous = cls._visual_failure_count(
            job,
            page_plan_id,
            field_id,
        )
        current = min(cls.VISUAL_FIELD_FAILURE_LIMIT, previous + 1)
        counts[key] = current
        job.visual_failure_counts = counts
        job.record(
            "visual_failure_budget_incremented",
            "A failed visual binding or value verification consumed the "
            "durable repair budget for this exact page field",
            pagePlanId=str(page_plan_id or ""),
            fieldId=str(field_id or ""),
            failureKind=str(failure_kind or ""),
            previousCount=previous,
            failureCount=current,
            limit=cls.VISUAL_FIELD_FAILURE_LIMIT,
            exhausted=(current >= cls.VISUAL_FIELD_FAILURE_LIMIT),
        )
        return previous, current

    @classmethod
    def _clear_visual_failure_after_verified_value(
        cls,
        job,
        page_plan_id,
        action,
    ):
        # A focus click is not a value verification and must never reset the
        # budget.  TYPE/SELECT and the deterministic repeater ensure action are
        # the only field actions that _mark_applied may declare complete.
        verified_value = bool(
            action.kind in {ActionKind.TYPE, ActionKind.SELECT}
            or (
                action.kind == ActionKind.CLICK
                and str(action.reason or "").startswith(
                    "Deterministic repeater ensure "
                )
            )
        )
        if not verified_value or not action.field_id:
            return False
        key = cls._visual_failure_key(page_plan_id, action.field_id)
        counts = dict(getattr(job, "visual_failure_counts", {}) or {})
        if key not in counts:
            return False
        previous = cls._visual_failure_count(
            job,
            page_plan_id,
            action.field_id,
        )
        counts.pop(key, None)
        job.visual_failure_counts = counts
        job.record(
            "visual_failure_budget_cleared",
            "The durable visual repair budget was cleared only after exact "
            "browser value verification succeeded",
            pagePlanId=str(page_plan_id or ""),
            fieldId=str(action.field_id or ""),
            previousCount=previous,
            actionId=str(action.id or ""),
        )
        return True

    def _yield_exhausted_visual_rebind(
        self,
        job,
        observation,
        page_plan_id,
        field_ids,
        fields,
    ):
        exhausted = sorted({
            str(field_id)
            for field_id in field_ids
            if self._visual_failure_count(
                job,
                page_plan_id,
                field_id,
            ) >= self.VISUAL_FIELD_FAILURE_LIMIT
        })
        for field_id in exhausted:
            self._invalidate_browser_field(field_id)
        labels = [
            str(fields[field_id].label or field_id)
            .split("[control=", 1)[0]
            .strip()
            for field_id in exhausted
            if field_id in fields
        ]
        display = "、".join(item for item in labels[:3] if item)
        message = (
            f"字段 {display or '当前控件'} 连续三次未通过绑定或网页校验，"
            "已用完持久化视觉修复预算；"
            "Gemini 不再重复处理该字段。系统已丢弃旧绑定，"
            "将以低成本 DOM 语义观测和重绑定自动继续。"
        )
        job.record(
            "visual_failure_budget_exhausted",
            "Gemini was disabled for exhausted page fields before a fresh "
            "semantic DOM rebind attempt",
            pagePlanId=str(page_plan_id or ""),
            fieldIds=exhausted,
            limit=self.VISUAL_FIELD_FAILURE_LIMIT,
        )
        if job.continuous_run_requested:
            return self._schedule_progress_retry(
                job,
                observation,
                kind="progress_stall",
                message=message,
                event_kind="visual_semantic_rebind_retry_scheduled",
                base_delay=2,
            )
        return self._wait_human(
            job,
            message,
            wait_kind="manual_hard_boundary",
        )

    def _mark_applied(self, job, action):
        if action.id not in job.applied_action_ids:
            job.applied_action_ids.append(action.id)
        meaningful_progress = bool(
            action.kind in {ActionKind.TYPE, ActionKind.SELECT}
            or self._is_next_action(action)
            or (
                action.kind == ActionKind.CLICK
                and str(action.reason or "").startswith(
                    "Deterministic repeater ensure "
                )
            )
        )
        if meaningful_progress:
            self._clear_progress_retry(job)
        # A field click only focuses the control; it does not prove that the
        # approved value was written. Mark fields complete only after a value
        # action has passed deterministic DOM verification.
        completed_value_action = bool(
            action.kind in {ActionKind.TYPE, ActionKind.SELECT}
            or (
                action.kind == ActionKind.CLICK
                and str(action.reason or "").startswith(
                    "Deterministic repeater ensure "
                )
            )
        )
        if completed_value_action and action.field_id:
            if action.field_id not in job.completed_field_ids:
                job.completed_field_ids.append(action.field_id)
            page_plan_id = str(job.current_page_plan_id or "").strip()
            if page_plan_id:
                job.completed_field_page_plan_by_id[action.field_id] = (
                    page_plan_id
                )
        job.pending_action = None
        job.record(
            "action_verified",
            "Browser state verified by deterministic checks",
            actionId=action.id,
            action=action.kind.value,
            fieldId=action.field_id,
        )

    def _complete_if_allowed(
        self,
        job,
        observation,
        page_plan,
        fields,
        *,
        allow_terminal_completion=False,
    ):
        if not page_plan.allow_complete:
            return self._block(job, "Completion is not allowed on this page")
        required = set(job.required_field_ids)
        if not required:
            required = set(page_plan.required_field_ids).intersection(fields)
        current_page_fields = {
            field_id
            for field_id in fields
            if page_plan.allows_field(field_id)
        }
        expected = (required | current_page_fields).difference(
            job.inapplicable_field_ids
        )
        missing = sorted(expected.difference(job.completed_field_ids))
        if missing:
            return self._review_required(
                job,
                "System rejected premature completion; required fields remain: "
                + ", ".join(missing),
            )
        if self.policy.observation_has_errors(observation):
            return self._review_required(
                job, "System rejected completion because page errors are visible"
            )
        if job.pending_action is not None:
            return self._review_required(
                job, "System rejected completion while an action is pending"
            )
        if not allow_terminal_completion:
            job.record(
                "model_completion_deferred_to_route",
                "Gemini marked the current page complete; the system retained "
                "route ownership and will use the fixed Next control instead "
                "of ending the DS-160 run",
                pagePlanId=page_plan.id,
            )
            self._save(job)
            return None
        job.state = JobState.COMPLETED
        job.wait_kind = ""
        job.sync_resume_pending = False
        self._clear_automatic_retry(job)
        job.record("completed", "Computer-use job completed by system checks")
        self._visual_status(
            "completed",
            "所有已授权字段已完成，最终核对与提交仍需人工处理。",
        )
        self._save(job)
        return job

    def _stale_completed_page_fields(
        self,
        job,
        fields,
        page_field_ids,
        field_labels,
        control_hints,
        local_planner,
    ):
        """Return authoritative mismatches and inconclusive live controls.

        A field is stale only when the current DOM exposes a different value,
        or when a deterministic locator resolves the control but it is empty.
        Failure to reconstruct a model-selected locator after a legacy CEAC
        postback is merely inconclusive and must not erase a previously
        verified completion.
        """
        completed = [
            field_id for field_id in page_field_ids
            if field_id in job.completed_field_ids
        ]
        if not completed:
            return [], []
        try:
            observation = self.browser.observe_lightweight()
        except Exception:
            return [], completed
        stale = []
        inconclusive = []
        for field_id in completed:
            if "[control=ensure_repeater" in str(
                fields[field_id].label or ""
            ).lower():
                continue
            descriptor = str(fields[field_id].label or "")
            matched_kind = re.search(
                r"\[control=([a-z0-9_-]+)",
                descriptor,
                flags=re.IGNORECASE,
            )
            control_kind = (
                matched_kind.group(1).casefold()
                if matched_kind else "text"
            )
            if control_kind in {
                "checkbox", "does_not_apply", "do_not_know",
            }:
                # CEAC's legacy UpdatePanel can replace or rewire a checkbox
                # after an unrelated radio/select postback while leaving the
                # rendered checked state intact.  A field action that already
                # passed exact checked-state verification must not be reopened
                # from that later marker/selector snapshot: doing so caused a
                # checked Postal-Code D/N/A box to be toggled repeatedly and
                # finally paused the page.  Retain the verified completion and
                # let CEAC's own Next validation be the authoritative guard if
                # the checkbox is actually no longer accepted.
                inconclusive.append(field_id)
                continue
            action = ComputerAction(
                kind=(
                    ActionKind.SELECT
                    if control_kind in {
                        "select", "select_text", "yes_no", "checkbox",
                        "does_not_apply", "do_not_know", "date",
                        "duration", "text_segments",
                    }
                    else ActionKind.TYPE
                ),
                field_id=field_id,
                target_hint=field_id,
                value=fields[field_id].value,
            )
            verified = self._apply_browser_action_postcondition(
                action,
                self.verifier.verify_current(action, observation),
            )
            if verified.verified:
                continue
            actual_is_exposed = any(
                key and key in observation.control_values
                for key in (action.field_id, action.target_hint)
            )
            # Revalidation must never run the planner again. Planning can
            # replace the exact selector token captured during the verified
            # write with a heuristic match to a different, similarly-labelled
            # CEAC control after an ASP.NET partial postback. Only a value
            # still exposed through the original selector is authoritative.
            if actual_is_exposed:
                stale.append(field_id)
            else:
                inconclusive.append(field_id)
        return stale, inconclusive

    @staticmethod
    def _durable_revalidation_failure_count(
        job,
        page_plan_id,
        field_id,
    ):
        """Count prior refills for one page field across resumed run calls."""
        requested_page = str(page_plan_id or "")
        requested_field = str(field_id or "")
        count = 0
        for event in getattr(job, "events", ()) or ():
            if str(getattr(event, "kind", "") or "") != (
                "page_revalidation_failed"
            ):
                continue
            detail = dict(getattr(event, "detail", {}) or {})
            event_page = str(detail.get("pagePlanId") or "")
            if event_page and event_page != requested_page:
                continue
            if requested_field in {
                str(item) for item in detail.get("fieldIds", ()) or ()
            }:
                count += 1
        return count

    def _should_classify_field_presence(self, visual_loop):
        """Whether live conditional-branch scope should be refreshed.

        Legacy execution classified branch presence only for its visual model
        loop.  Subclasses with a semantic-first browser may opt in while the
        default preserves the existing V1 planning contract.
        """
        return bool(visual_loop)

    def _wait_human(self, job, reason, wait_kind=""):
        self._clear_automatic_retry(job)
        job.state = JobState.WAITING_HUMAN
        job.human_checkpoint = reason
        resolved_wait_kind = (
            str(wait_kind or "").strip()
            or (
                "manual_page_change"
                if job.continuous_run_requested
                else "manual_hard_boundary"
            )
        )
        job.wait_kind = resolved_wait_kind
        if resolved_wait_kind == "manual_hard_boundary":
            # Hard consistency boundaries are not page-change waits.  They
            # must disarm the durable one-click intent so no watcher, restart,
            # or generic runtime recovery can silently cross them.
            job.continuous_run_requested = False
            job.sync_resume_pending = False
        job.record("human_checkpoint", reason)
        self._visual_status("paused", reason)
        self._save(job)
        return job

    @staticmethod
    def _discard_terminal_pending(job, terminal_kind):
        pending = job.pending_action
        if pending is None:
            return
        job.pending_action = None
        job.record(
            "terminal_pending_action_discarded",
            "A pending browser action was closed when the job entered a "
            "terminal state and was not claimed as applied",
            actionId=str(getattr(pending, "id", "") or ""),
            terminalKind=str(terminal_kind or ""),
        )

    @staticmethod
    def _missing_required_field_ids(job):
        """Return the durable required/completed truth for terminal decisions."""
        required = {
            str(field_id)
            for field_id in job.required_field_ids or ()
            if str(field_id)
        }
        completed = {
            str(field_id)
            for field_id in job.completed_field_ids or ()
            if str(field_id)
        }
        inapplicable = {
            str(field_id)
            for field_id in job.inapplicable_field_ids or ()
            if str(field_id)
        }
        return sorted(required.difference(completed | inapplicable))

    def _review_incomplete(self, job, reason, missing_required=None):
        """Stop at Review/Sign without falsely declaring a complete run.

        Review/Sign remains an uncrossable submission boundary, but reaching
        that URL is not evidence that every mapped field was actually written.
        Keep the job and its values available for diagnosis/resume instead of
        entering the terminal ``review_required`` state that DocFlow redacts.
        """
        missing_required = list(
            missing_required
            if missing_required is not None
            else self._missing_required_field_ids(job)
        )
        self._clear_automatic_retry(job)
        self._discard_terminal_pending(job, "review_incomplete")
        job.state = JobState.WAITING_HUMAN
        job.continuous_run_requested = False
        job.wait_kind = "manual_hard_boundary"
        job.sync_resume_pending = False
        job.human_checkpoint = reason
        job.record(
            "review_incomplete",
            reason,
            missingRequiredFieldIds=missing_required,
            completionComplete=False,
            finalSubmissionBoundaryReached=bool(
                job.final_submission_boundary_reached
            ),
        )
        self._visual_status("paused", reason)
        self._save(job)
        return job

    def _review_required(self, job, reason):
        self._clear_automatic_retry(job)
        self._discard_terminal_pending(job, "review_required")
        job.state = JobState.REVIEW_REQUIRED
        job.wait_kind = ""
        job.sync_resume_pending = False
        job.human_checkpoint = reason
        missing_required = self._missing_required_field_ids(job)
        job.record(
            "review_required",
            reason,
            missingRequiredFieldIds=missing_required,
            completionComplete=not missing_required,
            finalSubmissionBoundaryReached=bool(
                job.final_submission_boundary_reached
            ),
        )
        self._visual_status("paused", reason)
        self._save(job)
        return job

    def _block(self, job, reason):
        self._clear_automatic_retry(job)
        self._discard_terminal_pending(job, "blocked")
        job.state = JobState.BLOCKED
        job.continuous_run_requested = False
        job.wait_kind = ""
        job.sync_resume_pending = False
        job.human_checkpoint = reason
        job.record("blocked", reason)
        self._visual_status("blocked", reason)
        self._save(job)
        return job

    def _fail(self, job, reason, error_type=""):
        self._clear_automatic_retry(job)
        self._discard_terminal_pending(job, "failed")
        job.state = JobState.FAILED
        job.continuous_run_requested = False
        job.wait_kind = ""
        job.sync_resume_pending = False
        job.human_checkpoint = reason
        detail = {"errorType": error_type} if error_type else {}
        job.record("failed", reason, **detail)
        self._visual_status("error", reason)
        self._save(job)
        return job

    def _cancel_requested(self):
        if self.cancellation_check is None:
            return False
        try:
            return bool(self.cancellation_check())
        except ExecutionLeaseRevoked:
            raise
        except Exception:
            return False

    def _cancel(self, job):
        self._clear_automatic_retry(job)
        job.state = JobState.CANCELLED
        job.wait_kind = ""
        job.sync_resume_pending = False
        job.human_checkpoint = None
        job.pending_action = None
        job.record(
            "cancelled",
            "Computer-use job stopped by an explicit cancellation request",
        )
        self._visual_status("paused", "任务已停止")
        self._save(job)
        return job

    @staticmethod
    def _is_retryable_provider_exhaustion(error):
        """Distinguish a spent transient provider budget from hard failures."""
        return bool(
            getattr(error, "provider_retry_exhausted", False)
            and getattr(error, "retryable", True) is not False
        )

    @staticmethod
    def _provider_rejection_checkpoint(reason_code):
        messages = {
            "unsupported_location": (
                "Gemini 当前网络出口地区不受支持；网页和已完成字段已保留。"
                "请切换到 Gemini 支持的网络地区后，再点击“继续 Gemini”。"
            ),
            "invalid_credentials": (
                "Gemini 凭据被拒绝；网页和已完成字段已保留。"
                "请修复 API 密钥后，再点击“继续 Gemini”。"
            ),
            "model_unavailable": (
                "当前配置的 Gemini 模型不可用；网页和已完成字段已保留。"
                "请修复模型配置后，再点击“继续 Gemini”。"
            ),
        }
        return messages.get(
            str(reason_code or ""),
            "Gemini 请求被配置或权限限制拒绝；网页和已完成字段已保留。"
            "请修复配置后，再点击“继续 Gemini”。",
        )

    @staticmethod
    def _clear_automatic_retry(job, record_event=False):
        had_retry = bool(
            job.automatic_retry_pending
            or job.automatic_retry_after
            or job.automatic_retry_count
            or job.automatic_retry_kind
        )
        job.automatic_retry_pending = False
        job.automatic_retry_after = ""
        job.automatic_retry_count = 0
        job.automatic_retry_kind = ""
        job.automatic_retry_preserves_page_boundary = False
        if had_retry and record_event:
            job.record(
                "automatic_retry_cleared",
                "Verified workflow progress cleared the automatic retry backoff",
            )

    def _schedule_automatic_retry(self, job, observation, error):
        """Persist a bounded provider backoff without turning it into a click."""
        retry_count = (
            max(0, int(job.automatic_retry_count or 0))
            if job.automatic_retry_kind in {"", "provider"}
            else 0
        ) + 1
        # Each Gemini call already owns a bounded primary/recovery request
        # budget.  A short 5/10/20/30-second process-level backoff prevents a
        # provider outage from becoming either a tight loop or a second-click
        # requirement.  The cap also fits inside the visible status lease.
        retry_delay = min(
            30,
            5 * (2 ** min(max(0, retry_count - 1), 3)),
        )
        retry_after = (
            datetime.now(timezone.utc) + timedelta(seconds=retry_delay)
        ).isoformat()
        job.automatic_retry_pending = True
        job.automatic_retry_after = retry_after
        job.automatic_retry_count = retry_count
        job.automatic_retry_kind = "provider"
        job.automatic_retry_preserves_page_boundary = False
        job.state = JobState.WAITING_HUMAN
        job.wait_kind = "automatic_retry"
        job.wait_boundary_fingerprint = observation_fingerprint(
            job,
            observation,
        )
        job.human_checkpoint = (
            "Gemini 服务本轮未在时限内返回；系统将在 "
            f"{retry_delay} 秒后自动重试，网页保持不动，无需再次点击。"
        )
        job.record(
            "automatic_retry_scheduled",
            job.human_checkpoint,
            retryCount=retry_count,
            retryDelaySeconds=retry_delay,
            retryAfter=retry_after,
            errorType=type(error).__name__,
        )
        self._visual_status("thinking", job.human_checkpoint)
        self._save(job)
        return job

    @staticmethod
    def _browser_retry_is_pending(job):
        return bool(
            job.automatic_retry_pending
            and job.automatic_retry_kind == "browser"
        )

    def _schedule_browser_retry(self, job, purpose, error):
        """Retire and reconstruct a failed browser without replaying actions.

        The workflow cannot safely distinguish an observation transport loss
        from a closed page after its bounded read attempts are exhausted.  In
        a durable one-click run both have the same safe recovery: checkpoint
        the exact pending action, stop this worker, reopen the job-owned
        private profile and resolve that action against fresh live DOM.
        """
        retry_count = (
            max(0, int(job.automatic_retry_count or 0))
            if job.automatic_retry_kind == "browser"
            else 0
        ) + 1
        retry_delay = min(
            30,
            2 * (2 ** min(max(0, retry_count - 1), 4)),
        )
        retry_after = (
            datetime.now(timezone.utc) + timedelta(seconds=retry_delay)
        ).isoformat()
        job.automatic_retry_pending = True
        job.automatic_retry_after = retry_after
        job.automatic_retry_count = retry_count
        job.automatic_retry_kind = "browser"
        job.automatic_retry_preserves_page_boundary = False
        job.state = JobState.WAITING_HUMAN
        job.wait_kind = "runtime_recovery"
        job.human_checkpoint = (
            "浏览器连接本轮未能恢复；系统将保留当前动作并在 "
            f"{retry_delay} 秒后重建专用浏览器，无需再次点击运行。"
        )
        job.record(
            "browser_runtime_retry_scheduled",
            job.human_checkpoint,
            retryCount=retry_count,
            retryDelaySeconds=retry_delay,
            retryAfter=retry_after,
            purpose=str(purpose or "")[:80],
            errorType=type(error).__name__,
            pendingActionPreserved=job.pending_action is not None,
            runtimeResetRequired=True,
        )
        self._visual_status("observing", job.human_checkpoint)
        self._save(job)
        return job

    @staticmethod
    def _clear_progress_retry(job):
        if job.automatic_retry_kind not in {
            "navigation_observation",
            "progress_stall",
            "step_budget_yield",
        }:
            return
        ComputerUseAgent._clear_automatic_retry(
            job,
            record_event=True,
        )

    def _schedule_progress_retry(
        self,
        job,
        observation,
        *,
        kind,
        message,
        event_kind,
        base_delay,
    ):
        """Yield recoverable page progress without turning it into human work.

        The service watcher owns the due-time resume.  A retry here never
        executes a browser action: for a pending Next it preserves the exact
        action token, and for a step/no-progress yield it replans only fields
        that have not already passed deterministic verification.
        """
        allowed_kinds = {
            "navigation_observation",
            "progress_stall",
            "step_budget_yield",
        }
        if kind not in allowed_kinds:
            raise ValueError("Unknown progress retry kind")
        previous_kind = str(job.automatic_retry_kind or "")
        retry_count = (
            max(0, int(job.automatic_retry_count or 0))
            if previous_kind == kind
            else 0
        ) + 1
        if kind == "navigation_observation":
            # Observing a persisted Next receipt is read-only and cheap.  A
            # 30-second exponential backoff added up to 30 seconds of avoidable
            # idle time after CEAC had already changed pages.  Keep a fixed,
            # human-observable two-second cadence; the exact pending action is
            # still never clicked twice.
            retry_delay = min(2, max(1, int(base_delay)))
        else:
            retry_delay = min(
                30,
                max(1, int(base_delay))
                * (2 ** min(max(0, retry_count - 1), 4)),
            )
        retry_after = (
            datetime.now(timezone.utc) + timedelta(seconds=retry_delay)
        ).isoformat()
        job.automatic_retry_pending = True
        job.automatic_retry_after = retry_after
        job.automatic_retry_count = retry_count
        job.automatic_retry_kind = kind
        job.automatic_retry_preserves_page_boundary = False
        job.state = JobState.WAITING_HUMAN
        job.wait_kind = "automatic_retry"
        if observation is not None:
            job.wait_boundary_fingerprint = observation_fingerprint(
                job,
                observation,
            )
        job.human_checkpoint = (
            f"{message} 系统将在 {retry_delay} 秒后自动继续。"
        )
        job.record(
            event_kind,
            job.human_checkpoint,
            retryKind=kind,
            retryCount=retry_count,
            retryDelaySeconds=retry_delay,
            retryAfter=retry_after,
            pendingActionPreserved=job.pending_action is not None,
        )
        visual_state = (
            "navigating"
            if kind == "navigation_observation"
            else "observing"
        )
        self._visual_status(visual_state, job.human_checkpoint)
        self._save(job)
        return job

    def _yield_step_budget(self, job, observation):
        message = (
            "本轮已达到安全步数预算；已验证字段和未决动作均已保存，"
            "续跑只处理剩余字段"
        )
        if job.continuous_run_requested:
            return self._schedule_progress_retry(
                job,
                observation,
                kind="step_budget_yield",
                message=message,
                event_kind="step_budget_retry_scheduled",
                base_delay=1,
            )
        return self._wait_human(
            job,
            message + "；再次运行会从当前页面继续。",
        )

    def _visual_status(self, state, message=""):
        setter = getattr(self.browser, "set_visual_status", None)
        if callable(setter):
            try:
                setter(state, message)
            except Exception:
                # Status chrome is observational only and must never change
                # the form-filling safety or recovery outcome.
                pass

    def _save(self, job):
        if self.checkpoint_store:
            self.checkpoint_store.save(job)
