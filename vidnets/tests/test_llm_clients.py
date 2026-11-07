import os
import sys
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.groq_brain_client import GroqBrainClient
from services.groq_llm import GroqLLM
from services.openrouter_llm import OpenRouterLLM


def _mock_response(status_code=200, payload=None):
    response = Mock()
    response.status_code = status_code
    response.raise_for_status.return_value = None
    response.json.return_value = payload or {"choices": []}
    response.text = str(payload or "")
    return response


class OpenRouterTruncationTests(unittest.TestCase):
    @patch("services.openrouter_llm.requests.post")
    def test_empty_content_with_length_retries_with_larger_budget(self, post):
        first = _mock_response(payload={
            "choices": [{
                "message": {"content": ""},
                "finish_reason": "length",
            }]
        })
        second = _mock_response(payload={
            "choices": [{
                "message": {"content": "RMS Titanic"},
                "finish_reason": "stop",
            }]
        })
        post.side_effect = [first, second]

        out = OpenRouterLLM("test-key", "openai/gpt-oss-20b").chat(
            "Identify the subject",
            max_tokens=20,
            max_attempts=2,
        )

        self.assertEqual(out, "RMS Titanic")
        self.assertEqual(post.call_count, 2)
        second_payload = post.call_args_list[1].kwargs["json"]
        self.assertGreaterEqual(second_payload["max_tokens"], 40)

    @patch("services.openrouter_llm.requests.post")
    def test_reasoning_exclude_is_sent_for_reasoning_models(self, post):
        response = _mock_response(payload={
            "choices": [{
                "message": {"content": "OK"},
                "finish_reason": "stop",
            }]
        })
        post.return_value = response

        OpenRouterLLM("test-key", "openai/gpt-oss-20b").chat("Say OK", max_tokens=20)

        sent = post.call_args_list[0].kwargs["json"]
        self.assertTrue(sent.get("reasoning", {}).get("exclude"))
        self.assertGreaterEqual(sent["reasoning"].get("max_tokens", 0), 64)

    @patch("services.openrouter_llm.requests.post")
    def test_reasoning_exclude_not_sent_for_plain_models(self, post):
        response = _mock_response(payload={
            "choices": [{
                "message": {"content": "OK"},
                "finish_reason": "stop",
            }]
        })
        post.return_value = response

        OpenRouterLLM("test-key", "mistralai/mistral-small").chat("Say OK", max_tokens=20)

        sent = post.call_args_list[0].kwargs["json"]
        self.assertNotIn("reasoning", sent)

    @patch("services.openrouter_llm.requests.post")
    def test_still_raises_when_empty_after_retries(self, post):
        empty = _mock_response(payload={
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}]
        })
        post.side_effect = [empty, empty]

        with self.assertRaises(ValueError) as ctx:
            OpenRouterLLM("test-key", "openai/gpt-oss-20b").chat(
                "Say OK", max_tokens=20, max_attempts=2
            )

        self.assertIn("empty content", str(ctx.exception))


class GroqLLMTruncationTests(unittest.TestCase):
    @patch("services.groq_llm.requests.post")
    def test_empty_content_with_length_retries_with_larger_budget(self, post):
        first = _mock_response(payload={
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}]
        })
        second = _mock_response(payload={
            "choices": [{"message": {"content": "RMS Titanic"}, "finish_reason": "stop"}]
        })
        post.side_effect = [first, second]

        out = GroqLLM("test-key", "openai/gpt-oss-20b").chat(
            "Identify the subject", max_tokens=20
        )

        self.assertEqual(out, "RMS Titanic")
        self.assertEqual(post.call_count, 2)
        self.assertGreaterEqual(post.call_args_list[1].kwargs["json"]["max_tokens"], 40)


class GroqBrainClientTruncationTests(unittest.TestCase):
    @patch("services.groq_brain_client.requests.post")
    def test_truncated_reasoning_retries_instead_of_leaking_reasoning_text(self, post):
        first = _mock_response(payload={
            "choices": [{
                "message": {
                    "content": "",
                    "reasoning_content": "let me think about possible names",
                },
                "finish_reason": "length",
            }]
        })
        second = _mock_response(payload={
            "choices": [{
                "message": {"content": "RMS Titanic", "reasoning_content": ""},
                "finish_reason": "stop",
            }]
        })
        post.side_effect = [first, second]

        out = GroqBrainClient("test-key").chat(
            "Identify the subject", max_tokens=20, timeout_seconds=5
        )

        self.assertEqual(out, "RMS Titanic")
        self.assertEqual(post.call_count, 2)
        self.assertGreaterEqual(post.call_args_list[1].kwargs["json"]["max_tokens"], 40)


if __name__ == "__main__":
    unittest.main()
