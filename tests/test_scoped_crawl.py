import unittest
from unittest.mock import Mock
from collector.client import Client
class ScopedCrawlTests(unittest.TestCase):
    def test_claim_only_targets_one_still_queued_uncancelled_job(self):
        client=Client.__new__(Client)
        job={'id':'chosen','target_id':'target','config':{'interval_hours':24}}
        client.request=Mock(side_effect=[[job],None])
        self.assertEqual(client.claim_crawl_job('chosen'),job)
        params=client.request.call_args_list[0].kwargs['params']
        self.assertEqual(params,{'id':'eq.chosen','status':'eq.queued','cancel_requested':'eq.false'})
    def test_no_match_does_not_claim_another_job(self):
        client=Client.__new__(Client);client.request=Mock(return_value=[])
        self.assertIsNone(client.claim_crawl_job('already-running'))
        self.assertEqual(client.request.call_count,1)
