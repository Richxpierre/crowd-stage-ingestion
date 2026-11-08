import asyncio
import copy
import unittest
from unittest.mock import patch
from collector.aggregator_setup import run_setup
from collector.discovery import classify_links,run_job
from collector.discovery_mapping import observed_nodes,mapped_links

SEED='https://directory.example/conferences'
DETAIL='https://directory.example/conference/cloud-2027'
JOB={'id':'11111111-1111-1111-1111-111111111111','target_id':'22222222-2222-2222-2222-222222222222',
     'config':{'url':SEED,'settings':{},'setup_only':True,'setup_attempt':1}}

class DB:
 def __init__(self):self.report=None;self.generated=None;self.cancelled=False;self.calls=[]
 def crawl_job(self,id):return {'status':'running','cancel_requested':self.cancelled}
 def update_crawl_job(self,id,values):self.calls.append(values)
 def request(self,method,path,**kw):
  self.assert_no_source_write(path)
  if path=='rpc/finish_conference_crawler_setup':
   self.report=kw['json']['p_report'];self.generated=kw['json']['p_generated'];self.state=kw['json']['p_state'];return self.report
  return [{'status':'completed','result':{'selections':[{'field':'follow','node_id':0}]}}]
 def assert_no_source_write(self,path):
  if 'source' in path or 'event' in path:raise AssertionError('Setup must not create sources or events')

class Reader:
 pages={};reads=[]
 def __init__(self,*a,**kw):pass
 async def __aenter__(self):return self
 async def __aexit__(self,*a):pass
 async def read(self,url):
  self.reads.append(url)
  if url not in self.pages:raise RuntimeError('HTTP 403')
  return {'url':url,'html':self.pages[url]}

class SetupTests(unittest.TestCase):
 def setUp(self):Reader.reads=[];Reader.pages={SEED:'<main><a class="event-card" href="/conference/cloud-2027">Cloud Conference</a></main>',DETAIL:'<a class="official" href="https://cloud.example/">Visit conference website</a>'}
 def test_setup_saves_verified_sequence_without_source_writes(self):
  db=DB()
  with patch('collector.aggregator_setup.MiniEnricher') as ai:
   ai.return_value.audit={};result=asyncio.run(run_setup(db,copy.deepcopy(JOB),Reader))
   ai.return_value.choose.assert_not_called()
  self.assertEqual(result['status'],'ready');self.assertEqual(len(Reader.reads),2)
  self.assertEqual(result['example_urls'],['https://cloud.example/'])
  self.assertEqual(db.generated['discovery_mode'],'website_links')
  # Settings work on the following edition, rather than only the sampled URL.
  page={'url':'https://directory.example/conference/cloud-2028','html':'<a class="official" href="https://next.example/">Visit website</a>'}
  self.assertEqual(classify_links(page,SEED,db.generated)[1][0]['url'],'https://next.example/')
 def test_bounded_failure_does_not_fabricate_rules(self):
  Reader.pages={};db=DB();result=asyncio.run(run_setup(db,copy.deepcopy(JOB),Reader))
  self.assertEqual(result['status'],'needs_review');self.assertEqual(db.generated,{})
  self.assertEqual(len(Reader.reads),1);self.assertIn('403',result['errors'][0]['message'])
 def test_setup_branch_precedes_bulk_adapter(self):
  job=copy.deepcopy(JOB);job['config']['settings']['adapter']='sessionize';db=DB()
  result=asyncio.run(run_job(db,job,Reader));self.assertEqual(result['status'],'ready')
 def test_manus_pause_and_resume_uses_observed_links(self):
  Reader.pages[SEED]='<div class="entry"><a href="/cloud">Cloud 2027</a></div>'
  Reader.pages['https://directory.example/cloud']=Reader.pages[DETAIL]
  db=DB()
  with patch('collector.aggregator_setup.configured',return_value=True),patch('collector.aggregator_setup.MiniEnricher') as ai,patch('collector.manus_queue.enqueue',return_value={'id':'manus-job','status':'queued'}) as enqueue:
   ai.return_value.choose.return_value=[];ai.return_value.audit={}
   result=asyncio.run(run_setup(db,copy.deepcopy(JOB),Reader))
  self.assertEqual(result['status'],'awaiting_enrichment');self.assertEqual(db.generated,{})
  self.assertLessEqual(len(enqueue.call_args.args[0]['nodes']),20)
  resumed=copy.deepcopy(JOB);resumed['config']['setup_state']=copy.deepcopy(db.state)
  result=asyncio.run(run_setup(db,resumed,Reader));self.assertEqual(result['status'],'ready')
 def test_changed_manus_evidence_requires_review(self):
  db=DB();job=copy.deepcopy(JOB)
  job['config']['setup_state']={'queue':[SEED],'visited':[],'steps':[],'examples':[],'errors':[],
   'pending':{'id':'manus-job','url':SEED,'nodes':[{'id':0,'url':'https://directory.example/removed'}]}}
  result=asyncio.run(run_setup(db,job,Reader));self.assertEqual(result['status'],'needs_review')
 def test_cancelled_setup_does_not_open_browser(self):
  db=DB();db.cancelled=True;self.assertEqual(asyncio.run(run_setup(db,copy.deepcopy(JOB),Reader))['status'],'cancelled');self.assertFalse(Reader.reads)
 def test_explicit_profile_mapping_bypasses_generic_skip_safely(self):
  page={'url':SEED,'html':'<a class="profile" href="/speakers/alice">Alice</a><a class="profile" href="https://outside.example/">Outside</a>'}
  inside,outside=mapped_links(page,SEED,[{'match_path':'/conferences','follow_selector':'a.profile'}])
  self.assertEqual(inside,['https://directory.example/speakers/alice']);self.assertFalse(outside)
 def test_zero_mapped_matches_does_not_fall_back_to_unrelated_links(self):
  self.assertEqual(classify_links({'url':SEED,'html':'<a href="https://unrelated.example">Official website</a>'},SEED,{'discovery_steps':[{'match_path':'/conferences','website_selector':'.absent'}]}),([],[]))
 def test_no_class_listing_generalizes_and_social_links_excluded(self):
  nodes=observed_nodes({'url':SEED,'html':'<a href="/conference/a">Conference A</a><a href="https://facebook.com/a">Official website</a>'},SEED)
  self.assertEqual(len(nodes),1);self.assertIn('href^=',nodes[0]['selector'])
 def test_shared_button_class_does_not_collect_ticket_or_sponsor_links(self):
  page={'url':DETAIL,'html':'<a class="btn" href="https://official.example/">Visit website</a><a class="btn" href="https://tickets.example/">Register now</a><a class="btn" href="https://ads.example/">Sponsor</a>'}
  inside,outside=mapped_links(page,SEED,[{'match_path':'/conference/*','website_selector':'a.btn','website_require_label':True}])
  self.assertEqual([n['url'] for n in outside],['https://official.example/'])
 def test_event_listing_ranked_before_large_navigation(self):
  html='<nav>'+''.join('<a href="/top/topic-'+str(i)+'">Conference topic</a>' for i in range(30))+'</nav><main><a class="post-link" href="/conference/real">Real Conference</a></main>'
  nodes=observed_nodes({'url':SEED,'html':html},SEED)
  followed=[n for n in nodes if n['hint']=='follow']
  self.assertEqual([n['url'] for n in followed],[SEED.replace('/conferences','/conference/real')])

 def test_explicit_button_wins_over_earlier_duplicate_url(self):
  page={'url':DETAIL,'html':'<a class="more" href="https://official.example/">More Information</a><a class="button" href="https://official.example/">Visit Website</a>'}
  nodes=observed_nodes(page,SEED)
  self.assertEqual(len(nodes),1);self.assertEqual(nodes[0]['hint'],'website');self.assertIn('a.button',nodes[0]['selector'])

if __name__=='__main__':unittest.main()
