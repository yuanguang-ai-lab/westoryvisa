"""One logical Agent orchestrating document, review, and browser stages."""

from typing import Iterable, List

from .models import AgentJob, ExtractedField, JobState
from .safety import VisaFormSafetyPolicy
from .validation import DEFAULT_FIELD_SCHEMAS, field_schema


class AgentOrchestrator:
    def __init__(self, recognizer=None, checkpoint_store=None, computer_use_agent=None):
        self.recognizer = recognizer
        self.checkpoint_store = checkpoint_store
        self.computer_use_agent = computer_use_agent

    def prepare_documents(self, documents: Iterable[dict], start_url: str):
        if self.recognizer is None:
            raise RuntimeError("Document recognizer is not configured")
        self._validate_start_url(start_url)
        job = AgentJob(fields=[], start_url=str(start_url))
        job.state = JobState.PARSING_DOCUMENTS
        job.record("parsing_documents", "Document parsing started")
        self._save(job)
        results = []
        for document in documents:
            results.append(self.recognizer.recognize(
                document["content"],
                document["filename"],
                document.get("media_type", "application/octet-stream"),
                document.get("document_type", "unknown"),
                document.get("document_id"),
            ))
        job.state = JobState.EXTRACTING_FIELDS
        job.record(
            "extracting_fields",
            "Document extraction completed",
            documentCount=len(results),
        )
        job.fields, job.validation_errors, warnings = self._merge_results(results)
        job.state = JobState.VALIDATING
        job.record(
            "validated",
            "Cross-document validation completed",
            fieldCount=len(job.fields),
            errorCount=len(job.validation_errors),
            warnings=warnings,
        )
        job.state = JobState.WAITING_REVIEW
        job.record("waiting_review", "Human field review is required")
        self._save(job)
        return job

    def create_review_job(
        self, fields: List[ExtractedField], start_url: str, required_field_ids=None
    ):
        """Create a job at the narrow future DocFlow integration boundary."""
        self._validate_start_url(start_url)
        normalized = []
        errors = []
        seen = set()
        for item in fields:
            schema = field_schema(item.id)
            if schema is None:
                errors.append(f"Unknown field id: {item.id}")
                continue
            if item.id in seen:
                errors.append(f"Duplicate field id: {item.id}")
                continue
            seen.add(item.id)
            if item.id in DEFAULT_FIELD_SCHEMAS or not item.label.strip():
                item.label = schema.label
            item.risk_level = schema.risk_level
            item.unconfirm()
            if not schema.validator(str(item.value)):
                errors.append(f"Invalid unreviewed value for {item.id}")
            if (
                isinstance(item.confidence, bool)
                or not isinstance(item.confidence, (int, float))
                or not 0.0 <= float(item.confidence) <= 1.0
            ):
                errors.append(f"Invalid confidence for {item.id}")
                item.confidence = 0.0
            normalized.append(item)
        requested_required = [
            str(field_id) for field_id in (required_field_ids or [])
        ]
        for field_id in requested_required:
            if field_id not in seen:
                errors.append(f"Unknown required field id: {field_id}")
        job = AgentJob(
            fields=normalized,
            start_url=str(start_url),
            state=JobState.WAITING_REVIEW,
            validation_errors=errors,
            required_field_ids=[
                field_id for field_id in requested_required
                if field_id in seen
            ],
        )
        job.record(
            "waiting_review",
            "Job created in isolated review mode",
            fieldCount=len(normalized),
        )
        self._save(job)
        return job

    def apply_human_review(self, job, decisions, actor):
        if not actor:
            raise ValueError("Review actor is required")
        if job.state in {JobState.COMPLETED, JobState.CANCELLED}:
            raise ValueError(f"Cannot review a {job.state.value} job")
        fields = {item.id: item for item in job.fields}
        reviewed_ids = set()
        for decision in decisions:
            if not isinstance(decision, dict):
                raise ValueError("Each review decision must be an object")
            field_id = str(decision.get("fieldId") or "")
            field = fields.get(field_id)
            if field is None:
                raise ValueError(f"Unknown review field: {field_id}")
            if field_id in reviewed_ids:
                raise ValueError(f"Duplicate review decision: {field_id}")
            if not isinstance(decision.get("approved"), bool):
                raise ValueError(f"approved must be a boolean for {field_id}")
            reviewed_ids.add(field_id)
            if decision["approved"]:
                value = str(decision.get("value", field.value))
                schema = field_schema(field_id)
                if schema is None:
                    raise ValueError(f"Unknown review field: {field_id}")
                if not schema.validator(value):
                    raise ValueError(f"Invalid reviewed value for {field_id}")
                field.confirm(
                    value,
                    confirmed_by=actor,
                    source=str(decision.get("source") or "human-review"),
                    reason=str(decision.get("reason") or ""),
                )
            else:
                field.unconfirm()

        # A human decision resolves cross-document conflicts for that field.
        job.validation_errors = [
            error for error in job.validation_errors
            if not any(field_id in error for field_id in reviewed_ids)
        ]
        confirmed = job.confirmed_field_map()
        missing_required = set(job.required_field_ids).difference(confirmed)
        if confirmed and not job.validation_errors and not missing_required:
            job.state = JobState.READY_FOR_FORM
            job.human_checkpoint = None
            message = "Human review completed; job is ready for form filling"
        else:
            job.state = JobState.REVIEW_REQUIRED
            message = "Human review is incomplete or validation errors remain"
        job.record(
            "review_applied",
            message,
            actor=str(actor),
            confirmedFieldIds=sorted(confirmed),
            missingRequiredFieldIds=sorted(missing_required),
        )
        self._save(job)
        return job

    def run_form(self, job):
        if self.computer_use_agent is None:
            raise RuntimeError("Computer-use runtime is not configured")
        if job.state not in {
            JobState.READY_FOR_FORM,
            JobState.WAITING_HUMAN,
            JobState.REVIEW_REQUIRED,
            JobState.FILLING_FORM,
            JobState.BLOCKED,
            JobState.FAILED,
        }:
            raise ValueError(f"Job cannot start from state: {job.state.value}")
        return self.computer_use_agent.run(job)

    def cancel(self, job, actor="system"):
        if job.state == JobState.COMPLETED:
            raise ValueError("Completed jobs cannot be cancelled")
        job.state = JobState.CANCELLED
        job.pending_action = None
        job.record("cancelled", "Job cancelled", actor=str(actor))
        self._save(job)
        return job

    @staticmethod
    def _merge_results(results):
        merged = {}
        errors = []
        warnings = []
        for result in results:
            warnings.extend(result.warnings)
            for candidate in result.fields:
                current = merged.get(candidate.id)
                if current is None:
                    merged[candidate.id] = candidate
                    continue
                for evidence in candidate.evidence:
                    if evidence not in current.evidence:
                        current.evidence.append(evidence)
                if current.value != candidate.value:
                    if candidate.value not in current.alternatives:
                        current.alternatives.append(candidate.value)
                    errors.append(
                        f"Cross-document conflict for {candidate.id}: human review required"
                    )
                    current.confidence = min(
                        current.confidence, candidate.confidence
                    )
        return sorted(merged.values(), key=lambda item: item.id), errors, warnings

    @staticmethod
    def _validate_start_url(start_url):
        decision = VisaFormSafetyPolicy().inspect_navigation_target(start_url)
        if not decision.allowed:
            raise ValueError(decision.reason)

    def _save(self, job):
        if self.checkpoint_store:
            self.checkpoint_store.save(job)
