import json
import os
import unittest
from unittest.mock import Mock,patch
from collector.manus_backup import ManusClient,ManusError,prepare_task,validate_selection,readiness,fallback_report,MAX_PROMPT_BYTES

PAYLOAD={'stage':'event_location','url':'https://organizer.org','event':'Cloud Conference 2027','fields':['country'],
         'nodes':[{'id':0,'value':'France','context':'Venue country','hint':'country'}]}

class ManusTests(unittest.TestCase):
 def setUp(self):
  p=patch.dict(os.environ,{'MANUS_API_KEY':'test-key','MANUS_BACKUP_ENABLED':'1'});p.start();self.addCleanup(p.stop)
 def client(self,data):
  session=Mock();response=Mock();response.status_code=200;response.content=b'{}';response.json.return_value=data
  session.request.return_value=response
  return ManusClient(session),session
 def test_strict_limit_blocks_create_before_any_network_call(self):
  client,session=self.client({})
  with self.assertRaisesRegex(ManusError,'hard_token_cap_unavailable'):client.create(PAYLOAD)
  session.request.assert_not_called();self.assertFalse(readiness()['task_creation_allowed'])
 def test_authentication_returns_no_personal_or_credential_data(self):
  client,session=self.client({'ok':True,'user':{'email':'private@example.org','name':'Private'}})
  data=client.authenticate();self.assertTrue(data['authenticated'])
  self.assertNotIn('private',json.dumps(data).lower());self.assertNotIn('test-key',json.dumps(data))
  self.assertEqual(session.request.call_args.kwargs['headers']['x-manus-api-key'],'test-key')
  self.assertFalse(session.request.call_args.kwargs['allow_redirects'])
 def test_safe_provider_errors_do_not_expose_response_messages(self):
  client,_=self.client({'ok':False,'error':{'code':'unauthenticated','message':'secret should not be logged'}})
  with self.assertRaisesRegex(ManusError,'^manus_unauthenticated$'):client.authenticate()
 def test_private_lite_payload_is_small_and_not_submitted(self):
  task=prepare_task(PAYLOAD)
  self.assertEqual(task['agent_profile'],'lite');self.assertTrue(task['clear_connectors'])
  self.assertEqual(task['share_visibility'],'private')
  self.assertLessEqual(len(task['message']['content'].encode()),MAX_PROMPT_BYTES)
  self.assertNotIn('https://organizer.org',task['message']['content'])
 def test_missing_evidence_is_not_submitted(self):
  with self.assertRaises(ManusError):prepare_task({**PAYLOAD,'nodes':[]})
 def test_structured_result_must_reference_requested_observed_nodes(self):
  self.assertEqual(validate_selection({'selections':[{'field':'country','node_id':0}]},PAYLOAD)['selections'][0]['node_id'],0)
  for row in ({'field':'country','node_id':99},{'field':'url','node_id':0},{'field':'country','node_id':True}):
   with self.assertRaises(ManusError):validate_selection({'selections':[row]},PAYLOAD)
 def test_result_retrieval_does_not_use_arbitrary_assistant_text(self):
  client,_=self.client({'ok':True,'messages':[{'type':'assistant_message','assistant_message':{'content':'made up facts'}}]})
  self.assertIsNone(client.result('task123',PAYLOAD))
 def test_valid_structured_output_is_returned(self):
  client,_=self.client({'ok':True,'messages':[{'type':'structured_output_result','structured_output_result':{
   'success':True,'value':{'selections':[{'field':'country','node_id':0}]}}}]})
  self.assertEqual(client.result('task123',PAYLOAD)['selections'][0]['field'],'country')
 def test_credit_usage_is_never_reported_as_tokens(self):
  client,_=self.client({'ok':True,'task':{'status':'running','credit_usage':42}})
  data=client.task_status('task123');self.assertEqual(data['credits_used'],42);self.assertIsNone(data['tokens_used'])
 def test_bad_task_id_makes_no_network_request(self):
  client,session=self.client({})
  with self.assertRaises(ManusError):client.task_status('../other?key=x')
  session.request.assert_not_called()
 def test_fallback_report_keeps_hard_limit_and_primary_failure(self):
  r=fallback_report('provider_http_429');self.assertEqual(r['daily_limit'],2000)
  self.assertEqual(r['daily_limit_unit'],'model_tokens');self.assertEqual(r['reason'],'manus_hard_token_cap_unavailable')
  self.assertEqual(r['primary_reason'],'provider_http_429')
 def test_manus_only_configuration_does_not_resume_openai(self):
  from collector.mini_enrichment import MiniEnricher,WorkerBudget
  with patch.dict(os.environ,{'AI_ENRICHMENT_ENABLED':'0','OPENAI_API_KEY':'unused'}):
   ai=MiniEnricher(budget=WorkerBudget())
   self.assertEqual(ai.choose('event_location','https://organizer.org','Cloud',['country'],PAYLOAD['nodes']),[])
   self.assertEqual(ai.audit['calls'],0)
   self.assertEqual(ai.audit['passes'][0]['backup']['reason'],'manus_hard_token_cap_unavailable')
 def test_primary_failure_adds_backup_readiness_to_existing_report(self):
  from collector.mini_enrichment import MiniEnricher,WorkerBudget
  def primary(payload):raise RuntimeError('provider_http_429')
  ai=MiniEnricher(provider=primary,budget=WorkerBudget())
  ai.choose('event_location','https://organizer.org','Cloud',['country'],PAYLOAD['nodes'])
  self.assertEqual(ai.audit['passes'][0]['backup']['primary_reason'],'provider_http_429')

if __name__=='__main__':unittest.main()

