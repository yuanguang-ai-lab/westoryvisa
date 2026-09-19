import os
import unittest
from unittest import mock

from visa_agent.service import Handler


class AgentApiAuthenticationTests(unittest.TestCase):
    def handler(self, authorization=""):
        handler = object.__new__(Handler)
        handler.headers = {"Authorization": authorization}
        handler.json_response = mock.MagicMock()
        return handler

    def test_configured_token_is_required(self):
        handler = self.handler()
        with mock.patch.dict(
            os.environ,
            {"DOCFLOW_AGENT_API_TOKEN": "test-agent-token-32-random-characters"},
            clear=False,
        ):
            self.assertFalse(handler.require_api_token())
        handler.json_response.assert_called_once_with(
            {"error": "Agent API authorization required"}, 401
        )

    def test_valid_bearer_token_is_accepted(self):
        token = "test-agent-token-32-random-characters"
        handler = self.handler(f"Bearer {token}")
        with mock.patch.dict(
            os.environ,
            {"DOCFLOW_AGENT_API_TOKEN": token},
            clear=False,
        ):
            self.assertTrue(handler.require_api_token())
        handler.json_response.assert_not_called()

    def test_local_compatibility_allows_an_unconfigured_token(self):
        handler = self.handler()
        with mock.patch.dict(os.environ, {"DOCFLOW_AGENT_API_TOKEN": ""}, clear=False):
            self.assertTrue(handler.require_api_token())
        handler.json_response.assert_not_called()


if __name__ == "__main__":
    unittest.main()
