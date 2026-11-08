import asyncio
import copy
import time
import unittest
from unittest.mock import patch
from collector.access_recovery import AccessRecovery,AccessBlocked,block_kind,challenge_page

SEED='https://catalog.example/conferences'
ALT='https://catalog.example/events'
JOB={'id':'job','target_id':'target','config':{'url':SEED,'settings':{}}}

class DB:
 def __init__(self,rows=None):self.rows=rows or [];self.cancelled=False;self.requests=[]
 def crawl_job(self,id):return {'status':'running','cancel_requested':self.cancelled}
 def request(self,method,path,**kw):self.requests.append((method,path,kw));return self.rows if method=='GET' else {}

class Reader:
 def __init__(self,blocked=()):self.reads=[];self.blocked=set(blocked)
 async def read(self,url):
  self.reads.append(url)
  if url==SEED or url in self.blocked:raise RuntimeError('HTTP 403 Cloudflare challenge')
  return {'url':url,'html':'<h1>Conferences</h1><a href="/conference/one">Conference One</a>'}

class Search:
 results=[{'url':ALT,'title':'Conference directory'}];calls=0
 def __init__(self,**kw):pass
 def search(self,query):Search.calls+=1;return {'results':self.results}
 def close(self):pass

class AccessTests(unittest.TestCase):
 def setUp(self):Search.calls=0;Search.results=[{'url':ALT,'title':'Conference directory'}]
 def recovery(self,db=None,config=None):return AccessRecovery(db or DB(),JOB,config or copy.deepcopy(JOB['config']),time.monotonic()+120,Search)
 def test_only_access_errors_trigger(self):
  for text in ('HTTP 403','Cloudflare','CAPTCHA','Access denied'):self.assertEqual(block_kind(text),'anti_bot')
  for text in ('HTTP 404','robots denied (403)','unsafe private address','certificate expired'):self.assertIsNone(block_kind(text))
  self.assertEqual(block_kind('HTTP 429'),'rate_limited')
 def test_detect_200_challenge_without_confusing_conference_text(self):
  self.assertTrue(challenge_page({'html':'<title>Just a moment...</title><form id="challenge-form"></form>'}))
  self.assertFalse(challenge_page({'html':'<title>Cloudflare Developer Conference</title><p>Talk about CAPTCHA</p>'}))
 def test_mini_selects_observed_alternate_then_real_reader_checks_it(self):
  recovery=self.recovery();reader=Reader()
  with patch('collector.access_recovery.MiniEnricher') as ai:
   ai.return_value.choose.return_value=[{'field':'follow','node_id':0}];ai.return_value.audit={}
   page=asyncio.run(recovery.read(reader,SEED))
  self.assertEqual(page['url'],ALT);self.assertEqual(reader.reads,[SEED,ALT]);self.assertEqual(recovery.report['status'],'recovered')
 def test_unobserved_or_external_ai_urls_never_fetched(self):
  Search.results=[{'url':'https://evil.example/events','title':'external'},{'url':ALT,'title':'listing'}]
  recovery=self.recovery();reader=Reader()
  with patch('collector.access_recovery.MiniEnricher') as ai,patch('collector.access_recovery.configured',return_value=False):
   ai.return_value.choose.return_value=[{'field':'follow','node_id':99}];ai.return_value.audit={}
   with self.assertRaises(AccessBlocked):asyncio.run(recovery.read(reader,SEED))
  self.assertEqual(reader.reads,[SEED]);self.assertEqual(recovery.report['status'],'needs_review')
 def test_manus_wait_releases_worker_and_resume_skips_original_block(self):
  recovery=self.recovery()
  with patch('collector.access_recovery.MiniEnricher') as ai,patch('collector.access_recovery.configured',return_value=True),patch('collector.manus_queue.enqueue',return_value={'id':'mini','status':'queued'}):
   ai.return_value.choose.return_value=[];ai.return_value.audit={}
   with self.assertRaises(AccessBlocked):asyncio.run(recovery.read(Reader(),SEED))
  self.assertTrue(recovery.waiting)
  config=copy.deepcopy(recovery.config);db=DB([{'status':'completed','result':{'selections':[{'field':'follow','node_id':0}]}}]);reader=Reader()
  resumed=self.recovery(db,config);asyncio.run(resumed.read(reader,SEED))
  self.assertEqual(reader.reads,[ALT]);self.assertEqual(Search.calls,1)
 def test_failed_manus_is_review_instead_of_waiting_forever(self):
  cfg=copy.deepcopy(JOB['config']);cfg['access_recovery']={'status':'awaiting_enrichment','blocked_url':SEED,'manus_job_id':'mini','candidates':[]}
  recovery=self.recovery(DB([{'status':'failed'}]),cfg)
  with self.assertRaises(AccessBlocked):asyncio.run(recovery.read(Reader(),SEED))
  self.assertEqual(recovery.report['status'],'needs_review');self.assertFalse(recovery.waiting)
 def test_three_probes_max_and_no_cascade(self):
  Search.results=[{'url':ALT+str(i),'title':'Conference directory'} for i in range(8)]
  recovery=self.recovery();reader=Reader([n['url'] for n in Search.results])
  with patch('collector.access_recovery.MiniEnricher') as ai:
   ai.return_value.choose.return_value=[{'field':'follow','node_id':i} for i in range(8)];ai.return_value.audit={}
   with self.assertRaises(AccessBlocked):asyncio.run(recovery.read(reader,SEED))
   with self.assertRaises(AccessBlocked):asyncio.run(recovery.read(reader,SEED))
  self.assertEqual(len(recovery.report['attempts']),3);self.assertEqual(Search.calls,1)
 def test_rate_limit_never_triggers_paid_or_immediate_retry(self):
  recovery=self.recovery()
  with patch('collector.access_recovery.MiniEnricher') as ai:
   with self.assertRaises(AccessBlocked):asyncio.run(recovery.recover(Reader(),SEED,'rate_limited'))
   ai.assert_not_called()
  self.assertEqual(Search.calls,0);self.assertEqual(recovery.report['status'],'cooldown')
 def test_cancellation_prevents_search_and_paid_tasks(self):
  db=DB();db.cancelled=True;recovery=self.recovery(db)
  with self.assertRaises(AccessBlocked):asyncio.run(recovery.recover(Reader(),SEED,'anti_bot'))
  self.assertEqual(Search.calls,0)
 def test_exhausted_recovery_cannot_cascade_through_bulk_continuations(self):
  cfg=copy.deepcopy(JOB['config']);cfg['access_recovery']={'status':'needs_review','blocked_url':SEED,'reason':'Three probes exhausted'}
  recovery=self.recovery(config=cfg);reader=Reader()
  with self.assertRaises(AccessBlocked):asyncio.run(recovery.read(reader,SEED))
  self.assertFalse(reader.reads);self.assertEqual(Search.calls,0);self.assertTrue(recovery.stopped)

if __name__=='__main__':unittest.main()
