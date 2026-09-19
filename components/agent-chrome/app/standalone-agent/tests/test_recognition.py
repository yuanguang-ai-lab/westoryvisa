import unittest
from pathlib import Path

from visa_agent.mrz import check_digit, parse_td3
from visa_agent.providers import (
    FallbackOCRProvider,
    PlainTextOCRProvider,
    UnconfiguredExtractionModel,
)
from visa_agent.recognition import DocumentRecognizer


ROOT = Path(__file__).resolve().parents[1]


class RecognitionTests(unittest.TestCase):
    def test_i20_mineru_html_table_extracts_school_and_sevis_by_rule(self):
        text = (
            "<table><tr><td>SEVIS ID</td>"
            "<td>N0090001234 ID</td></tr></table>"
            "<table><tr><td>School name</td>"
            "<td>PACIFIC COAST DEMO UNIVERSITY</td></tr></table>"
        )
        result = DocumentRecognizer(
            PlainTextOCRProvider(),
            UnconfiguredExtractionModel(),
        ).recognize(
            text.encode(),
            "i20.txt",
            document_type="I-20 / Enrollment Letter",
        )
        fields = {item.id: item.value for item in result.fields}
        self.assertEqual(
            fields["education.schoolName"],
            "PACIFIC COAST DEMO UNIVERSITY",
        )
        self.assertEqual(
            fields["education.sevisId"],
            "N0090001234",
        )

    def setUp(self):
        self.sample = (
            ROOT / "examples" / "sample_passport_mrz.txt"
        ).read_text(encoding="utf-8")

    def test_td3_parser_validates_known_icao_example(self):
        parsed = parse_td3(self.sample)
        self.assertEqual(parsed["surname"], "ERIKSSON")
        self.assertEqual(parsed["givenNames"], "ANNA MARIA")
        self.assertEqual(parsed["passportNumber"], "L898902C3")
        self.assertEqual(parsed["dateOfBirth"], "1974-08-12")
        self.assertEqual(parsed["expirationDate"], "2012-04-15")
        self.assertTrue(all(parsed["checks"].values()))
        self.assertEqual(check_digit("L898902C3"), "6")

    def test_rule_recognition_works_without_model_api(self):
        recognizer = DocumentRecognizer(
            PlainTextOCRProvider(), UnconfiguredExtractionModel()
        )
        result = recognizer.recognize(
            self.sample.encode(), "passport.txt", document_type="passport"
        )
        fields = {item.id: item for item in result.fields}
        self.assertEqual(fields["passport.number"].value, "L898902C3")
        self.assertEqual(fields["personal.surname"].confidence, 0.98)
        self.assertFalse(fields["personal.surname"].confirmed)
        self.assertEqual(fields["passport.number"].evidence[0].method, "icao-td3-mrz")
        self.assertIn("Model API is not configured", result.warnings[0])

    def test_unreadable_text_is_not_invented(self):
        recognizer = DocumentRecognizer(
            PlainTextOCRProvider(), UnconfiguredExtractionModel()
        )
        result = recognizer.recognize(
            b"unreadable fragment", "passport.txt", document_type="passport"
        )
        self.assertEqual(result.fields, [])
        self.assertIn("No reliable fields were extracted", result.warnings)

    def test_independent_review_warnings_are_included(self):
        class Reviewer:
            def review(self, fields, document_type):
                return ["DeepSeek review: verify passport number"]

        recognizer = DocumentRecognizer(
            PlainTextOCRProvider(),
            UnconfiguredExtractionModel(),
            review_model=Reviewer(),
        )
        result = recognizer.recognize(
            self.sample.encode(), "passport.txt", document_type="passport"
        )
        self.assertIn(
            "DeepSeek review: verify passport number", result.warnings
        )

    def test_plain_text_bypasses_configured_ocr_provider(self):
        class MustNotRunOCR:
            def recognize(self, content, filename, media_type):
                raise AssertionError("plain text must bypass OCR")

        recognizer = DocumentRecognizer(
            MustNotRunOCR(), UnconfiguredExtractionModel()
        )
        result = recognizer.recognize(
            self.sample.encode(), "passport.txt", "text/plain", "passport"
        )
        self.assertTrue(
            any(item.id == "passport.number" for item in result.fields)
        )

    def test_prc_id_missing_number_forces_secondary_ocr(self):
        class Primary:
            def recognize(self, content, filename, media_type):
                return "居民身份证\n姓名 夏意程\n出生 2004年10月29日"

        class Secondary:
            calls = 0

            def recognize(self, content, filename, media_type):
                self.calls += 1
                return "公民身份号码 150203200410290610"

        secondary = Secondary()
        recognizer = DocumentRecognizer(
            FallbackOCRProvider(Primary(), secondary),
            UnconfiguredExtractionModel(),
        )
        result = recognizer.recognize(
            b"rotated-id-image",
            "id.jpg",
            "image/jpeg",
            "national_id",
        )
        self.assertEqual(secondary.calls, 1)
        self.assertIn("150203200410290610", result.raw_text)
        self.assertTrue(
            result.stages["text"]["supplementalFallback"]
        )


if __name__ == "__main__":
    unittest.main()
