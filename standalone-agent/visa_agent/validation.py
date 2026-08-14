"""Strict schemas for untrusted model extraction output."""

import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Dict, Iterable, List, Tuple

from .models import Evidence, ExtractedField, RiskLevel


@dataclass(frozen=True)
class FieldSchema:
    label: str
    risk_level: RiskLevel
    validator: Callable[[str], bool]


def _nonempty(value):
    return bool(value.strip()) and len(value) <= 500


def _short_name(value):
    return bool(re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ' .-]{1,100}", value.strip()))


def _iso_date(value):
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def _passport_number(value):
    return bool(re.fullmatch(r"[A-Z0-9]{5,20}", value.strip().upper()))


def _prc_national_id(value):
    return bool(re.fullmatch(r"\d{17}[0-9X]", value.strip().upper()))


def _sex(value):
    return value.strip().upper() in {
        "M", "F", "X", "U", "MALE", "FEMALE", "UNSPECIFIED"
    }


def _country_code(value):
    return bool(
        re.fullmatch(r"[A-Z]{3}", value.strip().upper())
        or re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,99}", value.strip())
    )


def _yes_no(value):
    return value.strip().lower() in {"yes", "no"}


DEFAULT_FIELD_SCHEMAS: Dict[str, FieldSchema] = {
    "personal.surname": FieldSchema("Surname", RiskLevel.HIGH, _short_name),
    "personal.givenNames": FieldSchema("Given Names", RiskLevel.HIGH, _short_name),
    "personal.dateOfBirth": FieldSchema("Date of Birth", RiskLevel.HIGH, _iso_date),
    "personal.sex": FieldSchema("Sex", RiskLevel.MEDIUM, _sex),
    "personal.nationality": FieldSchema(
        "Nationality", RiskLevel.HIGH, _country_code
    ),
    "personal.nationalId": FieldSchema(
        "National Identification Number", RiskLevel.HIGH, _prc_national_id
    ),
    "personal.placeOfBirth": FieldSchema(
        "Place of Birth", RiskLevel.HIGH, _nonempty
    ),
    "passport.number": FieldSchema(
        "Passport Number", RiskLevel.HIGH, _passport_number
    ),
    "passport.issuance": FieldSchema(
        "Passport Issuance", RiskLevel.HIGH, _iso_date
    ),
    "passport.expiration": FieldSchema(
        "Passport Expiration", RiskLevel.HIGH, _iso_date
    ),
    "passport.issuingCountry": FieldSchema(
        "Passport Issuing Country", RiskLevel.HIGH, _country_code
    ),
    "contact.address": FieldSchema(
        "U.S. Contact or Stay Address", RiskLevel.HIGH, _nonempty
    ),
    "contact.phone": FieldSchema(
        "U.S. Contact Phone (not applicant mobile)", RiskLevel.HIGH, _nonempty
    ),
    "contact.email": FieldSchema(
        "U.S. Contact Email (not applicant email)", RiskLevel.HIGH, _nonempty
    ),
    "travel.purpose": FieldSchema("Travel Purpose", RiskLevel.MEDIUM, _nonempty),
    "travel.arrivalDate": FieldSchema(
        "Arrival Date", RiskLevel.HIGH, _iso_date
    ),
    "education.schoolName": FieldSchema(
        "School Name", RiskLevel.MEDIUM, _nonempty
    ),
    "education.sevisId": FieldSchema("SEVIS ID", RiskLevel.HIGH, _nonempty),
    "security.criminal": FieldSchema(
        "Criminal History", RiskLevel.SENSITIVE, _yes_no
    ),
    "history.refusal": FieldSchema(
        "Visa Refusal History", RiskLevel.SENSITIVE, _yes_no
    ),
    "history.overstay": FieldSchema(
        "Overstay History", RiskLevel.SENSITIVE, _yes_no
    ),
}

CEAC_PLAN_FIELD_PATTERN = re.compile(
    r"^ceac\.(?:personal1|personal2|address_phone|passport|travel|"
    r"travel_companions|previous_us_travel|us_contact|relatives|spouse|"
    r"work_education1|work_education2|work_education3|sevis|"
    r"additional_contacts|security_background[1-5])"
    r"\.[A-Za-z0-9_.-]{1,180}$"
)
CEAC_PLAN_FIELD_SCHEMA = FieldSchema(
    "Human-approved CEAC plan field",
    RiskLevel.HIGH,
    _nonempty,
)


def field_schema(field_id):
    schema = DEFAULT_FIELD_SCHEMAS.get(str(field_id))
    if schema is not None:
        return schema
    if CEAC_PLAN_FIELD_PATTERN.fullmatch(str(field_id)):
        return CEAC_PLAN_FIELD_SCHEMA
    return None


class ExtractionOutputValidator:
    """Treat model fields as untrusted input and return only schema-valid fields."""

    def __init__(self, schemas=None):
        self.schemas = dict(schemas or DEFAULT_FIELD_SCHEMAS)

    def validate(
        self,
        candidates: Iterable[ExtractedField],
        source_text: str,
        document_id: str,
        filename: str,
    ) -> Tuple[List[ExtractedField], List[str]]:
        accepted = []
        warnings = []
        normalized_source = self._normalize(source_text)
        seen = set()
        for candidate in candidates or []:
            field_id = str(getattr(candidate, "id", "") or "")
            schema = self.schemas.get(field_id)
            if schema is None:
                warnings.append(f"Rejected model field outside allowlist: {field_id or '<empty>'}")
                continue
            if field_id in seen:
                warnings.append(f"Rejected duplicate model field: {field_id}")
                continue
            seen.add(field_id)
            value = str(getattr(candidate, "value", "") or "").strip()
            if not schema.validator(value):
                warnings.append(f"Rejected invalid model value for {field_id}")
                continue
            confidence = getattr(candidate, "confidence", None)
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0.0 <= float(confidence) <= 1.0
            ):
                warnings.append(f"Rejected invalid confidence for {field_id}")
                continue
            evidence = self._validated_evidence(
                getattr(candidate, "evidence", []),
                normalized_source,
                document_id,
                filename,
            )
            if not evidence:
                warnings.append(f"Rejected model field without source evidence: {field_id}")
                continue
            accepted.append(ExtractedField(
                id=field_id,
                value=value,
                label=schema.label,
                confidence=float(confidence),
                # Risk and confirmation are always system-owned.
                risk_level=schema.risk_level,
                confirmed=False,
                confirmation=None,
                evidence=evidence,
                alternatives=[
                    str(item)
                    for item in getattr(candidate, "alternatives", [])
                    if str(item) and str(item) != value
                ][:5],
            ))
        return accepted, warnings

    def _validated_evidence(
        self, evidence_items, normalized_source, document_id, filename
    ):
        output: List[Evidence] = []
        for item in evidence_items or []:
            excerpt = str(getattr(item, "excerpt", "") or "").strip()
            if not excerpt or self._normalize(excerpt) not in normalized_source:
                continue
            try:
                page = max(1, int(getattr(item, "page", 1)))
            except (TypeError, ValueError):
                page = 1
            output.append(Evidence(
                document_id=document_id,
                filename=filename,
                page=page,
                excerpt=excerpt[:500],
                method=str(getattr(item, "method", "") or "model-extraction"),
            ))
        return output

    @staticmethod
    def _normalize(value):
        # MinerU can emit compact HTML tables. Model evidence usually quotes
        # the visible cell text (for example "Surname CHEN"), while the raw
        # source contains closing/opening td tags between those words. Remove
        # markup before comparison without relaxing the requirement that the
        # complete visible excerpt must occur in the source.
        visible_text = html.unescape(str(value))
        visible_text = re.sub(r"<[^>]+>", " ", visible_text)
        return re.sub(r"\s+", " ", visible_text).strip().casefold()
