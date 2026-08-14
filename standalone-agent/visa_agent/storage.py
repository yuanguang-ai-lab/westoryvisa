"""Private, resumable checkpoints with optional authenticated encryption."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import job_from_primitive, to_primitive


class CheckpointProtectionError(RuntimeError):
    pass


class FernetCheckpointProtector:
    """Optional production protector; install the package's ``secure`` extra."""

    format = "fernet-v1"

    def __init__(self, key):
        try:
            from cryptography.fernet import Fernet, InvalidToken
        except ImportError as error:
            raise CheckpointProtectionError(
                "Encrypted checkpoints require: pip install .[secure]"
            ) from error
        try:
            self._fernet = Fernet(str(key).encode("ascii"))
            self._invalid_token = InvalidToken
        except (TypeError, ValueError) as error:
            raise CheckpointProtectionError(
                "AGENT_CHECKPOINT_ENCRYPTION_KEY must be a valid Fernet key"
            ) from error

    def protect(self, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self._fernet.encrypt(raw).decode("ascii")

    def unprotect(self, ciphertext):
        try:
            raw = self._fernet.decrypt(str(ciphertext).encode("ascii"))
            return json.loads(raw.decode("utf-8"))
        except (self._invalid_token, UnicodeError, json.JSONDecodeError) as error:
            raise CheckpointProtectionError(
                "Checkpoint decryption or integrity verification failed"
            ) from error


class FileCheckpointStore:
    def __init__(
        self,
        directory,
        encryption_key="",
        allow_plaintext=True,
        minimize_evidence=True,
    ):
        self.directory = Path(directory)
        self.allow_plaintext = bool(allow_plaintext)
        self.minimize_evidence = bool(minimize_evidence)
        self.protector = (
            FernetCheckpointProtector(encryption_key)
            if encryption_key
            else None
        )
        if self.protector is None and not self.allow_plaintext:
            raise CheckpointProtectionError(
                "Checkpoint encryption is required but no key is configured"
            )

    @property
    def protection_mode(self):
        return self.protector.format if self.protector else "plaintext-development"

    def save(self, job):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        target = self._target(job.id)
        temporary = target.with_name(f".{target.name}.tmp")
        payload = to_primitive(job)
        if self.minimize_evidence:
            self._minimize(payload)
        if self.protector:
            serialized = json.dumps({
                "format": self.protector.format,
                "ciphertext": self.protector.protect(payload),
            }, ensure_ascii=False, indent=2)
        else:
            serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        temporary.write_text(serialized, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        os.chmod(target, 0o600)
        return target

    def load_raw(self, job_id):
        stored = json.loads(self._target(job_id).read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            raise CheckpointProtectionError("Checkpoint payload is invalid")
        if stored.get("format") == "fernet-v1":
            if self.protector is None:
                raise CheckpointProtectionError(
                    "This checkpoint is encrypted but no matching key is configured"
                )
            return self.protector.unprotect(stored.get("ciphertext"))
        if not self.allow_plaintext:
            raise CheckpointProtectionError("Plaintext checkpoint is not allowed")
        return stored

    def load_job(self, job_id):
        return job_from_primitive(self.load_raw(job_id))

    def list_job_ids(self):
        """Return validated durable job ids without decrypting their payloads."""
        if not self.directory.exists():
            return []
        job_ids = []
        for target in self.directory.glob("agent-job-*.json"):
            job_id = target.stem
            try:
                expected = self._target(job_id)
            except ValueError:
                continue
            if target == expected:
                job_ids.append(job_id)
        return sorted(job_ids)

    def prune_expired(self, retention_days):
        """Delete only validated Agent checkpoint files older than retention."""
        if not self.directory.exists():
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=max(1, int(retention_days))
        )
        removed = []
        for target in self.directory.glob("agent-job-*.json"):
            modified = datetime.fromtimestamp(
                target.stat().st_mtime, tz=timezone.utc
            )
            if modified < cutoff:
                target.unlink()
                removed.append(target.name)
        return removed

    def _target(self, job_id):
        if not str(job_id).startswith("agent-job-"):
            raise ValueError("Invalid job id")
        if not all(character.isalnum() or character == "-" for character in str(job_id)):
            raise ValueError("Invalid job id")
        return self.directory / f"{job_id}.json"

    @staticmethod
    def _minimize(payload):
        # Evidence references remain useful for audit/resume, but the raw OCR
        # excerpt is not needed by the browser runtime.
        for field_payload in payload.get("fields") or []:
            for evidence in field_payload.get("evidence") or []:
                evidence["excerpt"] = ""
