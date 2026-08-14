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


def confirmed_field(field_id, value, label):
    return ExtractedField(
        id=field_id,
        value=value,
        label=label,
        confirmed=True,
        risk_level=RiskLevel.HIGH,
    )


class PageBatchParticipationModel:
    """Return one visual batch per physical page or postback generation."""

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
        if str(observation.page_id).endswith("dom-0"):
            pending = [
                field_id for field_id in pending
                if field_id != FourPagePostbackBrowser.CONDITIONAL_ID
            ]
        self.calls.append((str(observation.page_id), tuple(pending)))
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
                    "record_labels=Language Name]"
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


class FourPagePostbackBrowser:
    """Stable in-memory CEAC state machine for one continuous agent run.

    It deliberately keeps a completed radio group visible after a same-URL
    branch postback.  That recreates the production failure mode where a
    planner used to count all visible groups and either repeated the completed
    action or fell back to Gemini.
    """

    BRANCH_ID = "ceac.personal1.001.other_names.used"
    CONDITIONAL_ID = "ceac.personal1.002.other_names.native_name"
    REPEATER_ID = "ceac.personal2.001.languages.rows"
    OCCURRENCE_ONE_ID = "ceac.personal2.002.languages.name"
    OCCURRENCE_TWO_ID = "ceac.personal2.003.languages.name"
    SEGMENTS_ID = "ceac.address_phone.001.home_phone"
    FINAL_FORM_FIELD_ID = "ceac.work_education3.001.specialized_skill"

    def __init__(self):
        self.pages = (
            {
                "key": "personal1",
                "url": (
                    "https://ceac.state.gov/GenNIV/General/complete/"
                    "complete_personal.aspx?node=Personal1"
                ),
                "title": "Personal Information 1",
            },
            {
                "key": "personal2",
                "url": (
                    "https://ceac.state.gov/GenNIV/General/complete/"
                    "complete_personalcont.aspx?node=Personal2"
                ),
                "title": "Personal Information 2",
            },
            {
                "key": "address_phone",
                "url": (
                    "https://ceac.state.gov/GenNIV/General/complete/"
                    "complete_contact.aspx?node=AddressPhone"
                ),
                "title": "Address and Phone Information",
            },
            {
                "key": "work_education3",
                "url": (
                    "https://ceac.state.gov/GenNIV/General/complete/"
                    "complete_workeducation3.aspx?node=WorkEducation3"
                ),
                "title": "Additional Work, Education and Training",
            },
            {
                "key": "review",
                "url": (
                    "https://ceac.state.gov/GenNIV/General/Review/"
                    "ReviewReview.aspx?node=ReviewReview"
                ),
                "title": "Review Application",
            },
        )
        self.page_index = 0
        self.dom_generation = 0
        self.page_values = {
            page["key"]: {} for page in self.pages
        }
        self.acknowledged_action_ids = []
        self.executed_actions = []
        self.field_execution_counts = Counter()
        self.plan_generations = []
        self.settle_calls = []
        self.next_count = 0
        self.final_submit_attempts = 0
        self.radio_visible_after_postback = False
        self.repeater_count = 1
        self.occurrence_slots = {}
        self.segment_parts = []
        self.same_url_postbacks = []
        self._labels = {}

    @property
    def current_page(self):
        return self.pages[self.page_index]

    def observe(self):
        values = dict(self.page_values[self.current_page["key"]])
        if (
            self.current_page["key"] == "personal1"
            and self.dom_generation >= 1
            and self.BRANCH_ID in values
        ):
            self.radio_visible_after_postback = True
        return BrowserObservation(
            url=self.current_page["url"],
            title=self.current_page["title"],
            visible_text=self.current_page["title"],
            screenshot_ref=(
                f"mock://{self.current_page['key']}/{self.dom_generation}"
            ),
            page_id=(
                f"{self.current_page['key']}:dom-{self.dom_generation}"
            ),
            control_values=values,
            errors=[],
            acknowledged_action_ids=list(
                self.acknowledged_action_ids
            ),
        )

    def observe_lightweight(self):
        return self.observe()

    def plan_fields(self, field_ids, field_labels, _control_hints):
        self._labels.update({
            field_id: tuple(field_labels.get(field_id) or ())
            for field_id in field_ids
        })
        self.plan_generations.append((
            self.current_page["key"],
            self.dom_generation,
            tuple(field_ids),
        ))
        actions = []
        unresolved = []
        for field_id in field_ids:
            descriptor = " ".join(self._labels.get(field_id) or ())
            if "[control=yes_no" in descriptor.lower():
                unresolved.append(field_id)
                continue
            if (
                field_id == self.CONDITIONAL_ID
                and self.dom_generation == 0
            ):
                # The conditional control does not exist until the branch
                # radio triggers an UpdatePanel-style DOM replacement.
                unresolved.append(field_id)
                continue
            if "[control=ensure_repeater" in descriptor.lower():
                actions.append(ComputerAction(
                    kind=ActionKind.CLICK,
                    field_id=field_id,
                    target_hint="Add Another",
                    reason=(
                        "Deterministic repeater ensure "
                        "[expected_count=2; current_count=1; "
                        "record_labels=Language Name]"
                    ),
                ))
                continue
            actions.append(ComputerAction(
                kind=ActionKind.TYPE,
                field_id=field_id,
                target_hint=field_id,
                reason=(
                    "Deterministic synthetic DOM match "
                    f"[generation={self.dom_generation}]"
                ),
            ))
        return actions, unresolved

    def plan_choice_fields(
        self,
        field_ids,
        field_labels=None,
        control_hints=None,
    ):
        del field_labels, control_hints
        actions = []
        unresolved = []
        for field_id in field_ids:
            if (
                field_id == self.BRANCH_ID
                and self.current_page["key"] == "personal1"
                and self.dom_generation == 0
            ):
                actions.append(ComputerAction(
                    kind=ActionKind.SELECT,
                    field_id=field_id,
                    target_hint=field_id,
                    reason=(
                        "Deterministic descriptor-matched CEAC radio group "
                        "[match=control-hint]"
                    ),
                ))
            else:
                unresolved.append(field_id)
        return actions, unresolved

    def bind_visual_field(self, action, labels=(), hints=()):
        del hints
        self._labels[action.field_id] = tuple(labels or ())
        return bool(action.field_id)

    def settle_after_dynamic_refresh(self, field_id, labels, hints):
        self.settle_calls.append((
            field_id,
            tuple(labels),
            tuple(hints),
            self.current_page["url"],
            self.dom_generation,
        ))
        return (
            field_id == self.BRANCH_ID
            and self.current_page["key"] == "personal1"
            and self.dom_generation == 1
        )

    def plan_next(self):
        if self.current_page["key"] == "review":
            raise AssertionError("Review must stop before planning another Next")
        return ComputerAction(
            kind=ActionKind.CLICK,
            target_hint=f"Next: {self.pages[self.page_index + 1]['title']}",
            reason="Deterministic fixed CEAC Next control",
        )

    def execute(self, action):
        if re.search(
            r"\b(?:sign|final submit)\b",
            f"{action.target_hint} {action.reason}",
            flags=re.IGNORECASE,
        ):
            self.final_submit_attempts += 1
        self.executed_actions.append(action)
        if action.field_id:
            self.field_execution_counts[action.field_id] += 1

        if (
            action.kind == ActionKind.CLICK
            and not action.field_id
            and action.target_hint.startswith("Next:")
        ):
            self.next_count += 1
            self.acknowledged_action_ids.append(action.id)
            self.page_index += 1
            self.dom_generation = 0
            return

        page_values = self.page_values[self.current_page["key"]]
        if action.field_id == self.BRANCH_ID:
            before_url = self.current_page["url"]
            page_values[action.field_id] = action.value
            self.acknowledged_action_ids.append(action.id)
            self.dom_generation += 1
            self.same_url_postbacks.append((
                before_url,
                self.current_page["url"],
                self.dom_generation,
            ))
            return

        if action.field_id == self.REPEATER_ID:
            expected = re.search(
                r"expected_count=(\d+)",
                str(action.reason or ""),
            )
            self.repeater_count = int(expected.group(1)) if expected else 1
            self.acknowledged_action_ids.append(action.id)
            return

        descriptor = " ".join(
            self._labels.get(action.field_id) or ()
        )
        occurrence = re.search(
            r"(?:\[|;)\s*occurrence=(\d+)(?=\s*(?:;|\]))",
            descriptor,
            flags=re.IGNORECASE,
        )
        if occurrence:
            self.occurrence_slots[int(occurrence.group(1))] = action.value

        if action.field_id == self.SEGMENTS_ID:
            digits = re.sub(r"\D", "", action.value)
            self.segment_parts = [
                digits[:3],
                digits[3:6],
                digits[6:],
            ]

        page_values[action.field_id] = action.value
        self.acknowledged_action_ids.append(action.id)


class ContinuousFormStressTests(unittest.TestCase):
    def test_one_run_handles_postbacks_repeaters_and_review_boundary(self):
        browser = FourPagePostbackBrowser()
        model = PageBatchParticipationModel()
        fields = [
            confirmed_field(
                browser.BRANCH_ID,
                "yes",
                "Have you ever used other names? "
                "[control=yes_no; refresh_after_change=true; "
                "control_hints=OTHER_NAMES]",
            ),
            confirmed_field(
                browser.CONDITIONAL_ID,
                "XIA YICHENG",
                "Full Name in Native Alphabet "
                "[control=text; control_hints=NATIVE_NAME]",
            ),
            confirmed_field(
                browser.REPEATER_ID,
                "2",
                "Add Another [control=ensure_repeater; expected_count=2; "
                "record_labels=Language Name]",
            ),
            confirmed_field(
                browser.OCCURRENCE_ONE_ID,
                "ENGLISH",
                "Language Name [control=text; occurrence=1; "
                "control_hints=LANGUAGE_NAME]",
            ),
            confirmed_field(
                browser.OCCURRENCE_TWO_ID,
                "CHINESE",
                "Language Name [control=text; occurrence=2; "
                "control_hints=LANGUAGE_NAME]",
            ),
            confirmed_field(
                browser.SEGMENTS_ID,
                "4155550123",
                "Home Phone Number [control=text_segments; "
                "control_hints=HOME_PHONE]",
            ),
            confirmed_field(
                browser.FINAL_FORM_FIELD_ID,
                "DATA ANALYSIS",
                "Specialized Skills [control=text; "
                "control_hints=SPECIALIZED_SKILL]",
            ),
        ]
        job = AgentJob(
            fields=fields,
            start_url=browser.current_page["url"],
            required_field_ids=[item.id for item in fields],
        )

        result = ComputerUseAgent(
            model,
            browser,
            execution_mode="visual",
        ).run(job)

        self.assertEqual(result.state, JobState.REVIEW_REQUIRED)
        self.assertIn("最终签名和提交前停止", result.human_checkpoint)
        self.assertEqual(
            [page_id for page_id, _pending in model.calls],
            [
                "personal1:dom-0",
                "personal1:dom-1",
                "personal2:dom-0",
                "address_phone:dom-0",
                "work_education3:dom-0",
            ],
        )
        self.assertTrue(all(pending for _page_id, pending in model.calls))
        self.assertEqual(
            sum(
                event.kind == "model_planning_started"
                for event in result.events
            ),
            5,
        )
        self.assertEqual(
            sum(event.kind == "started" for event in result.events),
            1,
        )

        self.assertEqual(len(browser.same_url_postbacks), 1)
        before_url, after_url, generation = browser.same_url_postbacks[0]
        self.assertEqual(before_url, after_url)
        self.assertEqual(generation, 1)
        self.assertTrue(browser.radio_visible_after_postback)
        self.assertEqual(len(browser.settle_calls), 1)
        self.assertTrue(any(
            event.kind == "dynamic_refresh_replanned"
            for event in result.events
        ))

        self.assertEqual(browser.repeater_count, 2)
        self.assertEqual(
            browser.occurrence_slots,
            {1: "ENGLISH", 2: "CHINESE"},
        )
        self.assertEqual(
            browser.segment_parts,
            ["415", "555", "0123"],
        )

        self.assertEqual(browser.next_count, 4)
        self.assertEqual(
            sum(
                event.kind == "page_navigation_verified"
                for event in result.events
            ),
            4,
        )
        self.assertEqual(browser.current_page["key"], "review")
        self.assertEqual(browser.final_submit_attempts, 0)

        expected_field_ids = {item.id for item in fields}
        self.assertEqual(
            set(result.completed_field_ids),
            expected_field_ids,
        )
        self.assertEqual(
            browser.field_execution_counts,
            Counter({field_id: 1 for field_id in expected_field_ids}),
        )
        executed_ids = [
            action.id for action in browser.executed_actions
        ]
        self.assertEqual(len(executed_ids), len(set(executed_ids)))
        self.assertFalse(any(
            event.kind == "duplicate_action_ignored"
            for event in result.events
        ))


if __name__ == "__main__":
    unittest.main()
