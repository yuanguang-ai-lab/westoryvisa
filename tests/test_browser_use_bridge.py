import unittest
import json
import os
import re
from unittest import mock

from browser_use_bridge import (
    CEAC_START_URL,
    CEAC_TRAVEL_URL,
    build_browser_workflow,
    build_travel_actions,
    parse_us_address,
)
from browser_use_travel_worker import (
    COMMIT_INPUT_JS,
    LOCATE_CONTROL_JS,
    SET_SELECT_JS,
    apply_action,
    date_parts,
    public_error_message,
)


class FakeElement:
    def __init__(self):
        self.filled = []
        self.clicked = 0

    async def fill(self, value):
        self.filled.append(value)

    async def click(self):
        self.clicked += 1


class FakePage:
    def __init__(self, located):
        self.located = located
        self.elements = {}
        self.select_payloads = []
        self.committed = []

    async def evaluate(self, script, *args):
        if script == LOCATE_CONTROL_JS:
            return json.dumps(self.located)
        if script == SET_SELECT_JS:
            self.select_payloads.append(args[0])
            return json.dumps({"ok": True, "changed": True})
        if script == COMMIT_INPUT_JS:
            self.committed.append(args[0])
            return "true"
        raise AssertionError("unexpected script")

    async def get_elements_by_css_selector(self, selector):
        marker = selector.split('"')[1]
        return [self.elements.setdefault(marker, FakeElement())]


class BrowserUseTravelPlanTests(unittest.TestCase):
    def test_multi_page_workflow_excludes_sensitive_answers_and_controls_next(self):
        payload = {
            "visaType": "F1 学生签证",
            "extractedFields": [
                {"id": "personal.surname", "value": "ZHANG"},
                {"id": "personal.nationality", "value": "CHINA"},
                {"id": "passport.number", "value": "E12345678"},
                {"id": "travel.arrivalDate", "value": "2026-07-18"},
            ],
            "branchQuestionnaire": [
                {"id": "personal.other_names", "answer": "no", "answerType": "yes_no", "englishLabel": "Have you ever used other names?", "sensitive": False},
                {"id": "travel.specific_plans", "answer": "no", "details": {}},
                {"id": "us_history.visited", "answer": "no", "answerType": "yes_no", "englishLabel": "Have you ever been in the U.S.?", "sensitive": False},
                {"id": "us_history.refusal_or_admission", "answer": "yes", "answerType": "yes_no", "englishLabel": "Have you ever been refused a U.S. visa?", "sensitive": True},
            ],
        }
        workflow = build_browser_workflow(payload)
        pages = {page["key"]: page for page in workflow["pages"]}
        all_ids = {
            action["id"] for page in pages.values() for action in page["actions"]
        }
        self.assertEqual(workflow["version"], 3)
        self.assertEqual(workflow["targetUrl"], CEAC_START_URL)
        self.assertTrue(workflow["autoNext"])
        self.assertIn("personal1", pages)
        self.assertIn("travel", pages)
        self.assertIn("previous_us_travel", pages)
        self.assertNotIn("us_history.refusal_or_admission", all_ids)
        self.assertTrue(pages["previous_us_travel"]["manualReview"])
        self.assertFalse(pages["previous_us_travel"]["allowNext"])

    def test_invalid_placeholder_nationality_is_not_sent_to_ceac(self):
        payload = {
            "visaType": "F1 学生签证",
            "extractedFields": [
                {"id": "personal.nationality", "value": "DEMO NATIONAL"},
                {"id": "personal.nationalId", "value": "DOES NOT APPLY"},
            ],
            "branchQuestionnaire": [],
        }
        actions = {
            action["id"]: action
            for page in build_browser_workflow(payload)["pages"]
            for action in page["actions"]
        }
        self.assertNotIn("personal.nationality", actions)

    def test_common_chinese_nationality_alias_matches_ceac_option(self):
        payload = {
            "visaType": "F1 学生签证",
            "extractedFields": [
                {"id": "personal.nationality", "value": "Chinese"},
            ],
            "branchQuestionnaire": [],
        }
        actions = {
            action["id"]: action
            for page in build_browser_workflow(payload)["pages"]
            for action in page["actions"]
        }
        self.assertEqual(actions["personal.nationality"]["value"], "CHINA")
        self.assertEqual(actions["personal.nationality"]["optionTerms"], ["CHINA"])

    def test_chinese_values_are_translated_before_ceac_actions(self):
        payload = {
            "visaType": "F1 学生签证",
            "extractedFields": [
                {"id": "personal.nativeName", "value": "张明"},
                {"id": "personal.birthCity", "value": "青岛市"},
                {"id": "personal.birthRegion", "value": "山东省"},
                {"id": "personal.birthCountry", "value": "中国"},
                {"id": "work.employerName", "value": "青岛海洋大学"},
                {"id": "work.duties", "value": "负责学生档案管理"},
            ],
            "branchQuestionnaire": [],
        }
        workflow = build_browser_workflow(payload)
        actions = [
            action for page in workflow["pages"] for action in page["actions"]
        ]
        by_id = {action["id"]: action for action in actions}
        self.assertEqual(by_id["personal.nativeName"]["value"], "张明")
        self.assertEqual(by_id["personal.birthCity"]["value"], "QINGDAO")
        self.assertEqual(by_id["personal.birthRegion"]["value"], "SHANDONG")
        self.assertEqual(by_id["personal.birthCountry"]["value"], "CHINA")
        for action in actions:
            if action["id"] == "personal.nativeName":
                continue
            self.assertIsNone(
                re.search(r"[\u3400-\u9fff]", str(action.get("value") or "")),
                action,
            )
        self.assertEqual(workflow["translationBlockedFields"], [])

    def test_f1_travel_plan_contains_only_page_scoped_actions(self):
        payload = {
            "visaType": "F1 学生签证",
            "extractedFields": [
                {"id": "travel.arrivalDate", "value": "2026-07-18"},
                {"id": "travel.departureDate", "value": "2026-07-24"},
                {"id": "travel.arrivalFlight", "value": "DA101"},
                {"id": "travel.departureFlight", "value": "DA102"},
                {"id": "travel.stayDuration", "value": "6 DAYS"},
                {
                    "id": "contact.usAddress",
                    "value": "100 Demo Avenue, San Francisco, CA 94100",
                },
            ],
            "branchQuestionnaire": [
                {
                    "id": "travel.specific_plans",
                    "answer": "yes",
                    "details": {},
                }
            ],
        }
        plan = build_travel_actions(payload)
        by_id = {action["id"]: action for action in plan["actions"]}
        self.assertEqual(plan["targetUrl"], CEAC_TRAVEL_URL)
        self.assertFalse(plan["clickSave"])
        self.assertFalse(plan["clickNext"])
        self.assertEqual(by_id["travel.purpose.secondary"]["value"], "STUDENT (F1)")
        self.assertEqual(by_id["travel.specific_plans"]["value"], "yes")
        self.assertEqual(by_id["travel.stayDuration"]["duration"], {
            "amount": "6", "unit": "DAY",
        })
        self.assertEqual(by_id["travel.usCity"]["value"], "San Francisco")
        self.assertEqual(by_id["travel.usState"]["value"], "CALIFORNIA")
        self.assertEqual(by_id["travel.usPostalCode"]["value"], "94100")
        self.assertNotIn("passport.number", by_id)
        self.assertFalse(any(action["kind"] in {"save", "next"} for action in plan["actions"]))

    def test_question_details_take_priority_over_ocr_fields(self):
        payload = {
            "visaType": "B1/B2 访问签证",
            "extractedFields": [
                {"id": "travel.arrivalDate", "value": "2026-07-18"},
            ],
            "branchQuestionnaire": [
                {
                    "id": "travel.specific_plans",
                    "answer": "no",
                    "details": {"arrivalDate": "2026-08-02"},
                },
                {"id": "travel.b_visit_purpose", "answer": "b2_tourism"},
                {"id": "travel.payer", "answer": "self", "details": {}},
            ],
        }
        by_id = {
            action["id"]: action for action in build_travel_actions(payload)["actions"]
        }
        self.assertEqual(by_id["travel.arrivalDate"]["value"], "2026-08-02")
        self.assertEqual(by_id["travel.payer"]["optionTerms"], ["SELF"])
        self.assertIn("B2", by_id["travel.purpose.secondary"]["optionTerms"])

    def test_secondary_student_does_not_emit_course_of_study_action(self):
        workflow = build_browser_workflow({
            "visaType": "B1/B2 访问签证",
            "extractedFields": [
                {"id": "education.programName", "value": "GENERAL STUDIES"},
            ],
            "branchQuestionnaire": [{
                "id": "work.primary_occupation",
                "answer": "student",
                "details": {
                    "organization": "QINGDAO NO. 2 HIGH SCHOOL",
                    "schoolLevel": "secondary",
                    "courseOfStudy": "DOES NOT APPLY",
                },
            }],
        })
        work_page = next(page for page in workflow["pages"] if page["key"] == "work_education1")
        action_ids = {action["id"] for action in work_page["actions"]}
        self.assertNotIn("work.courseOfStudy", action_ids)

    def test_legal_guardian_payer_uses_ceac_other_relationship_option(self):
        plan = build_travel_actions({
            "visaType": "F1 Student",
            "extractedFields": [],
            "branchQuestionnaire": [
                {
                    "id": "travel.payer",
                    "answer": "other_person",
                    "details": {
                        "surname": "ZHANG",
                        "givenNames": "LI",
                        "relationship": "LEGAL GUARDIAN",
                    },
                }
            ],
        })
        relationship = next(
            action for action in plan["actions"]
            if action["id"] == "travel.payerRelationship"
        )
        self.assertEqual(relationship["value"], "LEGAL GUARDIAN")
        self.assertEqual(relationship["optionTerms"], ["OTHER"])

    def test_screenshot_fields_build_structured_choice_and_repeat_actions(self):
        payload = {
            "visaType": "F1 学生签证",
            "extractedFields": [
                {"id": "personal.surname", "value": "ZHANG"},
                {"id": "personal.sex", "value": "MALE"},
            ],
            "branchQuestionnaire": [
                {
                    "id": "personal.other_names", "answer": "no",
                    "answerType": "yes_no",
                    "englishLabel": "Have you ever used other names?",
                    "sensitive": False,
                },
                {
                    "id": "travel.specific_plans", "answer": "no",
                    "details": {
                        "arrivalDate": "2026-07-18",
                        "stayLength": "10", "stayUnit": "DAY",
                        "usStreet1": "100 DEMO AVENUE",
                        "usCity": "SAN FRANCISCO",
                        "usState": "CALIFORNIA",
                        "usPostalCode": "94100",
                    },
                },
                {"id": "travel.payer", "answer": "self", "details": {}},
                {
                    "id": "companions.has_companions", "answer": "yes",
                    "answerType": "yes_no",
                    "englishLabel": "Are there other persons traveling with you?",
                    "sensitive": False,
                },
                {
                    "id": "companions.is_group", "answer": "no",
                    "answerType": "yes_no",
                    "englishLabel": "Are you traveling as part of a group or organization?",
                    "sensitive": False,
                },
                {
                    "id": "companions.people", "answerType": "records",
                    "visible": True,
                    "records": [{
                        "surname": "LI", "givenNames": "HUA",
                        "relationship": "FRIEND",
                    }],
                },
                {
                    "id": "us_history.visited", "answer": "yes",
                    "answerType": "yes_no", "sensitive": False,
                    "englishLabel": "Have you ever been in the U.S.?",
                    "records": [{
                        "arrivalDate": "2024-01-02",
                        "stayLength": "5", "stayUnit": "DAY",
                    }],
                },
                {
                    "id": "us_history.previous_visa", "answer": "yes",
                    "answerType": "yes_no", "sensitive": False,
                    "englishLabel": "Have you ever been issued a U.S. visa?",
                    "details": {
                        "issueDate": "2023-01-02", "visaNumber": "12345678",
                        "sameClass": "yes", "sameLocation": "yes",
                        "tenPrinted": "yes",
                    },
                },
            ],
        }
        workflow = build_browser_workflow(payload)
        pages = {page["key"]: page for page in workflow["pages"]}
        travel = {item["id"]: item for item in pages["travel"]["actions"]}
        companions = {
            item["id"]: item for item in pages["travel_companions"]["actions"]
        }
        previous = {
            item["id"]: item for item in pages["previous_us_travel"]["actions"]
        }

        self.assertEqual(travel["travel.stayDuration"]["duration"], {
            "amount": "10", "unit": "DAY",
        })
        self.assertEqual(travel["travel.usState"]["kind"], "select_text")
        self.assertIn("ADDR_US_STATE", travel["travel.usState"]["controlHints"])
        self.assertEqual(
            companions["companions.people.0.relationship"]["occurrence"], 0
        )
        self.assertEqual(
            companions["companions.people.0.relationship"]["kind"], "select_text"
        )
        self.assertEqual(
            previous["us_history.visited.0.duration"]["duration"],
            {"amount": "5", "unit": "DAY"},
        )
        self.assertEqual(
            previous["us_history.previous_visa.sameClass"]["kind"], "yes_no"
        )
        self.assertIn(
            "PREV_VISA_SAME_TYPE_IND",
            previous["us_history.previous_visa.sameClass"]["controlHints"],
        )

    def test_unstructured_address_is_not_invented(self):
        parsed = parse_us_address("Campus housing address pending")
        self.assertEqual(parsed, {"street1": "Campus housing address pending"})
        self.assertNotIn("city", parsed)

    def test_parent_details_and_multiple_school_records_are_in_ceac_plan(self):
        payload = {
            "visaType": "B1/B2 访问签证",
            "extractedFields": [],
            "branchQuestionnaire": [
                {
                    "id": "family.father_known", "answerType": "details",
                    "details": {
                        "surname": "YUAN", "givenNames": "MING XIAN",
                        "dateOfBirth": "1979-04-12",
                    },
                },
                {
                    "id": "family.mother_known", "answerType": "details",
                    "details": {
                        "surname": "LIU", "givenNames": "YUN LU",
                        "dateOfBirth": "1981-09-30",
                    },
                },
                {
                    "id": "family.father_in_us", "answer": "no",
                    "answerType": "yes_no", "sensitive": False,
                },
                {
                    "id": "family.mother_in_us", "answer": "no",
                    "answerType": "yes_no", "sensitive": False,
                },
                {
                    "id": "work.education_secondary_or_above", "answer": "yes",
                    "answerType": "yes_no", "sensitive": False,
                    "records": [
                        {
                            "level": "secondary", "school": "YINGKOU NO. 1 HIGH SCHOOL",
                            "address": "NO. 1 BOHAI STREET", "city": "YINGKOU",
                            "region": "LIAONING", "postalCode": "115000", "country": "CHINA",
                            "startDate": "2018-09-10", "endDate": "2021-06-10",
                        },
                        {
                            "level": "college", "school": "LIAONING INSTITUTE OF SCIENCE AND ENGINEERING",
                            "address": "NO. 169 KUNMING STREET", "city": "JINZHOU",
                            "region": "LIAONING", "postalCode": "121000", "country": "CHINA",
                            "course": "FINANCIAL MANAGEMENT",
                            "startDate": "2021-09-10", "endDate": "2025-06-10",
                        },
                    ],
                },
            ],
        }
        pages = {page["key"]: page for page in build_browser_workflow(payload)["pages"]}
        relatives = {item["id"]: item for item in pages["relatives"]["actions"]}
        education = {item["id"]: item for item in pages["work_education2"]["actions"]}

        self.assertEqual(relatives["family.father.dateOfBirth"]["kind"], "date")
        self.assertEqual(relatives["family.mother.surname"]["value"], "LIU")
        self.assertEqual(education["work.education_secondary_or_above"]["kind"], "yes_no")
        self.assertEqual(education["work.education.ensure.2"]["kind"], "ensure_repeater")
        self.assertEqual(education["work.education.1.city"]["value"], "JINZHOU")
        self.assertNotIn("work.education.0.course", education)

    def test_legacy_school_record_is_resolved_before_ceac_actions_are_built(self):
        payload = {
            "visaType": "B1/B2 访问签证",
            "extractedFields": [],
            "branchQuestionnaire": [{
                "id": "work.education_secondary_or_above",
                "answer": "yes",
                "answerType": "yes_no",
                "records": [{
                    "level": "secondary",
                    "school": "YING KOU SHI DI YI GAO JI MIDDLE SCHOOL",
                    "address": "YING KOU SHI LAO BIAN QU JIN XIU DA JIE1HAO",
                    "startDate": "2018.9.10",
                    "endDate": "2021.6.10",
                }],
                "originalRecords": [{"school": "营口市第一高级中学"}],
            }],
        }

        workflow = build_browser_workflow(payload)
        page = next(item for item in workflow["pages"] if item["key"] == "work_education2")
        actions = {item["id"]: item for item in page["actions"]}

        self.assertEqual(
            actions["work.education.0.school"]["value"],
            "YINGKOU SENIOR HIGH SCHOOL",
        )
        self.assertEqual(
            actions["work.education.0.line1"]["value"],
            "NO. 1 JINXIU AVENUE, LAOBIAN DISTRICT",
        )
        self.assertEqual(actions["work.education.0.city"]["value"], "YINGKOU")
        self.assertEqual(actions["work.education.0.region"]["value"], "LIAONING")
        self.assertEqual(actions["work.education.0.postalCode"]["value"], "115005")
        self.assertEqual(actions["work.education.0.startDate"]["value"], "2018.9.10")
        self.assertEqual(actions["work.education.0.endDate"]["value"], "2021.6.10")

    def test_unresolved_legacy_pinyin_school_is_not_sent_to_ceac(self):
        payload = {
            "visaType": "B1/B2 访问签证",
            "extractedFields": [],
            "branchQuestionnaire": [{
                "id": "work.education_secondary_or_above",
                "answer": "yes",
                "answerType": "yes_no",
                "records": [{
                    "level": "secondary",
                    "school": "MO SHENG SHI DI YI GAO JI XUE XIAO",
                    "startDate": "2018-09-01",
                    "endDate": "2021-06-30",
                }],
                "originalRecords": [{"school": "陌生市第一高级学校"}],
            }],
        }

        with mock.patch.dict(os.environ, {"SCHOOL_LOOKUP_PROVIDER": "off"}):
            workflow = build_browser_workflow(payload)
        page = next(item for item in workflow["pages"] if item["key"] == "work_education2")
        actions = {item["id"]: item for item in page["actions"]}

        self.assertNotIn("work.education.0.school", actions)
        self.assertEqual(actions["work.education.0.startDate"]["value"], "2018-09-01")
        self.assertEqual(actions["work.education.0.endDate"]["value"], "2021-06-30")

    def test_legacy_place_of_birth_targets_ceac_city_only(self):
        payload = {
            "visaType": "F1 学生签证",
            "extractedFields": [
                {"id": "personal.placeOfBirth", "value": "QINGDAO"},
                {"id": "passport.issuePlace", "value": "DOCUMENT OFFICE"},
            ],
            "branchQuestionnaire": [],
        }
        workflow = build_browser_workflow(payload)
        pages = {page["key"]: page for page in workflow["pages"]}
        personal_actions = {
            action["id"]: action for action in pages["personal1"]["actions"]
        }
        all_action_ids = {
            action["id"] for page in pages.values() for action in page["actions"]
        }

        self.assertIn("APP_POB_CITY", personal_actions["personal.placeOfBirth"]["controlHints"])
        self.assertNotIn("personal.birthRegion", personal_actions)
        self.assertNotIn("personal.birthCountry", personal_actions)
        self.assertNotIn("passport.issuePlace", all_action_ids)

    def test_worker_formats_iso_date_for_ceac(self):
        self.assertEqual(date_parts("2026-07-18"), {
            "year": "2026", "month": "JUL", "day": "18",
            "full": "18-JUL-2026",
        })
        self.assertIsNone(date_parts("18/07/2026"))

    def test_browser_start_timeout_is_safe_and_actionable(self):
        message = public_error_message(
            TimeoutError("BrowserStartEvent internal handler details")
        )
        self.assertIn("Chrome 启动超时", message)
        self.assertIn("启动Screen Agent演示.command", message)
        self.assertNotIn("internal handler details", message)


class BrowserUseTravelActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_action_calls_element_fill(self):
        page = FakePage({
            "status": "found", "role": "text", "marker": "arrival-flight",
            "alreadySet": False,
        })
        result = await apply_action(page, {
            "id": "travel.arrivalFlight", "kind": "text", "value": "DA101",
        })
        self.assertEqual(result["status"], "filled")
        self.assertEqual(page.elements["arrival-flight"].filled, ["DA101"])
        self.assertEqual(page.committed, ["arrival-flight"])

    async def test_radio_action_clicks_visible_choice(self):
        page = FakePage({
            "status": "found", "role": "radio", "marker": "specific-yes",
            "alreadySet": False,
        })
        result = await apply_action(page, {
            "id": "travel.specific_plans", "kind": "yes_no", "value": "yes",
        })
        self.assertEqual(result["status"], "filled")
        self.assertEqual(page.elements["specific-yes"].clicked, 1)

    async def test_select_action_dispatches_selected_option(self):
        page = FakePage({
            "status": "found", "role": "select", "marker": "purpose",
            "optionValue": "F", "alreadySet": False,
        })
        result = await apply_action(page, {
            "id": "travel.purpose.primary", "kind": "select_text", "value": "F",
        })
        self.assertEqual(result["status"], "filled")
        self.assertEqual(page.select_payloads[0]["marker"], "purpose")
        self.assertEqual(page.select_payloads[0]["optionValue"], "F")


if __name__ == "__main__":
    unittest.main()
