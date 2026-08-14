import unittest
import tempfile

from visa_agent.config import load_config
from visa_agent.service import AgentService


class IntegrationHealthTests(unittest.TestCase):
    def test_health_reports_codex_docflow_integration(self):
        with tempfile.TemporaryDirectory() as data_dir:
            config = load_config({
                "AGENT_INTEGRATION_MODE": "docflow-local",
                "AGENT_COMPUTER_USE_EXECUTION": "codex-computer-use",
                "AGENT_ALLOW_PLAINTEXT_CHECKPOINTS": "true",
                "AGENT_DATA_DIR": data_dir,
            })
            service = AgentService(config=config)
            health = service.health()

            self.assertTrue(health["connectedToDocFlow"])
            self.assertEqual(health["mode"], "docflow-local")
            self.assertEqual(
                health["computerUseExecution"], "codex-computer-use"
            )


if __name__ == "__main__":
    unittest.main()
