import tempfile
import unittest
import re
import json
import os
from pathlib import Path
from unittest import mock

import server
import docling_client
from docling_client import (
    choose_image_orientation,
    extract_page_texts,
    multipart_body,
    orientation_layout_score,
    orientation_result_score,
    orientation_text_score,
    text_quality_score,
)
from ds160_mapper import map_document, merge_extracted_fields
from ds160_intake_schema import CLIENT_INTAKE_FIELDS
from ds160_rules import (
    build_questionnaire,
    details_complete,
    infer_questionnaire_answers,
    questionnaire_issues,
    sync_questionnaire_fields,
)
from email_service import mail_service_status, sendEmail


PASSPORT_MRZ = """P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<
L898902C36UTO7408122F1204159ZE184226B<<<<<10"""


class Ds160MapperTests(unittest.TestCase):
    def test_national_id_maps_identity_fields(self):
        fields = map_document(
            "身份证 / National ID",
            "national-id.png",
            "姓名 张明\n性别 男\n出生 1994年05月14日\n公民身份号码 370202199405141234",
            "document-national-id",
            "B1/B2 访问签证",
        )
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["personal.nativeName"]["value"], "张明")
        self.assertEqual(by_id["personal.nationalId"]["value"], "370202199405141234")
        self.assertEqual(by_id["personal.dateOfBirth"]["value"], "1994-05-14")
        self.assertEqual(by_id["personal.sex"]["value"], "MALE")

    def test_passport_mrz_maps_checked_fields(self):
        fields = map_document(
            "护照", "passport.pdf", PASSPORT_MRZ, "document-1", "B1/B2 访问签证"
        )
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["personal.surname"]["value"], "ERIKSSON")
        self.assertEqual(by_id["personal.givenNames"]["value"], "ANNA MARIA")
        self.assertEqual(by_id["passport.number"]["value"], "L898902C3")
        self.assertEqual(by_id["passport.number"]["confidence"], 0.99)
        self.assertEqual(by_id["personal.dateOfBirth"]["value"], "1974-08-12")

    def test_docling_markdown_table_maps_passport_and_travel_fields(self):
        passport_text = """| 护照号 / Passport No. | E12345678 |
| 姓名 / Name | ZHANG, WEI |
| 出生日期 / Date of birth | 14 MAY 1994 |
| 有效期至 / Date of expiry | 31 MAY 2034 |"""
        passport_fields = map_document(
            "护照", "passport.pdf", passport_text, "document-markdown-passport", "B1/B2 访问签证"
        )
        passport_by_id = {field["id"]: field for field in passport_fields}
        self.assertEqual(passport_by_id["passport.number"]["value"], "E12345678")
        self.assertEqual(passport_by_id["personal.surname"]["value"], "ZHANG")
        self.assertEqual(passport_by_id["personal.givenNames"]["value"], "WEI")
        self.assertEqual(passport_by_id["passport.expiration"]["value"], "2034-05-31")

        travel_fields = map_document(
            "旅行行程单",
            "itinerary.pdf",
            "旅行日期: 18 JUL 2026 - 24 JUL 2026\nDemo Air DA101 Arrival\nDemo Air DA102 Departure",
            "document-markdown-travel",
            "B1/B2 访问签证",
        )
        travel_by_id = {field["id"]: field for field in travel_fields}
        self.assertEqual(travel_by_id["travel.arrivalDate"]["value"], "2026-07-18")
        self.assertEqual(travel_by_id["travel.departureDate"]["value"], "2026-07-24")
        self.assertEqual(travel_by_id["travel.arrivalFlight"]["value"], "DA101")
        self.assertEqual(travel_by_id["travel.departureFlight"]["value"], "DA102")

    def test_passport_expiry_survives_bilingual_labels_and_ocr_noise(self):
        passport_text = """护照号 / Passport No.: DEMO000001
出生日期 / Date of birth: 14 MAY 1994
签发日期 / Date of issue: 01 JUN 2024
有效期至 / Date of expiry: · NOT 31 MAY 2034"""
        fields = map_document(
            "护照", "passport.pdf", passport_text, "document-noisy-expiry", "B1/B2 访问签证"
        )
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["passport.expiration"]["value"], "2034-05-31")

    def test_passport_expiry_can_be_on_the_line_after_label(self):
        fields = map_document(
            "护照",
            "passport.pdf",
            "Passport Number: E12345678\nDate of Expiration\n2033 SEP 07",
            "document-split-expiry",
            "F1 学生签证",
        )
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["passport.expiration"]["value"], "2033-09-07")

    def test_chinese_passport_handles_spaced_mrz_and_supplements_visible_fields(self):
        text = """中华人民共和国 PEOPLE'S REPUBLIC OF CHINA
P < CHN ZHANG << WEI <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
E 1 2 3 4 5 6 7 8 2 CHN 9 4 0 5 1 4 7 M 3 4 0 5 3 1 8 <<<<<<<<<<<<<<<< < 0
签发日期 / Date of Issue: 01 JUN 2024
出生地点 / Place of Birth: SHANDONG"""
        fields = map_document(
            "护照", "china-passport.jpg", text, "document-china-passport", "B1/B2 访问签证"
        )
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["passport.number"]["value"], "E12345678")
        self.assertEqual(by_id["personal.surname"]["value"], "ZHANG")
        self.assertEqual(by_id["personal.givenNames"]["value"], "WEI")
        self.assertEqual(by_id["personal.nationality"]["value"], "CHINA")
        self.assertEqual(by_id["passport.issueDate"]["value"], "2024-06-01")

    def test_passport_handles_reversed_mrz_lines_and_ocr_filler_substitution(self):
        first, second = PASSPORT_MRZ.splitlines()
        fields = map_document(
            "护照",
            "rotated-passport.png",
            f"{second} {first.replace('P<', 'P0', 1)}",
            "document-rotated-passport",
            "B1/B2 访问签证",
        )
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["personal.surname"]["value"], "ERIKSSON")
        self.assertEqual(by_id["personal.givenNames"]["value"], "ANNA MARIA")
        self.assertEqual(by_id["passport.number"]["value"], "L898902C3")

    def test_passport_rejects_bilingual_labels_as_field_values(self):
        fields = map_document(
            "护照",
            "sideways-passport.png",
            "Surname\n中国/CHINESE\nGiven Names\n国籍/NADONALITY\n"
            "Passport No.\ncase of need.",
            "document-sideways-passport",
            "B1/B2 访问签证",
        )
        field_ids = {field["id"] for field in fields}
        self.assertNotIn("personal.surname", field_ids)
        self.assertNotIn("personal.givenNames", field_ids)
        self.assertNotIn("passport.number", field_ids)

    def test_passport_uses_mrz_to_validate_native_name_and_reads_prefix_places(self):
        first = "P<CHNZHANG<<WEI<<<<<<<<<<<<<<<<<<<<<<<<<<<<"
        second = "E123456782CHN9405147M3405318<<<<<<<<<<<<<<04"
        fields = map_document(
            "护照",
            "china-passport.png",
            f"{second} {first}\n"
            "姓名/Name\n中国国籍\n张伟\n"
            "浙江/ZHEJIANG 签发地点/Place of issue\n"
            "浙江/ZHEJIANG 出生地点/Place of birth\n"
            "National Immigration Administration, PRC 签发机关/Authority",
            "document-china-passport-layout",
            "B1/B2 访问签证",
        )
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["personal.nativeName"]["value"], "张伟")
        self.assertEqual(by_id["passport.issuePlace"]["value"], "ZHEJIANG")
        self.assertEqual(by_id["personal.placeOfBirth"]["value"], "ZHEJIANG")
        self.assertEqual(
            by_id["passport.issuingAuthority"]["value"],
            "NATIONAL IMMIGRATION ADMINISTRATION, PRC",
        )

    def test_passport_place_parser_does_not_return_neighboring_bilingual_label(self):
        fields = map_document(
            "护照",
            "china-passport.jpg",
            "护照号码/Passport No. E12345678\n"
            "浙江/ZHEJIANG 签发地点/Place of issue 浙江/ZHEJIANG "
            "出生地点/Place of birth",
            "document-passport-places",
            "B1/B2 访问签证",
        )
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["passport.issuePlace"]["value"], "ZHEJIANG")
        self.assertNotIn("personal.placeOfBirth", by_id)

    def test_chinese_id_handles_spaced_number_and_multiline_address(self):
        text = """中华人民共和国居民身份证
姓名 张三
性别 女 民族 汉
出生 1949年12月31日
住址 北京市朝阳区
建国路 88 号
公民身份号码 1 1 0 1 0 5 1 9 4 9 1 2 3 1 0 0 2 X"""
        fields = map_document(
            "身份证 / National ID", "china-id.jpg", text, "document-china-id", "B1/B2 访问签证"
        )
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["personal.nativeName"]["value"], "张三")
        self.assertEqual(by_id["personal.nationalId"]["value"], "11010519491231002X")
        self.assertEqual(by_id["personal.dateOfBirth"]["value"], "1949-12-31")
        self.assertEqual(by_id["personal.sex"]["value"], "FEMALE")
        self.assertEqual(by_id["personal.nationalId"]["confidence"], 0.99)

    def test_chinese_id_reassembles_bottom_to_top_ocr_rows_by_field_meaning(self):
        text = """## 公民身份号码 11010519491231002X

<!-- image -->

602室

宁康东路花好悦园1幢

住址浙江省温州市乐清市乐成街道

出生1949年12月31日

性别女民族汉

张三

姓名"""
        fields = map_document(
            "身份证 / National ID",
            "bottom-to-top-id.jpg",
            text,
            "document-bottom-to-top-id",
            "B1/B2 访问签证",
        )
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["personal.nativeName"]["value"], "张三")
        self.assertEqual(by_id["personal.sex"]["value"], "FEMALE")
        self.assertEqual(by_id["personal.sex"]["extractionMethod"], "label")
        self.assertIn("性别女民族汉", by_id["personal.sex"]["evidence"])
        self.assertEqual(
            by_id["contact.homeStreet1"]["value"],
            "ROOM 602, BUILDING 1, HUAHAOYUEYUAN",
        )
        self.assertEqual(
            by_id["contact.homeStreet2"]["value"],
            "NINGKANG EAST ROAD, LECHENG SUBDISTRICT",
        )
        self.assertEqual(by_id["contact.homeCity"]["value"], "LEQING")
        self.assertEqual(by_id["contact.homeRegion"]["value"], "ZHEJIANG")

    def test_student_fields_do_not_map_for_b_visa(self):
        text = """SEVIS ID: N0034567891
School Name: Northwest State University
School Address: 1200 College Ave, Seattle, WA
Program of Study: Computer Science"""
        f1_fields = map_document(
            "I-20 / 录取或在读证明", "i20.pdf", text, "document-2", "F1 学生签证"
        )
        b_fields = map_document(
            "I-20 / 录取或在读证明", "i20.pdf", text, "document-2", "B1/B2 访问签证"
        )
        self.assertIn("education.sevisId", {field["id"] for field in f1_fields})
        self.assertEqual(b_fields, [])

    def test_j1_and_previous_visa_safe_fields_map_without_sensitive_answers(self):
        ds2019 = """SEVIS ID: N0034567891
Program Number: P-4-12345
Sponsor Name: Example Exchange Foundation
Program Name: Research Scholar"""
        j1_fields = map_document(
            "DS-2019 / 交流项目材料", "ds2019.pdf", ds2019, "document-j1", "J1 交流访问签证"
        )
        previous_visa = """Visa Number: 12345678
Issue Date: 10 JUL 2024
Visa Class: B1/B2"""
        visa_fields = map_document(
            "过往美国签证", "visa.pdf", previous_visa, "document-visa", "B1/B2 访问签证"
        )
        field_ids = {field["id"] for field in j1_fields + visa_fields}
        self.assertIn("education.programNumber", field_ids)
        self.assertIn("education.sponsorName", field_ids)
        self.assertIn("history.previousVisaNumber", field_ids)
        self.assertFalse(any(field_id.startswith("security.") for field_id in field_ids))
        self.assertFalse(any(field_id.startswith("history.refusal") for field_id in field_ids))

    def test_visa_control_number_is_not_treated_as_visa_number(self):
        fields = map_document(
            "过往美国签证",
            "visa.pdf",
            "Control Number: 20261234567890\nVisa Class: B1/B2",
            "document-visa-control",
            "B1/B2 访问签证",
        )
        self.assertNotIn("history.previousVisaNumber", {field["id"] for field in fields})

    def test_manual_mismatch_creates_review_issue(self):
        existing = [{
            "id": "passport.number",
            "label": "护照号码",
            "section": "护照信息",
            "value": "E00000000",
            "sourceDocument": "客户档案",
            "confidence": 1,
            "riskLevel": "high",
            "confirmed": False,
            "editedByUser": False,
        }]
        extracted = map_document(
            "护照", "passport.pdf", PASSPORT_MRZ, "document-1", "B1/B2 访问签证"
        )
        merged, issues = merge_extracted_fields(existing, extracted, "B1/B2 访问签证")
        self.assertEqual(
            next(field for field in merged if field["id"] == "passport.number")["value"],
            "L898902C3",
        )
        self.assertIn(
            "ocr.conflict.manual.passport.number",
            {issue["id"] for issue in issues},
        )

    def test_only_noncritical_consistent_fields_are_auto_verified(self):
        extracted = map_document(
            "在职证明",
            "employment.pdf",
            "Employer Name: Example Technology Co., Ltd.\nEmployer Phone: +86 138 0000 0000",
            "document-employment",
            "B1/B2 访问签证",
        ) + map_document(
            "护照", "passport.pdf", PASSPORT_MRZ, "document-passport", "B1/B2 访问签证"
        )
        merged, _ = merge_extracted_fields([], extracted, "B1/B2 访问签证")
        by_id = {field["id"]: field for field in merged}
        self.assertTrue(by_id["work.employerName"]["autoVerified"])
        self.assertFalse(by_id["passport.number"]["autoVerified"])


class DoclingClientTests(unittest.TestCase):
    def test_page_text_and_multipart_options(self):
        pages = extract_page_texts({
            "texts": [
                {"text": "Passport Number: L898902C3", "prov": [{"page_no": 1}]},
                {"text": "School Name: Example University", "prov": [{"page_no": 2}]},
            ]
        })
        self.assertEqual([page["page"] for page in pages], [1, 2])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.pdf"
            path.write_bytes(b"%PDF-1.4 test")
            body, boundary = multipart_body(path, path.name, "application/pdf")
        self.assertTrue(boundary.startswith("----DocFlow"))
        self.assertIn(b'name="ocr_preset"', body)
        self.assertIn(b"rapidocr", body)
        self.assertIn(b'name="files"', body)

    def test_force_ocr_option_and_quality_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.png"
            path.write_bytes(b"mock image")
            body, _ = multipart_body(
                path, path.name, "image/png", force_ocr=True
            )
        self.assertIn(b'name="force_ocr"\r\n\r\ntrue', body)
        self.assertGreater(text_quality_score(PASSPORT_MRZ), 0.7)
        self.assertLess(text_quality_score(""), 0.1)

    def test_identity_document_orientation_score_rewards_document_anchors(self):
        sideways_noise = "1 1 0 1 0 5 scattered glyphs"
        upright = "居民身份证\n姓名 张三\n性别 女\n公民身份号码 11010519491231002X"
        self.assertGreater(
            orientation_text_score(upright, "national_id"),
            orientation_text_score(sideways_noise, "national_id") + 0.2,
        )

    def test_orientation_layout_score_distinguishes_sideways_text_boxes(self):
        vertical = {
            "texts": [{
                "text": "PEOPLE'S REPUBLIC OF CHINA Passport Number",
                "prov": [{"bbox": {"l": 10, "r": 35, "b": 10, "t": 410}}],
            }]
        }
        horizontal = {
            "texts": [{
                "text": "PEOPLE'S REPUBLIC OF CHINA Passport Number",
                "prov": [{"bbox": {"l": 10, "r": 410, "b": 10, "t": 35}}],
            }]
        }
        self.assertGreater(
            orientation_layout_score(horizontal),
            orientation_layout_score(vertical) + 0.8,
        )
        text = "PEOPLE'S REPUBLIC OF CHINA\nPassport Number\nNationality\nDate of birth"
        self.assertGreater(
            orientation_result_score({"text": text, "json": horizontal}, "passport"),
            orientation_result_score({"text": text, "json": vertical}, "passport") + 0.3,
        )

    def test_readable_sideways_passport_is_rotated_using_text_box_geometry(self):
        text = "PEOPLE'S REPUBLIC OF CHINA\nPassport Number\nNationality\nDate of birth"

        def layout(width, height):
            return {"texts": [{
                "text": text,
                "prov": [{"bbox": {"l": 0, "r": width, "b": 0, "t": height}}],
            }]}

        def conversion(width, height):
            return {
                "status": "success",
                "document": {"text_content": text, "json_content": layout(width, height)},
            }

        results = iter([
            conversion(30, 400),
            conversion(30, 400),
            conversion(400, 30),
        ])
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            docling_client, "image_rotation_available", return_value=True
        ), mock.patch.object(
            docling_client,
            "rotate_image",
            side_effect=lambda source, target, degrees: Path(target).write_bytes(b"rotated"),
        ), mock.patch.object(
            docling_client, "request_conversion", side_effect=lambda *args, **kwargs: next(results)
        ):
            path = Path(directory) / "passport.jpg"
            path.write_bytes(b"source")
            selected = choose_image_orientation(
                path,
                path.name,
                "image/jpeg",
                30,
                {"text": text, "json": layout(30, 400), "forcedOcr": True},
                document_type="护照",
            )
        self.assertTrue(selected["autoRotated"])
        self.assertEqual(selected["rotationApplied"], 270)

    def test_low_confidence_image_selects_best_ocr_rotation(self):
        def conversion(text):
            return {
                "status": "success",
                "document": {"text_content": text, "json_content": {}},
            }

        results = iter([
            conversion("fragment"),
            conversion("居民身份证\n姓名 张三\n性别 女\n出生 1949年12月31日\n公民身份号码 11010519491231002X"),
            conversion("upside down fragment"),
        ])
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            docling_client, "image_rotation_available", return_value=True
        ), mock.patch.object(
            docling_client,
            "rotate_image",
            side_effect=lambda source, target, degrees: Path(target).write_bytes(b"rotated"),
        ), mock.patch.object(
            docling_client, "request_conversion", side_effect=lambda *args, **kwargs: next(results)
        ):
            path = Path(directory) / "id.jpg"
            path.write_bytes(b"source")
            selected = choose_image_orientation(
                path,
                path.name,
                "image/jpeg",
                30,
                {"text": "unreadable fragment", "forcedOcr": True},
                document_type="身份证 / National ID",
            )
        self.assertTrue(selected["autoRotated"])
        self.assertEqual(selected["rotationApplied"], 180)
        self.assertIn("公民身份号码", selected["text"])


class DoclingServiceStartupTests(unittest.TestCase):
    def test_missing_local_scanner_returns_install_instruction(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            server, "ROOT", Path(directory)
        ), mock.patch.object(
            server,
            "ocr_service_status",
            return_value={"available": False, "installed": False},
        ):
            with self.assertRaisesRegex(server.DoclingError, "安装文档扫描"):
                server.start_docling_service()

    def test_installed_scanner_is_started_once_and_waited_until_ready(self):
        class FakeProcess:
            def __init__(self):
                self.running = True

            def poll(self):
                return None if self.running else 0

            def terminate(self):
                self.running = False

            def wait(self, timeout=None):
                return 0

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / ".venv-docling" / "bin" / "docling-serve"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            fake_process = FakeProcess()
            original_globals = (
                server.ROOT,
                server.DATA_DIR,
                server.DOCLING_PROCESS,
                server.DOCLING_LOG_HANDLE,
            )
            server.ROOT = root
            server.DATA_DIR = root / "data"
            server.DOCLING_PROCESS = None
            server.DOCLING_LOG_HANDLE = None
            try:
                with mock.patch.object(
                    server,
                    "ocr_service_status",
                    side_effect=[
                        {"available": False, "installed": True},
                        {"available": True, "installed": True, "message": "服务可用"},
                    ],
                ), mock.patch.object(
                    server, "check_docling", return_value={"available": True}
                ), mock.patch.object(
                    server.subprocess, "Popen", return_value=fake_process
                ) as popen:
                    result = server.start_docling_service()
                self.assertTrue(result["available"])
                self.assertFalse(result["starting"])
                self.assertEqual(popen.call_count, 1)
                self.assertTrue((server.DATA_DIR / "docling_api_key").exists())
            finally:
                server.stop_managed_docling_service()
                (
                    server.ROOT,
                    server.DATA_DIR,
                    server.DOCLING_PROCESS,
                    server.DOCLING_LOG_HANDLE,
                ) = original_globals


class EmailServiceTests(unittest.TestCase):
    def test_explicit_file_mode_writes_local_test_outbox(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = Path(directory) / "outbox.eml"
            with mock.patch.dict(
                "os.environ",
                {
                    "MAIL_PROVIDER": "file",
                    "DOCFLOW_EMAIL_OUTBOX": str(outbox),
                },
                clear=True,
            ):
                self.assertTrue(mail_service_status()["configured"])
                result = sendEmail(
                    "reviewer@example.com",
                    "DocFlow 注册邮箱验证码",
                    "验证码：123456",
                )
            self.assertEqual(result["mode"], "file")
            self.assertIn("123456", outbox.read_text(encoding="utf-8"))


class Ds160BranchRuleTests(unittest.TestCase):
    def test_f2_and_j2_have_dependent_specific_branches(self):
        f2_ids = {item["id"] for item in build_questionnaire("F2 学生家属签证")}
        j2_ids = {item["id"] for item in build_questionnaire("J2 交流家属签证")}
        self.assertIn("dependent.principal_applicant", f2_ids)
        self.assertIn("fj.additional_contacts", f2_ids)
        self.assertIn("inadmissibility.f1_public_school", f2_ids)
        self.assertNotIn("j.intends_to_study", f2_ids)
        self.assertIn("dependent.principal_applicant", j2_ids)
        self.assertIn("fj.additional_contacts", j2_ids)
        self.assertIn("j.intends_to_study", j2_ids)
        self.assertNotIn("inadmissibility.f1_public_school", j2_ids)

    def test_fj_additional_contacts_are_optional_in_client_collection(self):
        questionnaire = build_questionnaire("F1 学生签证")
        contacts = next(item for item in questionnaire if item["id"] == "fj.additional_contacts")
        self.assertEqual(contacts["minRecords"], 0)
        self.assertTrue(contacts["clientOptional"])
        self.assertEqual(contacts["status"], "已回答")
        complete_contact = {
            "surname": "LI", "givenNames": "MING", "address": "1 Example Road",
            "country": "CHINA", "phone": "+8613800000000",
            "email": "ming@example.com", "relationship": "FRIEND",
        }
        contacts["records"] = [complete_contact]
        questionnaire = build_questionnaire("F1 学生签证", questionnaire)
        contacts = next(item for item in questionnaire if item["id"] == "fj.additional_contacts")
        self.assertEqual(contacts["status"], "已回答")

    def test_visa_specific_rules_and_sensitive_defaults(self):
        b_questions = build_questionnaire("B1/B2 访问签证")
        f_questions = build_questionnaire("F1 学生签证")
        self.assertNotIn("inadmissibility.f1_public_school", {item["id"] for item in b_questions})
        self.assertIn("inadmissibility.f1_public_school", {item["id"] for item in f_questions})
        sensitive = [item for item in f_questions if item["sensitive"]]
        self.assertTrue(sensitive)
        self.assertTrue(all(item["answer"] == "" for item in sensitive))
        self.assertTrue(all(item["status"] == "待客户确认" for item in sensitive))
        sensitive_ids = {item["id"] for item in sensitive}
        self.assertIn("security.trafficking_participation", sensitive_ids)
        self.assertIn("security.trafficking_assistance", sensitive_ids)
        self.assertIn("security.trafficking_benefit", sensitive_ids)
        self.assertIn("security.terrorist_support", sensitive_ids)
        self.assertNotIn("security.trafficking", sensitive_ids)

    def test_widowed_uses_deceased_spouse_branch(self):
        questionnaire = build_questionnaire("B1/B2 访问签证")
        marital = next(item for item in questionnaire if item["id"] == "personal.marital_status")
        marital["answer"] = "widowed"
        questionnaire = build_questionnaire("B1/B2 访问签证", questionnaire)
        by_id = {item["id"]: item for item in questionnaire}
        self.assertFalse(by_id["personal.current_spouse"]["visible"])
        self.assertTrue(by_id["personal.deceased_spouse"]["visible"])

    def test_parent_no_clears_triggered_records(self):
        questionnaire = build_questionnaire("B1/B2 访问签证")
        by_id = {item["id"]: item for item in questionnaire}
        by_id["companions.has_companions"]["answer"] = "yes"
        questionnaire = build_questionnaire("B1/B2 访问签证", questionnaire)
        by_id = {item["id"]: item for item in questionnaire}
        by_id["companions.is_group"]["answer"] = "no"
        questionnaire = build_questionnaire("B1/B2 访问签证", questionnaire)
        by_id = {item["id"]: item for item in questionnaire}
        people = by_id["companions.people"]
        people["records"] = [{"surname": "LI", "givenNames": "MING", "relationship": "FRIEND"}]
        by_id["companions.has_companions"]["answer"] = "no"
        questionnaire = build_questionnaire("B1/B2 访问签证", questionnaire)
        by_id = {item["id"]: item for item in questionnaire}
        self.assertFalse(by_id["companions.people"]["visible"])
        self.assertEqual(by_id["companions.people"]["records"], [])

    def test_material_prefill_and_sensitive_issue_generation(self):
        questionnaire = build_questionnaire(
            "F1 学生签证",
            extracted_fields=[{
                "id": "contact.usAddress",
                "value": "1200 College Ave, Seattle, WA",
                "sourceDocument": "i20.pdf",
            }],
        )
        travel = next(item for item in questionnaire if item["id"] == "travel.specific_plans")
        self.assertEqual(travel["details"]["usStreet1"], "1200 College Ave")
        self.assertEqual(travel["details"]["usCity"], "Seattle")
        self.assertEqual(travel["details"]["usState"], "WA")
        issues = questionnaire_issues(questionnaire)
        self.assertIn("branch.sensitive.unanswered", {item["id"] for item in issues})

    def test_screenshot_choice_fields_match_ceac_control_shapes(self):
        questionnaire = build_questionnaire("F1 学生签证")
        by_id = {item["id"]: item for item in questionnaire}
        travel_fields = {
            field["id"]: field
            for field in by_id["travel.specific_plans"]["detailFields"]
        }
        companion_fields = {
            field["id"]: field
            for field in by_id["companions.people"]["recordFields"]
        }
        visa_fields = {
            field["id"]: field
            for field in by_id["us_history.previous_visa"]["detailFields"]
        }
        self.assertEqual(travel_fields["stayUnit"]["type"], "select")
        self.assertNotIn("usStreet1", travel_fields)
        intake_fields = {field["id"]: field for field in CLIENT_INTAKE_FIELDS}
        self.assertEqual(intake_fields["contact.usStreet1"]["section"], "在美停留地址")
        self.assertFalse(intake_fields["contact.usStreet2"]["required"])
        self.assertEqual(companion_fields["relationship"]["type"], "select")
        self.assertEqual(visa_fields["sameClass"]["type"], "select")
        self.assertEqual(visa_fields["tenPrinted"]["type"], "select")

    def test_applicant_email_never_prefills_us_contact_email(self):
        questionnaire = build_questionnaire(
            "B1/B2 访问签证",
            extracted_fields=[{
                "id": "contact.email", "value": "applicant@example.com",
                "sourceDocument": "客户资料",
            }],
        )
        us_contact = next(
            item for item in questionnaire if item["id"] == "us_contact.knows_person"
        )
        self.assertNotIn("email", us_contact["details"])

        questionnaire = build_questionnaire(
            "B1/B2 访问签证",
            extracted_fields=[{
                "id": "contact.usEmail", "value": "host@example.com",
                "sourceDocument": "邀请函",
            }],
        )
        us_contact = next(
            item for item in questionnaire if item["id"] == "us_contact.knows_person"
        )
        self.assertEqual(us_contact["details"]["email"], "host@example.com")

    def test_confirmed_branch_details_sync_to_ds160_draft_fields(self):
        questionnaire = build_questionnaire("B1/B2 访问签证")
        travel = next(item for item in questionnaire if item["id"] == "travel.specific_plans")
        travel["answer"] = "no"
        travel["source"] = "客户确认"
        travel["details"] = {
            "arrivalDate": "2026-09-12",
            "stayDuration": "14 DAYS",
            "usAddress": "350 Fifth Avenue, New York, NY",
        }
        questionnaire = build_questionnaire("B1/B2 访问签证", questionnaire)
        fields = {item["id"]: item for item in sync_questionnaire_fields([], questionnaire)}
        self.assertEqual(fields["travel.arrivalDate"]["value"], "2026-09-12")
        self.assertEqual(fields["contact.usAddress"]["extractionMethod"], "questionnaire")

    def test_student_school_material_is_reused_and_secondary_has_no_major_requirement(self):
        questionnaire = build_questionnaire("F1 学生签证")
        occupation = next(item for item in questionnaire if item["id"] == "work.primary_occupation")
        occupation["answer"] = "student"
        questionnaire = build_questionnaire(
            "F1 学生签证",
            questionnaire,
            extracted_fields=[
                {"id": "education.schoolName", "value": "QINGDAO NO. 2 HIGH SCHOOL"},
                {"id": "education.schoolAddress", "value": "10 HONG KONG ROAD, QINGDAO"},
            ],
        )
        occupation = next(item for item in questionnaire if item["id"] == "work.primary_occupation")
        self.assertEqual(occupation["details"]["organization"], "QINGDAO NO. 2 HIGH SCHOOL")
        self.assertEqual(occupation["details"]["schoolLevel"], "secondary")
        self.assertNotIn("courseOfStudy", occupation["details"])
        current_course = next(
            field for field in occupation["detailFields"] if field["id"] == "courseOfStudy"
        )
        self.assertTrue(current_course["required"])
        self.assertEqual(current_course["hideWhen"], {
            "field": "schoolLevel", "values": ["secondary"],
        })
        public_occupation = server.public_question_definition(occupation)
        self.assertNotIn(
            "courseOfStudy",
            {field["id"] for field in public_occupation["detailFields"]},
        )
        occupation["details"].update({
            "phone": "0532-55555555",
            "startDate": "2021-09-01",
        })
        self.assertTrue(details_complete(occupation))
        occupation["details"]["schoolLevel"] = "college"
        self.assertFalse(details_complete(occupation))
        education = next(
            item for item in questionnaire if item["id"] == "work.education_secondary_or_above"
        )
        course = next(field for field in education["recordFields"] if field["id"] == "course")
        self.assertTrue(course["required"])
        education["answer"] = "yes"
        education["records"] = [{
            "level": "secondary",
            "school": "QINGDAO NO. 2 HIGH SCHOOL",
            "address": "10 HONG KONG ROAD, QINGDAO",
            "startDate": "2021-09-01",
            "endDate": "2024-06-30",
        }]
        self.assertTrue(details_complete(education))
        education["records"][0]["level"] = "college"
        self.assertFalse(details_complete(education))
        public_education = server.public_question_definition(education)
        public_record_ids = {field["id"] for field in public_education["recordFields"]}
        self.assertFalse(
            {"address", "city", "region", "postalCode", "country"}
            & public_record_ids
        )

    def test_intended_travel_data_does_not_imply_specific_travel_plans(self):
        questionnaire = build_questionnaire("B1/B2 访问签证")
        inferred, _ = infer_questionnaire_answers(
            questionnaire,
            extracted_fields=[{
                "id": "travel.arrivalDate",
                "label": "预计抵达日期",
                "value": "2026-10-06",
                "sourceDocument": "客户行程说明.pdf",
            }],
        )
        travel = next(item for item in inferred if item["id"] == "travel.specific_plans")
        self.assertEqual(travel["answer"], "")
        required_for_both = {
            field["id"] for field in travel["detailFields"]
            if field["required"] and not field.get("when")
        }
        self.assertEqual(required_for_both, {"arrivalDate", "stayLength", "stayUnit"})

    def test_planned_submission_date_is_not_collected(self):
        self.assertNotIn(
            "application.plannedSubmissionDate",
            {field["id"] for field in CLIENT_INTAKE_FIELDS},
        )

    def test_explicit_document_answer_is_extracted_without_defaulting_others(self):
        questionnaire = build_questionnaire("B1/B2 访问签证")
        inferred, issues = infer_questionnaire_answers(
            questionnaire,
            [{
                "fileName": "客户确认表.pdf",
                "text": (
                    "Have you ever been refused a U.S. visa, refused admission, "
                    "or withdrawn your application for admission? Answer: No"
                ),
            }],
            [],
        )
        rebuilt = build_questionnaire("B1/B2 访问签证", inferred)
        by_id = {item["id"]: item for item in rebuilt}
        refusal = by_id["us_history.refusal_or_admission"]
        self.assertEqual(refusal["answer"], "no")
        self.assertTrue(refusal["autoDetermined"])
        self.assertIn("客户确认表.pdf", refusal["source"])
        self.assertEqual(by_id["us_history.immigrant_petition"]["answer"], "")
        self.assertEqual(issues, [])


class ServerPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.original_paths = (server.DATA_DIR, server.UPLOAD_DIR, server.DB_PATH)
        root = Path(self.temp_directory.name)
        server.DATA_DIR = root / "data"
        server.UPLOAD_DIR = server.DATA_DIR / "uploads"
        server.DB_PATH = server.DATA_DIR / "test.sqlite3"
        server.init_db()
        delivered_codes = {}
        original_send_email = server.sendEmail
        original_mail_status = server.mail_service_status
        original_verification_mode = server.registration_verification_mode
        server.mail_service_status = lambda: {
            "configured": True, "provider": "test", "message": "测试服务已配置"
        }
        server.registration_verification_mode = lambda: "email"
        server.sendEmail = lambda email, subject, text, html="": (
            delivered_codes.update({email: re.search(r"\d{6}", text).group(0)})
            or {"mode": "test", "provider": "test"}
        )
        try:
            server.request_email_verification({"email": "reviewer@example.com"})
            self.user = server.register_user({
                "email": "reviewer@example.com",
                "emailCode": delivered_codes["reviewer@example.com"],
                "password": "secure-pass-123",
                "organizationName": "Test Visa Team",
                "name": "Reviewer",
                "phone": "+8613800000000",
                "role": "reviewer",
            })
        finally:
            server.sendEmail = original_send_email
            server.mail_service_status = original_mail_status
            server.registration_verification_mode = original_verification_mode
        self.case_id = "app-test-ocr"
        self.document_id = f"{self.case_id}-doc-0"
        server.upsert_case({
            "id": self.case_id,
            "applicantName": "Anna Eriksson",
            "visaType": "B1/B2 访问签证",
            "currentStep": 1,
            "documents": [{
                "id": self.document_id,
                "slot": "护照",
                "fileName": "",
                "scanStatus": "empty",
            }],
            "extractedFields": [{
                "id": "travel.visaType",
                "label": "签证类型 / 访问目的",
                "section": "旅行信息",
                "value": "B1/B2 访问签证",
                "sourceDocument": "客户档案",
                "confidence": 1,
                "riskLevel": "medium",
                "requiresUserConfirmation": True,
                "confirmed": False,
                "editedByUser": False,
            }],
            "missingQuestions": [],
            "validationResults": [],
            "agentTimeline": [{"name": "OCR / 文档解析 Agent", "status": "pending", "output": ""}],
            "caseMeta": {"owner": "Reviewer", "passportNumber": ""},
        }, self.user)

    def test_uploaded_slot_only_covers_fields_that_were_actually_extracted(self):
        payload = {
            "visaType": "B1/B2 访问签证",
            "extractedFields": [{
                "id": "passport.number", "value": "E12345678",
                "sourceDocumentId": "passport-document", "sourceDocument": "passport.pdf",
                "extractionMethod": "ocr",
            }],
            "branchQuestionnaire": [],
        }
        documents = [{
            "id": "passport-document", "slot": "护照", "file_name": "passport.pdf",
            "scan_status": "completed", "ocr_text": "Passport Number: E12345678",
        }]
        definition = server.build_public_intake_definition(payload, documents)
        field_ids = {item["id"] for item in definition["fields"]}
        self.assertNotIn("passport.number", field_ids)
        self.assertIn("personal.surname", field_ids)
        self.assertIn("passport.expiration", field_ids)

    def test_consultant_known_information_is_parsed_and_saved_to_case(self):
        result = server.apply_consultant_information(self.case_id, self.user, {
            "text": (
                "姓名：张明；身份证号：370202199405140011；"
                "家庭住址：山东省青岛市市南区香港中路10号；邮编：266071"
            )
        })
        self.assertGreaterEqual(result["parsedCount"], 6)
        saved = server.get_case_payload(self.case_id, self.user)
        fields = {item["id"]: item for item in saved["extractedFields"]}
        self.assertEqual(fields["personal.nativeName"]["value"], "张明")
        self.assertEqual(fields["personal.dateOfBirth"]["value"], "1994-05-14")
        self.assertEqual(fields["contact.homeCity"]["value"], "QINGDAO")
        self.assertEqual(fields["contact.homePostalCode"]["value"], "266071")
        self.assertEqual(saved["knownInformation"]["text"].split("；")[0], "姓名：张明")
        self.assertIn("deterministic_rules", result["analysisProviders"])

    def test_numbered_questionnaire_replaces_previous_garbage_and_syncs_answers(self):
        payload = server.get_case_payload(self.case_id, self.user)
        payload["extractedFields"].extend([
            {
                "id": "personal.nativeName", "label": "完整母语姓名",
                "section": "基础信息", "value": "HE LIAN XI FANG SHI",
                "sourceDocument": "顾问已知信息", "extractionMethod": "consultant_text",
                "confirmed": False, "editedByUser": False,
            },
            {
                "id": "education.schoolName", "label": "学校名称",
                "section": "工作 / 教育 / 培训", "value": "ZHUAN YE",
                "sourceDocument": "顾问已知信息", "extractionMethod": "consultant_text",
                "confirmed": False, "editedByUser": False,
            },
            {
                "id": "education.programName", "label": "课程或专业名称",
                "section": "工作 / 教育 / 培训", "value": "KAI SHI AND BI YE SHI JIAN",
                "sourceDocument": "顾问已知信息", "extractionMethod": "consultant_text",
                "confirmed": False, "editedByUser": False,
            },
        ])
        server.upsert_case(payload, self.user)

        note = """1. 是否已婚，如有，配偶的名字，生日，家庭住址
未婚
8. 现在家庭住址
山东省青岛市市南区香港中路10号
9. 邮箱
applicant@example.com
10. 社媒账号，YouTube，Instagram，Facebook，LinkedIn 等等，说几个就行
INS:clean.case
18. 公司的地址，电话，开始时间，工作职责
广东省深圳市福田区侨香路岭南大厦10C +86 15000000005
开始时间：2025年8月6日
职位：财务
20. 高中及以上学校，专业，开始和毕业时间
高中：青岛市第一高级中学 2018.9.10—2021.6.10
大学：青岛理工学院 专业：财务管理 2021.9.10—2025.6.10
21. 语言数量及名称
中文 英语"""
        with mock.patch.dict(os.environ, {
            "DS160_TRANSLATION_PROVIDER": "off",
            "DS160_TEXT_ANALYSIS_PROVIDER": "off",
        }):
            result = server.apply_consultant_information(
                self.case_id, self.user, {"text": note}
            )

        self.assertEqual(result["qaPairCount"], 7)
        self.assertEqual(result["parsedQuestionCount"], 5)
        self.assertGreater(result["recognizedGroupCount"], result["parsedCount"])
        self.assertGreater(result["recognizedValueCount"], result["recognizedGroupCount"])
        saved = server.get_case_payload(self.case_id, self.user)
        field_ids = {item["id"] for item in saved["extractedFields"]}
        self.assertNotIn("personal.nativeName", field_ids)
        self.assertNotIn("education.schoolName", field_ids)
        self.assertNotIn("education.programName", field_ids)
        self.assertIn("contact.homeStreet1", field_ids)
        self.assertIn("work.employerAddress", field_ids)
        fields = {item["id"]: item for item in saved["extractedFields"]}
        self.assertIn("SHENZHEN", fields["work.employerAddress"]["value"])
        self.assertEqual(
            fields["work.employerAddress"]["extractionMethod"], "consultant_text"
        )

        questions = {item["id"]: item for item in saved["branchQuestionnaire"]}
        self.assertEqual(questions["personal.marital_status"]["answer"], "single")
        self.assertEqual(questions["contact.social_media"]["answer"], "yes")
        self.assertEqual(
            questions["contact.social_media"]["records"][0]["handle"],
            "clean.case",
        )
        education = questions["work.education_secondary_or_above"]["records"]
        self.assertEqual(len(education), 2)
        self.assertNotIn("course", education[0])
        self.assertTrue(questions["additional.languages"]["records"])
        self.assertEqual(questions["work.primary_occupation"]["answer"], "business")
        self.assertEqual(
            questions["work.primary_occupation"]["details"]["startDate"],
            "2025-08-06",
        )
        self.assertTrue(saved["knownInformation"]["parsedQuestions"])
        self.assertEqual(
            saved["knownInformation"]["recognizedGroupCount"],
            result["recognizedGroupCount"],
        )
        self.assertEqual(saved["knownInformation"]["parsedQuestionCount"], result["parsedQuestionCount"])

    def test_consultant_note_does_not_overwrite_a_confirmed_question(self):
        payload = server.get_case_payload(self.case_id, self.user)
        questionnaire = server.build_questionnaire(
            payload.get("visaType"), payload.get("branchQuestionnaire"),
            payload.get("extractedFields"),
        )
        marital = next(
            item for item in questionnaire if item["id"] == "personal.marital_status"
        )
        marital.update({
            "answer": "married",
            "confirmedByUser": True,
            "source": "顾问人工核查",
        })
        payload["branchQuestionnaire"] = questionnaire
        server.upsert_case(payload, self.user)

        result = server.apply_consultant_information(self.case_id, self.user, {
            "text": "1. 是否已婚，如有，配偶的名字，生日，家庭住址\n未婚\n2. 邮箱\napplicant@example.com",
        })
        saved = server.get_case_payload(self.case_id, self.user)
        questions = {item["id"]: item for item in saved["branchQuestionnaire"]}
        self.assertEqual(result["parsedQuestionCount"], 0)
        self.assertEqual(questions["personal.marital_status"]["answer"], "married")
        self.assertTrue(questions["personal.marital_status"]["confirmedByUser"])

    def test_consultant_note_is_preserved_when_no_reliable_field_is_found(self):
        with mock.patch.dict(os.environ, {
            "DS160_TRANSLATION_PROVIDER": "off",
            "DS160_TEXT_ANALYSIS_PROVIDER": "off",
        }):
            result = server.apply_consultant_information(self.case_id, self.user, {
                "text": "客户说后面有空再把情况发过来"
            })
        self.assertEqual(result["parsedCount"], 0)
        self.assertTrue(result["warnings"])
        saved = server.get_case_payload(self.case_id, self.user)
        self.assertEqual(
            saved["knownInformation"]["text"], "客户说后面有空再把情况发过来"
        )
        self.assertEqual(saved["knownInformation"]["parsedFields"], [])

    def test_placeholder_country_from_mock_document_is_requested_again(self):
        payload = {
            "visaType": "F1 学生签证",
            "extractedFields": [{
                "id": "personal.nationality", "value": "DEMO NATIONAL",
                "sourceDocumentId": "passport-document", "sourceDocument": "passport.pdf",
                "extractionMethod": "label",
            }],
            "branchQuestionnaire": [],
        }
        documents = [{
            "id": "passport-document", "slot": "护照", "file_name": "passport.pdf",
            "scan_status": "completed", "ocr_text": "Nationality: DEMO NATIONAL",
        }]
        definition = server.build_public_intake_definition(payload, documents)
        field_ids = {item["id"] for item in definition["fields"]}
        self.assertIn("personal.nationality", field_ids)

    def test_composite_location_does_not_hide_required_ceac_components(self):
        payload = {
            "visaType": "F1 学生签证",
            "extractedFields": [
                {"id": "personal.placeOfBirth", "value": "QINGDAO"},
                {"id": "passport.issuePlace", "value": "DOCUMENT OFFICE"},
                {"id": "contact.homeAddress", "value": "1 DEMO ROAD, QINGDAO"},
            ],
            "branchQuestionnaire": [],
        }
        definition = server.build_public_intake_definition(payload, [])
        fields = {item["id"]: item for item in definition["fields"]}

        self.assertNotIn("personal.birthCity", fields)
        self.assertIn("personal.birthRegion", fields)
        self.assertIn("personal.birthCountry", fields)
        self.assertIn("passport.issuingAuthority", fields)
        self.assertIn("passport.issueCity", fields)
        self.assertIn("passport.issueRegion", fields)
        self.assertIn("passport.issueCountry", fields)
        self.assertIn("contact.homeCity", fields)
        self.assertIn("contact.homeRegion", fields)
        self.assertIn("contact.homeCountry", fields)
        self.assertTrue(fields["personal.birthCountry"]["required"])
        self.assertFalse(fields["contact.homeStreet2"]["required"])

    def test_public_intake_collects_both_parent_names_and_birth_dates(self):
        payload = {
            "visaType": "B1/B2 访问签证",
            "extractedFields": [],
            "branchQuestionnaire": [],
        }
        definition = server.build_public_intake_definition(payload, [])
        questions = {item["id"]: item for item in definition["questions"]}

        father = questions["family.father_known"]
        mother = questions["family.mother_known"]
        self.assertEqual(
            {item["id"] for item in father["detailFields"]},
            {"surname", "givenNames", "dateOfBirth"},
        )
        self.assertEqual(
            {item["id"] for item in mother["detailFields"]},
            {"surname", "givenNames", "dateOfBirth"},
        )
        self.assertTrue(all(item["required"] for item in father["detailFields"]))
        self.assertTrue(all(item["required"] for item in mother["detailFields"]))
        self.assertIn("family.father_in_us", questions)
        self.assertIn("family.mother_in_us", questions)

    def test_generic_repeating_records_are_preserved_in_intake_draft(self):
        payload = {
            "visaType": "F1 学生签证",
            "extractedFields": [],
            "branchQuestionnaire": [],
        }
        definition = server.build_public_intake_definition(payload, [])
        submitted = {
            "questions": {
                "fj.additional_contacts": {
                    "records": [{
                        "surname": "LI", "givenNames": "MING",
                        "address": "1 Example Road", "country": "CHINA",
                        "phone": "+8613800000000", "email": "ming@example.com",
                        "relationship": "FRIEND",
                    }]
                }
            }
        }
        sanitized = server.sanitize_intake_draft(submitted, definition)
        records = sanitized["questions"]["fj.additional_contacts"]["records"]
        self.assertEqual(records[0]["surname"], "LI")
        self.assertEqual(records[0]["relationship"], "FRIEND")

    def test_one_social_media_account_completes_the_question(self):
        link = server.create_intake_link(self.case_id, self.user)
        result = server.submit_client_intake(link["token"], {
            "respondentName": "Anna Eriksson",
            "questions": {
                "contact.social_media": {
                    "answer": "yes",
                    "records": [{"platform": "SINA_WEIBO", "handle": "anna_demo"}],
                }
            },
        })
        self.assertEqual(result["status"], "submitted")
        saved = server.get_case_payload(self.case_id, self.user)
        social = next(
            item for item in saved["branchQuestionnaire"]
            if item["id"] == "contact.social_media"
        )
        self.assertEqual(social["status"], "已回答")
        self.assertEqual(
            social["records"],
            [{"platform": "SINA_WEIBO", "handle": "anna_demo"}],
        )

    def test_no_companions_hides_and_clears_all_companion_children(self):
        case = server.get_case_payload(self.case_id, self.user)
        by_id = {item["id"]: item for item in case["branchQuestionnaire"]}
        by_id["companions.has_companions"]["answer"] = "yes"
        by_id["companions.is_group"]["answer"] = "no"
        by_id["companions.people"]["records"] = [{
            "surname": "LI", "givenNames": "MING", "relationship": "FRIEND",
        }]
        server.upsert_case(case, self.user)

        case = server.get_case_payload(self.case_id, self.user)
        by_id = {item["id"]: item for item in case["branchQuestionnaire"]}
        by_id["companions.has_companions"]["answer"] = "no"
        saved = server.upsert_case(case, self.user)
        saved_by_id = {item["id"]: item for item in saved["branchQuestionnaire"]}

        self.assertEqual(saved_by_id["companions.has_companions"]["status"], "已回答")
        self.assertFalse(saved_by_id["companions.is_group"]["visible"])
        self.assertFalse(saved_by_id["companions.people"]["visible"])
        self.assertEqual(saved_by_id["companions.people"]["records"], [])

    def test_registration_email_code_is_hashed_and_consumed(self):
        with server.connect() as connection:
            verification = connection.execute(
                "SELECT code_hash, consumed_at FROM email_verifications WHERE email = ?",
                ("reviewer@example.com",),
            ).fetchone()
            user = connection.execute(
                "SELECT email_verified_at FROM users WHERE email = ?",
                ("reviewer@example.com",),
            ).fetchone()
        self.assertIsNotNone(verification["consumed_at"])
        self.assertRegex(verification["code_hash"], r"^[0-9a-f]{64}$")
        self.assertIsNotNone(user["email_verified_at"])
        self.assertTrue(self.user["emailVerified"])

    def test_wrong_email_code_increments_attempts_without_consuming(self):
        delivered_codes = {}
        original_send_email = server.sendEmail
        original_mail_status = server.mail_service_status
        original_verification_mode = server.registration_verification_mode
        server.mail_service_status = lambda: {
            "configured": True, "provider": "test", "message": "测试服务已配置"
        }
        server.registration_verification_mode = lambda: "email"
        server.sendEmail = lambda email, subject, text, html="": (
            delivered_codes.update({email: re.search(r"\d{6}", text).group(0)})
            or {"mode": "test", "provider": "test"}
        )
        try:
            server.request_email_verification({"email": "second@example.com"})
        finally:
            server.sendEmail = original_send_email
            server.mail_service_status = original_mail_status
            server.registration_verification_mode = original_verification_mode
        wrong_code = "000000" if delivered_codes["second@example.com"] != "000000" else "000001"
        with self.assertRaisesRegex(ValueError, "验证码不正确"):
            server.verify_and_consume_email_code("second@example.com", wrong_code)
        with server.connect() as connection:
            row = connection.execute(
                "SELECT attempts, consumed_at FROM email_verifications WHERE email = ?",
                ("second@example.com",),
            ).fetchone()
        self.assertEqual(row["attempts"], 1)
        self.assertIsNone(row["consumed_at"])
        server.verify_and_consume_email_code(
            "second@example.com", delivered_codes["second@example.com"]
        )

    def tearDown(self):
        server.DATA_DIR, server.UPLOAD_DIR, server.DB_PATH = self.original_paths
        self.temp_directory.cleanup()

    def test_uploaded_document_is_mapped_and_persisted(self):
        server.save_uploaded_document(
            self.case_id,
            self.document_id,
            self.user,
            "passport.pdf",
            "application/pdf",
            b"%PDF-1.4 mock passport",
        )
        original_convert = server.convert_file
        server.convert_file = lambda *args, **kwargs: {
            "text": PASSPORT_MRZ,
            "json": {"texts": [{"text": PASSPORT_MRZ, "prov": [{"page_no": 1}]}]},
            "pages": [{"page": 1, "text": PASSPORT_MRZ}],
            "parser": "docling-serve",
            "ocrEngine": "rapidocr",
        }
        try:
            server.process_case_documents(
                self.case_id,
                self.user,
                (self.user["organizationId"], self.case_id),
            )
        finally:
            server.convert_file = original_convert

        case = server.get_case_payload(self.case_id, self.user)
        fields = {field["id"]: field for field in case["extractedFields"]}
        self.assertEqual(fields["passport.number"]["value"], "L898902C3")
        self.assertEqual(fields["passport.number"]["sourcePage"], 1)
        self.assertEqual(case["documents"][0]["scanStatus"], "completed")
        with server.connect() as connection:
            evidence_count = connection.execute(
                "SELECT COUNT(*) AS count FROM field_evidence WHERE case_id = ?",
                (self.case_id,),
            ).fetchone()["count"]
            answer_count = connection.execute(
                "SELECT COUNT(*) AS count FROM ds160_answers WHERE case_id = ?",
                (self.case_id,),
            ).fetchone()["count"]
        self.assertGreater(evidence_count, 0)
        self.assertGreater(answer_count, 60)

    def test_screen_agent_job_is_private_and_scoped_to_ceac_travel(self):
        case = server.get_case_payload(self.case_id, self.user)
        case["extractedFields"].extend([
            {
                "id": "passport.number",
                "label": "护照号码",
                "section": "护照信息",
                "value": "E12345678",
                "sourceDocument": "passport.pdf",
            },
            {
                "id": "education.sevisId",
                "label": "SEVIS ID",
                "section": "SEVIS / 学生信息",
                "value": "N0012345678",
                "sourceDocument": "i20.pdf",
            },
            {
                "id": "security.refusal",
                "label": "拒签记录",
                "section": "安全与背景问题",
                "value": "No",
                "sourceDocument": "unknown.pdf",
            },
            {
                "id": "travel.arrivalDate",
                "label": "预计抵达日期",
                "section": "旅行信息",
                "value": "2026-07-18",
                "sourceDocument": "itinerary.pdf",
            },
            {
                "id": "contact.usAddress",
                "label": "在美停留地址",
                "section": "旅行信息",
                "value": "100 Demo Avenue, San Francisco, CA 94100",
                "sourceDocument": "hotel.pdf",
            },
        ])
        server.upsert_case(case, self.user)

        job, paths = server.prepare_screen_agent_job(
            self.case_id, self.user, 4175
        )
        action_ids = {action["id"] for action in job["actions"]}
        self.assertIn("travel.purpose.primary", action_ids)
        self.assertIn("travel.arrivalDate", action_ids)
        self.assertIn("travel.usStreet1", action_ids)
        self.assertNotIn("passport.number", action_ids)
        self.assertNotIn("education.sevisId", action_ids)
        self.assertFalse(any(field_id.startswith("security.") for field_id in action_ids))
        self.assertEqual(job["targetUrl"], server.CEAC_TRAVEL_URL)
        self.assertEqual(job["executor"], "browser-use")
        self.assertEqual(job["page"], "travel")
        self.assertFalse(job["clickSave"])
        self.assertFalse(job["clickNext"])
        self.assertEqual(job["safety"]["allowedDomain"], "ceac.state.gov")
        self.assertEqual(job["safety"]["save"], "manual_only")
        arrival_action = next(
            action for action in job["actions"] if action["id"] == "travel.arrivalDate"
        )
        self.assertEqual(arrival_action["value"], "2026-07-18")
        self.assertNotIn("passport.pdf", json.dumps(job, ensure_ascii=False))
        self.assertEqual(paths["job"].stat().st_mode & 0o777, 0o600)
        self.assertEqual(paths["status"].stat().st_mode & 0o777, 0o600)

        case["visaType"] = "F1 学生签证"
        server.upsert_case(case, self.user)
        f1_job, f1_paths = server.prepare_screen_agent_job(
            self.case_id, self.user, 4175
        )
        self.assertNotIn(
            "education.sevisId", {action["id"] for action in f1_job["actions"]}
        )
        f1_purpose = next(
            action for action in f1_job["actions"]
            if action["id"] == "travel.purpose.primary"
        )
        self.assertIn("ACADEMIC OR LANGUAGE STUDENT", f1_purpose["value"])
        server.redact_screen_agent_job(f1_paths)
        redacted = json.loads(
            f1_paths["job"].read_text(encoding="utf-8")
        )
        self.assertTrue(all(action["value"] == "" for action in redacted["actions"]))

    def test_screen_agent_launcher_uses_browser_use_worker_and_private_job(self):
        class FakeProcess:
            def __init__(self):
                self.running = True

            def poll(self):
                return None if self.running else 0

            def terminate(self):
                self.running = False

        fake_process = FakeProcess()
        with mock.patch.object(server.sys, "platform", "darwin"), mock.patch.dict(
            server.os.environ, {"CODEX_SANDBOX": ""}
        ), mock.patch.object(server.subprocess, "Popen", return_value=fake_process) as popen:
            result = server.launch_screen_agent(self.case_id, self.user, 4175)
        try:
            command = popen.call_args.args[0]
            self.assertEqual(command[0], str(server.BROWSER_USE_PYTHON))
            self.assertEqual(command[1], str(server.BROWSER_USE_TRAVEL_WORKER))
            self.assertIn("--job", command)
            self.assertIn("--status", command)
            self.assertIn("--stop", command)
            self.assertNotIn("--url", command)
            self.assertNotIn("ceac.state.gov", " ".join(command).lower())
            job_path = Path(command[command.index("--job") + 1])
            saved_job = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_job["targetUrl"], server.CEAC_TRAVEL_URL)
            self.assertFalse(saved_job["clickSave"])
            self.assertFalse(saved_job["clickNext"])
            self.assertEqual(result["state"], "starting")
            self.assertEqual(result["targetUrl"], server.CEAC_TRAVEL_URL)
        finally:
            server.stop_managed_screen_agents()

    def test_screen_agent_runtime_distinguishes_codex_preview(self):
        with mock.patch.object(server.sys, "platform", "darwin"), mock.patch.dict(
            server.os.environ, {"CODEX_SANDBOX": "seatbelt"}
        ):
            status = server.screen_agent_runtime_status()
        self.assertFalse(status["available"])
        self.assertEqual(status["mode"], "codex_preview")
        self.assertIn("Finder", status["message"])

    def test_screen_agent_runtime_is_ready_outside_preview(self):
        existing_path = Path(__file__)
        with mock.patch.object(server.sys, "platform", "darwin"), mock.patch.dict(
            server.os.environ, {"CODEX_SANDBOX": ""}
        ), mock.patch.object(server, "BROWSER_USE_PYTHON", existing_path), mock.patch.object(
            server, "BROWSER_USE_TRAVEL_WORKER", existing_path
        ), mock.patch.object(server, "CHROME_EXECUTABLE", existing_path):
            status = server.screen_agent_runtime_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["mode"], "ready")
        self.assertIn("专用 Chrome", status["message"])

    def test_open_cowork_task_uses_only_fixed_demo_values_and_redacts(self):
        case = server.get_case_payload(self.case_id, self.user)
        case["extractedFields"].extend([
            {
                "id": "personal.surname",
                "label": "姓氏",
                "section": "基础信息",
                "value": "ERIKSSON",
                "sourceDocument": "passport-private.pdf",
            },
            {
                "id": "passport.number",
                "label": "护照号码",
                "section": "护照信息",
                "value": "PRIVATE987654",
                "sourceDocument": "passport-private.pdf",
            },
            {
                "id": "education.sevisId",
                "label": "SEVIS ID",
                "section": "SEVIS / 学生信息",
                "value": "N0099999999",
                "sourceDocument": "i20-private.pdf",
            },
            {
                "id": "security.refusal",
                "label": "拒签记录",
                "section": "安全与背景问题",
                "value": "No",
                "sourceDocument": "client-answer.pdf",
            },
        ])
        server.upsert_case(case, self.user)

        with mock.patch.object(server, "open_cowork_application_path", return_value=None):
            result = server.prepare_open_cowork_job(self.case_id, self.user)
        paths = server.open_cowork_job_paths(result["jobId"])
        job = json.loads(paths["job"].read_text(encoding="utf-8"))
        serialized = json.dumps(job, ensure_ascii=False)
        fields = {field["id"]: field for field in job["fields"]}

        self.assertEqual(job["executor"], "open-cowork")
        self.assertEqual(fields["personal.surname"]["value"], "Example")
        self.assertEqual(fields["passport.number"]["value"], "DEMO123456")
        self.assertNotIn("education.sevisId", fields)
        self.assertNotIn("security.refusal", fields)
        self.assertNotIn("ERIKSSON", serialized)
        self.assertNotIn("PRIVATE987654", serialized)
        self.assertNotIn("passport-private.pdf", serialized)
        self.assertTrue(job["safety"]["sanitizedDemoOnly"])
        self.assertTrue(job["safety"]["perFieldVisualAcknowledgement"])
        self.assertEqual(paths["job"].stat().st_mode & 0o777, 0o600)
        self.assertEqual(paths["status"].stat().st_mode & 0o777, 0o600)
        prepared_status = server.open_cowork_status(
            self.case_id, result["jobId"], self.user
        )
        self.assertEqual(prepared_status["state"], "prepared")
        self.assertFalse(prepared_status["redacted"])

        import importlib.util
        helper_path = (
            Path(server.__file__).resolve().parent
            / ".claude/skills/docflow-practice-lab/scripts/job.py"
        )
        specification = importlib.util.spec_from_file_location(
            "docflow_open_cowork_job", helper_path
        )
        helper = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(helper)
        helper.JOB_DIRECTORY = server.DATA_DIR / "open_cowork_jobs"
        _helper_paths, _payload, safe_view = helper.validate_job(result["jobId"])
        self.assertEqual(safe_view["targetMarker"], "VISA FORM PRACTICE LAB")
        self.assertEqual(safe_view["fieldCount"], result["totalFields"])
        self.assertNotIn("caseId", safe_view)
        with mock.patch("builtins.print"):
            helper.complete_task(result["jobId"])
        redacted = json.loads(paths["job"].read_text(encoding="utf-8"))
        self.assertTrue(all(field["value"] == "" for field in redacted["fields"]))
        self.assertTrue(redacted["redactedAt"])
        completed_status = server.open_cowork_status(
            self.case_id, result["jobId"], self.user
        )
        self.assertEqual(completed_status["state"], "completed_local_demo")
        self.assertTrue(completed_status["redacted"])

    def test_uploaded_document_can_be_deleted_with_ocr_evidence(self):
        server.save_uploaded_document(
            self.case_id,
            self.document_id,
            self.user,
            "passport.pdf",
            "application/pdf",
            b"%PDF-1.4 mock passport",
        )
        with server.connect() as connection:
            stored_path = Path(connection.execute(
                "SELECT stored_path FROM documents WHERE id = ?",
                (self.document_id,),
            ).fetchone()["stored_path"])
        original_convert = server.convert_file
        server.convert_file = lambda *args, **kwargs: {
            "text": PASSPORT_MRZ,
            "json": {"texts": [{"text": PASSPORT_MRZ, "prov": [{"page_no": 1}]}]},
            "pages": [{"page": 1, "text": PASSPORT_MRZ}],
            "parser": "docling-serve",
            "ocrEngine": "rapidocr",
        }
        try:
            server.process_case_documents(
                self.case_id,
                self.user,
                (self.user["organizationId"], self.case_id),
            )
        finally:
            server.convert_file = original_convert

        preview = server.get_document_ocr(self.case_id, self.document_id, self.user)
        original_file = server.get_document_file(
            self.case_id, self.document_id, self.user
        )
        self.assertIn("L898902C3", preview["text"])
        self.assertGreater(len(preview["fields"]), 0)
        self.assertEqual(original_file["fileName"], "passport.pdf")
        self.assertEqual(original_file["path"], stored_path.resolve())
        self.assertTrue(stored_path.exists())

        deleted = server.delete_uploaded_document(
            self.case_id, self.document_id, self.user
        )
        document = next(item for item in deleted["documents"] if item["id"] == self.document_id)
        self.assertEqual(document["fileName"], "")
        self.assertEqual(document["scanStatus"], "empty")
        self.assertFalse(any(
            field.get("sourceDocumentId") == self.document_id
            for field in deleted["extractedFields"]
        ))
        self.assertFalse(stored_path.exists())
        with server.connect() as connection:
            row = connection.execute(
                "SELECT stored_path, ocr_text FROM documents WHERE id = ?",
                (self.document_id,),
            ).fetchone()
            evidence_count = connection.execute(
                "SELECT COUNT(*) AS count FROM field_evidence WHERE document_id = ?",
                (self.document_id,),
            ).fetchone()["count"]
        self.assertIsNone(row["stored_path"])
        self.assertIsNone(row["ocr_text"])
        self.assertEqual(evidence_count, 0)

    def test_branch_answer_and_triggered_fields_are_persisted_together(self):
        case = server.get_case_payload(self.case_id, self.user)
        travel = next(
            item for item in case["branchQuestionnaire"]
            if item["id"] == "travel.specific_plans"
        )
        travel["answer"] = "no"
        travel["details"] = {
            "arrivalDate": "2026-10-06",
            "stayDuration": "12 DAYS",
            "usAddress": "350 Fifth Avenue, New York, NY",
        }
        travel["source"] = "客户确认"
        saved = server.upsert_case(case, self.user)

        saved_fields = {field["id"]: field for field in saved["extractedFields"]}
        self.assertEqual(saved_fields["travel.arrivalDate"]["value"], "2026-10-06")
        self.assertEqual(saved_fields["contact.usAddress"]["extractionMethod"], "questionnaire")
        with server.connect() as connection:
            answer_row = connection.execute(
                "SELECT answer_value, details_json FROM ds160_answers "
                "WHERE case_id = ? AND question_id = ?",
                (self.case_id, "travel.specific_plans"),
            ).fetchone()
            field_row = connection.execute(
                "SELECT value, extraction_method FROM ds160_fields "
                "WHERE case_id = ? AND field_key = ?",
                (self.case_id, "travel.arrivalDate"),
            ).fetchone()
        self.assertEqual(answer_row["answer_value"], "no")
        self.assertIn("2026-10-06", answer_row["details_json"])
        self.assertEqual(field_row["value"], "2026-10-06")
        self.assertEqual(field_row["extraction_method"], "questionnaire")

    def test_client_intake_link_only_requests_missing_data_and_flows_back(self):
        case = server.get_case_payload(self.case_id, self.user)
        case["appointmentPreparation"] = {
            "portalUsername": "internal_consultant_only",
            "contactEmail": "advisor-only@example.com",
            "homePhone": "053288886666",
            "mailingStreet": "内部预约邮寄地址",
            "pickupLocation": "内部领取网点",
        }
        case["extractedFields"].append({
            "id": "passport.number",
            "label": "护照号码",
            "section": "护照信息",
            "value": "E12345678",
            "sourceDocument": "passport.pdf",
            "confidence": 0.99,
            "riskLevel": "high",
            "requiresUserConfirmation": True,
            "confirmed": False,
            "editedByUser": False,
        })
        server.upsert_case(case, self.user)

        link = server.create_intake_link(self.case_id, self.user)
        public_form = server.public_intake_payload(link["token"])
        requested_field_ids = {item["id"] for item in public_form["fields"]}
        requested_question_ids = {item["id"] for item in public_form["questions"]}
        self.assertNotIn("passport.number", requested_field_ids)
        self.assertIn("passport.expiration", requested_field_ids)
        self.assertIn("us_history.visited", requested_question_ids)
        self.assertNotIn("documents", public_form)
        public_json = json.dumps(public_form, ensure_ascii=False)
        self.assertNotIn("appointmentPreparation", public_json)
        for internal_value in case["appointmentPreparation"].values():
            self.assertNotIn(internal_value, public_json)

        result = server.submit_client_intake(link["token"], {
            "respondentName": "Anna Eriksson",
            "fields": {"passport.expiration": "2034-05-31"},
            "questions": {
                "us_history.visited": {"answer": "no", "details": {}, "clientResponse": ""}
            },
        })
        self.assertEqual(result["status"], "submitted")
        saved = server.get_case_payload(self.case_id, self.user)
        fields = {item["id"]: item for item in saved["extractedFields"]}
        questions = {item["id"]: item for item in saved["branchQuestionnaire"]}
        self.assertEqual(fields["passport.expiration"]["value"], "2034-05-31")
        self.assertEqual(fields["passport.expiration"]["extractionMethod"], "client_intake")
        self.assertEqual(questions["us_history.visited"]["answer"], "no")
        self.assertTrue(questions["us_history.visited"]["clientSubmitted"])
        self.assertEqual(
            saved["appointmentPreparation"]["portalUsername"],
            "internal_consultant_only",
        )
        self.assertEqual(
            saved["appointmentPreparation"]["contactEmail"],
            "advisor-only@example.com",
        )
        self.assertEqual(saved["intakeMeta"]["status"], "submitted")
        self.assertEqual(server.public_intake_payload(link["token"])["status"], "submitted")
        with self.assertRaises(PermissionError):
            server.submit_client_intake(link["token"], {"fields": {}})


if __name__ == "__main__":
    unittest.main()
