"""Document recognition pipeline with rules first and validated model enrichment."""

import html
import re
import time
from uuid import uuid4

from .models import Evidence, ExtractedField, RecognitionResult, RiskLevel
from .mrz import parse_td3
from .providers import ProviderNotConfigured
from .validation import DEFAULT_FIELD_SCHEMAS, ExtractionOutputValidator


FIELD_META = {
    field_id: (schema.label, schema.risk_level)
    for field_id, schema in DEFAULT_FIELD_SCHEMAS.items()
}


class DocumentRecognizer:
    def __init__(
        self,
        ocr_provider,
        extraction_model,
        output_validator=None,
        document_parser=None,
        review_model=None,
    ):
        self.ocr_provider = ocr_provider
        self.extraction_model = extraction_model
        self.output_validator = output_validator or ExtractionOutputValidator()
        self.document_parser = document_parser
        self.review_model = review_model

    def recognize(
        self,
        content,
        filename,
        media_type="text/plain",
        document_type="passport",
        document_id=None,
    ):
        document_id = document_id or f"document-{uuid4().hex}"
        warnings = []
        stages = {}
        text_stage_detail = {}
        started_at = time.monotonic()
        text_started_at = time.monotonic()
        try:
            if self._is_plain_text(media_type):
                text = content.decode("utf-8")
                text_provider = "plain-text"
            elif self.document_parser is not None and self._use_parser(
                filename, media_type
            ):
                try:
                    text = self.document_parser.parse(content, filename, media_type)
                    text_provider = "document-parser"
                except Exception as parser_error:
                    warnings.append(
                        "Document parser failed; OCR fallback used: "
                        f"{type(parser_error).__name__}"
                    )
                    text = self.ocr_provider.recognize(
                        content, filename, media_type
                    )
                    text_provider = "ocr-fallback"
                    text_stage_detail = {
                        "fallbackFrom": "document-parser",
                        "primaryStatus": "failed",
                    }
            else:
                text = self.ocr_provider.recognize(content, filename, media_type)
                text_provider = "ocr"
        except ProviderNotConfigured as error:
            return RecognitionResult(
                document_id=document_id,
                filename=filename,
                document_type=document_type,
                warnings=[str(error)],
                stages={
                    "text": {
                        "status": "not-configured",
                        "durationMs": round((time.monotonic() - text_started_at) * 1000),
                    }
                },
            )
        except Exception as error:
            return RecognitionResult(
                document_id=document_id,
                filename=filename,
                document_type=document_type,
                warnings=[f"Document text provider failed: {type(error).__name__}"],
                stages={
                    "text": {
                        "status": "failed",
                        "durationMs": round((time.monotonic() - text_started_at) * 1000),
                    }
                },
            )
        if not isinstance(text, str):
            return RecognitionResult(
                document_id=document_id,
                filename=filename,
                document_type=document_type,
                warnings=["Document text provider returned a non-text result"],
                stages={
                    "text": {
                        "status": "failed",
                        "durationMs": round((time.monotonic() - text_started_at) * 1000),
                    }
                },
            )
        if self._needs_supplemental_ocr(text, document_type):
            fallback = getattr(self.ocr_provider, "recognize_fallback", None)
            if callable(fallback):
                try:
                    supplemental_text = fallback(
                        content, filename, media_type
                    )
                    if (
                        isinstance(supplemental_text, str)
                        and supplemental_text.strip()
                        and self._normalize_text(supplemental_text)
                        != self._normalize_text(text)
                    ):
                        text = f"{text.rstrip()}\n\n{supplemental_text.strip()}"
                        text_provider = f"{text_provider}+critical-field-fallback"
                        text_stage_detail["supplementalFallback"] = True
                        text_stage_detail["supplementalReason"] = (
                            "PRC national ID number missing from primary OCR"
                        )
                except Exception as fallback_error:
                    warnings.append(
                        "Critical-field OCR fallback failed: "
                        f"{type(fallback_error).__name__}"
                    )
        stages["text"] = {
            "status": "completed",
            "provider": text_provider,
            "durationMs": round((time.monotonic() - text_started_at) * 1000),
            "characters": len(text),
            **text_stage_detail,
        }

        fields = self._rule_fields(
            text, filename, document_id, document_type
        )
        rule_field_count = len(fields)
        extraction_started_at = time.monotonic()
        try:
            untrusted_model_fields = self.extraction_model.extract(
                text, document_type, filename
            )
            extraction_status = "completed"
        except ProviderNotConfigured as error:
            warnings.append(str(error))
            untrusted_model_fields = []
            extraction_status = "not-configured"
        except Exception as error:
            warnings.append(
                f"Extraction model failed: {type(error).__name__}"
            )
            untrusted_model_fields = []
            extraction_status = "failed"
        model_fields, model_warnings = self.output_validator.validate(
            untrusted_model_fields,
            text,
            document_id,
            filename,
        )
        warnings.extend(model_warnings)
        fields = self._merge(fields, model_fields)
        stages["extraction"] = {
            "status": extraction_status,
            "provider": "model",
            "durationMs": round((time.monotonic() - extraction_started_at) * 1000),
            "candidateFields": len(untrusted_model_fields),
            "acceptedFields": len(model_fields),
            "ruleFields": rule_field_count,
        }
        review_started_at = time.monotonic()
        review_status = "skipped"
        if self.review_model is not None and fields:
            try:
                review_warnings = self.review_model.review(fields, document_type)
                if not isinstance(review_warnings, list):
                    warnings.append("Review model returned an invalid result")
                    review_status = "failed"
                else:
                    warnings.extend(
                        str(item)[:1000] for item in review_warnings if str(item).strip()
                    )
                    review_status = "completed"
            except ProviderNotConfigured as error:
                warnings.append(str(error))
                review_status = "not-configured"
            except Exception as error:
                warnings.append(f"Review model failed: {type(error).__name__}")
                review_status = "failed"
        stages["review"] = {
            "status": review_status,
            "provider": "model",
            "durationMs": round((time.monotonic() - review_started_at) * 1000),
        }
        if not fields:
            warnings.append("No reliable fields were extracted")
        stages["totalDurationMs"] = round((time.monotonic() - started_at) * 1000)
        return RecognitionResult(
            document_id=document_id,
            filename=filename,
            document_type=document_type,
            fields=fields,
            warnings=warnings,
            raw_text_available=bool(text.strip()),
            raw_text=text,
            stages=stages,
        )

    @staticmethod
    def _is_plain_text(media_type):
        return (
            str(media_type).split(";", 1)[0].strip().lower()
            == "text/plain"
        )

    @staticmethod
    def _use_parser(filename, media_type):
        normalized_type = str(media_type).split(";", 1)[0].strip().lower()
        suffix = str(filename).lower().rsplit(".", 1)[-1]
        return (
            normalized_type in {
                "application/pdf",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
            or suffix in {"pdf", "doc", "docx"}
        )

    @staticmethod
    def _normalize_text(value):
        return re.sub(r"\s+", "", str(value or "")).upper()

    @classmethod
    def _needs_supplemental_ocr(cls, text, document_type):
        normalized_type = str(document_type or "").lower()
        is_prc_id = (
            "身份证" in str(document_type or "")
            or "national id" in normalized_type
            or "national_id" in normalized_type
            or "identity card" in normalized_type
            or "identity_card" in normalized_type
        )
        if not is_prc_id:
            return False
        compact = re.sub(r"[\s·•:：-]+", "", str(text or ""))
        return re.search(r"(?<!\d)\d{17}[0-9Xx](?!\w)", compact) is None

    def _rule_fields(self, text, filename, document_id, document_type):
        normalized_type = str(document_type or "").lower()
        if any(
            marker in normalized_type
            for marker in ("i-20", "i20", "sevis", "enrollment letter")
        ):
            return self._i20_rule_fields(
                text,
                filename,
                document_id,
            )
        if normalized_type != "passport":
            return []
        mrz = parse_td3(text)
        if not mrz:
            return []
        excerpt = " / ".join(mrz["evidence"])
        evidence = Evidence(
            document_id=document_id,
            filename=filename,
            page=1,
            excerpt=excerpt,
            method="icao-td3-mrz",
        )
        values = {
            "personal.surname": mrz["surname"],
            "personal.givenNames": mrz["givenNames"],
            "personal.dateOfBirth": mrz["dateOfBirth"],
            "personal.sex": mrz["sex"],
            "personal.nationality": mrz["nationality"],
            "passport.number": mrz["passportNumber"],
            "passport.expiration": mrz["expirationDate"],
        }
        check_map = {
            "personal.dateOfBirth": mrz["checks"]["birth"],
            "passport.number": mrz["checks"]["passport"],
            "passport.expiration": mrz["checks"]["expiry"],
        }
        output = []
        for field_id, value in values.items():
            if not value:
                continue
            label, risk = FIELD_META[field_id]
            valid = check_map.get(field_id, True)
            output.append(ExtractedField(
                id=field_id,
                value=value,
                label=label,
                confidence=0.98 if valid else 0.70,
                risk_level=risk,
                confirmed=False,
                evidence=[evidence],
            ))
        return output

    @staticmethod
    def _i20_rule_fields(text, filename, document_id):
        # MinerU represents table cells as compact HTML. Turning each tag into
        # a line boundary preserves the label/value relationship without
        # trusting a model to reconstruct an evidence sentence.
        visible = html.unescape(re.sub(r"<[^>]+>", "\n", str(text)))
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in visible.splitlines()
            if re.sub(r"\s+", " ", line).strip()
        ]

        def value_after(label):
            target = label.casefold()
            for index, line in enumerate(lines[:-1]):
                if line.casefold() == target:
                    return lines[index + 1]
            return ""

        candidates = {
            "education.schoolName": value_after("School name"),
            "education.sevisId": value_after("SEVIS ID"),
        }
        sevis_match = re.search(
            r"\bN\d{10}\b",
            candidates["education.sevisId"].upper(),
        )
        candidates["education.sevisId"] = (
            sevis_match.group(0) if sevis_match else ""
        )

        output = []
        for field_id, value in candidates.items():
            if not value:
                continue
            label, risk = FIELD_META[field_id]
            output.append(ExtractedField(
                id=field_id,
                value=value,
                label=label,
                confidence=0.99,
                risk_level=risk,
                confirmed=False,
                evidence=[Evidence(
                    document_id=document_id,
                    filename=filename,
                    page=1,
                    excerpt=f"{label} {value}",
                    method="mineru-visible-table-rule",
                )],
            ))
        return output

    @staticmethod
    def _merge(rule_fields, model_fields):
        merged = {item.id: item for item in rule_fields}
        for candidate in model_fields:
            current = merged.get(candidate.id)
            if current is None:
                merged[candidate.id] = candidate
            else:
                for evidence in candidate.evidence:
                    if evidence not in current.evidence:
                        current.evidence.append(evidence)
                if current.value != candidate.value:
                    if candidate.value not in current.alternatives:
                        current.alternatives.append(candidate.value)
                    current.confidence = min(current.confidence, candidate.confidence)
        return sorted(merged.values(), key=lambda item: item.id)
