import unittest
from pathlib import Path

from visa_agent_v2.settings import load_v2_config


class V2SettingsTests(unittest.TestCase):
    def test_default_data_dir_is_isolated_from_v1(self):
        config = load_v2_config({
            "AGENT_DATA_DIR": "/tmp/docflow-agent-test",
        })

        self.assertEqual(
            config.data_dir,
            Path("/tmp/docflow-agent-test-v2"),
        )

    def test_explicit_v2_data_dir_wins(self):
        config = load_v2_config({
            "AGENT_DATA_DIR": "/tmp/docflow-agent-test",
            "AGENT_V2_DATA_DIR": "/tmp/docflow-agent-test-explicit-v2",
        })

        self.assertEqual(
            config.data_dir,
            Path("/tmp/docflow-agent-test-explicit-v2"),
        )


if __name__ == "__main__":
    unittest.main()
