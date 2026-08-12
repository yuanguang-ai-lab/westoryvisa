import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import server


CASE_PAYLOAD = {
    "id": "case-codex-1",
    "visaType": "F1 学生签证",
    "extractedFields": [
        {"id": "travel.arrivalDate", "value": "2026-08-12"},
        {"id": "travel.stayDuration", "value": "12 MONTHS"},
        {
            "id": "contact.usAddress",
            "value": "100 Example Avenue, Boston, MA 02110",
        },
    ],
    "branchQuestionnaire": [
        {"id": "travel.specific_plans", "answer": "no", "details": {}},
    ],
}


class CodexAgentBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_patch = mock.patch.object(
            server, "DATA_DIR", Path(self.temporary_directory.name)
        )
        self.case_patch = mock.patch.object(
            server, "get_case_payload", return_value=CASE_PAYLOAD
        )
        self.data_patch.start()
        self.case_patch.start()
        self.user = {"organizationId": "org-a"}

    def tearDown(self):
        self.case_patch.stop()
        self.data_patch.stop()
        self.temporary_directory.cleanup()

    def prepare(self):
        result = server.prepare_codex_agent_job(
            CASE_PAYLOAD["id"], self.user, 4197
        )
        return result, result["accessToken"]

    def test_prepare_stores_only_token_hash_and_returns_page_scoped_plan(self):
        result, token = self.prepare()
        job, _ = server.load_codex_agent_job(result["jobId"])

        self.assertNotIn(token, json.dumps(job, ensure_ascii=False))
        self.assertEqual(job["accessTokenHash"], server.codex_agent_token_hash(token))
        self.assertEqual(job["executor"], "codex-computer-use")
        self.assertEqual(job["page"], "workflow")
        self.assertEqual(job["version"], 4)
        self.assertEqual(job["interactionPolicy"]["maxActionsBeforeReinspect"], 1)
        self.assertEqual(job["interactionPolicy"]["betweenActionsMs"], {
            "min": 900, "max": 1500,
        })
        self.assertEqual(job["interactionPolicy"]["afterDynamicSelectionMs"], 2000)
        self.assertEqual(job["interactionPolicy"]["afterNavigationMs"], 2800)
        self.assertEqual(job["safety"]["browserExtension"], "never")
        self.assertEqual(job["safety"]["domInjection"], "never")
        self.assertGreater(len(job["pages"]), 0)
        self.assertFalse(any(
            action.get("kind") in {"save", "next"}
            for action in server.codex_agent_actions(job)
        ))

        task = server.codex_agent_task_payload(result["jobId"], token, 4197)
        self.assertEqual(task["page"], "workflow")
        self.assertEqual(task["targetUrl"], server.CEAC_START_URL)
        self.assertIn("/status", task["statusUrl"])
        self.assertGreater(len(task["pages"]), 0)
        self.assertTrue(task["autoNext"])
        self.assertTrue(task["interactionPolicy"]["reinspectAfterEveryAction"])
        self.assertTrue(any(
            "Computer Use only" in instruction
            for instruction in task["instructions"]
        ))
        self.assertTrue(any(
            "dynamic fields" in instruction
            for instruction in task["instructions"]
        ))
        self.assertEqual(server.CODEX_AGENT_EXECUTORS, {"codex-computer-use"})

    def test_waiting_for_manual_entry_is_persisted_without_closing_task(self):
        result, token = self.prepare()
        server.codex_agent_task_payload(result["jobId"], token, 4197)
        status = server.update_codex_agent_task_status(
            result["jobId"], token,
            {"state": "waiting_for_entry", "completedFields": 0},
        )
        self.assertEqual(status["state"], "waiting_for_entry")
        self.assertIn("等待", status["message"])

        job, _ = server.load_codex_agent_job(result["jobId"])
        self.assertFalse(job.get("closedAt"))
        self.assertTrue(job.get("accessTokenHash"))

    def test_blocked_status_preserves_sanitized_failure_details(self):
        result, token = self.prepare()
        task = server.codex_agent_task_payload(result["jobId"], token, 4197)
        action_id = task["pages"][0]["actions"][0]["id"]
        status = server.update_codex_agent_task_status(
            result["jobId"], token,
            {
                "state": "blocked",
                "completedFields": 1,
                "reason": "字段未找到\n已暂停",
                "statusCode": "required_fields_missing",
                "failedActionIds": [action_id, "not-in-plan"],
                "missingFields": ["City", "Country/Region"],
                "currentRoute": {
                    "path": "/GenNIV/General/complete/complete_personalcont.aspx",
                    "node": "Personal2",
                    "title": "Personal Information 2",
                    "mappedKey": "personal2",
                },
                "observedRoutes": [{
                    "path": "/GenNIV/General/complete/complete_personalcont.aspx",
                    "node": "Personal2",
                    "title": "Personal Information 2",
                    "mappedKey": "personal2",
                }],
            },
        )

        self.assertEqual(status["message"], "字段未找到 已暂停")
        self.assertEqual(status["failedActionIds"], [action_id])
        self.assertEqual(status["missingFields"], ["City", "Country/Region"])
        self.assertEqual(status["statusCode"], "required_fields_missing")
        self.assertEqual(status["currentRoute"]["node"], "Personal2")
        public = server.codex_agent_status(CASE_PAYLOAD["id"], result["jobId"], self.user)
        self.assertEqual(public["failedActionIds"], [action_id])
        self.assertEqual(public["missingFields"], ["City", "Country/Region"])
        self.assertEqual(public["observedRoutes"][0]["node"], "Personal2")

    def test_wrong_token_and_other_organization_are_rejected(self):
        result, _ = self.prepare()
        with self.assertRaises(PermissionError):
            server.codex_agent_task_payload(result["jobId"], "wrong-token", 4197)
        with self.assertRaises(PermissionError):
            server.revoke_codex_agent_job(
                CASE_PAYLOAD["id"], result["jobId"], {"organizationId": "org-b"}
            )

    def test_expired_task_is_redacted(self):
        result, token = self.prepare()
        job, paths = server.load_codex_agent_job(result["jobId"])
        job["expiresAt"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        server.write_private_json(paths["job"], job)

        with self.assertRaises(PermissionError):
            server.codex_agent_task_payload(result["jobId"], token, 4197)
        redacted, _ = server.load_codex_agent_job(result["jobId"])
        self.assertEqual(redacted["accessTokenHash"], "")
        self.assertTrue(all(
            not action["value"] for action in server.codex_agent_actions(redacted)
        ))

    def test_completion_redacts_values_and_closes_token(self):
        result, token = self.prepare()
        task = server.codex_agent_task_payload(result["jobId"], token, 4197)
        total = sum(len(page["actions"]) for page in task["pages"])
        status = server.update_codex_agent_task_status(
            result["jobId"], token,
            {"state": "review_required", "completedFields": total},
        )
        self.assertEqual(status["completedFields"], total)

        redacted, _ = server.load_codex_agent_job(result["jobId"])
        self.assertEqual(redacted["closedReason"], "completed")
        self.assertEqual(redacted["accessTokenHash"], "")
        self.assertTrue(all(
            not action["value"] for action in server.codex_agent_actions(redacted)
        ))
        with self.assertRaises(PermissionError):
            server.codex_agent_task_payload(result["jobId"], token, 4197)


if __name__ == "__main__":
    unittest.main()
