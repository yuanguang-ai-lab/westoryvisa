import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from visa_agent.config import AgentConfig
from visa_agent.models import (
    ActionKind,
    AgentJob,
    BrowserObservation,
    ComputerAction,
    ExtractedField,
    JobState,
    RiskLevel,
)
from visa_agent.page_plans import PagePlanRegistry
from visa_agent.service import AgentService
from visa_agent.storage import FileCheckpointStore
from visa_agent.workflow import ComputerUseAgent


TRAVEL_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_travel.aspx?node=Travel"
)
SEVIS_URL = (
    "https://ceac.state.gov/GenNIV/General/complete/"
    "complete_sevisexchange.aspx?node=SEVISExchange"
)
REVIEW_URL = (
    "https://ceac.state.gov/GenNIV/General/Review/"
    "ReviewReview.aspx?node=ReviewReview"
)


def confirmed(field_id, value, label):
    return ExtractedField(
        id=field_id,
        value=value,
        label=label,
        confidence=1.0,
        risk_level=RiskLevel.HIGH,
        confirmed=True,
    )


class ModelMustNotRun:
    def __init__(self):
        self.calls = 0

    def propose_actions(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("Exact Travel/SEVIS routes must remain local")

    def propose_action(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("Exact Travel/SEVIS routes must remain local")


class TravelSevisBrowser:
    TRAVEL_FIELD = "travel.purpose"
    SEVIS_FIELD = "education.sevisId"

    def __init__(self):
        self.pages = (
            {
                "key": "travel",
                "url": TRAVEL_URL,
                "title": "Travel Information",
            },
            {
                "key": "sevis",
                "url": SEVIS_URL,
                "title": "Student/Exchange Visitor Information",
            },
            {
                "key": "review",
                "url": REVIEW_URL,
                "title": "Review Application",
            },
        )
        self.page_index = 0
        self.values = {
            page["key"]: {}
            for page in self.pages
        }
        self.acknowledged_action_ids = []
        self.plan_calls = []
        self.executed_fields = []
        self.next_from_pages = []

    @property
    def page(self):
        return self.pages[self.page_index]

    def observe(self):
        return BrowserObservation(
            url=self.page["url"],
            title=self.page["title"],
            visible_text=self.page["title"],
            screenshot_ref=f"memory://{self.page['key']}",
            page_id=f"{self.page['key']}-page",
            control_values=dict(self.values[self.page["key"]]),
            acknowledged_action_ids=list(
                self.acknowledged_action_ids
            ),
        )

    def observe_lightweight(self):
        return self.observe()

    def observe_action(self, _action):
        return self.observe()

    def plan_fields(self, field_ids, _field_labels, _control_hints):
        requested = tuple(field_ids)
        self.plan_calls.append((self.page["key"], requested))
        return [
            ComputerAction(
                kind=ActionKind.TYPE,
                field_id=field_id,
                target_hint=field_id,
                reason="Exact physical-route control binding",
            )
            for field_id in requested
        ], []

    def plan_next(self):
        return ComputerAction(
            kind=ActionKind.CLICK,
            target_hint=f"Next: {self.pages[self.page_index + 1]['title']}",
            reason="Deterministic fixed CEAC Next control",
        )

    def execute(self, action):
        if action.field_id:
            self.executed_fields.append((
                self.page["key"],
                action.field_id,
            ))
            self.values[self.page["key"]][action.field_id] = action.value
            self.acknowledged_action_ids.append(action.id)
            return
        if (
            action.kind == ActionKind.CLICK
            and action.target_hint.startswith("Next:")
        ):
            self.next_from_pages.append(self.page["key"])
            self.acknowledged_action_ids.append(action.id)
            self.page_index += 1
            return
        raise AssertionError(f"Unexpected browser action: {action}")

    def clear_page_state(self):
        return None


class PendingCheckpointCapture:
    def __init__(self, browser):
        self.browser = browser
        self.pending_field_snapshots = []

    def save(self, job):
        pending = job.pending_action
        if pending is None or not pending.field_id:
            return
        self.pending_field_snapshots.append((
            self.browser.page["key"],
            pending.field_id,
            job.current_page_plan_id,
        ))


class PhysicalRouteAndProfileGcTests(unittest.TestCase):
    def test_coarse_fields_have_one_exact_live_physical_page_owner(self):
        registry = PagePlanRegistry.default()
        live_routes = {
            "ceac-plan-personal1": (
                "complete_personal.aspx?node=Personal1",
                "Personal Information 1",
            ),
            "ceac-plan-personal2": (
                "complete_personalcont.aspx?node=Personal2",
                "Personal Information 2",
            ),
            "ceac-plan-address_phone": (
                "complete_contact.aspx?node=AddressPhone",
                "Address and Phone Information",
            ),
            "ceac-plan-us_contact": (
                "complete_uscontact.aspx?node=USContact",
                "U.S. Point of Contact Information",
            ),
            "ceac-plan-passport": (
                "complete_pptvisa.aspx?node=PptVisa",
                "Passport Information",
            ),
        }
        live_plans = {}
        for expected_plan_id, (route, title) in live_routes.items():
            observation = BrowserObservation(
                url=(
                    "https://ceac.state.gov/GenNIV/General/complete/"
                    + route
                ),
                title=title,
                visible_text=title,
            )
            live_plan = registry.match(observation)
            self.assertIsNotNone(live_plan)
            self.assertEqual(live_plan.id, expected_plan_id)
            live_plans[expected_plan_id] = live_plan

        physical_owners = {
            "ceac-plan-personal1": (
                "personal.surname",
                "personal.givenNames",
                "personal.nativeName",
                "personal.dateOfBirth",
                "personal.placeOfBirth",
                "personal.birthCity",
                "personal.birthRegion",
                "personal.birthCountry",
            ),
            "ceac-plan-personal2": (
                "personal.nationality",
                "personal.nationalId",
            ),
            "ceac-plan-address_phone": (
                "contact.homeAddress",
                "contact.homeStreet1",
                "contact.homeStreet2",
                "contact.homeCity",
                "contact.homeRegion",
                "contact.homePostalCode",
                "contact.homeCountry",
                "contact.primaryPhone",
                "contact.secondaryPhone",
                "contact.workPhone",
            ),
            "ceac-plan-us_contact": (
                "contact.phone",
                "contact.email",
            ),
            "ceac-plan-passport": (
                "passport.issuance",
                "passport.issueDate",
                "passport.issuingCountry",
                "passport.issuingAuthority",
                "passport.issueCity",
                "passport.issueRegion",
                "passport.issueCountry",
            ),
        }

        for expected_plan_id, field_ids in physical_owners.items():
            for field_id in field_ids:
                with self.subTest(
                    fieldId=field_id,
                    owner=expected_plan_id,
                ):
                    self.assertTrue(
                        live_plans[expected_plan_id].allows_field(
                            field_id
                        )
                    )
                    for other_plan_id, other_plan in live_plans.items():
                        if other_plan_id == expected_plan_id:
                            continue
                        self.assertFalse(
                            other_plan.allows_field(field_id),
                            (
                                f"{field_id} leaked from "
                                f"{expected_plan_id} into {other_plan_id}"
                            ),
                        )
                    self.assertEqual(
                        registry.canonical_owner_for_field(field_id),
                        expected_plan_id,
                    )

    def test_travel_and_sevis_fields_execute_only_on_their_physical_pages(self):
        browser = TravelSevisBrowser()
        model = ModelMustNotRun()
        checkpoints = PendingCheckpointCapture(browser)
        fields = [
            confirmed(
                browser.TRAVEL_FIELD,
                "ACADEMIC OR LANGUAGE STUDENT (F)",
                "Purpose of Trip to the U.S. [control=text]",
            ),
            confirmed(
                browser.SEVIS_FIELD,
                "N0012345678",
                "SEVIS ID [control=text]",
            ),
        ]
        job = AgentJob(
            fields=fields,
            start_url=TRAVEL_URL,
            required_field_ids=[field.id for field in fields],
            auto_next=True,
        )

        result = ComputerUseAgent(
            model,
            browser,
            checkpoint_store=checkpoints,
            max_steps=20,
        ).run(job)

        self.assertEqual(result.state, JobState.REVIEW_REQUIRED)
        self.assertTrue(result.final_submission_boundary_reached)
        self.assertEqual(browser.page["key"], "review")
        self.assertEqual(model.calls, 0)
        self.assertEqual(
            browser.plan_calls,
            [
                ("travel", (browser.TRAVEL_FIELD,)),
                ("sevis", (browser.SEVIS_FIELD,)),
            ],
        )
        self.assertEqual(
            browser.executed_fields,
            [
                ("travel", browser.TRAVEL_FIELD),
                ("sevis", browser.SEVIS_FIELD),
            ],
        )
        self.assertEqual(
            browser.next_from_pages,
            ["travel", "sevis"],
        )
        self.assertFalse(any(
            page == "travel" and field_id == browser.SEVIS_FIELD
            for page, field_id, _page_plan_id
            in checkpoints.pending_field_snapshots
        ))
        self.assertIn(
            (
                "travel",
                browser.TRAVEL_FIELD,
                "ceac-plan-travel",
            ),
            checkpoints.pending_field_snapshots,
        )
        self.assertIn(
            (
                "sevis",
                browser.SEVIS_FIELD,
                "ceac-plan-sevis",
            ),
            checkpoints.pending_field_snapshots,
        )
        self.assertEqual(
            result.completed_field_page_plan_by_id,
            {
                browser.TRAVEL_FIELD: "ceac-plan-travel",
                browser.SEVIS_FIELD: "ceac-plan-sevis",
            },
        )

    def test_restart_gcs_exact_terminal_profile_without_runtime_reference(self):
        for terminal_state in (
            JobState.COMPLETED,
            JobState.CANCELLED,
        ):
            with self.subTest(state=terminal_state.value):
                with tempfile.TemporaryDirectory() as directory:
                    data_dir = Path(directory)
                    store = FileCheckpointStore(data_dir)
                    job = AgentJob(
                        fields=[],
                        start_url=TRAVEL_URL,
                        state=terminal_state,
                    )
                    store.save(job)
                    profile_root = data_dir / "browser-profiles"
                    owned_profile = profile_root / job.id
                    sibling_profile = (
                        profile_root / "agent-job-unrelated"
                    )
                    owned_profile.mkdir(parents=True)
                    sibling_profile.mkdir()
                    (owned_profile / "owned-state").write_text(
                        "terminal browser state",
                        encoding="utf-8",
                    )
                    (sibling_profile / "keep-state").write_text(
                        "unrelated browser state",
                        encoding="utf-8",
                    )

                    restarted = AgentService(
                        AgentConfig(data_dir=data_dir),
                    )
                    with restarted._runtime_lock:
                        self.assertNotIn(job.id, restarted._runtimes)

                    self.assertEqual(
                        restarted.recover_durable_continuous_runs(),
                        [],
                    )

                    self.assertFalse(owned_profile.exists())
                    self.assertTrue(
                        (sibling_profile / "keep-state").is_file()
                    )

    def test_restart_gcs_only_expired_review_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            store = FileCheckpointStore(data_dir)
            expired = AgentJob(
                fields=[],
                start_url=REVIEW_URL,
                state=JobState.REVIEW_REQUIRED,
                final_submission_boundary_reached=True,
                review_lease_expires_at=(
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                ).isoformat(),
            )
            retained = AgentJob(
                fields=[],
                start_url=REVIEW_URL,
                state=JobState.REVIEW_REQUIRED,
                final_submission_boundary_reached=True,
                review_lease_expires_at=(
                    datetime.now(timezone.utc) + timedelta(minutes=30)
                ).isoformat(),
            )
            store.save(expired)
            store.save(retained)
            profile_root = data_dir / "browser-profiles"
            expired_profile = profile_root / expired.id
            retained_profile = profile_root / retained.id
            expired_profile.mkdir(parents=True)
            retained_profile.mkdir()
            (expired_profile / "state").write_text(
                "expired review",
                encoding="utf-8",
            )
            (retained_profile / "state").write_text(
                "active review",
                encoding="utf-8",
            )

            restarted = AgentService(AgentConfig(data_dir=data_dir))
            self.assertEqual(
                restarted.recover_durable_continuous_runs(),
                [],
            )

            expired_job = store.load_job(expired.id)
            retained_job = store.load_job(retained.id)
            self.assertEqual(expired_job.state, JobState.CANCELLED)
            self.assertEqual(
                expired_job.events[-1].kind,
                "review_lease_expired",
            )
            self.assertFalse(expired_profile.exists())
            self.assertEqual(
                retained_job.state,
                JobState.REVIEW_REQUIRED,
            )
            self.assertTrue(retained_profile.exists())

    def test_restart_gcs_expired_incomplete_review_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            store = FileCheckpointStore(data_dir)
            job = AgentJob(
                fields=[],
                start_url=REVIEW_URL,
                state=JobState.WAITING_HUMAN,
                wait_kind="manual_hard_boundary",
                final_submission_boundary_reached=True,
                review_lease_expires_at=(
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                ).isoformat(),
            )
            store.save(job)
            profile = data_dir / "browser-profiles" / job.id
            profile.mkdir(parents=True)
            (profile / "state").write_text(
                "incomplete review",
                encoding="utf-8",
            )

            restarted = AgentService(AgentConfig(data_dir=data_dir))
            self.assertEqual(
                restarted.recover_durable_continuous_runs(),
                [],
            )

            expired = store.load_job(job.id)
            self.assertEqual(expired.state, JobState.CANCELLED)
            self.assertEqual(
                expired.events[-1].kind,
                "review_lease_expired",
            )
            self.assertFalse(profile.exists())

    def test_restart_unlinks_terminal_profile_symlink_without_following(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            store = FileCheckpointStore(data_dir)
            job = AgentJob(
                fields=[],
                start_url=TRAVEL_URL,
                state=JobState.CANCELLED,
            )
            store.save(job)
            profile_root = data_dir / "browser-profiles"
            profile_root.mkdir()
            external_target = data_dir / "must-not-delete"
            external_target.mkdir()
            sentinel = external_target / "sentinel"
            sentinel.write_text(
                "outside the owned profile leaf",
                encoding="utf-8",
            )
            owned_profile = profile_root / job.id
            owned_profile.symlink_to(
                external_target,
                target_is_directory=True,
            )
            self.assertTrue(owned_profile.is_symlink())

            restarted = AgentService(
                AgentConfig(data_dir=data_dir),
            )
            with restarted._runtime_lock:
                self.assertNotIn(job.id, restarted._runtimes)
            restarted.recover_durable_continuous_runs()

            self.assertFalse(os.path.lexists(owned_profile))
            self.assertTrue(sentinel.is_file())
            self.assertEqual(
                sentinel.read_text(encoding="utf-8"),
                "outside the owned profile leaf",
            )


if __name__ == "__main__":
    unittest.main()
