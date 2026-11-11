import unittest
from unittest.mock import patch, MagicMock
import requests
from collector.claude_client import ClaudeClient

class TestClaudeTimeoutAndTokenConservation(unittest.TestCase):
    def test_timeout_abandonment_raises_clean_error(self):
        client = ClaudeClient(key="test-key")
        # Mock requests session post to raise requests.Timeout
        with patch.object(client.session, 'post', side_effect=requests.Timeout("Operation timed out")):
            with self.assertRaises(RuntimeError) as ctx:
                client.research_fields("Test Conference", "https://example.com", ["city"], "Some long page text")
            self.assertIn("verification_timeout_abandoned", str(ctx.exception))

    def test_token_conservation_truncates_input(self):
        client = ClaudeClient(key="test-key")
        captured_body = {}

        def mock_post(*args, **kwargs):
            nonlocal captured_body
            captured_body = kwargs.get('json', {})
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                'content': [{'type': 'tool_use', 'name': 'research_evidence', 'input': {'evidence': []}}],
                'usage': {'input_tokens': 100, 'output_tokens': 20}
            }
            return resp

        with patch.object(client.session, 'post', side_effect=mock_post):
            huge_text = "A" * 20000
            client.research_fields("Big Conf", "https://example.com", ["city"], huge_text)
            
            # Verify excerpt was truncated to 3,500 chars to conserve tokens
            messages = captured_body.get('messages', [])
            self.assertTrue(len(messages) > 0)
            user_content = messages[0]['content']
            self.assertLessEqual(len(user_content), 4500)
            # Verify max_tokens is capped to 400
            self.assertEqual(captured_body.get('max_tokens'), 400)

if __name__ == '__main__':
    unittest.main()
