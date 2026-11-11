import json
import os
import unittest
from unittest.mock import Mock, patch
from collector.claude_client import ClaudeClient, configured, DEFAULT_MODEL
from collector.mini_enrichment import MiniEnricher, WorkerBudget

class ClaudeEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'ANTHROPIC_API_KEY': 'test-ant-key',
            'CLAUDE_ENRICHMENT_ENABLED': '1',
            'ANTHROPIC_MODEL': 'claude-haiku-4-5-20251001'
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_configured(self):
        self.assertTrue(configured())
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}):
            self.assertFalse(configured())

    def test_select_evidence_tool_use_parsing(self):
        client = ClaudeClient()
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'content': [
                {
                    'type': 'tool_use',
                    'name': 'evidence_selection',
                    'input': {'selections': [{'field': 'country', 'node_id': 3}]}
                }
            ],
            'usage': {'input_tokens': 120, 'output_tokens': 24}
        }
        with patch.object(client.session, 'post', return_value=mock_resp):
            res, usage = client.select_evidence({'fields': ['country']}, 'Test instructions')
            self.assertEqual(res, {'selections': [{'field': 'country', 'node_id': 3}]})
            self.assertEqual(usage['input_tokens'], 120)

    def test_research_fields_parsing(self):
        client = ClaudeClient()
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'content': [
                {
                    'type': 'tool_use',
                    'name': 'research_evidence',
                    'input': {
                        'evidence': [
                            {
                                'field': 'country',
                                'value': 'Germany',
                                'quote': 'Join us in Berlin, Germany',
                                'page_url': 'https://example.com',
                                'event_title': 'Cloud 2027'
                            }
                        ]
                    }
                }
            ],
            'usage': {'input_tokens': 200, 'output_tokens': 40}
        }
        with patch.object(client.session, 'post', return_value=mock_resp):
            evidence, usage = client.research_fields('Cloud 2027', 'https://example.com', ['country'], 'Page text')
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]['field'], 'country')
            self.assertEqual(evidence[0]['value'], 'Germany')

    def test_mini_enricher_with_claude_provider(self):
        def mock_claude_provider(p):
            return {'selections': [{'field': 'country', 'node_id': 0}]}, {'input_tokens': 50, 'output_tokens': 10}
        enricher = MiniEnricher(None, provider=mock_claude_provider, budget=WorkerBudget(10, 10000))
        selections = enricher.choose('location', 'https://example.com', 'Test Event', ['country'], [{'id': 0, 'value': 'Japan'}])
        self.assertEqual(selections, [{'field': 'country', 'node_id': 0}])
        self.assertEqual(enricher.audit['input_tokens'], 50)

if __name__ == '__main__':
    unittest.main()
