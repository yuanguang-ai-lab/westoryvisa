import json
import tempfile
import unittest
from pathlib import Path

from visa_agent.models import (
    Evidence,
    ExtractedField,
    JobState,
    RiskLevel,
)
from visa_agent.orchestrator import AgentOrchestrator
from visa_agent.storage import (
    CheckpointProtectionError,
    FileCheckpointStore,
)


class OrchestratorAndStorageTests(unittest.TestCase):
    def test_human_reviewed_dynamic_ceac_plan_field_is_allowed(self):
        orchestrator = AgentOrchestrator()
        field_id = "ceac.personal1.001.personal.surname"
        job = orchestrator.create_review_job([
            ExtractedField(
                id=field_id,
                value="ZHANG",
                label=(
                    "Surnames [control=text; "
                    "human-approved value=ZHANG]"
                ),
                confidence=1.0,
            )
        ], "https://ceac.state.gov/GenNIV/Default.aspx",
            required_field_ids=[field_id])
        reviewed = orchestrator.apply_human_review(job, [{
            "fieldId": field_id,
            "approved": True,
            "value": "ZHANG",
        }], actor="consultant-1")
        self.assertEqual(reviewed.state, JobState.READY_FOR_FORM)
        self.assertIn("human-approved value=ZHANG", reviewed.fields[0].label)

    def test_human_review_records_provenance_and_makes_job_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FileCheckpointStore(directory)
            orchestrator = AgentOrchestrator(checkpoint_store=store)
            job = orchestrator.create_review_job([
                ExtractedField(
                    id="personal.surname",
                    value="ZHANG",
                    confidence=0.9,
                    risk_level=RiskLevel.LOW,
                )
            ], "https://ceac.state.gov/GenNIV/form")
            self.assertEqual(job.state, JobState.WAITING_REVIEW)
            reviewed = orchestrator.apply_human_review(job, [{
                "fieldId": "personal.surname",
                "approved": True,
                "value": "ZHANG",
                "reason": "Matched passport",
            }], actor="consultant-1")
            self.assertEqual(reviewed.state, JobState.READY_FOR_FORM)
            confirmation = reviewed.fields[0].confirmation
            self.assertEqual(confirmation.confirmed_by, "consultant-1")
            self.assertEqual(confirmation.original_value, "ZHANG")
            self.assertEqual(confirmation.confirmed_value, "ZHANG")

    def test_checkpoint_load_is_typed_and_plaintext_evidence_is_minimized(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FileCheckpointStore(directory)
            job = AgentOrchestrator(checkpoint_store=store).create_review_job([
                ExtractedField(
                    id="passport.number",
                    value="E12345678",
                    confidence=0.9,
                    evidence=[
                        Evidence("doc-1", "passport.png", 1, "E12345678", "ocr")
                    ],
                )
            ], "https://ceac.state.gov/GenNIV/form")
            raw_file = Path(directory) / f"{job.id}.json"
            serialized = json.loads(raw_file.read_text(encoding="utf-8"))
            self.assertEqual(serialized["fields"][0]["evidence"][0]["excerpt"], "")
            loaded = store.load_job(job.id)
            self.assertEqual(loaded.id, job.id)
            self.assertEqual(loaded.state, JobState.WAITING_REVIEW)
            self.assertEqual(loaded.fields[0].risk_level, RiskLevel.HIGH)

    def test_checkpoint_store_lists_only_valid_job_files(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FileCheckpointStore(directory)
            first = AgentOrchestrator(checkpoint_store=store).create_review_job(
                [ExtractedField(
                    id="personal.surname",
                    value="ZHANG",
                    confidence=0.9,
                )],
                "https://ceac.state.gov/GenNIV/form",
            )
            second = AgentOrchestrator(checkpoint_store=store).create_review_job(
                [ExtractedField(
                    id="personal.givenNames",
                    value="SAN",
                    confidence=0.9,
                )],
                "https://ceac.state.gov/GenNIV/form",
            )
            Path(directory, "agent-job-not-json.txt").write_text(
                "ignored",
                encoding="utf-8",
            )
            Path(directory, "unrelated.json").write_text(
                "{}",
                encoding="utf-8",
            )

            self.assertEqual(
                store.list_job_ids(),
                sorted([first.id, second.id]),
            )

    def test_production_store_refuses_unencrypted_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CheckpointProtectionError):
                FileCheckpointStore(directory, allow_plaintext=False)

    def test_all_required_fields_must_be_confirmed_before_ready(self):
        orchestrator = AgentOrchestrator()
        job = orchestrator.create_review_job([
            ExtractedField(id="personal.surname", value="ZHANG", confidence=0.9),
            ExtractedField(id="personal.givenNames", value="SAN", confidence=0.9),
        ], "https://ceac.state.gov/GenNIV/form", required_field_ids=[
            "personal.surname", "personal.givenNames"
        ])
        reviewed = orchestrator.apply_human_review(job, [{
            "fieldId": "personal.surname",
            "approved": True,
            "value": "ZHANG",
        }], actor="consultant-1")
        self.assertEqual(reviewed.state, JobState.REVIEW_REQUIRED)
        self.assertEqual(
            reviewed.events[-1].detail["missingRequiredFieldIds"],
            ["personal.givenNames"],
        )


if __name__ == "__main__":
    unittest.main()
