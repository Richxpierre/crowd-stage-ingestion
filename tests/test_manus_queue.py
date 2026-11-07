import os
import unittest
from unittest.mock import Mock,patch
from datetime import datetime,timezone
from collector.manus_backup import ManusClient,ManusError
from collector.manus_queue import enqueue,tick,settle_finished

PAYLOAD={'stage':'event_location','url':'https://organizer.example/event','event':'Cloud Conference','fields':['country'],'nodes':[{'id':0,'value':'France','context':'Venue country'}]}
class QueueTests(unittest.TestCase):
 def setUp(self):
  p=patch.dict(os.environ,{'MANUS_API_KEY':'fixture-key','MANUS_BACKUP_ENABLED':'1'});p.start();self.addCleanup(p.stop)
 def test_enqueue_is_fingerprinted_and_never_calls_manus(self):
  db=Mock();db.request.return_value={'id':'job','status':'queued'}
  with patch('collector.manus_queue.ManusClient') as client:
   enqueue(PAYLOAD,db);enqueue(PAYLOAD,db);client.assert_not_called()
  self.assertEqual(db.request.call_args_list[0].kwargs['json']['p_fingerprint'],db.request.call_args_list[1].kwargs['json']['p_fingerprint'])
 def test_daily_gate_block_never_creates_task(self):
  db=Mock();db.request.side_effect=[[],{'reason':'manus_daily_buffer_reached'}]
  client=Mock();client.usage.return_value={'allowance':300,'available':60,'spent':240,'reset_at':'future'}
  self.assertEqual(tick(db,client)['reason'],'manus_daily_buffer_reached');client.create.assert_not_called()
 def test_shared_claim_precedes_creation_and_persists_task_id(self):
  job={'id':'job','claim_token':'claim','status':'claimed','payload':PAYLOAD}
  db=Mock();db.request.side_effect=[[],job,None]
  client=Mock();client.usage.return_value={'allowance':300,'available':300,'spent':0,'reset_at':'future'};client.create.return_value='task123'
  result=tick(db,client);self.assertEqual(result['task_id'],'task123')
  self.assertEqual(db.request.call_args.kwargs['json']['status'],'running')
 def test_ambiguous_submission_is_held_and_not_retried(self):
  db=Mock();db.request.side_effect=[[],{'id':'job','claim_token':'claim','status':'claimed','payload':PAYLOAD},None]
  client=Mock();client.usage.return_value={'allowance':300,'available':300,'spent':0,'reset_at':'future'};client.create.side_effect=ManusError('manus_connection_error')
  tick(db,client);self.assertEqual(db.request.call_args.kwargs['json']['status'],'unknown');client.create.assert_called_once()
 def test_completed_selection_wakes_normal_pipeline_not_event_publication(self):
  job={'id':'job','claim_token':'claim','status':'running','task_id':'task123','payload':PAYLOAD,'started_at':datetime.now(timezone.utc).isoformat()}
  db=Mock();db.request.side_effect=lambda method,path,**kwargs:[job] if method=='GET' else None
  client=Mock();client.task_status.return_value={'status':'stopped','credits_used':12,'background_running':False}
  client.result.return_value={'selections':[{'field':'country','node_id':0}]}
  client.usage.return_value={'allowance':300,'available':288,'spent':12,'reset_at':'future'}
  self.assertEqual(tick(db,client)['reason'],'completed')
  self.assertFalse(any(c.args[1]=='events' for c in db.request.call_args_list));client.create.assert_not_called()
 def test_usage_includes_live_allowance_and_consumption_not_grants(self):
  client=ManusClient(Mock());import time
  with patch.object(client,'_request',side_effect=[{'max_refresh_credits':300,'refresh_credits':250,'total_credits':1250,'next_refresh_time':str(time.time()+3600),'refresh_interval':'daily'},
       {'data':[{'created_at':time.time(),'credits':-50,'type':'cost'},{'created_at':time.time(),'credits':300,'type':'grant'}],'has_more':False}]):
   u=client.usage();self.assertEqual(u['allowance'],300);self.assertEqual(u['available'],250);self.assertEqual(u['spent'],50)
 def test_unavailable_usage_blocks_new_tasks(self):
  db=Mock();db.request.return_value=[];client=Mock();client.usage.side_effect=ManusError('manus_usage_unknown')
  with self.assertRaises(ManusError):tick(db,client)
  client.create.assert_not_called()
 def test_running_task_hits_low_balance_trigger_and_stop_is_not_repeated(self):
  job={'id':'job','claim_token':'claim','status':'running','task_id':'task123','payload':PAYLOAD,'started_at':datetime.now(timezone.utc).isoformat()}
  db=Mock();db.request.side_effect=lambda method,path,**kwargs:[job] if method=='GET' else None
  client=Mock();client.task_status.return_value={'status':'running','credits_used':250,'background_running':False}
  client.usage.return_value={'allowance':300,'available':50,'spent':250,'reset_at':'future'}
  tick(db,client);client.stop.assert_called_once_with('task123');client.create.assert_not_called()
  job['reason']='manus_stop_requested';tick(db,client);client.stop.assert_called_once()
 def test_late_credit_settlement_does_not_create_or_wake_tasks(self):
  job={'id':'job','claim_token':'claim','task_id':'task123'}
  db=Mock();db.request.side_effect=[[job],None];client=Mock();client.task_status.return_value={'credits_used':7}
  settle_finished(db,client)
  self.assertEqual(db.request.call_args.kwargs['json']['credits_used'],7);client.create.assert_not_called()

if __name__=='__main__':unittest.main()

