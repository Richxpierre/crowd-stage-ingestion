import os
import unittest
from unittest.mock import Mock, patch
import requests
from collector.client import Client, DatabaseError

class DatabaseRetryTests(unittest.TestCase):
 def client(self):
  with patch.dict(os.environ, {'SUPABASE_URL':'https://db.example','SUPABASE_SERVICE_ROLE_KEY':'fixture'}): c=Client()
  c.session=Mock();return c
 def response(self,status=200,body=None):
  r=Mock();r.status_code=status;r.ok=status<400;r.content=b'{}';r.text='redacted failure';r.json.return_value=body or {};return r
 @patch('collector.client.time.sleep')
 def test_read_and_stable_parse_upsert_recover_after_transport_failure(self,sleep):
  for method,path,kwargs in [('GET','conference_sources',{}),('POST','conference_source_parse_results',{'params':{'on_conflict':'run_id,pass_type'}})]:
   c=self.client();c.session.request.side_effect=[requests.Timeout('private'),self.response(body={'ok':True})]
   self.assertEqual(c.request(method,path,**kwargs),{'ok':True});self.assertEqual(c.session.request.call_count,2)
 @patch('collector.client.time.sleep')
 def test_uncertain_claim_and_create_are_not_replayed(self,sleep):
  for path in ['rpc/claim_conference_frontier','conference_ingestion_runs']:
   c=self.client();c.session.request.side_effect=requests.ReadTimeout('private')
   with self.assertRaises(DatabaseError):c.request('POST',path)
   self.assertEqual(c.session.request.call_count,1)
 @patch('collector.client.time.sleep')
 def test_confirmed_rolled_back_statement_timeout_can_retry_claim(self,sleep):
  c=self.client();c.session.request.side_effect=[self.response(500,{'code':'57014'}),self.response(body={'id':'claimed'})]
  self.assertEqual(c.request('POST','rpc/claim_conference_frontier'),{'id':'claimed'})
 @patch('collector.client.time.sleep')
 def test_retries_are_bounded_and_permissions_are_not_retried(self,sleep):
  c=self.client();c.session.request.side_effect=requests.Timeout('private token')
  with self.assertRaises(DatabaseError) as error:c.request('GET','conference_sources')
  self.assertEqual(c.session.request.call_count,3);self.assertNotIn('private',str(error.exception))
  c=self.client();c.session.request.return_value=self.response(403)
  with self.assertRaises(DatabaseError):c.request('GET','conference_sources')
  self.assertEqual(c.session.request.call_count,1)
 @patch('collector.client.time.sleep')
 def test_generic_server_error_does_not_replay_a_write(self,sleep):
  c=self.client();c.session.request.return_value=self.response(503)
  with self.assertRaises(DatabaseError):c.request('POST','rpc/ingest_crowdstage_conference_batch')
  self.assertEqual(c.session.request.call_count,1)
