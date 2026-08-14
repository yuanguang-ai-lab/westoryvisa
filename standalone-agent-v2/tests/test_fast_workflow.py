import time
import unittest

from visa_agent.mocks import MockBrowserDriver
from visa_agent.models import (
    ActionKind,
    AgentJob,
    BrowserObservation,
    ComputerAction,
    ExtractedField,
    JobState,
)

from visa_agent_v2.workflow import FastComputerUseAgent
from visa_agent.verification import VerificationResult


PERSONAL_1 = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_personal.aspx?node=Personal1"
)
REVIEW = (
    "https://ceac.state.gov/GenNIV/General/Review/"
    "ReviewReview.aspx?node=ReviewReview"
)

CORE_PAGES = (
    (
        PERSONAL_1,
        "Personal Information 1",
        "personal.surname",
        "XIA",
    ),
    (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_personalcont.aspx?node=Personal2",
        "Personal Information 2",
        "personal.nationality",
        "CHINA",
    ),
    (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_travel.aspx?node=Travel",
        "Travel Information",
        "travel.purpose",
        "BUSINESS",
    ),
    (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_contact.aspx?node=AddressPhone",
        "Address and Phone Information",
        "contact.homeAddress",
        "1 TEST ROAD",
    ),
    (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_pptvisa.aspx?node=PptVisa",
        "Passport Information",
        "passport.number",
        "DEMO00001",
    ),
)


def confirmed_field(field_id, value="XIA"):
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
        raise AssertionError("Known semantic controls must not call Gemini")


class SemanticBrowser(MockBrowserDriver):
    def __init__(self, delayed_next=False):
        super().__init__(
            url=PERSONAL_1,
            title="Personal Information 1",
            visible_text="Personal Information 1",
        )
        self.delayed_next = bool(delayed_next)
        self.next_clicks = 0
        self.visual_mode = ""

    def set_execution_mode(self, mode):
        self.visual_mode = str(mode)

    def family_other_relative_value_matches(self, action):
        return (
            str(action.field_id).endswith(".family.other_relatives_us")
            and str(action.value).casefold() == "no"
        )

    def plan_fields(self, field_ids, _labels, _hints):
        return (
            [
                ComputerAction(
                    kind=ActionKind.TYPE,
                    field_id=field_id,
                    target_hint=field_id,
                    reason="V2 semantic control",
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

    def execute(self, action):
        if (
            action.kind == ActionKind.CLICK
            and action.target_hint.startswith("Next")
        ):
            self.executed.append(action)
            self.next_clicks += 1
            if not self.delayed_next:
                self.url = REVIEW
                self.title = "Review Application"
                self.visible_text = "Review Application"
            return
        super().execute(action)

    def observe(self):
        observed = super().observe()
        return BrowserObservation(
            url=observed.url,
            title=observed.title,
            visible_text=observed.visible_text,
            screenshot_ref=observed.screenshot_ref,
            page_id=self.url,
            control_values=observed.control_values,
            acknowledged_action_ids=observed.acknowledged_action_ids,
        )


class EquivalentTravelPurposeBrowser(SemanticBrowser):
    def travel_purpose_matches_approved(self, field_id, approved):
        return (
            str(field_id).endswith(".travel.purpose.secondary")
            and approved == "BUSINESS & TOURISM (TEMPORARY VISITOR) (B1/B2)"
        )


class EquivalentUsContactRelationshipBrowser(SemanticBrowser):
    def us_contact_relationship_matches_approved(self, field_id, approved):
        return (
            str(field_id).endswith(".us_contact.relationship")
            and approved == "Hotel Hotel Hostel"
        )


class ProviderExhaustedModel:
    def __init__(self):
        self.calls = 0

    def propose_actions(self, *_args):
        self.calls += 1
        error = TimeoutError("provider timeout")
        error.provider_retry_exhausted = True
        error.retryable = True
        raise error


class UnresolvedBrowser(SemanticBrowser):
    def plan_fields(self, field_ids, _labels, _hints):
        return [], list(field_ids)


class RequiredBranchBlockedBrowser(UnresolvedBrowser):
    def __init__(self):
        super().__init__()
        self.url = CORE_PAGES[2][0]
        self.title = "Travel Information"
        self.visible_text = self.title

    def model_fallback_block_reason(self, field_ids):
        if any(
            str(field_id).endswith("travel.purpose.secondary")
            for field_id in field_ids
        ):
            return "Travel required branch is still absent"
        return ""


class MissingTravelSecondaryPostconditionBrowser(SemanticBrowser):
    def __init__(self):
        super().__init__()
        self.url = CORE_PAGES[2][0]
        self.title = "Travel Information"
        self.visible_text = self.title

    def plan_fields(self, field_ids, _labels, _hints):
        return (
            [
                ComputerAction(
                    kind=ActionKind.SELECT,
                    field_id=field_id,
                    target_hint=field_id,
                    reason="Travel primary fixture",
                )
                for field_id in field_ids
            ],
            [],
        )

    def action_postcondition(self, action):
        if action.field_id.endswith("travel.purpose.primary"):
            return False, "Specify visa class did not appear"
        return True, ""

    def action_postcondition_requires_hard_boundary(self, action):
        return action.field_id.endswith("travel.purpose.primary")


class UnknownControllerPostbackBrowser(SemanticBrowser):
    def __init__(self):
        super().__init__()
        self.url = CORE_PAGES[2][0]
        self.title = "Travel Information"
        self.visible_text = self.title
        self.execution_count = 0

    def plan_fields(self, field_ids, _labels, _hints):
        return (
            [
                ComputerAction(
                    kind=ActionKind.SELECT,
                    field_id=field_id,
                    target_hint=field_id,
                    reason="Unknown controller POST fixture",
                )
                for field_id in field_ids
            ],
            [],
        )

    def execute(self, _action):
        self.execution_count += 1
        raise RuntimeError("controller request outcome unknown")

    @staticmethod
    def interrupted_action_retry_safe(_action, _error):
        return False


class ResetTravelControllerBrowser(SemanticBrowser):
    def __init__(self):
        super().__init__()
        self.url = CORE_PAGES[2][0]
        self.title = "Travel Information"
        self.visible_text = self.title
        self.secondary_is_reset = True

    def stale_completed_branch_controller_fields(
        self,
        field_ids,
        _field_values,
        _field_labels,
    ):
        secondary = "ceac.travel.travel.purpose.secondary"
        if self.secondary_is_reset and secondary in set(field_ids):
            return [secondary]
        return []

    def execute(self, action):
        if action.field_id.endswith("travel.purpose.secondary"):
            self.secondary_is_reset = False
        return super().execute(action)


class PersistentlyResetTravelControllerBrowser(ResetTravelControllerBrowser):
    def execute(self, action):
        # CEAC accepts the client-side value long enough for ordinary action
        # verification, then returns the controller to its placeholder.
        return SemanticBrowser.execute(self, action)


class LegacyFamilyAliasBrowser(SemanticBrowser):
    @staticmethod
    def _is_dependent_family_choice(field_id):
        return ".family.other_relatives_us" in str(field_id).casefold()

    def classify_field_presence(self, field_ids, *_args):
        return {
            "present": [],
            "absent": list(field_ids),
            "unresolved": [],
        }

    def rebind_page_fields_for_revalidation(self, *_args):
        return []


class UnansweredChoicePreflightBrowser(SemanticBrowser):
    def unanswered_visible_choice_fields(self, field_ids, *_args):
        return [
            field_id for field_id in field_ids
            if field_id.endswith("permanent_resident_other_country")
        ]


class AddressPhonePostalDnaBrowser(SemanticBrowser):
    def __init__(self, matches):
        super().__init__()
        self.url = CORE_PAGES[3][0]
        self.title = "Address and Phone Information"
        self.visible_text = self.title
        self.matches = bool(matches)

    def address_phone_dna_value_matches(self, _field_id, _approved):
        return self.matches

    def address_phone_exact_value_matches(self, _field_id, _approved):
        return self.matches


class AddressPhoneCheckboxDesyncBrowser(AddressPhonePostalDnaBrowser):
    def __init__(self):
        super().__init__(matches=False)

    def address_phone_exact_dna_state(self, _field_id):
        return {
            "found": True,
            "checked": True,
            "hiddenFound": True,
            "hiddenValue": "N",
            "textFound": True,
            "textDisabled": False,
        }


class MultiPageSemanticBrowser(SemanticBrowser):
    def __init__(self):
        super().__init__()
        self.pages = [
            (url, title)
            for url, title, _field_id, _value in CORE_PAGES
        ] + [(REVIEW, "Review Application")]
        self.page_index = 0
        self.url, self.title = self.pages[0]
        self.visible_text = self.title

    def execute(self, action):
        if (
            action.kind == ActionKind.CLICK
            and action.target_hint.startswith("Next")
        ):
            self.executed.append(action)
            self.next_clicks += 1
            self.page_index += 1
            self.url, self.title = self.pages[self.page_index]
            self.visible_text = self.title
            return
        MockBrowserDriver.execute(self, action)


class FastWorkflowTests(unittest.TestCase):
    def test_travel_leaf_repair_budget_does_not_raise_controller_budget(self):
        limit = FastComputerUseAgent._branch_controller_repair_limit

        self.assertEqual(
            limit("ceac.travel.travel.arrivaldate", 2), 8
        )
        self.assertEqual(
            limit("ceac.travel.travel.stayduration", 2), 8
        )
        self.assertEqual(
            limit("ceac.travel.travel.purpose.secondary", 2), 2
        )
        self.assertEqual(
            limit("ceac.us_contact.us_contact.relationship", 2), 4
        )
        self.assertEqual(
            limit(
                "ceac.us_contact.us_contact.person.does_not_know",
                2,
            ),
            4,
        )

    def test_unknown_controller_postback_is_never_replayed(self):
        primary = "ceac.travel.travel.purpose.primary"
        browser = UnknownControllerPostbackBrowser()
        job = AgentJob(
            fields=[confirmed_field(primary, "B")],
            start_url=CORE_PAGES[2][0],
            required_field_ids=[primary],
            continuous_run_requested=True,
        )

        result = FastComputerUseAgent(ModelMustNotRun(), browser).run(job)

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertNotEqual(result.wait_kind, "automatic_retry")
        self.assertFalse(result.continuous_run_requested)
        self.assertEqual(browser.execution_count, 1)
        self.assertFalse(any(
            event.kind == "browser_execution_replanned"
            for event in result.events
        ))

    def test_travel_primary_requires_secondary_before_completion(self):
        primary = "ceac.travel.travel.purpose.primary"
        model = ModelMustNotRun()
        browser = MissingTravelSecondaryPostconditionBrowser()
        job = AgentJob(
            fields=[confirmed_field(primary, "B")],
            start_url=CORE_PAGES[2][0],
            required_field_ids=[primary],
            continuous_run_requested=True,
        )

        result = FastComputerUseAgent(model, browser).run(job)

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertEqual(result.wait_kind, "manual_hard_boundary")
        self.assertNotIn(primary, result.completed_field_ids)
        self.assertIn("Specify visa class", result.human_checkpoint)
        self.assertEqual(model.calls, 0)

    def test_reset_travel_controller_reopens_before_hidden_dependants(self):
        secondary = "ceac.travel.travel.purpose.secondary"
        street = "ceac.travel.travel.usstreet1"
        model = ModelMustNotRun()
        browser = ResetTravelControllerBrowser()
        job = AgentJob(
            fields=[
                confirmed_field(secondary, "B1/B2"),
                confirmed_field(street, "1 TEST ROAD"),
            ],
            start_url=CORE_PAGES[2][0],
            required_field_ids=[secondary, street],
            completed_field_ids=[secondary],
            continuous_run_requested=True,
        )

        result = FastComputerUseAgent(model, browser).run(job)

        self.assertEqual(result.state, JobState.REVIEW_REQUIRED)
        self.assertEqual(model.calls, 0)
        self.assertIn(secondary, result.completed_field_ids)
        self.assertIn(street, result.completed_field_ids)
        self.assertTrue(any(
            event.kind == "stale_branch_controller_reopened"
            and secondary in event.detail.get("fieldIds", ())
            for event in result.events
        ))

    def test_persistent_travel_controller_reset_has_durable_ceiling(self):
        secondary = "ceac.travel.travel.purpose.secondary"
        street = "ceac.travel.travel.usstreet1"
        model = ModelMustNotRun()
        browser = PersistentlyResetTravelControllerBrowser()
        job = AgentJob(
            fields=[
                confirmed_field(secondary, "B1/B2"),
                confirmed_field(street, "1 TEST ROAD"),
            ],
            start_url=CORE_PAGES[2][0],
            required_field_ids=[secondary, street],
            completed_field_ids=[secondary],
            continuous_run_requested=True,
        )

        result = FastComputerUseAgent(model, browser).run(job)

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertEqual(result.wait_kind, "manual_hard_boundary")
        self.assertFalse(result.continuous_run_requested)
        self.assertEqual(model.calls, 0)
        self.assertEqual(sum(
            1
            for event in result.events
            if event.kind == "stale_branch_controller_reopened"
        ), 2)
        self.assertTrue(any(
            event.kind == "branch_controller_repair_exhausted"
            and secondary in event.detail.get("fieldIds", ())
            for event in result.events
        ))

    def test_missing_required_branch_never_falls_through_to_gemini(self):
        field_id = "ceac.travel.travel.purpose.secondary"
        model = ModelMustNotRun()
        browser = RequiredBranchBlockedBrowser()
        job = AgentJob(
            fields=[confirmed_field(field_id, "B1/B2")],
            start_url=CORE_PAGES[2][0],
            required_field_ids=[field_id],
            continuous_run_requested=True,
        )

        result = FastComputerUseAgent(model, browser).run(job)

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertEqual(result.wait_kind, "manual_hard_boundary")
        self.assertEqual(model.calls, 0)
        self.assertTrue(any(
            event.kind == "model_fallback_blocked_by_required_branch"
            for event in result.events
        ))

    def test_unanswered_visible_choice_is_reopened_before_next(self):
        field_id = (
            "ceac.personal2.personal.permanent_resident_other_country"
        )
        browser = UnansweredChoicePreflightBrowser()
        job = AgentJob(
            fields=[confirmed_field(field_id, "no")],
            start_url=CORE_PAGES[1][0],
            required_field_ids=[field_id],
            inapplicable_field_ids=[field_id],
        )
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        stale, inconclusive = agent._stale_completed_page_fields(
            job,
            job.confirmed_field_map(),
            [field_id],
            {field_id: (
                "Permanent resident other country [control=yes_no]",
            )},
            {},
            None,
        )

        self.assertEqual(stale, [field_id])
        self.assertEqual(inconclusive, [])
        self.assertNotIn(field_id, job.inapplicable_field_ids)
        self.assertTrue(any(
            event.kind == "v2_next_blocked_by_unanswered_choices"
            for event in job.events
        ))

    def test_reset_address_phone_postal_dna_is_reopened_before_next(self):
        field_id = "ceac.address_phone.contact.homepostalcode"
        browser = AddressPhonePostalDnaBrowser(matches=False)
        job = AgentJob(
            fields=[confirmed_field(field_id, "true")],
            start_url=CORE_PAGES[3][0],
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
        )
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        stale, inconclusive = agent._stale_completed_page_fields(
            job,
            job.confirmed_field_map(),
            [field_id],
            {field_id: (
                "Home Postal Zone/ZIP Code "
                "[control=does_not_apply]",
            )},
            {},
            None,
        )

        self.assertEqual(stale, [field_id])
        self.assertEqual(inconclusive, [])
        self.assertTrue(any(
            event.kind == "v2_address_phone_exact_controls_revalidated"
            and field_id in event.detail.get("resetFieldIds", ())
            for event in job.events
        ))

    def test_checked_address_phone_postal_dna_stays_complete(self):
        field_id = "ceac.address_phone.contact.homepostalcode"
        browser = AddressPhonePostalDnaBrowser(matches=True)
        job = AgentJob(
            fields=[confirmed_field(field_id, "true")],
            start_url=CORE_PAGES[3][0],
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
        )
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        stale, inconclusive = agent._stale_completed_page_fields(
            job,
            job.confirmed_field_map(),
            [field_id],
            {field_id: (
                "Home Postal Zone/ZIP Code "
                "[control=does_not_apply]",
            )},
            {},
            None,
        )

        self.assertEqual(stale, [])
        self.assertEqual(inconclusive, [])

    def test_checked_phone_dna_overrides_stale_generic_text_snapshot(self):
        field_id = "ceac.address_phone.contact.secondaryphone"
        browser = AddressPhonePostalDnaBrowser(matches=True)
        browser.control_values[field_id] = ""
        job = AgentJob(
            fields=[confirmed_field(field_id, "DNA")],
            start_url=CORE_PAGES[3][0],
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
        )
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        stale, inconclusive = agent._stale_completed_page_fields(
            job,
            job.confirmed_field_map(),
            [field_id],
            {field_id: ("Secondary Phone Number [control=text]",)},
            {},
            None,
        )

        self.assertEqual(stale, [])
        self.assertEqual(inconclusive, [])
        self.assertTrue(any(
            event.kind == "v2_address_phone_exact_controls_revalidated"
            and field_id in event.detail.get("provedFieldIds", ())
            for event in job.events
        ))

    def test_checked_n_phone_desync_records_one_upgrade_repair_budget(self):
        field_id = "ceac.address_phone.contact.secondaryphone"
        browser = AddressPhoneCheckboxDesyncBrowser()
        job = AgentJob(
            fields=[confirmed_field(field_id, "DNA")],
            start_url=CORE_PAGES[3][0],
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
        )
        for _index in range(4):
            job.record(
                "page_revalidation_failed",
                "pre-upgrade false mismatch",
                fieldIds=[field_id],
                pagePlanId="ceac-plan-address_phone",
            )
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        stale, _inconclusive = agent._stale_completed_page_fields(
            job,
            job.confirmed_field_map(),
            [field_id],
            {field_id: ("Secondary Phone Number [control=text]",)},
            {},
            None,
        )

        self.assertEqual(stale, [field_id])
        self.assertTrue(any(
            event.kind
            == "v2_address_phone_checkbox_desync_upgrade_reopened"
            and field_id in event.detail.get("fieldIds", ())
            for event in job.events
        ))
        self.assertEqual(
            agent._durable_revalidation_failure_count(
                job,
                "ceac-plan-address_phone",
                field_id,
            ),
            0,
        )

    def test_exact_phone_audit_retires_only_pre_fix_retry_failures(self):
        field_id = "ceac.address_phone.contact.workphone"
        page_plan_id = "ceac-plan-address_phone"
        job = AgentJob(
            fields=[confirmed_field(field_id, "DNA")],
            start_url=CORE_PAGES[3][0],
        )
        for _index in range(4):
            job.record(
                "page_revalidation_failed",
                "legacy false text mismatch",
                fieldIds=[field_id],
                pagePlanId=page_plan_id,
            )
        job.record(
            "v2_address_phone_exact_controls_revalidated",
            "exact checkbox audit loaded",
            provedFieldIds=[],
            resetFieldIds=[field_id],
        )

        self.assertEqual(
            FastComputerUseAgent._durable_revalidation_failure_count(
                job,
                page_plan_id,
                field_id,
            ),
            0,
        )
        job.record(
            "page_revalidation_failed",
            "new exact-control failure",
            fieldIds=[field_id],
            pagePlanId=page_plan_id,
        )
        self.assertEqual(
            FastComputerUseAgent._durable_revalidation_failure_count(
                job,
                page_plan_id,
                field_id,
            ),
            1,
        )
        job.record(
            "v2_address_phone_checkbox_desync_upgrade_reopened",
            "one bounded off-on migration",
            fieldIds=[field_id],
        )
        self.assertEqual(
            FastComputerUseAgent._durable_revalidation_failure_count(
                job,
                page_plan_id,
                field_id,
            ),
            0,
        )
        job.record(
            "page_revalidation_failed",
            "post-upgrade exact failure",
            fieldIds=[field_id],
            pagePlanId=page_plan_id,
        )
        self.assertEqual(
            FastComputerUseAgent._durable_revalidation_failure_count(
                job,
                page_plan_id,
                field_id,
            ),
            1,
        )

    def test_phone_pending_recovery_has_durable_loop_ceiling(self):
        field_id = "ceac.address_phone.contact.workphone"
        browser = AddressPhonePostalDnaBrowser(matches=False)
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            value="DNA",
        )
        job = AgentJob(
            fields=[confirmed_field(field_id, "DNA")],
            start_url=CORE_PAGES[3][0],
            pending_action=action,
        )
        for _index in range(FastComputerUseAgent.VISUAL_FIELD_FAILURE_LIMIT):
            job.record(
                "pending_value_action_replanned",
                "legacy recovery attempt",
                fieldId=field_id,
            )
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        resolved = agent._resolve_pending(
            job,
            browser.observe(),
            current_page_plan_id="ceac-plan-address_phone",
        )

        self.assertFalse(resolved)
        self.assertIsNone(job.pending_action)
        self.assertEqual(job.state, JobState.WAITING_HUMAN)
        self.assertEqual(job.wait_kind, "manual_hard_boundary")
        self.assertTrue(any(
            event.kind == "v2_address_phone_pending_recovery_stalled"
            for event in job.events
        ))

    def test_address_phone_postal_error_maps_to_reviewed_dna_field(self):
        field_id = "ceac.address_phone.contact.homepostalcode"

        matched, has_unscoped = FastComputerUseAgent._field_ids_from_errors(
            ["Postal Zone/ZIP Code has not been completed."],
            {field_id},
        )

        self.assertEqual(matched, [field_id])
        self.assertFalse(has_unscoped)

    def test_repaired_postal_dna_ignores_only_stale_submit_error(self):
        field_id = "ceac.address_phone.contact.homepostalcode"
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            AddressPhonePostalDnaBrowser(matches=True),
            execution_mode="hybrid",
        )
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            value="true",
        )

        result = agent._apply_browser_action_postcondition(
            action,
            VerificationResult(
                False,
                "Browser reported new or target-field validation errors",
            ),
        )

        self.assertTrue(result.verified)

    def test_repaired_phone_dna_accepts_exact_checkbox_hidden_proof(self):
        field_id = "ceac.address_phone.contact.secondaryphone"
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            AddressPhonePostalDnaBrowser(matches=True),
            execution_mode="hybrid",
        )
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            value="DNA",
        )

        result = agent._apply_browser_action_postcondition(
            action,
            VerificationResult(
                False,
                "Control value does not match approved value",
            ),
        )

        self.assertTrue(result.verified)

    def test_recovered_phone_dna_accepts_exact_proof_without_text_marker(self):
        field_id = "ceac.address_phone.contact.workphone"
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            AddressPhonePostalDnaBrowser(matches=True),
            execution_mode="hybrid",
        )
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            value="DNA",
        )

        result = agent._apply_browser_action_postcondition(
            action,
            VerificationResult(
                False,
                "Browser did not expose the target control value",
            ),
        )

        self.assertTrue(result.verified)

    def test_equivalent_travel_secondary_survives_page_revalidation(self):
        field_id = "ceac.travel.travel.purpose.secondary"
        approved = "BUSINESS & TOURISM (TEMPORARY VISITOR) (B1/B2)"
        browser = EquivalentTravelPurposeBrowser()
        browser.url = CORE_PAGES[2][0]
        browser.title = "Travel Information"
        browser.visible_text = browser.title
        job = AgentJob(
            fields=[confirmed_field(field_id, approved)],
            start_url=CORE_PAGES[2][0],
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
        )
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        stale, inconclusive = agent._stale_completed_page_fields(
            job,
            job.confirmed_field_map(),
            [field_id],
            {field_id: ("Specify [control=select_text]",)},
            {},
            None,
        )

        self.assertEqual(stale, [])
        self.assertEqual(inconclusive, [])
        self.assertTrue(any(
            event.kind == "v2_equivalent_travel_purpose_revalidation"
            for event in job.events
        ))

    def test_legacy_hotel_relationship_survives_other_revalidation(self):
        field_id = "ceac.us_contact.us_contact.relationship"
        approved = "Hotel Hotel Hostel"
        browser = EquivalentUsContactRelationshipBrowser()
        browser.url = (
            "https://ceac.state.gov/GenNIV/General/complete/"
            "complete_uscontact.aspx?node=USContact"
        )
        browser.title = "U.S. Point of Contact Information"
        browser.visible_text = browser.title
        job = AgentJob(
            fields=[confirmed_field(field_id, approved)],
            start_url=browser.url,
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
        )
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        stale, inconclusive = agent._stale_completed_page_fields(
            job,
            job.confirmed_field_map(),
            [field_id],
            {field_id: ("Relationship to You [control=select_text]",)},
            {},
            None,
        )

        self.assertEqual(stale, [])
        self.assertEqual(inconclusive, [])
        self.assertTrue(any(
            event.kind
            == "v2_equivalent_us_contact_relationship_revalidation"
            for event in job.events
        ))

    def test_passport_does_not_apply_survives_sensitive_page_reaudit(self):
        field_id = "ceac.passport.passport.book_number.does_not_apply"
        browser = SemanticBrowser()
        job = AgentJob(
            fields=[confirmed_field(field_id, "true")],
            start_url=CORE_PAGES[-1][0],
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
        )
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        stale, inconclusive = agent._stale_completed_page_fields(
            job,
            job.confirmed_field_map(),
            [field_id],
            {field_id: (
                "Passport Book Number "
                "[control=does_not_apply; human-approved value=true]",
            )},
            {},
            None,
        )

        self.assertEqual(stale, [])
        self.assertEqual(inconclusive, [field_id])
        self.assertFalse(any(
            event.kind == "v2_sensitive_page_submit_gate_reaudit"
            for event in job.events
        ))

    def test_passport_inconclusive_readback_keeps_next_locked(self):
        field_id = "ceac.passport.passport.issuedate"
        browser = SemanticBrowser()
        job = AgentJob(
            fields=[confirmed_field(field_id, "2024-07-12")],
            start_url=CORE_PAGES[-1][0],
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
        )
        fields = job.confirmed_field_map()
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        stale, inconclusive = agent._stale_completed_page_fields(
            job,
            fields,
            [field_id],
            {field_id: ("Issuance Date [control=date]",)},
            {},
            None,
        )

        self.assertEqual(stale, [field_id])
        self.assertEqual(inconclusive, [])
        self.assertTrue(any(
            event.kind == "v2_sensitive_page_submit_gate_reaudit"
            for event in job.events
        ))

    def test_family_inconclusive_readback_keeps_next_locked(self):
        field_id = "ceac.relatives.family.father.surname"
        browser = SemanticBrowser()
        job = AgentJob(
            fields=[confirmed_field(field_id, "XIA")],
            start_url=(
                "https://ceac.state.gov/GenNIV/General/complete/"
                "complete_family1.aspx?node=Relatives"
            ),
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
        )
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        stale, inconclusive = agent._stale_completed_page_fields(
            job,
            job.confirmed_field_map(),
            [field_id],
            {field_id: ("Father's Surnames [control=text]",)},
            {},
            None,
        )

        self.assertEqual(stale, [field_id])
        self.assertEqual(inconclusive, [])
        self.assertTrue(any(
            event.kind == "v2_sensitive_page_submit_gate_reaudit"
            for event in job.events
        ))

    def test_absent_legacy_other_relative_alias_no_longer_locks_next(self):
        field_id = "ceac.relatives.family.other_relatives_us"
        browser = LegacyFamilyAliasBrowser()
        job = AgentJob(
            fields=[confirmed_field(field_id, "no")],
            start_url=(
                "https://ceac.state.gov/GenNIV/General/complete/"
                "complete_family1.aspx?node=Relatives"
            ),
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
        )
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            browser,
            execution_mode="hybrid",
        )

        stale, inconclusive = agent._stale_completed_page_fields(
            job,
            job.confirmed_field_map(),
            [field_id],
            {field_id: ("Other Relatives [control=yes_no]",)},
            {},
            None,
        )

        self.assertEqual(stale, [])
        self.assertEqual(inconclusive, [])
        self.assertNotIn(field_id, job.completed_field_ids)
        self.assertNotIn(field_id, job.inapplicable_field_ids)
        self.assertTrue(any(
            event.kind == "v2_absent_completed_control_retired"
            for event in job.events
        ))

    def test_semantic_v2_classifies_conditional_scope_in_hybrid_mode(self):
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            SemanticBrowser(),
            execution_mode="hybrid",
        )

        self.assertTrue(
            agent._should_classify_field_presence(False)
        )

    def test_late_family_validation_reopens_exact_dependent_field(self):
        field_id = "ceac.relatives.family.other_relatives_us"
        errors = [
            "Please correct all areas in error as indicated below.",
            'The question "Do you have any other relatives in the United '
            'States?" has not been answered.',
        ]

        matched, has_unscoped = (
            FastComputerUseAgent._field_ids_from_errors(
                errors,
                {field_id},
            )
        )

        self.assertEqual(matched, [field_id])
        self.assertFalse(has_unscoped)

    def test_late_school_validation_reopens_exact_course_field(self):
        field_id = (
            "ceac.work_education2.work.education.record.course."
            "2893ea107dce"
        )
        errors = [
            "Please correct all areas in error as indicated below.",
            "Course of Study has not been completed.",
        ]

        matched, has_unscoped = (
            FastComputerUseAgent._field_ids_from_errors(
                errors,
                {field_id},
            )
        )

        self.assertEqual(matched, [field_id])
        self.assertFalse(has_unscoped)

    def test_late_family_choice_ignores_only_stale_submit_error(self):
        field_id = "ceac.relatives.family.other_relatives_us"
        agent = FastComputerUseAgent(
            ModelMustNotRun(),
            SemanticBrowser(),
            execution_mode="hybrid",
        )
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            value="no",
        )

        result = agent._apply_browser_action_postcondition(
            action,
            VerificationResult(
                False,
                "Browser reported new or target-field validation errors",
            ),
        )

        self.assertTrue(result.verified)

    def test_known_page_uses_semantic_fast_path_without_gemini(self):
        field_id = "personal.surname"
        model = ModelMustNotRun()
        browser = SemanticBrowser()
        job = AgentJob(
            fields=[confirmed_field(field_id)],
            start_url=PERSONAL_1,
            required_field_ids=[field_id],
            continuous_run_requested=True,
        )

        result = FastComputerUseAgent(
            model,
            browser,
            execution_mode="visual",
        ).run(job)

        self.assertEqual(result.state, JobState.REVIEW_REQUIRED)
        self.assertEqual(model.calls, 0)
        self.assertEqual(browser.next_clicks, 1)
        self.assertIn(field_id, result.completed_field_ids)
        self.assertTrue(any(
            event.kind == "v2_execution_profile"
            for event in result.events
        ))

    def test_dispatched_next_has_a_durable_retry_ceiling(self):
        field_id = "personal.surname"
        model = ModelMustNotRun()
        browser = SemanticBrowser(delayed_next=True)
        browser.control_values[field_id] = "XIA"
        job = AgentJob(
            fields=[confirmed_field(field_id)],
            start_url=PERSONAL_1,
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
            continuous_run_requested=True,
        )
        agent = FastComputerUseAgent(model, browser)

        result = agent.run(job)
        self.assertEqual(result.automatic_retry_count, 1)
        for expected in range(2, agent.NAVIGATION_OBSERVATION_LIMIT + 1):
            result = agent.run(result)
            self.assertEqual(result.automatic_retry_count, expected)
        result = agent.run(result)

        self.assertEqual(result.state, JobState.WAITING_HUMAN)
        self.assertEqual(result.wait_kind, "manual_hard_boundary")
        self.assertFalse(result.continuous_run_requested)
        self.assertEqual(browser.next_clicks, 1)
        self.assertIsNotNone(result.pending_action)
        self.assertTrue(any(
            event.kind == "v2_navigation_observation_exhausted"
            for event in result.events
        ))

    def test_slow_ceac_postback_remains_automatic_after_three_observations(self):
        field_id = "personal.surname"
        browser = SemanticBrowser(delayed_next=True)
        browser.control_values[field_id] = "XIA"
        job = AgentJob(
            fields=[confirmed_field(field_id)],
            start_url=PERSONAL_1,
            required_field_ids=[field_id],
            completed_field_ids=[field_id],
            continuous_run_requested=True,
        )
        agent = FastComputerUseAgent(ModelMustNotRun(), browser)

        result = agent.run(job)
        for _ in range(3):
            result = agent.run(result)

        self.assertEqual(result.wait_kind, "automatic_retry")
        self.assertTrue(result.continuous_run_requested)
        self.assertEqual(browser.next_clicks, 1)
        self.assertIsNotNone(result.pending_action)

        browser.url = REVIEW
        browser.title = "Review Application"
        browser.visible_text = "Review Application"
        result = agent.run(result)

        self.assertEqual(result.state, JobState.REVIEW_REQUIRED)
        self.assertEqual(browser.next_clicks, 1)
        self.assertIsNone(result.pending_action)

    def test_provider_retries_stop_after_three_durable_rounds(self):
        field_id = "personal.surname"
        model = ProviderExhaustedModel()
        browser = UnresolvedBrowser()
        job = AgentJob(
            fields=[confirmed_field(field_id)],
            start_url=PERSONAL_1,
            required_field_ids=[field_id],
            continuous_run_requested=True,
        )
        agent = FastComputerUseAgent(model, browser)

        result = agent.run(job)
        self.assertEqual(result.automatic_retry_count, 1)
        for expected in (2, 3):
            result = agent.run(result)
            self.assertEqual(result.automatic_retry_count, expected)
        result = agent.run(result)

        self.assertEqual(result.wait_kind, "manual_hard_boundary")
        self.assertFalse(result.continuous_run_requested)
        self.assertEqual(model.calls, 4)
        self.assertTrue(any(
            event.kind == "v2_provider_retry_exhausted"
            for event in result.events
        ))

    def test_fifty_core_flows_finish_without_model_or_duplicate_next(self):
        started = time.monotonic()
        for _run_number in range(50):
            model = ModelMustNotRun()
            browser = MultiPageSemanticBrowser()
            fields = [
                confirmed_field(field_id, value)
                for _url, _title, field_id, value in CORE_PAGES
            ]
            required = [field.id for field in fields]
            job = AgentJob(
                fields=fields,
                start_url=PERSONAL_1,
                required_field_ids=required,
                continuous_run_requested=True,
            )

            result = FastComputerUseAgent(model, browser).run(job)

            self.assertEqual(result.state, JobState.REVIEW_REQUIRED)
            self.assertEqual(set(result.completed_field_ids), set(required))
            self.assertEqual(model.calls, 0)
            self.assertEqual(browser.next_clicks, len(CORE_PAGES))
            next_actions = [
                action
                for action in browser.executed
                if (
                    action.kind == ActionKind.CLICK
                    and action.target_hint.startswith("Next")
                )
            ]
            self.assertEqual(len(next_actions), len(CORE_PAGES))
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
