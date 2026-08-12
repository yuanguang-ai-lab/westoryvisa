import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server
from appointment_bridge import (
    APPOINTMENT_ALLOWED_DOMAIN,
    APPOINTMENT_START_URL,
    appointment_preflight_issues,
    build_appointment_workflow,
)


def case_payload(visa_type="F1 学生签证"):
    return {
        "id": "case-appointment-1",
        "visaType": visa_type,
        "appointmentPreparation": {
            "accountReady": True,
            "portalUsername": "zhangwei_f1",
            "ds160ConfirmationNumber": "AA00BC12DE",
            "schedulingEmail": "applicant@example.com",
            "contactEmail": "consultant@example.com",
            "preferredLanguage": "zh-CN",
            "countryOfApplication": "CHINA",
            "countryOfBirth": "CHINA",
            "homePhoneCountryCode": "+86",
            "homePhone": "53288886666",
            "mobilePhoneCountryCode": "+86",
            "mobilePhone": "13812345678",
            "primaryPhone": "+8613812345678",
            "mailingStreet": "山东省青岛市市南区香港中路 10 号",
            "mailingCity": "青岛",
            "mailingState": "山东",
            "mailingPostalCode": "266000",
            "applicationLocation": "北京",
            "postVisaCategory": "STUDENTS - OTHER STUDENTS",
            "visaPriority": "REGULAR",
            "deliveryOption": "PICK_UP",
            "pickupLocation": "北京中信银行指定网点",
            "schoolZipCode": "98105",
        },
        "extractedFields": [
            {"id": "personal.surname", "value": "ZHANG"},
            {"id": "personal.givenNames", "value": "WEI"},
            {"id": "personal.dateOfBirth", "value": "1998-04-16"},
            {"id": "personal.birthCountry", "value": "CHINA"},
            {"id": "personal.nationality", "value": "CHINA"},
            {"id": "passport.number", "value": "E12345678"},
            {"id": "passport.issueDate", "value": "2021-08-20"},
            {"id": "passport.expiration", "value": "2031-08-20"},
            {"id": "education.sevisId", "value": "N0034567891"},
            {"id": "education.schoolName", "value": "Northwest State University"},
        ],
    }


class AppointmentBridgeTests(unittest.TestCase):
    def test_f1_plan_is_fill_only_and_includes_sevis(self):
        payload = case_payload()
        self.assertEqual(appointment_preflight_issues(payload), [])
        plan = build_appointment_workflow(payload)

        self.assertEqual(plan["workflowType"], "appointment")
        self.assertEqual(plan["targetUrl"], APPOINTMENT_START_URL)
        self.assertFalse(plan["autoNext"])
        self.assertFalse(plan["clickNext"])
        self.assertTrue(all(not page["allowNext"] for page in plan["pages"]))
        actions = [action for page in plan["pages"] for action in page["actions"]]
        action_ids = {action["id"] for action in actions}
        self.assertIn("appointment.confirmation.sevis_id", action_ids)
        self.assertIn("appointment.confirmation.school_name", action_ids)
        self.assertIn("appointment.confirmation.school_zip", action_ids)
        self.assertIn("appointment.confirmation.ds160", action_ids)
        self.assertIn("appointment.home_phone", action_ids)
        self.assertIn("appointment.mailing_street", action_ids)
        self.assertIn("appointment.post_visa_category", action_ids)
        self.assertTrue(all(
            action["kind"] not in {"save", "next", "submit", "payment", "schedule"}
            for action in actions
        ))
        serialized = json.dumps(plan).lower()
        self.assertNotIn("password", serialized)
        self.assertNotIn("security answer", serialized)

    def test_b1b2_plan_does_not_include_student_fields(self):
        payload = case_payload("B1/B2 访问签证")
        payload["appointmentPreparation"]["postVisaCategory"] = "BUSINESS / TOURISM"
        payload["extractedFields"] = [
            item for item in payload["extractedFields"]
            if not item["id"].startswith("education.")
        ]
        plan = build_appointment_workflow(payload)
        action_ids = {
            action["id"] for page in plan["pages"] for action in page["actions"]
        }
        self.assertNotIn("appointment.confirmation.sevis_id", action_ids)
        self.assertNotIn("appointment.confirmation.school_name", action_ids)
        visa_action = next(
            action for page in plan["pages"] for action in page["actions"]
            if action["id"] == "appointment.visa_class"
        )
        self.assertEqual(visa_action["value"], "B1/B2")

    def test_preflight_reports_missing_confirmation_and_location(self):
        payload = case_payload()
        payload["appointmentPreparation"]["ds160ConfirmationNumber"] = ""
        payload["appointmentPreparation"]["applicationLocation"] = ""
        labels = {item["label"] for item in appointment_preflight_issues(payload)}
        self.assertIn("DS-160 确认号", labels)
        self.assertIn("使领馆", labels)

    def test_preflight_requires_confirmed_appointment_account(self):
        payload = case_payload()
        payload["appointmentPreparation"]["accountReady"] = False
        labels = {item["label"] for item in appointment_preflight_issues(payload)}
        self.assertIn("可用的预约账户", labels)

    def test_preflight_reports_consultant_only_contact_and_delivery_fields(self):
        payload = case_payload()
        payload["appointmentPreparation"]["homePhone"] = ""
        payload["appointmentPreparation"]["mailingStreet"] = ""
        payload["appointmentPreparation"]["pickupLocation"] = ""
        labels = {item["label"] for item in appointment_preflight_issues(payload)}
        self.assertIn("家庭电话", labels)
        self.assertIn("中文邮寄街道地址", labels)
        self.assertIn("领取服务点", labels)

    def test_premium_delivery_address_is_prepared_without_payment_actions(self):
        payload = case_payload()
        payload["appointmentPreparation"].update({
            "deliveryOption": "PREMIUM_DELIVERY",
            "deliveryStreet1": "山东省青岛市市南区香港中路 10 号",
            "deliveryCity": "青岛",
            "deliveryState": "山东",
            "deliveryPostalCode": "266000",
        })
        plan = build_appointment_workflow(payload)
        action_ids = {
            action["id"] for page in plan["pages"] for action in page["actions"]
        }
        self.assertIn("appointment.delivery.street1", action_ids)
        self.assertNotIn("payment", " ".join(action_ids).lower())


class AppointmentServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_patch = mock.patch.object(
            server, "DATA_DIR", Path(self.temporary_directory.name)
        )
        self.case_patch = mock.patch.object(
            server, "get_case_payload", return_value=case_payload()
        )
        self.data_patch.start()
        self.case_patch.start()
        self.user = {"organizationId": "org-appointment"}

    def tearDown(self):
        self.case_patch.stop()
        self.data_patch.stop()
        self.temporary_directory.cleanup()

    def test_prepare_appointment_job_has_narrow_domain_and_hashed_token(self):
        result = server.prepare_appointment_agent_job(
            "case-appointment-1", self.user, 4197
        )
        job, _ = server.load_codex_agent_job(result["jobId"])
        self.assertEqual(job["workflowType"], "appointment")
        self.assertEqual(job["version"], 4)
        self.assertEqual(job["executor"], "codex-computer-use")
        self.assertEqual(job["safety"]["allowedDomain"], APPOINTMENT_ALLOWED_DOMAIN)
        self.assertEqual(job["safety"]["browserExtension"], "never")
        self.assertEqual(job["safety"]["payment"], "never")
        self.assertEqual(job["safety"]["appointmentSlot"], "manual_only")
        self.assertFalse(job["autoNext"])
        self.assertNotIn(result["accessToken"], json.dumps(job))

        task = server.codex_agent_task_payload(
            result["jobId"], result["accessToken"], 4197
        )
        self.assertEqual(task["workflowType"], "appointment")
        self.assertEqual(task["executor"], "codex-computer-use")
        self.assertTrue(task["interactionPolicy"]["reinspectAfterEveryAction"])
        self.assertFalse(task["autoNext"])
        self.assertEqual(task["targetUrl"], APPOINTMENT_START_URL)


if __name__ == "__main__":
    unittest.main()
