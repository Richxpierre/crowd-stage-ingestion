"""The website queues refreshes via next_check_at; worker selection is the handoff."""
import unittest
from unittest.mock import patch

from collector.client import Client


class SourceSelectionTests(unittest.TestCase):
    def client(self):
        with patch.dict('os.environ', {
            'SUPABASE_URL': 'https://example.supabase.co',
            'SUPABASE_SERVICE_ROLE_KEY': 'test-only',
        }):
            return Client()

    def test_scheduled_run_reads_due_approved_sources(self):
        client = self.client()
        with patch.object(client, 'request', return_value=[{'slug': 'queued'}]) as request:
            self.assertEqual(client.sources(), [{'slug': 'queued'}])
        params = request.call_args.kwargs['params']
        self.assertTrue(params['next_check_at'].startswith('lte.'))
        self.assertEqual(params['order'], 'next_check_at.asc')
        for gate in ('is_active', 'is_official_source', 'ingestion_allowed'):
            self.assertEqual(params[gate], 'eq.true')

    def test_manual_workflow_can_force_one_approved_source(self):
        client = self.client()
        with patch.object(client, 'request', return_value=[]) as request:
            client.sources(slug='requested-source')
        params = request.call_args.kwargs['params']
        self.assertEqual(params['slug'], 'eq.requested-source')
        self.assertNotIn('next_check_at', params)
        self.assertEqual(params['is_active'], 'eq.true')
        self.assertEqual(params['is_official_source'], 'eq.true')
        self.assertEqual(params['ingestion_allowed'], 'eq.true')
