import unittest

from visa_agent.config import load_config
from visa_agent.models import Evidence, ExtractedField, RiskLevel
from visa_agent.providers import (
    FallbackOCRProvider,
    PlainTextOCRProvider,
    ProviderRegistry,
)
from visa_agent.recognition import DocumentRecognizer


class StaticExtractionModel:
    def __init__(self, fields):
        self.fields = fields

    def extract(self, text, document_type, filename):
        return self.fields


class FailingOCR:
    def recognize(self, content, filename, media_type):
        raise RuntimeError("primary unavailable")


class StaticParser:
    def parse(self, content, filename, media_type):
        return "MINERU FALLBACK"


class ValidationAndProviderTests(unittest.TestCase):
    def test_model_output_is_allowlisted_evidenced_and_system_owned(self):
        source = "Applicant surname is ZHANG."
        matching = Evidence("wrong-id", "wrong.pdf", 1, "surname is ZHANG", "model")
        fields = [
            ExtractedField(
                id="personal.surname",
                value="ZHANG",
                confidence=0.91,
                risk_level=RiskLevel.LOW,
                confirmed=True,
                evidence=[matching],
            ),
            ExtractedField(
                id="made.up.field",
                value="invented",
                confidence=1.0,
                evidence=[matching],
            ),
            ExtractedField(
                id="passport.number",
                value="E12345678",
                confidence=0.9,
                evidence=[
                    Evidence("wrong", "wrong", 1, "not in source", "model")
                ],
            ),
        ]
        result = DocumentRecognizer(
            PlainTextOCRProvider(), StaticExtractionModel(fields)
        ).recognize(
            source.encode(),
            "source.txt",
            document_type="other",
            document_id="document-expected",
        )
        self.assertEqual([item.id for item in result.fields], ["personal.surname"])
        accepted = result.fields[0]
        self.assertEqual(accepted.risk_level, RiskLevel.HIGH)
        self.assertFalse(accepted.confirmed)
        self.assertIsNone(accepted.confirmation)
        self.assertEqual(accepted.evidence[0].document_id, "document-expected")
        self.assertEqual(accepted.evidence[0].filename, "source.txt")
        self.assertTrue(any("outside allowlist" in item for item in result.warnings))
        self.assertTrue(any("without source evidence" in item for item in result.warnings))

    def test_mineru_html_table_evidence_matches_visible_cell_text(self):
        source = (
            "<table><tr><td>Surname/姓</td>"
            "<td rowspan=1 colspan=1>CHEN</td></tr></table>"
        )
        field = ExtractedField(
            id="personal.surname",
            value="CHEN",
            confidence=0.96,
            evidence=[
                Evidence(
                    "untrusted",
                    "passport.pdf",
                    1,
                    "Surname/姓 CHEN",
                    "model",
                )
            ],
        )
        result = DocumentRecognizer(
            PlainTextOCRProvider(),
            StaticExtractionModel([field]),
        ).recognize(
            source.encode(),
            "passport.pdf",
            document_type="other",
        )
        self.assertEqual(
            [item.id for item in result.fields],
            ["personal.surname"],
        )

    def test_capability_models_are_configured_independently(self):
        config = load_config({
            "OCR_PROVIDER": "mineru",
            "OCR_MODEL": "vlm",
            "OCR_FALLBACK_PROVIDER": "paddle",
            "OCR_FALLBACK_MODEL": "pp-ocr-v6",
            "EXTRACTION_PROVIDER": "openai",
            "EXTRACTION_MODEL": "extraction-model",
            "COMPUTER_USE_PROVIDER": "other-vendor",
            "COMPUTER_USE_MODEL": "computer-model",
            "AGENT_BROWSER_STARTUP_TIMEOUT_SECONDS": "18",
        })
        self.assertEqual(config.ocr.provider, "mineru")
        self.assertEqual(config.ocr_fallback.provider, "paddle")
        self.assertEqual(config.extraction.model, "extraction-model")
        self.assertEqual(config.computer_use.provider, "other-vendor")
        self.assertEqual(config.computer_use.model, "computer-model")
        self.assertEqual(config.browser_startup_timeout_seconds, 18.0)
        self.assertFalse(config.allow_plaintext_checkpoints)

    def test_provider_registry_can_replace_an_adapter_without_domain_changes(self):
        registry = ProviderRegistry()
        registry.register("ocr", "first", lambda config: ("first", config.model))
        registry.register("ocr", "second", lambda config: ("second", config.model))
        first = load_config({"OCR_PROVIDER": "first", "OCR_MODEL": "a"})
        second = load_config({"OCR_PROVIDER": "second", "OCR_MODEL": "b"})
        self.assertEqual(registry.create("ocr", first.ocr), ("first", "a"))
        self.assertEqual(registry.create("ocr", second.ocr), ("second", "b"))

    def test_ocr_uses_mineru_style_parser_as_fallback(self):
        provider = FallbackOCRProvider(FailingOCR(), StaticParser())
        self.assertEqual(
            provider.recognize(b"image", "passport.png", "image/png"),
            "MINERU FALLBACK",
        )

    def test_ocr_keeps_usable_primary_result(self):
        provider = FallbackOCRProvider(
            PlainTextOCRProvider(), StaticParser()
        )
        self.assertEqual(
            provider.recognize(b"PADDLE RESULT", "passport.png", "image/png"),
            "PADDLE RESULT",
        )


if __name__ == "__main__":
    unittest.main()
