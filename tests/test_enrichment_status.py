import json
import os
import unittest
from unittest.mock import Mock, patch
from scripts.report_enrichment_status import publish

class StatusTests(unittest.TestCase):
    def test_publishes_configuration_without_keys_or_paid_requests(self):
        with patch.dict(os.environ, {
            'OPENAI_API_KEY': '',
            'ANTHROPIC_API_KEY': 'private-claude',
            'MANUS_API_KEY': 'private-manus',
            'AI_ENRICHMENT_ENABLED': '0',
            'MANUS_BACKUP_ENABLED': '0',
            'CLAUDE_ENRICHMENT_ENABLED': '1'
        }), patch('scripts.report_enrichment_status.ManusClient') as client:
            db = Mock()
            reports = publish(db)
            self.assertGreaterEqual(db.request.call_count, 1)
            client.assert_not_called()
            self.assertEqual(reports['openai']['reason'], 'openai_not_connected')
            self.assertTrue(reports['claude']['task_creation_allowed'])
            self.assertEqual(reports['claude']['reason'], 'active_core_brain')
            self.assertIn('live_tokens', reports['claude'])
            self.assertNotIn('private-claude', json.dumps(reports))

    def test_explicit_read_only_authentication_publishes_result(self):
        with patch('scripts.report_enrichment_status.ManusClient') as client:
            client.return_value.authenticate.return_value = {
                'authenticated': True, 'configured': False, 'credential_available': True, 'task_creation_allowed': False
            }
            reports = publish(Mock(), True)
            self.assertTrue(reports['manus']['authenticated'])
            client.return_value.close.assert_called_once()

if __name__ == '__main__':
    unittest.main()
