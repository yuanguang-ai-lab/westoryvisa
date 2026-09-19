import os
import unittest
from unittest import mock

from visa_agent_v2.country_guard import (
    country_guard_issues,
    require_country_guard,
)


class CountryGuardTests(unittest.TestCase):
    def test_legacy_china_payload_remains_compatible(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(country_guard_issues({}), [])
            require_country_guard({})

    def test_mexico_worker_accepts_only_mexico_v1_protocol_v2(self):
        environment = {
            "DOCFLOW_WORKER_COUNTRY_CODE": "MX",
            "DOCFLOW_WORKER_EXECUTOR_VERSION": "mx-v1",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            require_country_guard({
                "countryCode": "MX",
                "executorVersion": "mx-v1",
                "protocolVersion": 2,
            })
            with self.assertRaisesRegex(ValueError, "国家执行隔离"):
                require_country_guard({
                    "countryCode": "BR",
                    "executorVersion": "br-v1",
                    "protocolVersion": 2,
                })

    def test_international_worker_rejects_missing_target_metadata(self):
        environment = {
            "DOCFLOW_WORKER_COUNTRY_CODE": "IN",
            "DOCFLOW_WORKER_EXECUTOR_VERSION": "in-v1",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            issues = country_guard_issues({})

        self.assertTrue(any("任务国家" in item for item in issues))
        self.assertTrue(any("任务执行版本" in item for item in issues))
        self.assertTrue(any("任务协议版本" in item for item in issues))


if __name__ == "__main__":
    unittest.main()
