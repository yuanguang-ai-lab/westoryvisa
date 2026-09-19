import os
import unittest
from unittest import mock

from visa_agent_v2.country_executors import (
    executor_class_for_worker,
    executor_metadata,
)
from visa_agent_v2.country_executors.br import BrazilComputerUseAgent
from visa_agent_v2.country_executors.india import IndiaComputerUseAgent
from visa_agent_v2.country_executors.mx import MexicoComputerUseAgent
from visa_agent_v2.workflow import FastComputerUseAgent


class CountryExecutorTests(unittest.TestCase):
    def _environment(self, country, version):
        return mock.patch.dict(os.environ, {
            "DOCFLOW_WORKER_COUNTRY_CODE": country,
            "DOCFLOW_WORKER_EXECUTOR_VERSION": version,
        }, clear=True)

    def test_china_uses_existing_runtime_directly(self):
        with self._environment("CN", "cn-legacy"):
            self.assertIs(executor_class_for_worker(), FastComputerUseAgent)
            self.assertEqual(
                executor_metadata()["executionRules"],
                ["frozen-china-path"],
            )

    def test_each_international_country_has_its_own_runtime_class(self):
        cases = (
            ("MX", "mx-v1", MexicoComputerUseAgent),
            ("BR", "br-v1", BrazilComputerUseAgent),
            ("IN", "in-v1", IndiaComputerUseAgent),
        )
        for country, version, expected in cases:
            with self.subTest(country=country):
                with self._environment(country, version):
                    self.assertIs(executor_class_for_worker(), expected)
                    self.assertEqual(
                        executor_metadata()["countryCode"], country
                    )

    def test_worker_cannot_boot_with_wrong_country_release(self):
        with self._environment("MX", "br-v1"):
            with self.assertRaisesRegex(ValueError, "执行版本"):
                executor_class_for_worker()


if __name__ == "__main__":
    unittest.main()
