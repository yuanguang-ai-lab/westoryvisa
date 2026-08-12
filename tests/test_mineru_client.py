import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from backend import mineru_client, ocr_provider


def result_archive(markdown="# Passport\nPassport Number: E12345678"):
    buffer = io.BytesIO()
    content_list = [
        {"type": "text", "page_idx": 0, "text": "Passport Number: E12345678"},
        {"type": "text", "page_idx": 1, "text": "Date of expiry: 31 MAY 2034"},
    ]
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("demo/full.md", markdown)
        archive.writestr(
            "demo/demo_content_list.json",
            json.dumps(content_list, ensure_ascii=False),
        )
    return buffer.getvalue()


class MinerUArchiveTests(unittest.TestCase):
    def test_reads_markdown_and_page_aware_content_list(self):
        markdown, content = mineru_client.parse_result_archive(result_archive())
        self.assertIn("Passport Number", markdown)
        pages = mineru_client.content_list_pages(content)
        self.assertEqual(pages[0]["page"], 1)
        self.assertEqual(pages[1]["page"], 2)
        self.assertIn("Date of expiry", pages[1]["text"])

    def test_rejects_invalid_result_archive(self):
        with self.assertRaisesRegex(mineru_client.MinerUError, "无法读取"):
            mineru_client.parse_result_archive(b"not a zip")


class MinerUConversionTests(unittest.TestCase):
    def test_signed_upload_poll_and_normalized_result(self):
        responses = [
            {
                "code": 0,
                "data": {
                    "batch_id": "batch-1",
                    "file_urls": ["https://upload.example.test/file"],
                },
            },
            {
                "code": 0,
                "data": {
                    "extract_result": [{
                        "data_id": "ignored-by-single-result-fallback",
                        "file_name": "passport.pdf",
                        "state": "done",
                        "full_zip_url": "https://cdn.example.test/result.zip",
                    }],
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "passport.pdf"
            path.write_bytes(b"%PDF-1.4 test")
            with mock.patch.dict(os.environ, {
                "MINERU_API_TOKEN": "secret-token",
                "MINERU_MODEL_VERSION": "vlm",
            }, clear=False), mock.patch.object(
                mineru_client, "_request_json", side_effect=responses
            ) as request_json, mock.patch.object(
                mineru_client, "_upload_file"
            ) as upload_file, mock.patch.object(
                mineru_client, "_download_archive", return_value=result_archive()
            ), mock.patch.object(
                mineru_client.time, "monotonic", side_effect=[10, 10, 12]
            ):
                result = mineru_client.convert_file(path)

        self.assertEqual(result["parser"], "mineru")
        self.assertEqual(result["ocrEngine"], "vlm")
        self.assertIn("Passport Number", result["text"])
        self.assertEqual(result["pages"][1]["page"], 2)
        upload_file.assert_called_once()
        create_payload = request_json.call_args_list[0].kwargs["payload"]
        self.assertEqual(create_payload["model_version"], "vlm")
        self.assertTrue(create_payload["files"][0]["is_ocr"])
        self.assertNotIn("secret-token", json.dumps(result))

    def test_missing_token_stops_before_upload(self):
        with mock.patch.dict(os.environ, {"MINERU_API_TOKEN": ""}, clear=False):
            with self.assertRaisesRegex(mineru_client.MinerUError, "MINERU_API_TOKEN"):
                mineru_client.convert_file("passport.pdf")


class ProviderSelectionTests(unittest.TestCase):
    def test_auto_uses_mineru_only_when_token_is_configured(self):
        with mock.patch.dict(os.environ, {
            "OCR_PROVIDER": "auto",
            "MINERU_API_TOKEN": "configured",
        }, clear=False):
            self.assertEqual(ocr_provider.selected_provider(), "mineru")
        with mock.patch.dict(os.environ, {
            "OCR_PROVIDER": "auto",
            "MINERU_API_TOKEN": "",
        }, clear=False):
            self.assertEqual(ocr_provider.selected_provider(), "docling")

    def test_explicit_docling_does_not_use_cloud_even_with_token(self):
        with mock.patch.dict(os.environ, {
            "OCR_PROVIDER": "docling",
            "MINERU_API_TOKEN": "configured",
        }, clear=False):
            self.assertEqual(ocr_provider.selected_provider(), "docling")


if __name__ == "__main__":
    unittest.main()
