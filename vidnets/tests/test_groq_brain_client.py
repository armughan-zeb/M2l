import os
import sys
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.groq_brain_client import GroqBrainClient


class GroqBrainClientTests(unittest.TestCase):
    @patch("services.groq_brain_client.requests.post")
    def test_json_can_be_recovered_from_reasoning_content(self, post):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{
                "message": {
                    "content": "",
                    "reasoning_content": '{"containers": []}',
                },
                "finish_reason": "stop",
            }]
        }
        post.return_value = response
        schema = {
            "type": "object",
            "properties": {"containers": {"type": "array"}},
            "required": ["containers"],
        }

        raw = GroqBrainClient("test-key").chat(
            "Return containers",
            json_mode=True,
            json_schema=schema,
            timeout_seconds=1,
        )

        self.assertEqual(raw, '{"containers": []}')


if __name__ == "__main__":
    unittest.main()
