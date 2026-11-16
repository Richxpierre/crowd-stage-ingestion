import os
import time
import unittest
from unittest.mock import Mock,patch,AsyncMock
from collector.enrichment_values import normalize_evidence,scope_issue
from collector.pipeline_control import configuration
from collector.client import DatabaseError
from collector.mini_enrichment import ask,ask_openai
from collector.claude_worker import process_events,process_research

class RecoveryTests(unittest.TestCase):
 def row(self,field,value,quote=None):
  return {'field':field,'value':value,'quote':quote or value,'page_url':'https://conf.org/','event_title':'Cloud Summit 2027'}
 def test_invalid_enum_and_unknown_values_never_saved(self):
  rows=[self.row('region','San Francisco, CA'),self.row('format','<UNKNOWN>')]
  fields,_,_=normalize_evidence(rows,'San Francisco, CA <UNKNOWN>','Cloud Summit 2027','https://conf.org/',{})
  self.assertEqual(fields,{})
 def test_direct_country_maps_to_macro_region(self):
  fields,_,_=normalize_evidence([self.row('country','Germany'),self.row('format','onsite')],'Germany onsite','Cloud Summit 2027','https://conf.org/',{})
  self.assertEqual(fields,{'country':'DE','region':'Europe','format':'in-person'})
 def test_quote_wrong_event_or_url_does_not_become_evidence(self):
  row=self.row('country','Germany');row['event_title']='Different edition'
  self.assertFalse(normalize_evidence([row],'Germany','Cloud Summit 2027','https://conf.org/',{})[0])
  row=self.row('country','Germany');row['page_url']='https://other.org/'
  self.assertFalse(normalize_evidence([row],'Germany','Cloud Summit 2027','https://conf.org/',{})[0])
 def test_unknown_and_non_usd_prices_remain_unknown(self):
  fields,_,_=normalize_evidence([self.row('price_from','100','Tickets EUR 100')],'Tickets EUR 100','Cloud Summit 2027','https://conf.org/',{})
  self.assertNotIn('price_from',fields)
 def test_workshop_uncertainty_does_not_archive_conference(self):
  self.assertIsNone(scope_issue('Conference with workshops'))
  self.assertIsNotNone(scope_issue('Weekly workshop'))
 def test_control_fails_closed_and_removes_openai_key(self):
  db=Mock();db.request.return_value={'enabled':True,'manual_paused':False}
  with patch.dict(os.environ,{'OPENAI_API_KEY':'fixture','AI_ENRICHMENT_ENABLED':'1'}):
   self.assertTrue(configuration(db,'parsing')['can_run'])
   self.assertNotIn('OPENAI_API_KEY',os.environ);self.assertEqual(os.environ['AI_ENRICHMENT_ENABLED'],'0')
  db.request.return_value=None
  with self.assertRaises(DatabaseError):configuration(db,'parsing')
 def test_openai_never_used_as_fallback_while_paused(self):
  with patch.dict(os.environ,{'OPENAI_API_KEY':'fixture','AI_ENRICHMENT_ENABLED':'0','OPENAI_PAUSED':'1'}),patch('collector.claude_client.configured',return_value=True),patch('collector.mini_enrichment.ask_claude',side_effect=RuntimeError('provider_error')),patch('collector.mini_enrichment.ask_openai') as fallback:
   with self.assertRaises(RuntimeError):ask({})
   fallback.assert_not_called()
  with patch.dict(os.environ,{'AI_ENRICHMENT_ENABLED':'0'}),patch('requests.Session') as session:
   with self.assertRaisesRegex(RuntimeError,'openai_paused'):ask_openai({})
   session.assert_not_called()
 def test_failed_page_keeps_event_in_review_with_bounded_retry(self):
  db=Mock();db.request.side_effect=[{'id':'job','claim_token':'uuid','event':{'id':'event','name':'Cloud Summit','external_url':'https://conf.org/'}},{'status':'queued'}]
  with patch('collector.claude_worker.configuration',return_value={'can_run':True}),patch('collector.claude_worker.read_page',side_effect=TimeoutError('page timeout')):
   process_events(db,Mock(),1,time.monotonic()+100)
  saved=db.request.call_args.kwargs['json']
  self.assertEqual(saved['p_outcome'],'retry');self.assertNotIn('status',saved['p_patch'])
 def test_research_selections_keep_correct_payload_shape(self):
  db=Mock();db.request.side_effect=[{'id':'job','claim_token':'uuid','payload':{'stage':'details','fields':['country'],'nodes':[{'id':2}]}},None]
  claude=Mock();claude.select_evidence.return_value=({'selections':[{'field':'country','node_id':2},{'field':'country','node_id':99}]},{})
  with patch('collector.claude_worker.configuration',return_value={'can_run':True}),patch('collector.manus_queue.wake_consumers'):
   process_research(db,claude,1,time.monotonic()+100)
  self.assertEqual(db.request.call_args.kwargs['json']['p_result'],{'selections':[{'field':'country','node_id':2}]})
 def test_saved_research_is_reused_with_both_providers_paused(self):
  from collector.manus_research import research_job
  db=Mock();db.request.return_value=[{'id':'saved','status':'completed','result':{'evidence':[]}}]
  with patch('collector.manus_backup.configured',return_value=False),patch('collector.claude_client.configured',return_value=False):
   self.assertEqual(research_job({'url':'https://conf.org/'},{},db=db)['id'],'saved')
  self.assertEqual(db.request.call_count,1)
 def test_claude_research_queue_does_not_require_paid_manus_enabled(self):
  from collector.manus_queue import enqueue
  db=Mock();db.request.return_value={'id':'queued'}
  payload={'stage':'research','url':'https://conf.org/','event':'Cloud Summit 2027','fields':['country']}
  with patch('collector.manus_queue.configured',return_value=False),patch('collector.claude_client.configured',return_value=True),patch('collector.manus_queue.ManusClient') as paid:
   self.assertEqual(enqueue(payload,db)['id'],'queued');paid.assert_not_called()
 def test_manus_browser_failure_holds_existing_task_without_resubmission(self):
  from datetime import datetime,timezone
  from collector.manus_queue import reconcile
  job={'id':'job','claim_token':'uuid','task_id':'existing','started_at':datetime.now(timezone.utc).isoformat(),'payload':{'stage':'research'}}
  db=Mock();client=Mock()
  client.task_status.return_value={'status':'completed','background_running':False,'credits_used':1}
  client.usage.return_value={'allowance':300,'available':299,'spent':1}
  client.result.return_value={'evidence':[{'field':'country'}]}
  with patch('collector.manus_research.verify',new=AsyncMock(side_effect=RuntimeError('Browser unavailable'))):
   self.assertEqual(reconcile(db,client,job),'review')
  client.create.assert_not_called()
  self.assertEqual(db.request.call_args.kwargs['json']['status'],'review')
  self.assertEqual(db.request.call_args.kwargs['json']['result']['evidence'],[])
