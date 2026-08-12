import os
import json
import unittest
from unittest import mock

import server
from ds160_language import (
    contains_cjk,
    normalize_ceac_text,
    normalize_does_not_apply,
    parse_consultant_information,
    structure_address,
    translation_service_status,
    translate_ds160_value,
)


class Ds160LanguageTests(unittest.TestCase):
    def test_exact_d_becomes_does_not_apply(self):
        self.assertEqual(normalize_does_not_apply(" d "), "DOES NOT APPLY")
        self.assertEqual(normalize_does_not_apply("D road"), "D road")

    def test_client_draft_normalizes_d_in_fields_details_and_records(self):
        definition = {
            "fields": [{"id": "personal.birthRegion"}],
            "questions": [{
                "id": "example.question",
                "choices": [{"value": "yes"}, {"value": "no"}],
                "detailFields": [{"id": "detail"}],
                "recordFields": [{"id": "note", "choices": []}],
            }],
        }
        result = server.sanitize_intake_draft({
            "fields": {"personal.birthRegion": "D"},
            "questions": {"example.question": {
                "answer": "yes",
                "details": {"detail": "d"},
                "records": [{"note": " D "}],
            }},
        }, definition)
        self.assertEqual(result["fields"]["personal.birthRegion"], "DOES NOT APPLY")
        self.assertEqual(result["questions"]["example.question"]["details"]["detail"], "DOES NOT APPLY")
        self.assertEqual(result["questions"]["example.question"]["records"][0]["note"], "DOES NOT APPLY")

    def test_common_chinese_purpose_is_translated_without_external_service(self):
        with mock.patch.dict(os.environ, {"DS160_TRANSLATION_PROVIDER": "off"}):
            translated = translate_ds160_value("旅游", context="Purpose of Trip")
        self.assertEqual(translated["value"], "TOURISM")
        self.assertEqual(translated["originalValue"], "旅游")

    def test_ollama_translation_path_returns_server_translation(self):
        response_payload = {
            "response": json.dumps({"translation": "MAINTAIN CUSTOMER RECORDS"})
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(response_payload).encode("utf-8")

        with mock.patch.dict(os.environ, {"DS160_TRANSLATION_PROVIDER": "ollama"}), mock.patch(
            "ds160_language._OLLAMA_DISABLED_UNTIL", 0
        ), mock.patch(
            "ds160_language.url_request.urlopen", return_value=FakeResponse()
        ) as urlopen:
            translated = translate_ds160_value(
                "维护一批测试档案", field_id="work.duties"
            )
        self.assertEqual(translated["value"], "MAINTAIN CUSTOMER RECORDS")
        self.assertEqual(translated["provider"], "ollama")
        urlopen.assert_called_once()

    def test_libretranslate_returns_english_word_order_without_pinyin(self):
        response_payload = {
            "translatedText": "No. 16, Unit 2, Building 14, Yujing Huating Community"
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(response_payload).encode("utf-8")

        with mock.patch.dict(os.environ, {
            "DS160_TRANSLATION_PROVIDER": "libretranslate",
            "LIBRETRANSLATE_URL": "http://127.0.0.1:5000",
        }), mock.patch(
            "ds160_language._LIBRETRANSLATE_DISABLED_UNTIL", 0
        ), mock.patch(
            "ds160_language.url_request.urlopen", return_value=FakeResponse()
        ) as urlopen:
            translated = translate_ds160_value(
                "御景华庭小区14号楼二单元三楼16号",
                field_id="contact.homeStreet1",
            )

        self.assertEqual(
            translated["value"],
            "NO. 16, UNIT 2, BUILDING 14, YUJING HUATING COMMUNITY",
        )
        self.assertEqual(translated["provider"], "libretranslate")
        self.assertNotIn("XIAO QU", translated["value"])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:5000/translate")
        self.assertNotIn("LIBRETRANSLATE_API_KEY", request.headers)

    def test_libretranslate_health_probe_clears_stale_circuit_breaker(self):
        response_payload = [{"code": "zh-Hans"}, {"code": "en"}]

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(response_payload).encode("utf-8")

        with mock.patch.dict(os.environ, {
            "DS160_TRANSLATION_PROVIDER": "libretranslate",
            "LIBRETRANSLATE_URL": "http://127.0.0.1:5000",
        }), mock.patch(
            "ds160_language._LIBRETRANSLATE_LAST_SUCCESS", 0
        ), mock.patch(
            "ds160_language._LIBRETRANSLATE_DISABLED_UNTIL", 999999999
        ), mock.patch(
            "ds160_language.url_request.urlopen", return_value=FakeResponse()
        ):
            status = translation_service_status()

        self.assertTrue(status["libreTranslate"])

    def test_ceac_text_normalizer_removes_chinese_and_disallowed_punctuation(self):
        normalized = normalize_ceac_text("青岛，山东省：客户地址（已核对）！")
        self.assertFalse(contains_cjk(normalized))
        self.assertRegex(normalized, r"^[A-Z0-9$?.,'\- ]*$")

    def test_place_fields_use_ceac_safe_english_without_external_service(self):
        with mock.patch.dict(os.environ, {"DS160_TRANSLATION_PROVIDER": "off"}):
            city = translate_ds160_value("青岛市", field_id="personal.birthCity")
            region = translate_ds160_value("山东省", field_id="personal.birthRegion")
        self.assertEqual(city["value"], "QINGDAO")
        self.assertEqual(region["value"], "SHANDONG")
        self.assertFalse(contains_cjk(city["value"] + region["value"]))

    def test_school_company_and_duties_use_offline_ds160_glossary(self):
        with mock.patch.dict(os.environ, {"DS160_TRANSLATION_PROVIDER": "off"}):
            school = translate_ds160_value(
                "青岛海洋大学", field_id="education.schoolName"
            )
            company = translate_ds160_value(
                "上海科技有限公司", field_id="work.employerName"
            )
            duties = translate_ds160_value(
                "负责学生档案管理，核对申请信息", field_id="work.duties"
            )
        self.assertEqual(school["value"], "QINGDAO OCEAN UNIVERSITY")
        self.assertEqual(company["value"], "SHANGHAI TECHNOLOGY COMPANY LIMITED")
        self.assertIn("RESPONSIBLE FOR STUDENT RECORDS MANAGEMENT", duties["value"])
        self.assertFalse(contains_cjk(school["value"] + company["value"] + duties["value"]))
        self.assertTrue(school["reviewRequired"])

    def test_fallback_translation_can_be_upgraded_from_preserved_chinese(self):
        with mock.patch.dict(os.environ, {"DS160_TRANSLATION_PROVIDER": "ollama"}), mock.patch(
            "ds160_language._ollama_translation",
            return_value="OCEAN UNIVERSITY OF CHINA",
        ):
            fields = server.normalize_extracted_fields_language([{
                "id": "education.schoolName",
                "label": "学校名称",
                "value": "QINGDAO OCEAN UNIVERSITY",
                "originalValue": "青岛海洋大学",
                "translationProvider": "local_glossary",
            }])
        self.assertEqual(fields[0]["value"], "OCEAN UNIVERSITY OF CHINA")
        self.assertEqual(fields[0]["translationProvider"], "ollama")

    def test_legacy_case_fields_and_question_details_are_language_upgraded(self):
        with mock.patch.dict(os.environ, {"DS160_TRANSLATION_PROVIDER": "off"}):
            fields = server.normalize_extracted_fields_language([
                {"id": "personal.nativeName", "label": "完整母语姓名", "value": "张明"},
                {"id": "personal.birthCity", "label": "出生城市", "value": "青岛市"},
                {"id": "personal.birthRegion", "label": "出生省份", "value": "山东省"},
            ])
            questions = server.normalize_questionnaire_language([{
                "id": "personal.current_spouse",
                "label": "配偶资料",
                "details": {"birthCity": "青岛市", "address": "山东省青岛市市南区香港中路10号"},
                "detailFields": [
                    {"id": "birthCity", "label": "出生城市", "type": "text"},
                    {"id": "address", "label": "地址", "type": "textarea"},
                ],
            }])
        by_id = {item["id"]: item for item in fields}
        self.assertEqual(by_id["personal.nativeName"]["value"], "张明")
        self.assertEqual(by_id["personal.birthCity"]["value"], "QINGDAO")
        self.assertEqual(by_id["personal.birthRegion"]["value"], "SHANDONG")
        self.assertEqual(by_id["personal.birthCity"]["originalValue"], "青岛市")
        self.assertFalse(contains_cjk(questions[0]["details"]["birthCity"]))
        self.assertFalse(contains_cjk(questions[0]["details"]["address"]))
        self.assertEqual(questions[0]["originalDetails"]["birthCity"], "青岛市")

    def test_address_keeps_line_one_and_uses_line_two_only_for_overflow(self):
        parsed = structure_address(
            "123 Very Long Demonstration Avenue Building Five Apartment Twelve, "
            "San Francisco, CA 94100",
            "UNITED STATES OF AMERICA",
        )
        self.assertTrue(parsed["line1"])
        self.assertEqual(parsed["city"], "San Francisco")
        self.assertEqual(parsed["postalCode"], "94100")

    def test_chinese_postal_address_transliterates_names_instead_of_translating_them(self):
        bad_machine_translation = (
            "THE STREETS OF LOK QING CITY ARE LIKE A 602 HOUSE ON CONDONG ROAD"
        )
        with mock.patch(
            "ds160_language._libretranslate_translation",
            return_value=bad_machine_translation,
        ) as translator:
            parsed = structure_address(
                "浙江省温州市乐清市乐成街道宁康东路花好悦园1幢602",
                "CHINA",
            )

        translator.assert_not_called()
        self.assertEqual(parsed["line1"], "ROOM 602, BUILDING 1, HUAHAOYUEYUAN")
        self.assertEqual(parsed["line2"], "NINGKANG EAST ROAD, LECHENG SUBDISTRICT")
        self.assertEqual(parsed["city"], "LEQING")
        self.assertEqual(parsed["region"], "ZHEJIANG")

    def test_saved_bad_address_is_rebuilt_from_preserved_chinese_source(self):
        fields = server.normalize_extracted_fields_language([{
            "id": "contact.homeStreet1",
            "label": "家庭地址 · 街道地址 1",
            "section": "地址 / 电话 / 社交媒体",
            "value": "THE STREETS OF LOK QING CITY ARE LIKE A 602 HOUSE ON CONDONG ROAD",
            "originalValue": "浙江省温州市乐清市乐成街道宁康东路花好悦园1幢602",
            "translationProvider": "address_parser",
            "confirmed": False,
            "editedByUser": False,
        }])
        by_id = {field["id"]: field for field in fields}
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

    def test_consultant_note_splits_chinese_address_and_identity(self):
        def fake_romanize(value):
            replacements = {
                "山东": "SHANDONG",
                "青岛": "QINGDAO",
                "市南": "SHINAN",
                "香港中 ROAD NO. 10": "HONG KONG MIDDLE ROAD NO. 10",
            }
            return replacements.get(str(value).strip(), str(value).upper())

        with mock.patch("ds160_language.romanize", side_effect=fake_romanize), mock.patch.dict(
            os.environ, {"DS160_TRANSLATION_PROVIDER": "off"}
        ):
            parsed = parse_consultant_information(
                "姓名：张明；身份证号：370202199405141234；"
                "家庭住址：山东省青岛市市南区香港中路10号；邮编：266071"
            )
        by_id = {field["id"]: field for field in parsed["fields"]}
        self.assertEqual(by_id["personal.dateOfBirth"]["value"], "1994-05-14")
        self.assertEqual(by_id["contact.homeCity"]["value"], "QINGDAO")
        self.assertEqual(by_id["contact.homePostalCode"]["value"], "266071")
        self.assertTrue(by_id["contact.homeStreet1"]["value"])

    def test_consultant_note_scans_messy_continuous_ds160_facts(self):
        note = (
            "客户张明，性别男，出生日期1994年5月14日，出生地山东省青岛市，"
            "国籍中国，身份证号370202199405141234，护照号码E12345678，"
            "护照签发日期2024年3月8日，护照有效期2034年3月7日。"
            "家庭地址山东省青岛市市南区香港中路10号，邮编266071，"
            "手机号13800138000，邮箱zhangming@example.com。"
            "当前学校名称青岛大学，专业计算机科学，SEVIS ID N0012345678。"
            "公司名称海洋科技有限公司，职位产品经理，入职日期2022年7月1日，"
            "工作职责负责产品设计和项目管理。赴美目的旅游，"
            "预计抵达美国日期2026年10月6日，预计停留时间10天。"
        )
        with mock.patch.dict(os.environ, {"DS160_TRANSLATION_PROVIDER": "off"}):
            parsed = parse_consultant_information(note)
        by_id = {field["id"]: field for field in parsed["fields"]}
        self.assertEqual(by_id["passport.number"]["value"], "E12345678")
        self.assertEqual(by_id["passport.expiration"]["value"], "2034-03-07")
        self.assertEqual(by_id["personal.birthCity"]["value"], "QINGDAO")
        self.assertEqual(by_id["contact.homeCity"]["value"], "QINGDAO")
        self.assertEqual(by_id["education.sevisId"]["value"], "N0012345678")
        self.assertEqual(by_id["education.programName"]["value"], "COMPUTER SCIENCE")
        self.assertEqual(by_id["work.employerName"]["value"], "OCEAN TECHNOLOGY COMPANY LIMITED")
        self.assertEqual(by_id["work.title"]["value"], "PRODUCT MANAGER")
        self.assertEqual(by_id["travel.arrivalDate"]["value"], "2026-10-06")
        self.assertEqual(by_id["travel.stayDuration"]["value"], "10 DAYS")
        self.assertTrue(all(not contains_cjk(field["value"]) for field in parsed["fields"] if field["id"] != "personal.nativeName"))

    def test_numbered_client_questionnaire_never_treats_prompts_as_facts(self):
        note = """问题：
1. 是否已婚，如有，配偶的名字，生日，家庭住址
未婚
2. 是否已有赴美旅行计划，如有，具体时间地点和时长？如没有，大约时间地点和时长？
否
3. 谁会支付本次出行？自己，他人，还是公司？
公司
4. 是否曾被拒签，如有，大概解释一下
否
5. 是否有美国驾照？
否
6. 曾经去美国的时间和时长？
否
7. 办过美签移民吗？
否
8. 现在家庭住址
山东省青岛市市南区香港中路10号
9. 邮箱
applicant@example.com
10. 社媒账号，YouTube，Instagram，Facebook，LinkedIn 等等，说几个就行
INS:clean.case
11. 电话，备用电话
+86 13800138000
+86 13900139000
12. 5年内用过其他电话号吗
无
13. 5年内用过其他邮箱吗
history@example.com
14. 曾经丢过护照吗
否
15. 在美国的联系人或组织，和您的关系以及联系方式
否
16. 父亲和母亲的全名，出生日期，以及是否在美国
父亲：张建国 1970-04-12
母亲：李春华 1972-09-30
17. 是否有亲戚在美国
否
18. 公司的地址，电话，开始时间，工作职责
广东省深圳市福田区侨香路岭南大厦10C +86 15000000005
开始时间：2025年8月6日
职位：财务
19. 曾经就职公司，公司的地址，电话，开始时间，工作职责，和上级姓名和联系方式
无，毕业后一直就职该公司
20. 高中及以上学校，专业，开始和毕业时间
高中：青岛市第一高级中学 2018.9.10—2021.6.10
大学：青岛理工学院 专业：财务管理 2021.9.10—2025.6.10
21. 语言数量及名称
中文 英语
22. 有没有其他特殊情况
无"""

        with mock.patch.dict(os.environ, {
            "DS160_TRANSLATION_PROVIDER": "off",
            "DS160_TEXT_ANALYSIS_PROVIDER": "off",
        }):
            parsed = parse_consultant_information(note)

        by_id = {field["id"]: field for field in parsed["fields"]}
        self.assertEqual(parsed["qaPairCount"], 22)
        self.assertEqual(by_id["contact.email"]["value"], "applicant@example.com")
        self.assertEqual(by_id["contact.primaryPhone"]["value"], "+8613800138000")
        self.assertEqual(by_id["contact.secondaryPhone"]["value"], "+8613900139000")
        self.assertEqual(by_id["contact.homeCity"]["value"], "QINGDAO")
        self.assertEqual(by_id["work.startDate"]["value"], "2025-08-06")
        self.assertEqual(by_id["work.title"]["value"], "FINANCE")
        self.assertIn("SHENZHEN", by_id["work.employerAddress"]["value"])

        prompt_only_fields = {
            "personal.nativeName", "education.schoolName", "education.programName",
            "work.employerName", "work.duties",
        }
        self.assertTrue(prompt_only_fields.isdisjoint(by_id))
        garbage_fragments = {
            "HE LIAN XI FANG SHI", "ZHUAN YE", "KAI SHI AND BI YE SHI JIAN",
        }
        self.assertFalse(any(
            fragment in str(field.get("value") or "")
            for field in parsed["fields"]
            for fragment in garbage_fragments
        ))

        updates = parsed["questionnaireUpdates"]
        self.assertEqual(updates["personal.marital_status"]["answer"], "single")
        self.assertEqual(updates["travel.specific_plans"]["answer"], "no")
        self.assertEqual(updates["travel.payer"]["answer"], "present_employer")
        self.assertEqual(updates["work.primary_occupation"]["answer"], "business")
        self.assertEqual(
            updates["work.primary_occupation"]["details"]["startDate"],
            "2025-08-06",
        )
        self.assertEqual(
            updates["work.primary_occupation"]["details"]["jobTitle"], "财务"
        )
        self.assertNotIn(
            "organization", updates["work.primary_occupation"]["details"]
        )
        self.assertNotIn("duties", updates["work.primary_occupation"]["details"])
        self.assertEqual(updates["contact.social_media"]["records"], [{
            "platform": "INSTAGRAM", "handle": "clean.case",
        }])
        self.assertEqual(
            updates["family.father_known"]["details"]["dateOfBirth"],
            "1970-04-12",
        )
        education = updates["work.education_secondary_or_above"]["records"]
        self.assertEqual(len(education), 2)
        self.assertNotIn("course", education[0])
        self.assertEqual(education[1]["course"], "财务管理")
        self.assertEqual(
            updates["additional.languages"]["records"],
            [{"language": "中文"}, {"language": "英语"}],
        )

    def test_numbered_client_questionnaire_accepts_answers_on_prompt_lines(self):
        note = """问题：
1. 是否已婚，如有，配偶的名字，生日，家庭住址. 无
2. 是否已有赴美旅行计划，如有，具体时间地点和时长？如没有，大约时间地点和时长？
3. 谁会支付本次出行？自己，他人，还是公司？——公司
4. 是否曾被拒签，如有，大概解释一下 ——无
5. 是否有美国驾照？——无
6. 曾经去美国的时间和时长？——无
7. 办过美签移民吗？——无
8. 现在家庭住址——浙江省温州市乐清市乐成街道宁康东路花好悦园1幢602
9. 邮箱——z2233802862@qq.com
10. 社媒账号，YouTube，Instagram，Facebook，linkedin等等，说几个就行 无
11. 电话，备用电话 19209841136
12. 5年内用过其他电话号吗无
13. 5年内用过其他邮箱吗无
14. 曾经丢过护照吗无
15. 在美国的联系人或组织，和您的关系以及联系方式 无
16. 父亲和母亲的全名，出生日期，以及是否在美国 夏晓海 1976-05-30 林娣1976-08-24 否
17. 是否有亲戚在美国 无
18. 公司的地址，电话，开始时间，工作职责 广东省深圳市福田区侨香路3085岭南大厦10c 15078485005 2026-04-01 经理
19. 曾经就职公司，公司的地址，电话，开始时间，工作职责，和上级姓名和联系方式 无
20. 高中及以上学校，专业，开始和毕业时间 邢台医学院 口腔医学 2023-2026
21. 语言数量及名称 2种 英文 中文
22. 有没有其他特殊情况无"""

        with mock.patch.dict(os.environ, {
            "DS160_TRANSLATION_PROVIDER": "off",
            "DS160_TEXT_ANALYSIS_PROVIDER": "off",
        }):
            parsed = parse_consultant_information(note)

        by_id = {field["id"]: field for field in parsed["fields"]}
        self.assertEqual(parsed["qaPairCount"], 22)
        self.assertEqual(by_id["contact.email"]["value"], "z2233802862@qq.com")
        self.assertEqual(by_id["contact.primaryPhone"]["value"], "19209841136")
        self.assertEqual(by_id["contact.homeCity"]["value"], "LEQING")
        self.assertEqual(
            by_id["contact.homeStreet1"]["value"],
            "ROOM 602, BUILDING 1, HUAHAOYUEYUAN",
        )
        self.assertEqual(
            by_id["contact.homeStreet2"]["value"],
            "NINGKANG EAST ROAD, LECHENG SUBDISTRICT",
        )
        self.assertEqual(by_id["work.startDate"]["value"], "2026-04-01")
        self.assertEqual(by_id["work.title"]["value"], "MANAGER")

        updates = parsed["questionnaireUpdates"]
        self.assertNotIn("travel.specific_plans", updates)
        self.assertEqual(updates["personal.marital_status"]["answer"], "single")
        self.assertEqual(updates["travel.payer"]["answer"], "present_employer")
        self.assertEqual(updates["family.father_in_us"]["answer"], "no")
        self.assertEqual(updates["family.mother_in_us"]["answer"], "no")
        self.assertEqual(
            updates["family.father_known"]["details"]["dateOfBirth"],
            "1976-05-30",
        )
        self.assertEqual(
            updates["family.mother_known"]["details"]["dateOfBirth"],
            "1976-08-24",
        )
        education = updates["work.education_secondary_or_above"]["records"]
        self.assertEqual(education, [{
            "level": "college", "school": "邢台医学院", "course": "口腔医学",
        }])
        self.assertEqual(
            updates["additional.languages"]["records"],
            [{"language": "英文"}, {"language": "中文"}],
        )

    def test_twenty_numbered_answers_can_be_pasted_as_one_paragraph(self):
        items = [
            ("是否已婚，如有，配偶的名字，生日，家庭住址", "未婚"),
            ("是否已有赴美旅行计划，如有，具体时间地点和时长？如没有，大约时间地点和时长？", "否"),
            ("谁会支付本次出行？自己，他人，还是公司？", "公司"),
            ("是否曾被拒签，如有，大概解释一下", "否"),
            ("是否有美国驾照？", "否"),
            ("曾经去美国的时间和时长？", "否"),
            ("办过美签移民吗？", "否"),
            ("现在家庭住址", "辽宁省营口市西市区御景华庭小区14号楼二单元三楼16号"),
            ("邮箱", "applicant@example.com"),
            ("社媒账号，YouTube，Instagram，Facebook，LinkedIn 等等，说几个就行", "INS:clean.case"),
            ("电话，备用电话", "+86 13800138000 +86 13900139000"),
            ("5年内用过其他电话号吗", "无"),
            ("5年内用过其他邮箱吗", "old@example.com"),
            ("曾经丢过护照吗", "否"),
            ("在美国的联系人或组织，和您的关系以及联系方式", "否"),
            ("父亲和母亲的全名，出生日期，以及是否在美国", "父亲：张建国 1970-04-12 母亲：李春华 1972-09-30 都不在美国"),
            ("是否有亲戚在美国", "否"),
            ("公司的地址，电话，开始时间，工作职责", "广东省深圳市福田区侨香路岭南大厦10C +86 15000000005 开始时间：2025年8月6日 职位：财务"),
            ("曾经就职公司，公司的地址，电话，开始时间，工作职责，和上级姓名和联系方式", "无，毕业后一直就职该公司"),
            ("语言数量及名称", "中文 英语"),
        ]
        note = " ".join(
            f"{index}. {question} {answer}"
            for index, (question, answer) in enumerate(items, start=1)
        )
        with mock.patch.dict(os.environ, {
            "DS160_TRANSLATION_PROVIDER": "off",
            "DS160_TEXT_ANALYSIS_PROVIDER": "off",
        }):
            parsed = parse_consultant_information(note)

        self.assertEqual(parsed["qaPairCount"], 20)
        self.assertEqual(parsed["answeredQaCount"], 20)
        self.assertEqual(len(parsed["recognizedEntries"]), 20)
        self.assertGreaterEqual(parsed["matchedQaCount"], 19)
        by_id = {field["id"]: field for field in parsed["fields"]}
        self.assertEqual(by_id["contact.email"]["value"], "applicant@example.com")
        self.assertEqual(by_id["contact.primaryPhone"]["value"], "+8613800138000")
        self.assertEqual(
            parsed["questionnaireUpdates"]["travel.payer"]["answer"],
            "present_employer",
        )

    def test_schema_matcher_handles_exact_ds160_prompts_outside_sales_template(self):
        note = (
            "（1）客户现在或过去是否使用过其他姓名？ 否 "
            "（2）客户是否是其他国家或地区的永久居民？ 否 "
            "（3）客户是否属于氏族或部落？ 否"
        )
        with mock.patch.dict(os.environ, {
            "DS160_TRANSLATION_PROVIDER": "off",
            "DS160_TEXT_ANALYSIS_PROVIDER": "off",
        }):
            parsed = parse_consultant_information(note)
        updates = parsed["questionnaireUpdates"]
        self.assertEqual(updates["personal.other_names"]["answer"], "no")
        self.assertEqual(
            updates["personal.permanent_resident_other_country"]["answer"], "no"
        )
        self.assertEqual(updates["additional.clan_tribe"]["answer"], "no")
        self.assertIn("schema_question_matcher", parsed["analysisProviders"])

    def test_numbered_questionnaire_does_not_treat_company_contact_as_home_contact(self):
        note = """17. 是否有亲戚在美国 无
18. 公司的地址，电话，开始时间，工作职责 广东省深圳市福田区侨香路3085岭南大厦10c 15078485005 2026-04-01 经理
19. 曾经就职公司，公司的地址，电话，开始时间，工作职责，和上级姓名和联系方式 无
20. 高中及以上学校，专业，开始和毕业时间 邢台医学院 口腔医学 2023-2026
21. 语言数量及名称 2种 英文 中文
22. 有没有其他特殊情况无"""

        with mock.patch.dict(os.environ, {
            "DS160_TRANSLATION_PROVIDER": "off",
            "DS160_TEXT_ANALYSIS_PROVIDER": "off",
        }):
            parsed = parse_consultant_information(note)

        by_id = {field["id"]: field for field in parsed["fields"]}
        self.assertEqual(parsed["qaPairCount"], 6)
        self.assertNotIn("contact.primaryPhone", by_id)
        self.assertNotIn("contact.homeStreet1", by_id)
        self.assertNotIn("contact.homeCity", by_id)
        self.assertNotIn("contact.homeRegion", by_id)
        self.assertEqual(by_id["work.employerPhone"]["value"], "15078485005")
        self.assertIn("SHENZHEN", by_id["work.employerAddress"]["value"])
        self.assertEqual(by_id["work.startDate"]["value"], "2026-04-01")
        self.assertEqual(by_id["work.title"]["value"], "MANAGER")
        updates = parsed["questionnaireUpdates"]
        self.assertEqual(updates["work.previously_employed"]["answer"], "no")
        self.assertEqual(
            updates["work.education_secondary_or_above"]["records"],
            [{
                "level": "college",
                "school": "邢台医学院",
                "course": "口腔医学",
            }],
        )
        self.assertEqual(
            updates["additional.languages"]["records"],
            [{"language": "英文"}, {"language": "中文"}],
        )

    def test_consultant_note_uses_evidence_backed_ollama_only_for_missing_fields(self):
        response_payload = {
            "response": json.dumps({
                "fields": [
                    {
                        "fieldId": "work.duties",
                        "value": "维护客户资料",
                        "englishValue": "MAINTAINING CUSTOMER RECORDS",
                        "evidence": "平时主要做维护客户资料",
                    },
                    {
                        "fieldId": "work.title",
                        "value": "虚构职位",
                        "englishValue": "INVENTED TITLE",
                        "evidence": "原文里不存在的证据",
                    },
                ]
            }, ensure_ascii=False)
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(response_payload, ensure_ascii=False).encode("utf-8")

        with mock.patch.dict(os.environ, {
            "DS160_TRANSLATION_PROVIDER": "auto",
            "DS160_TEXT_ANALYSIS_PROVIDER": "auto",
        }), mock.patch("ds160_language._OLLAMA_DISABLED_UNTIL", 0), mock.patch(
            "ds160_language.url_request.urlopen", return_value=FakeResponse()
        ) as urlopen:
            parsed = parse_consultant_information("张明，平时主要做维护客户资料")
        by_id = {field["id"]: field for field in parsed["fields"]}
        self.assertEqual(by_id["work.duties"]["value"], "MAINTAINING CUSTOMER RECORDS")
        self.assertEqual(by_id["work.duties"]["extractionMethod"], "consultant_text_semantic")
        self.assertTrue(by_id["work.duties"]["requiresUserConfirmation"])
        self.assertNotIn("work.title", by_id)
        self.assertEqual(parsed["semanticAddedCount"], 1)
        urlopen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
