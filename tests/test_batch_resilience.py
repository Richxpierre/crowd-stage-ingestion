import os
import unittest
from unittest.mock import Mock,patch
import requests
from collector.client import Client,DatabaseError
from collector.sync import main,expected_source_error

class BatchResilienceTests(unittest.TestCase):
 def run_batch(self,effects,arguments=()):
  db=Mock();db.claim_parse_request.return_value=None
  db.sources.return_value=[{'id':str(i),'slug':'source-'+str(i)} for i in range(len(effects))]
  with patch('sys.argv',['sync',*arguments]),patch('collector.sync.Client',return_value=db),\
       patch('collector.sync.sync_one',side_effect=effects) as sync,patch.dict(os.environ,{'MAX_PARSE_REQUESTS':'1'}):
   main();return sync.call_count
 def test_bad_website_does_not_disable_the_batch_or_skip_a_healthy_source(self):
  self.assertEqual(self.run_batch([requests.HTTPError('HTTP 403'),{'fetched_count':1}]),2)
 def test_missing_event_evidence_stays_a_source_error(self):
  self.assertEqual(self.run_batch([ValueError('Event date pass failed; other pass results were saved'),{}]),2)
 def test_database_schema_failure_still_fails_the_worker(self):
  with self.assertRaises(SystemExit) as error:self.run_batch([DatabaseError('Supabase POST: missing column'),{}])
  self.assertEqual(error.exception.code,1)
 def test_unexpected_programming_error_still_fails_the_worker(self):
  with self.assertRaises(SystemExit):self.run_batch([TypeError('Unexpected parser bug'),{}])
 def test_explicit_single_source_failure_is_visible_as_a_failed_run(self):
  with self.assertRaises(SystemExit):self.run_batch([requests.HTTPError('HTTP 404')],['--slug','source-0'])
 def test_database_timeouts_cannot_be_classified_as_website_timeouts(self):
  with patch.dict(os.environ,{'SUPABASE_URL':'https://database.example.org','SUPABASE_SERVICE_ROLE_KEY':'fixture'}):
   client=Client();client.session=Mock();client.session.request.side_effect=requests.Timeout('private transport details')
   with self.assertRaises(DatabaseError) as error:client.request('GET','conference_sources')
  self.assertFalse(expected_source_error(error.exception));self.assertNotIn('private',str(error.exception))

if __name__=='__main__':unittest.main()
