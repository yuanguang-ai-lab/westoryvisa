"""Approved, encrypted-only credentials for retrieving an existing DS-160.

The visual model never receives these values.  They are a separate contract
from ordinary form fields because CEAC retrieval is an authentication-like
transition, not another questionnaire page.  A profile is usable only as one
complete, explicitly approved snapshot; partial values are never guessed or
derived by the Agent.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


RECOVERY_COMPONENT_LABELS = {
    "application_id": "DS-160 Application ID",
    "surname_prefix": "申请人姓氏前 5 个字母",
    "birth_year": "申请人出生年份",
    "security_question": "已批准的安全问题",
    "security_answer": "已批准的安全问题答案",
    "approved_by": "恢复资料确认人",
}


def normalize_security_question(value: Any) -> str:
    """Return a punctuation-insensitive identity, never a fuzzy match."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[\W_]+", " ", text.casefold(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _clean_text(value: Any, *, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) > maximum or any(
        ord(character) < 32 and character not in "\t\r\n"
        for character in text
    ):
        raise ValueError("Recovery credential contains invalid text")
    return text


@dataclass(frozen=True)
class RecoveryCredentials:
    """One human-approved snapshot for an existing application only."""

    application_id: str
    surname_prefix: str
    birth_year: str
    security_question: str
    security_answer: str
    approved_by: str
    approved_at: str = ""
    approved: bool = True

    def missing_components(self) -> Tuple[str, ...]:
        missing = []
        for name in (
            "application_id",
            "surname_prefix",
            "birth_year",
            "security_question",
            "security_answer",
            "approved_by",
        ):
            if not str(getattr(self, name, "") or "").strip():
                missing.append(name)
        if self.approved is not True:
            missing.append("approval")
        return tuple(missing)

    @property
    def complete(self) -> bool:
        return not self.missing_components()

    def public_summary(self) -> Dict[str, Any]:
        """Return liveness metadata without echoing credentials or question."""
        return {
            "configured": self.complete,
            "approved": self.approved is True,
            "approvedByPresent": bool(self.approved_by),
            "approvedAtPresent": bool(self.approved_at),
            "securityQuestionMapped": bool(
                normalize_security_question(self.security_question)
            ),
        }


def recovery_credentials_from_primitive(
    payload: Any,
    *,
    require_approval: bool = True,
) -> Optional[RecoveryCredentials]:
    """Validate API/checkpoint input without manufacturing missing values.

    Both camelCase API payloads and snake_case encrypted checkpoints are
    accepted.  No aliases for business meaning are accepted: a caller must
    explicitly provide the exact recovery snapshot.
    """
    if payload is None or payload == "":
        return None
    if not isinstance(payload, dict):
        raise ValueError("recoveryCredentials must be an object")

    def read(snake_name: str, camel_name: str):
        if snake_name in payload:
            return payload.get(snake_name)
        return payload.get(camel_name)

    application_id = _clean_text(
        read("application_id", "applicationId"), maximum=32
    ).upper()
    surname_prefix = _clean_text(
        read("surname_prefix", "surnamePrefix"), maximum=5
    ).upper()
    birth_year = _clean_text(
        read("birth_year", "birthYear"), maximum=4
    )
    security_question = _clean_text(
        read("security_question", "securityQuestion"), maximum=500
    )
    security_answer = _clean_text(
        read("security_answer", "securityAnswer"), maximum=500
    )
    approved_by = _clean_text(
        read("approved_by", "approvedBy"), maximum=200
    )
    approved_at = _clean_text(
        read("approved_at", "approvedAt"), maximum=100
    )
    approved = payload.get("approved") is True

    if application_id and not re.fullmatch(r"[A-Z0-9]{8,20}", application_id):
        raise ValueError("Invalid DS-160 Application ID recovery credential")
    if surname_prefix and not re.fullmatch(r"[A-Z]{1,5}", surname_prefix):
        raise ValueError("Invalid surname-prefix recovery credential")
    if birth_year and not re.fullmatch(r"(?:18|19|20)\d{2}", birth_year):
        raise ValueError("Invalid birth-year recovery credential")
    if security_question and not normalize_security_question(
        security_question
    ):
        raise ValueError("Invalid security-question recovery credential")

    credentials = RecoveryCredentials(
        application_id=application_id,
        surname_prefix=surname_prefix,
        birth_year=birth_year,
        security_question=security_question,
        security_answer=security_answer,
        approved_by=approved_by,
        approved_at=approved_at,
        approved=approved,
    )
    missing = credentials.missing_components()
    if require_approval and missing:
        labels = [
            RECOVERY_COMPONENT_LABELS.get(item, item)
            for item in missing
        ]
        raise ValueError(
            "Recovery credential snapshot is incomplete or unapproved: "
            + ", ".join(labels)
        )
    return credentials


def missing_recovery_components(
    credentials: Optional[RecoveryCredentials],
) -> Tuple[str, ...]:
    if credentials is None:
        return tuple(RECOVERY_COMPONENT_LABELS)
    return credentials.missing_components()
