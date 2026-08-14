"""Semantic-first Computer Use workflow with durable retry ceilings."""

from visa_agent.workflow import ComputerUseAgent
from visa_agent.models import ActionKind
from visa_agent.verification import VerificationResult


class FastComputerUseAgent(ComputerUseAgent):
    """Keep the legacy safety contract while removing unbounded hot loops."""

    # CEAC occasionally accepts Next immediately but completes its ASP.NET
    # postback well after the first bounded browser observation.  Keep the
    # exact dispatch receipt and extend only the read-only observation window
    # to roughly one minute.  Normal page changes still continue immediately,
    # and the original Next action can never be clicked a second time.
    NAVIGATION_OBSERVATION_LIMIT = 30
    PROGRESS_STALL_LIMIT = 5
    PROVIDER_RETRY_LIMIT = 3
    BRANCH_CONTROLLER_REOPEN_LIMIT = 2

    @staticmethod
    def _branch_controller_repair_limit(field_id, default_limit):
        """Give exact Travel leaf values room to survive prior bad runs.

        The two-attempt ceiling still protects real branch controllers.  Date
        and duration are exact-value leaf controls routed through the early
        stale audit only to prevent hidden-address fields from reaching
        Gemini; historical failures from older builds must not permanently
        block their deterministic refill.
        """
        if str(field_id or "").casefold().endswith((
            ".travel.arrivaldate",
            ".travel.stayduration",
        )):
            return max(int(default_limit or 1), 8)
        if str(field_id or "").casefold().endswith((
            ".us_contact.person.does_not_know",
            ".us_contact.organization",
            ".us_contact.relationship",
        )):
            # Older V2 builds could persist a checked/value-only completion
            # while CEAC omitted the mandatory address FormView.  A repaired
            # job may therefore already carry two stale-reopen events before
            # the trusted checkbox and placeholder-transition fix is loaded.
            # Keep the default budget everywhere else; allow exactly two
            # migration attempts for these three stable CEAC controllers.
            return max(int(default_limit or 1), 4)
        return default_limit

    @staticmethod
    def _durable_revalidation_failure_count(job, page_plan_id, field_id):
        """Do not let pre-fix Address/Phone failures poison repaired jobs.

        Builds that treated a checked D/N/A phone control as an empty text
        input could exhaust the durable refill budget before the stable CEAC
        checkbox verifier existed.  Once the new exact audit has observed the
        field, count only failures recorded after its first exact observation.
        A genuinely failing repaired control therefore still reaches the
        ordinary bounded hard stop; only historical false failures are
        ignored.
        """
        normalized = str(field_id or "").casefold()
        if normalized.endswith((
            ".address_phone.contact.homeregion",
            ".address_phone.contact.homepostalcode",
            ".address_phone.contact.secondaryphone",
            ".address_phone.contact.workphone",
        )):
            marker_index = None
            events = list(getattr(job, "events", ()) or ())
            for index, event in enumerate(events):
                if str(getattr(event, "kind", "") or "") != (
                    "v2_address_phone_exact_controls_revalidated"
                ):
                    continue
                detail = dict(getattr(event, "detail", {}) or {})
                observed = {
                    str(item)
                    for item in (
                        list(detail.get("provedFieldIds", ()) or ())
                        + list(detail.get("resetFieldIds", ()) or ())
                    )
                }
                if str(field_id or "") in observed:
                    marker_index = index
                    break
            for index, event in enumerate(events):
                if str(getattr(event, "kind", "") or "") != (
                    "v2_address_phone_checkbox_desync_upgrade_reopened"
                ):
                    continue
                if str(field_id or "") in {
                    str(item)
                    for item in dict(
                        getattr(event, "detail", {}) or {}
                    ).get("fieldIds", ()) or ()
                }:
                    # This one-time marker belongs to a newer, bounded
                    # checked/hidden replay migration and supersedes the
                    # earlier exact-audit marker for retry accounting.
                    marker_index = index
                    break
            if marker_index is not None:
                requested_page = str(page_plan_id or "")
                requested_field = str(field_id or "")
                return sum(
                    1
                    for event in events[marker_index + 1:]
                    if str(getattr(event, "kind", "") or "")
                    == "page_revalidation_failed"
                    and (
                        not str(
                            dict(getattr(event, "detail", {}) or {}).get(
                                "pagePlanId"
                            )
                            or ""
                        )
                        or str(
                            dict(getattr(event, "detail", {}) or {}).get(
                                "pagePlanId"
                            )
                            or ""
                        ) == requested_page
                    )
                    and requested_field in {
                        str(item)
                        for item in dict(
                            getattr(event, "detail", {}) or {}
                        ).get("fieldIds", ()) or ()
                    }
                )
        return ComputerUseAgent._durable_revalidation_failure_count(
            job,
            page_plan_id,
            field_id,
        )

    def _should_classify_field_presence(self, _visual_loop):
        """V2's section-scoped DOM classifier is safe in hybrid mode."""
        return True

    def _resolve_pending(
        self,
        job,
        observation,
        current_page_plan_id="",
    ):
        """Bound restored Address/Phone value recovery across resumes.

        Generic value writes are normally safe to rebind indefinitely, but a
        CEAC D/N/A checkbox is represented by an empty disabled text input.
        If its exact checked/hidden proof cannot be observed, repeatedly
        rebuilding the same SELECT action cannot make progress.  Stop after a
        small durable budget so the browser runtime becomes inspectable and a
        single bad control cannot create a one-second auto-resume loop.
        """
        action = getattr(job, "pending_action", None)
        field_id = str(getattr(action, "field_id", "") or "")
        normalized = field_id.casefold()
        exact_phone = normalized.endswith((
            ".address_phone.contact.secondaryphone",
            ".address_phone.contact.workphone",
        ))
        if (
            action is not None
            and exact_phone
            and getattr(action, "kind", None) in {
                ActionKind.TYPE,
                ActionKind.SELECT,
            }
        ):
            replans = sum(
                1
                for event in list(getattr(job, "events", ()) or ())
                if str(getattr(event, "kind", "") or "")
                == "pending_value_action_replanned"
                and str(
                    dict(getattr(event, "detail", {}) or {}).get("fieldId")
                    or ""
                ) == field_id
            )
            if replans >= self.VISUAL_FIELD_FAILURE_LIMIT:
                self._invalidate_browser_field(field_id)
                job.pending_action = None
                job.record(
                    "v2_address_phone_pending_recovery_stalled",
                    "The exact Address/Phone D/N/A control did not expose a "
                    "stable checked/hidden proof within the recovery budget",
                    fieldId=field_id,
                    replanCount=replans,
                    limit=self.VISUAL_FIELD_FAILURE_LIMIT,
                )
                self._wait_human(
                    job,
                    "电话不适用复选框未返回稳定状态；V2 已停止重复操作并等待修复。",
                    wait_kind="manual_hard_boundary",
                )
                return False
        return super()._resolve_pending(
            job,
            observation,
            current_page_plan_id=current_page_plan_id,
        )

    @staticmethod
    def _field_ids_from_errors(errors, allowed_field_ids):
        """Map CEAC's late Family validation to its reviewed field.

        Some CEAC sessions omit the final other-relative radio group from the
        initial DOM, then render it only after Next returns a server-side
        validation error.  That error has no DocFlow marker, so the base
        workflow treats it as unscoped and stops.  The prompt text is exact
        and unique to the reviewed Family field, making this one mapping safe.
        """
        allowed = set(allowed_field_ids or ())
        dependent_id = "ceac.relatives.family.other_relatives_us"
        course_ids = [
            field_id for field_id in allowed
            if str(field_id).casefold().endswith(
                ".work.education.record.course.2893ea107dce"
            )
            or ".work.education.record.course." in str(
                field_id
            ).casefold()
        ]
        home_postal_ids = [
            field_id for field_id in allowed
            if str(field_id).casefold().endswith(
                ".address_phone.contact.homepostalcode"
            )
        ]
        matched = []
        has_unscoped = False
        for error in errors or ():
            value = str(error or "")
            normalized = " ".join(value.casefold().split())
            marker_match, marker_unscoped = (
                ComputerUseAgent._field_ids_from_errors(
                    [value],
                    allowed,
                )
            )
            if marker_match:
                for field_id in marker_match:
                    if field_id not in matched:
                        matched.append(field_id)
                continue
            if (
                dependent_id in allowed
                and "do you have any other relatives in the united states"
                in normalized
            ):
                if dependent_id not in matched:
                    matched.append(dependent_id)
                continue
            if (
                course_ids
                and "course of study has not been completed" in normalized
            ):
                for course_id in course_ids:
                    if course_id not in matched:
                        matched.append(course_id)
                continue
            if (
                home_postal_ids
                and "postal zone/zip code has not been completed"
                in normalized
            ):
                for postal_id in home_postal_ids:
                    if postal_id not in matched:
                        matched.append(postal_id)
                continue
            if (
                "please correct all areas in error as indicated below"
                in normalized
            ):
                # CEAC emits this generic summary alongside one or more
                # concrete errors.  It is not a second, unknown field.
                continue
            has_unscoped = has_unscoped or marker_unscoped
        return matched, has_unscoped

    def _apply_browser_action_postcondition(self, action, verification):
        """Let an exact live repaired control supersede stale CEAC errors.

        CEAC retains the previous Next validation summary after the newly
        revealed radio or reset D/N/A checkbox is selected.  The summary is
        cleared only by the next submit, so it cannot invalidate an exact
        checked-value proof for that one field.  Next itself remains strict
        and will still reject every error.
        """
        field_id = str(getattr(action, "field_id", "") or "").casefold()
        if (
            not verification.verified
            and any(
                field_id.endswith(suffix)
                for suffix in (
                    ".address_phone.contact.homeregion",
                    ".address_phone.contact.homepostalcode",
                    ".address_phone.contact.secondaryphone",
                    ".address_phone.contact.workphone",
                )
            )
            and verification.reason in {
                "Control value does not match approved value",
                "Browser did not expose the target control value",
                "Browser reported new or target-field validation errors",
            }
        ):
            prove = getattr(
                self.browser,
                "address_phone_exact_value_matches",
                None,
            )
            try:
                live_match = bool(
                    callable(prove)
                    and prove(action.field_id, action.value) is True
                )
            except Exception:
                live_match = False
            if live_match:
                verification = VerificationResult(True)
        if (
            not verification.verified
            and field_id.endswith(".family.other_relatives_us")
            and verification.reason
            == "Browser reported new or target-field validation errors"
        ):
            prove = getattr(
                self.browser,
                "family_other_relative_value_matches",
                None,
            )
            try:
                live_match = bool(callable(prove) and prove(action))
            except Exception:
                live_match = False
            if live_match:
                verification = VerificationResult(True)
        if (
            not verification.verified
            and field_id.endswith(
                ".address_phone.contact.homepostalcode"
            )
            and verification.reason
            == "Browser reported new or target-field validation errors"
        ):
            prove = getattr(
                self.browser,
                "address_phone_dna_value_matches",
                None,
            )
            try:
                live_match = bool(
                    callable(prove)
                    and prove(action.field_id, action.value) is True
                )
            except Exception:
                live_match = False
            if live_match:
                verification = VerificationResult(True)
        return super()._apply_browser_action_postcondition(
            action,
            verification,
        )

    def _stale_completed_branch_controller_fields(
        self,
        job,
        fields,
        page_field_ids,
        field_labels,
        _control_hints,
    ):
        """Reopen only controllers whose current live value is authoritative."""
        audit = getattr(
            self.browser,
            "stale_completed_branch_controller_fields",
            None,
        )
        if not callable(audit):
            return []
        completed = [
            field_id
            for field_id in page_field_ids
            if field_id in job.completed_field_ids
        ]
        values = {
            field_id: fields[field_id].value
            for field_id in completed
            if field_id in fields
        }
        return audit(completed, values, field_labels)

    def run(self, job):
        if not any(
            event.kind == "v2_execution_profile"
            for event in job.events
        ):
            job.record(
                "v2_execution_profile",
                "V2 semantic-first planning with visible browser execution",
                planningStrategy="semantic-first",
                interactionStyle="visible",
                navigationObservationLimit=self.NAVIGATION_OBSERVATION_LIMIT,
                progressStallLimit=self.PROGRESS_STALL_LIMIT,
                providerRetryLimit=self.PROVIDER_RETRY_LIMIT,
            )
        # ``hybrid`` is intentional. The browser may still be visual; this
        # flag controls planning ownership, not whether the user sees actions.
        self.execution_mode = "hybrid"
        return super().run(job)

    def _stale_completed_page_fields(
        self,
        job,
        fields,
        page_field_ids,
        field_labels,
        control_hints,
        local_planner,
    ):
        """Safely rebind V2's section-scoped Travel controls before checking.

        An older runtime could have verified a value against the wrong one of
        CEAC's repeated address labels.  The base workflow correctly refuses
        to reconstruct ambiguous legacy selectors.  V2's Travel selectors are
        now bounded by explicit U.S./payer section anchors, so they can expose
        the live value without mutating it and let the ordinary verifier repair
        stale values once.
        """
        rebind = getattr(
            self.browser,
            "rebind_page_fields_for_revalidation",
            None,
        )
        # Older V2 builds could bind CEAC's internal OTHER_RELATIVE control
        # id to the distinct reviewed ``other_relatives_us`` field while only
        # the immediate-relative prompt was visible.  Retire that legacy
        # completion when the exact dependent prompt cannot yet be proved,
        # but keep the field pending: CEAC reveals it shortly afterward with
        # client-side script and no reliable postback signal.
        dependent_family = getattr(
            self.browser,
            "_is_dependent_family_choice",
            None,
        )
        classify_presence = getattr(
            self.browser,
            "classify_field_presence",
            None,
        )
        dependent_completed = [
            field_id
            for field_id in page_field_ids
            if field_id in job.completed_field_ids
            and callable(dependent_family)
            and dependent_family(field_id)
        ]
        if dependent_completed and callable(classify_presence):
            try:
                presence = classify_presence(
                    dependent_completed,
                    field_labels,
                    control_hints,
                )
                present = {
                    str(field_id)
                    for field_id in dict(presence or {}).get("present", ())
                    if str(field_id) in set(dependent_completed)
                }
            except Exception:
                # A failed read cannot revoke a verified value.  Only the
                # successful Family classifier may identify the exact prompt
                # as not currently provable.
                present = set(dependent_completed)
            retired = set(dependent_completed).difference(present)
            if retired:
                job.completed_field_ids = [
                    field_id
                    for field_id in job.completed_field_ids
                    if field_id not in retired
                ]
                job.inapplicable_field_ids = sorted(
                    set(job.inapplicable_field_ids or ()).difference(retired)
                )
                job.record(
                    "v2_absent_completed_control_retired",
                    "A legacy Family completion was retired because its exact "
                    "dependent prompt could not yet be proved on the live page",
                    fieldIds=sorted(retired),
                )
        if callable(rebind):
            completed = [
                field_id
                for field_id in page_field_ids
                if field_id in job.completed_field_ids
            ]
            try:
                rebind(completed, field_labels)
            except Exception:
                pass
        stale, inconclusive = super()._stale_completed_page_fields(
            job,
            fields,
            page_field_ids,
            field_labels,
            control_hints,
            local_planner,
        )
        # Action-time Travel option selection already accepts CEAC wording
        # that differs from the reviewed label only by harmless connectors
        # (for example OR versus &).  The generic verifier is intentionally
        # stricter because it also verifies free-form text.  Reconcile only
        # the two exact Travel-purpose selects through a read-only browser
        # hook so page-wide revalidation cannot falsely reopen them.
        purpose_match = getattr(
            self.browser,
            "travel_purpose_matches_approved",
            None,
        )
        equivalent_purpose_fields = []
        if callable(purpose_match):
            candidates = list(dict.fromkeys([*stale, *inconclusive]))
            for field_id in candidates:
                normalized_id = str(field_id or "").casefold()
                if not normalized_id.endswith((
                    ".travel.purpose.primary",
                    ".travel.purpose.secondary",
                )):
                    continue
                approved = fields.get(field_id)
                try:
                    matches = bool(
                        approved is not None
                        and purpose_match(field_id, approved.value)
                    )
                except Exception:
                    matches = False
                if matches:
                    equivalent_purpose_fields.append(field_id)
        if equivalent_purpose_fields:
            equivalent = set(equivalent_purpose_fields)
            stale = [
                field_id for field_id in stale
                if field_id not in equivalent
            ]
            inconclusive = [
                field_id for field_id in inconclusive
                if field_id not in equivalent
            ]
            job.record(
                "v2_equivalent_travel_purpose_revalidation",
                "Travel purpose remained complete because the live CEAC "
                "option was equivalent to the reviewed wording",
                fieldIds=equivalent_purpose_fields,
            )

        # Pre-migration jobs can hold an institution category such as
        # ``Hotel Hotel Hostel`` while CEAC correctly stores the fixed
        # relationship option ``OTHER``.  Revalidate that single exact select
        # through the browser's narrow alias matcher so the repaired value is
        # not reopened as stale on the next observation.
        relationship_match = getattr(
            self.browser,
            "us_contact_relationship_matches_approved",
            None,
        )
        equivalent_relationship_fields = []
        if callable(relationship_match):
            candidates = list(dict.fromkeys([*stale, *inconclusive]))
            for field_id in candidates:
                if not str(field_id or "").casefold().endswith(
                    ".us_contact.relationship"
                ):
                    continue
                approved = fields.get(field_id)
                try:
                    matches = bool(
                        approved is not None
                        and relationship_match(field_id, approved.value)
                    )
                except Exception:
                    matches = False
                if matches:
                    equivalent_relationship_fields.append(field_id)
        if equivalent_relationship_fields:
            equivalent = set(equivalent_relationship_fields)
            stale = [
                field_id for field_id in stale
                if field_id not in equivalent
            ]
            inconclusive = [
                field_id for field_id in inconclusive
                if field_id not in equivalent
            ]
            job.record(
                "v2_equivalent_us_contact_relationship_revalidation",
                "U.S. Contact relationship remained complete because CEAC "
                "OTHER was equivalent to the legacy institution category",
                fieldIds=equivalent_relationship_fields,
            )

        # Address/Phone contains an ASP.NET D/N/A checkbox whose rendered
        # marker is replaced by later radio postbacks.  Unlike a generic
        # checkbox, this one retains a stable CEAC id, so an exact native
        # checked-state read can distinguish a harmless lost marker from a
        # real server-side reset.  Reopen only the latter; a matching live
        # checkbox is conclusively complete and must not be toggled again.
        address_phone_match = getattr(
            self.browser,
            "address_phone_exact_value_matches",
            None,
        )
        address_phone_proved = []
        address_phone_reset = []
        address_phone_desync_upgrade = []
        dna_state = getattr(
            self.browser,
            "address_phone_exact_dna_state",
            None,
        )
        if callable(address_phone_match):
            candidates = list(dict.fromkeys([*stale, *inconclusive]))
            exact_suffixes = (
                ".address_phone.contact.homeregion",
                ".address_phone.contact.homepostalcode",
                ".address_phone.contact.secondaryphone",
                ".address_phone.contact.workphone",
            )
            for field_id in candidates:
                if not str(field_id or "").casefold().endswith(exact_suffixes):
                    continue
                approved = fields.get(field_id)
                try:
                    matches = (
                        None if approved is None
                        else address_phone_match(field_id, approved.value)
                    )
                except Exception:
                    matches = None
                if matches is True:
                    address_phone_proved.append(field_id)
                elif matches is False:
                    address_phone_reset.append(field_id)
                    if (
                        callable(dna_state)
                        and str(field_id or "").casefold().endswith((
                            ".address_phone.contact.secondaryphone",
                            ".address_phone.contact.workphone",
                        ))
                    ):
                        try:
                            snapshot = dict(dna_state(field_id) or {})
                        except Exception:
                            snapshot = {}
                        hidden = " ".join(
                            str(snapshot.get("hiddenValue") or "")
                            .strip().upper().split()
                        )
                        contradictory = bool(
                            snapshot.get("found")
                            and snapshot.get("hiddenFound")
                            and (
                                (
                                    snapshot.get("checked")
                                    and hidden in {"N", "NO", "FALSE", "0"}
                                )
                                or (
                                    not snapshot.get("checked")
                                    and hidden in {"Y", "YES", "TRUE", "1"}
                                    and not snapshot.get("textDisabled")
                                )
                            )
                        )
                        already_migrated = any(
                            str(getattr(event, "kind", "") or "")
                            == "v2_address_phone_checkbox_desync_upgrade_reopened"
                            and str(field_id) in {
                                str(item)
                                for item in dict(
                                    getattr(event, "detail", {}) or {}
                                ).get("fieldIds", ()) or ()
                            }
                            for event in list(getattr(job, "events", ()) or ())
                        )
                        if contradictory and not already_migrated:
                            address_phone_desync_upgrade.append(field_id)
        if address_phone_desync_upgrade:
            job.record(
                "v2_address_phone_checkbox_desync_upgrade_reopened",
                "A pre-fix checked/hidden mismatch received one bounded "
                "off-on replay budget",
                fieldIds=address_phone_desync_upgrade,
            )
        if address_phone_proved or address_phone_reset:
            proved = set(address_phone_proved)
            audited = set([*address_phone_proved, *address_phone_reset])
            stale = [
                field_id for field_id in stale
                if field_id not in proved
            ]
            inconclusive = [
                field_id for field_id in inconclusive
                if field_id not in audited
            ]
            stale = list(dict.fromkeys([*stale, *address_phone_reset]))
            job.record(
                "v2_address_phone_exact_controls_revalidated",
                "Stable CEAC Address/Phone controls were read after "
                "Address/Phone postbacks",
                provedFieldIds=address_phone_proved,
                resetFieldIds=address_phone_reset,
            )
        # Passport, Family and Present Work are identity-data boundaries.
        # Never let a
        # selector that could not be reconstructed count as a successful
        # page-wide audit and unlock Next.  This is especially important after
        # CEAC has rendered a red validation summary: browser form restoration
        # can leave values visible while the preceding POST did not contain
        # the complete control set.  Moving inconclusive controls back into
        # the normal refill path forces a fresh semantic bind and an exact
        # live-value verification before a new Next action is eligible.
        strict = [
            field_id
            for field_id in inconclusive
            if not any(
                f"[control={kind}" in " ".join(
                    str(value or "")
                    for value in field_labels.get(field_id, ())
                ).casefold()
                for kind in (
                    "checkbox", "does_not_apply", "do_not_know",
                )
            )
            if any(
                token in str(field_id or "").casefold()
                for token in (
                    ".passport.",
                    ".relatives.",
                    ".work_education1.",
                )
            )
        ]
        if strict:
            strict_set = set(strict)
            stale = list(dict.fromkeys([*stale, *strict]))
            inconclusive = [
                field_id for field_id in inconclusive
                if field_id not in strict_set
            ]
            job.record(
                "v2_sensitive_page_submit_gate_reaudit",
                "Sensitive-page Next remained locked because one or more "
                "live controls could not be conclusively rebound",
                fieldIds=strict,
            )

        # Last gate before deterministic Next: re-read every semantically
        # bound visible Yes/No group, including fields an earlier presence
        # probe called inapplicable.  A visible group with neither option
        # selected is reopened for the ordinary approved-value fill path; CEAC
        # must never be used as the first detector by clicking Next early.
        audit_choices = getattr(
            self.browser,
            "unanswered_visible_choice_fields",
            None,
        )
        unanswered = []
        if callable(audit_choices):
            try:
                unanswered = [
                    str(field_id)
                    for field_id in audit_choices(
                        page_field_ids,
                        field_labels,
                        control_hints,
                    )
                    if str(field_id) in set(page_field_ids)
                ]
            except Exception as error:
                job.record(
                    "v2_next_choice_preflight_unavailable",
                    "The read-only unanswered-choice audit was unavailable; "
                    "the existing verified-field gate remained in force",
                    errorType=type(error).__name__,
                )
        if unanswered:
            reopened = set(unanswered)
            stale = list(dict.fromkeys([*stale, *unanswered]))
            inconclusive = [
                field_id for field_id in inconclusive
                if field_id not in reopened
            ]
            job.inapplicable_field_ids = sorted(
                set(job.inapplicable_field_ids or ()).difference(reopened)
            )
            job.record(
                "v2_next_blocked_by_unanswered_choices",
                "Next remained locked because an exact visible approved "
                "radio group had no selected option",
                fieldIds=unanswered,
            )
        return stale, inconclusive

    def _schedule_automatic_retry(self, job, observation, error):
        previous_count = (
            max(0, int(job.automatic_retry_count or 0))
            if job.automatic_retry_kind in {"", "provider"}
            else 0
        )
        if previous_count >= self.PROVIDER_RETRY_LIMIT:
            job.record(
                "v2_provider_retry_exhausted",
                "Gemini fallback reached its durable retry ceiling",
                retryCount=previous_count,
                retryLimit=self.PROVIDER_RETRY_LIMIT,
                errorType=type(error).__name__,
            )
            return self._wait_human(
                job,
                "Gemini 连续三轮未返回可用的兜底定位结果；V2 已停止继续"
                "消耗请求。网页和已验证字段保持不变，请检查网络或人工处理"
                "当前未解析控件。",
                wait_kind="manual_hard_boundary",
            )
        return super()._schedule_automatic_retry(job, observation, error)

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
        previous_count = (
            max(0, int(job.automatic_retry_count or 0))
            if job.automatic_retry_kind == kind
            else 0
        )
        limit = (
            self.NAVIGATION_OBSERVATION_LIMIT
            if kind == "navigation_observation"
            else self.PROGRESS_STALL_LIMIT
            if kind == "progress_stall"
            else None
        )
        if limit is not None and previous_count >= limit:
            terminal_event = (
                "v2_navigation_observation_exhausted"
                if kind == "navigation_observation"
                else "v2_progress_stall_exhausted"
            )
            job.record(
                terminal_event,
                "V2 stopped a durable retry loop at its configured ceiling",
                retryKind=kind,
                retryCount=previous_count,
                retryLimit=limit,
                pendingActionPreserved=job.pending_action is not None,
            )
            if kind == "navigation_observation":
                reason = (
                    "Next 已派发，但在有界观察期内页面路由、node 和页面计划"
                    "始终没有变化。V2 已停止自动轮询并保留派发凭据，绝不会"
                    "重复点击 Next。请检查当前页未被识别的网页提示。"
                )
            else:
                reason = (
                    "当前页面连续多轮没有新增已验证字段。V2 已停止自动"
                    "重试，避免空转；请检查未映射必填控件或页面提示。"
                )
            return self._wait_human(
                job,
                reason,
                wait_kind="manual_hard_boundary",
            )
        return super()._schedule_progress_retry(
            job,
            observation,
            kind=kind,
            message=message,
            event_kind=event_kind,
            base_delay=base_delay,
        )
