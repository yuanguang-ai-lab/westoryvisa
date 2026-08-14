import re
import unittest
from collections import Counter

from visa_agent.models import (
    ActionKind,
    AgentJob,
    BrowserObservation,
    ComputerAction,
    ExtractedField,
    JobState,
    RiskLevel,
)
from visa_agent.workflow import ComputerUseAgent


def confirmed(field_id, value, label):
    return ExtractedField(
        id=field_id,
        value=value,
        label=label,
        confirmed=True,
        risk_level=RiskLevel.HIGH,
    )


class GeminiPageBatchPlanner:
    """Return one value-free visual batch for each physical page state."""

    def __init__(self):
        self.calls = []
        self.page_context = {}

    def set_page_context(self, context):
        self.page_context = dict(context or {})

    def propose_actions(
        self,
        observation,
        _available_field_ids,
        completed_field_ids,
        page_field_ids,
    ):
        completed = set(completed_field_ids)
        pending = [
            field_id for field_id in page_field_ids
            if field_id not in completed
        ]
        # This conditional control is inserted only by the branch postback.
        if str(observation.page_id).endswith("dom-0"):
            pending = [
                field_id for field_id in pending
                if field_id != FullDs160MemoryBrowser.CONDITIONAL
            ]
        self.calls.append((
            str(observation.page_id),
            tuple(pending),
        ))
        actions = []
        for index, field_id in enumerate(pending, start=1):
            label = str(
                (self.page_context.get(field_id) or {}).get("label") or ""
            ).casefold()
            if "[control=ensure_repeater" in label:
                kind = ActionKind.CLICK
                reason = (
                    "Deterministic repeater ensure "
                    "[expected_count=2; current_count=1; "
                    "record_labels=Companion Surname]"
                )
            elif "[control=yes_no" in label:
                kind = ActionKind.SELECT
                reason = "Gemini screenshot page batch"
            else:
                kind = ActionKind.TYPE
                reason = "Gemini screenshot page batch"
            actions.append(ComputerAction(
                kind=kind,
                field_id=field_id,
                target_hint=field_id,
                reason=reason,
                coordinate_x=100 + index,
                coordinate_y=200 + index,
            ))
        return actions


class FullDs160MemoryBrowser:
    """A deterministic in-memory model of the 17 core DS-160 form pages.

    The state machine models the properties that matter to continuous
    computer use: ASP.NET same-URL postback, a repeated record group,
    occurrence-bound inputs, a segmented control, and an explicit Review
    boundary after every form page has advanced exactly once.
    """

    PAGES = (
        (
            "personal1",
            "complete_personal.aspx",
            "Personal1",
            "Personal Information 1",
        ),
        (
            "personal2",
            "complete_personalcont.aspx",
            "Personal2",
            "Personal Information 2",
        ),
        (
            "travel",
            "complete_travel.aspx",
            "Travel",
            "Travel Information",
        ),
        (
            "travel_companions",
            "complete_travelcompanions.aspx",
            "TravelCompanions",
            "Travel Companions Information",
        ),
        (
            "previous_us_travel",
            "complete_previousustravel.aspx",
            "PreviousUSTravel",
            "Previous U.S. Travel Information",
        ),
        (
            "address_phone",
            "complete_contact.aspx",
            "AddressPhone",
            "Address and Phone Information",
        ),
        (
            "passport",
            "complete_pptvisa.aspx",
            "PptVisa",
            "Passport Information",
        ),
        (
            "us_contact",
            "complete_uscontact.aspx",
            "USContact",
            "U.S. Point of Contact Information",
        ),
        (
            "relatives",
            "complete_family1.aspx",
            "Relatives",
            "Family Information: Relatives",
        ),
        (
            "work_education1",
            "complete_workeducation1.aspx",
            "WorkEducation1",
            "Present Work/Education/Training Information",
        ),
        (
            "work_education2",
            "complete_workeducation2.aspx",
            "WorkEducation2",
            "Previous Work/Education/Training Information",
        ),
        (
            "work_education3",
            "complete_workeducation3.aspx",
            "WorkEducation3",
            "Additional Work/Education/Training Information",
        ),
        *tuple(
            (
                f"security_background{part}",
                f"complete_securityandbackground{part}.aspx",
                f"SecurityandBackground{part}",
                f"Security and Background: Part {part}",
            )
            for part in range(1, 6)
        ),
    )

    BRANCH = "ceac.personal1.001.other_names.used"
    CONDITIONAL = "ceac.personal1.002.other_names.surname"
    BIRTH_DATE = "ceac.personal2.001.birth_date"
    TRAVEL_PURPOSE = "ceac.travel.001.purpose"
    REPEATER = "ceac.travel_companions.001.rows"
    COMPANION_ONE = "ceac.travel_companions.002.surname"
    COMPANION_TWO = "ceac.travel_companions.003.surname"
    PREVIOUS_TRAVEL = "ceac.previous_us_travel.001.has_visited"
    PHONE_SEGMENTS = "ceac.address_phone.001.home_phone"
    PASSPORT_NUMBER = "ceac.passport.001.number"
    CONTACT_ORGANIZATION = "ceac.us_contact.001.organization"
    FATHER_SURNAME = "ceac.relatives.001.father_surname"
    OCCUPATION = "ceac.work_education1.001.occupation"
    FORMER_EMPLOYER = "ceac.work_education2.001.employer"
    SPECIALIZED_SKILL = "ceac.work_education3.001.skill"
    SECURITY_FIELDS = tuple(
        f"ceac.security_background{part}.001.answer"
        for part in range(1, 6)
    )

    def __init__(self):
        self.pages = tuple(
            {
                "key": key,
                "url": (
                    "https://ceac.state.gov/GenNIV/General/complete/"
                    f"{filename}?node={node}"
                ),
                "title": title,
            }
            for key, filename, node, title in self.PAGES
        ) + ({
            "key": "review",
            "url": (
                "https://ceac.state.gov/GenNIV/General/Review/"
                "ReviewReview.aspx?node=ReviewReview"
            ),
            "title": "Review Application",
        },)
        self.page_index = 0
        self.dom_generation = 0
        self.values = {page["key"]: {} for page in self.pages}
        self.labels = {}
        self.acknowledged_ids = []
        self.executed_actions = []
        self.field_execution_counts = Counter()
        self.next_counts = Counter()
        self.postbacks = []
        self.settle_calls = []
        self.repeater_count = 1
        self.companion_occurrences = {}
        self.birth_date_parts = []
        self.phone_parts = []
        self.final_action_attempts = 0

    @property
    def current_page(self):
        return self.pages[self.page_index]

    def observe(self):
        return BrowserObservation(
            url=self.current_page["url"],
            title=self.current_page["title"],
            visible_text=self.current_page["title"],
            screenshot_ref=(
                f"memory://{self.current_page['key']}/{self.dom_generation}"
            ),
            page_id=(
                f"{self.current_page['key']}:dom-{self.dom_generation}"
            ),
            control_values=dict(self.values[self.current_page["key"]]),
            errors=[],
            acknowledged_action_ids=list(self.acknowledged_ids),
        )

    def observe_lightweight(self):
        return self.observe()

    def observe_action(self, _action):
        return self.observe()

    def plan_fields(self, field_ids, field_labels, _control_hints):
        self.labels.update({
            field_id: tuple(field_labels.get(field_id) or ())
            for field_id in field_ids
        })
        actions = []
        unresolved = []
        for field_id in field_ids:
            descriptor = " ".join(self.labels.get(field_id) or ())
            if "[control=yes_no" in descriptor.casefold():
                unresolved.append(field_id)
                continue
            if field_id == self.CONDITIONAL and self.dom_generation == 0:
                unresolved.append(field_id)
                continue
            if "[control=ensure_repeater" in descriptor.casefold():
                actions.append(ComputerAction(
                    kind=ActionKind.CLICK,
                    field_id=field_id,
                    target_hint="Add Another",
                    reason=(
                        "Deterministic repeater ensure "
                        "[expected_count=2; current_count=1; "
                        "record_labels=Companion Surname]"
                    ),
                ))
                continue
            actions.append(ComputerAction(
                kind=ActionKind.TYPE,
                field_id=field_id,
                target_hint=field_id,
                reason="Deterministic live DOM control binding",
            ))
        return actions, unresolved

    def plan_choice_fields(
        self,
        field_ids,
        field_labels=None,
        control_hints=None,
    ):
        del field_labels, control_hints
        return [
            ComputerAction(
                kind=ActionKind.SELECT,
                field_id=field_id,
                target_hint=field_id,
                reason="Deterministic CEAC yes/no group",
            )
            for field_id in field_ids
        ], []

    def bind_visual_field(self, action, labels=(), hints=()):
        del hints
        # The visual coordinate is only a hint. This fake mirrors production's
        # DOM-owned identity gate and preserves descriptors used by structured
        # controls and occurrence-bound fields.
        self.labels[action.field_id] = tuple(labels or ())
        return bool(action.field_id)

    def plan_next(self):
        if self.current_page["key"] == "review":
            raise AssertionError("Review must be terminal")
        return ComputerAction(
            kind=ActionKind.CLICK,
            target_hint=f"Next: {self.pages[self.page_index + 1]['title']}",
            reason="Deterministic fixed CEAC Next control",
        )

    def settle_after_dynamic_refresh(self, field_id, labels, hints):
        self.settle_calls.append((
            field_id,
            tuple(labels),
            tuple(hints),
            self.current_page["url"],
            self.dom_generation,
        ))
        return (
            field_id == self.BRANCH
            and self.current_page["key"] == "personal1"
            and self.dom_generation == 1
        )

    def clear_page_state(self):
        # Production clears locator caches here. The memory driver has no
        # locator objects, but exposing the hook exercises the same workflow.
        return None

    def execute(self, action):
        action_text = f"{action.target_hint} {action.reason}"
        if re.search(
            r"\b(?:sign|final submit)\b",
            action_text,
            flags=re.IGNORECASE,
        ):
            self.final_action_attempts += 1
        self.executed_actions.append(action)
        if action.field_id:
            self.field_execution_counts[action.field_id] += 1

        if (
            action.kind == ActionKind.CLICK
            and not action.field_id
            and action.target_hint.startswith("Next:")
        ):
            from_key = self.current_page["key"]
            self.next_counts[from_key] += 1
            self.acknowledged_ids.append(action.id)
            self.page_index += 1
            self.dom_generation = 0
            return

        page_values = self.values[self.current_page["key"]]
        if action.field_id == self.BRANCH:
            before_url = self.current_page["url"]
            page_values[action.field_id] = action.value
            self.acknowledged_ids.append(action.id)
            self.dom_generation += 1
            self.postbacks.append((
                before_url,
                self.current_page["url"],
                self.dom_generation,
            ))
            return

        if action.field_id == self.REPEATER:
            expected = re.search(
                r"expected_count=(\d+)",
                str(action.reason or ""),
            )
            self.repeater_count = int(expected.group(1)) if expected else 1
            self.acknowledged_ids.append(action.id)
            return

        descriptor = " ".join(self.labels.get(action.field_id) or ())
        occurrence = re.search(
            r"(?:\[|;)\s*occurrence=(\d+)(?=\s*(?:;|\]))",
            descriptor,
            flags=re.IGNORECASE,
        )
        if occurrence:
            self.companion_occurrences[int(occurrence.group(1))] = (
                action.value
            )
        if action.field_id == self.BIRTH_DATE:
            self.birth_date_parts = action.value.split("-")
        if action.field_id == self.PHONE_SEGMENTS:
            digits = re.sub(r"\D", "", action.value)
            self.phone_parts = [digits[:3], digits[3:6], digits[6:]]

        page_values[action.field_id] = action.value
        self.acknowledged_ids.append(action.id)


class FullDs160ContinuousRunTests(unittest.TestCase):
    def test_one_run_crosses_all_core_pages_and_stops_before_review(self):
        browser = FullDs160MemoryBrowser()
        model = GeminiPageBatchPlanner()
        fields = [
            confirmed(
                browser.BRANCH,
                "yes",
                "Have you ever used other names? "
                "[control=yes_no; refresh_after_change=true; "
                "control_hints=OTHER_NAMES]",
            ),
            confirmed(
                browser.CONDITIONAL,
                "CHEN",
                "Other Surnames "
                "[control=text; control_hints=OTHER_SURNAME]",
            ),
            confirmed(
                browser.BIRTH_DATE,
                "2004-10-29",
                "Date of Birth [control=date; control_hints=DOB]",
            ),
            confirmed(
                browser.TRAVEL_PURPOSE,
                "TEMP. BUSINESS OR PLEASURE VISITOR (B)",
                "Purpose of Trip to the U.S. "
                "[control=text; control_hints=TRAVEL_PURPOSE]",
            ),
            confirmed(
                browser.REPEATER,
                "2",
                "Add Another [control=ensure_repeater; expected_count=2; "
                "record_labels=Companion Surname]",
            ),
            confirmed(
                browser.COMPANION_ONE,
                "XIA",
                "Companion Surname [control=text; occurrence=1; "
                "control_hints=COMPANION_SURNAME]",
            ),
            confirmed(
                browser.COMPANION_TWO,
                "CHEN",
                "Companion Surname [control=text; occurrence=2; "
                "control_hints=COMPANION_SURNAME]",
            ),
            confirmed(
                browser.PREVIOUS_TRAVEL,
                "no",
                "Have you ever been in the U.S.? "
                "[control=yes_no; control_hints=PREVIOUS_US_TRAVEL]",
            ),
            confirmed(
                browser.PHONE_SEGMENTS,
                "4155550123",
                "Primary Phone Number [control=text_segments; "
                "control_hints=HOME_PHONE]",
            ),
            confirmed(
                browser.PASSPORT_NUMBER,
                "EM8139116",
                "Passport/Travel Document Number "
                "[control=text; control_hints=PPT_NUM]",
            ),
            confirmed(
                browser.CONTACT_ORGANIZATION,
                "EXAMPLE CONFERENCE CENTER",
                "Organization Name "
                "[control=text; control_hints=US_CONTACT_ORG]",
            ),
            confirmed(
                browser.FATHER_SURNAME,
                "XIA",
                "Father's Surnames "
                "[control=text; control_hints=FATHER_SURNAME]",
            ),
            confirmed(
                browser.OCCUPATION,
                "STUDENT",
                "Primary Occupation "
                "[control=text; control_hints=PRIMARY_OCCUPATION]",
            ),
            confirmed(
                browser.FORMER_EMPLOYER,
                "DOES NOT APPLY",
                "Employer Name "
                "[control=text; control_hints=PREVIOUS_EMPLOYER]",
            ),
            confirmed(
                browser.SPECIALIZED_SKILL,
                "DATA ANALYSIS",
                "Specialized Skills "
                "[control=text; control_hints=SPECIALIZED_SKILL]",
            ),
            *[
                confirmed(
                    field_id,
                    "no",
                    f"Security and Background Part {part} Question "
                    "[control=yes_no; "
                    f"control_hints=SECURITY_PART_{part}]",
                )
                for part, field_id in enumerate(
                    browser.SECURITY_FIELDS,
                    start=1,
                )
            ],
        ]
        job = AgentJob(
            fields=fields,
            start_url=browser.current_page["url"],
            required_field_ids=[field.id for field in fields],
            auto_next=True,
        )

        result = ComputerUseAgent(
            model,
            browser,
            max_steps=200,
            execution_mode="visual",
        ).run(job)

        self.assertEqual(result.state, JobState.REVIEW_REQUIRED)
        self.assertIn("最终签名和提交前停止", result.human_checkpoint)
        expected_page_states = ["personal1:dom-0", "personal1:dom-1"] + [
            f"{page[0]}:dom-0" for page in browser.PAGES[1:]
        ]
        self.assertEqual(
            [page_id for page_id, _pending in model.calls],
            expected_page_states,
        )
        self.assertTrue(all(pending for _page_id, pending in model.calls))
        self.assertEqual(
            sum(
                event.kind == "model_planning_started"
                for event in result.events
            ),
            len(expected_page_states),
        )
        self.assertEqual(
            sum(event.kind == "started" for event in result.events),
            1,
        )

        self.assertEqual(browser.current_page["key"], "review")
        self.assertEqual(len(browser.next_counts), 17)
        self.assertEqual(
            browser.next_counts,
            Counter({page[0]: 1 for page in browser.PAGES}),
        )
        self.assertEqual(
            sum(
                event.kind == "page_navigation_verified"
                for event in result.events
            ),
            17,
        )
        self.assertEqual(browser.final_action_attempts, 0)

        self.assertEqual(len(browser.postbacks), 1)
        before_url, after_url, generation = browser.postbacks[0]
        self.assertEqual(before_url, after_url)
        self.assertEqual(generation, 1)
        self.assertEqual(len(browser.settle_calls), 1)
        self.assertTrue(any(
            event.kind == "dynamic_refresh_replanned"
            for event in result.events
        ))

        self.assertEqual(browser.repeater_count, 2)
        self.assertEqual(
            browser.companion_occurrences,
            {1: "XIA", 2: "CHEN"},
        )
        self.assertEqual(browser.birth_date_parts, ["2004", "10", "29"])
        self.assertEqual(browser.phone_parts, ["415", "555", "0123"])

        expected_field_ids = {field.id for field in fields}
        self.assertEqual(set(result.completed_field_ids), expected_field_ids)
        self.assertEqual(
            browser.field_execution_counts,
            Counter({field_id: 1 for field_id in expected_field_ids}),
        )
        executed_ids = [action.id for action in browser.executed_actions]
        self.assertEqual(len(executed_ids), len(set(executed_ids)))
        self.assertFalse(any(
            event.kind == "duplicate_action_ignored"
            for event in result.events
        ))


if __name__ == "__main__":
    unittest.main()
