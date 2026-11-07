import json
import os
import unittest
import time
from unittest.mock import patch
from collector.mini_enrichment import MiniEnricher,WorkerBudget,enrich_pass,discover_links,MAX_INPUT_BYTES,field_nodes
from collector.parse_passes import extract_pass,assemble,PASS_TYPES

URL='https://organizer.org/event'
SOURCE={'name':'Cloud Conference','url':URL,'publisher':'Organizer','kind':'auto','selectors':{'single_event':True}}
BODY=('<main><h1>Cloud Conference</h1><p>Conference dates June 12-13, 2027</p>'
      '<p>Event location</p><p id="country">France</p><p id="mode">Hybrid conference</p></main>'
      '<script type="application/ld+json">'+json.dumps({'@type':'Event','name':'Cloud Conference','url':URL,'startDate':'2027-06-12','endDate':'2027-06-13'})+'</script>').encode()

class MiniTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{'AI_ENRICHMENT_ENABLED':'1'});self.env.start();self.addCleanup(self.env.stop)
 def worker(self,fn,previous=None,budget=None):return MiniEnricher(previous,fn,budget or WorkerBudget(20,200000))
 def test_small_payload_and_only_observed_ids(self):
  sent=[]
  def provider(p):
   sent.append(p);return {'selections':[{'field':'country','node_id':0},{'field':'url','node_id':0},{'field':'country','node_id':999}]},{'input_tokens':100,'output_tokens':12}
  ai=self.worker(provider)
  rows=ai.choose('location',URL,'Cloud',['country'],[{'id':i,'value':'France'*50} for i in range(100)])
  self.assertEqual(rows,[{'field':'country','node_id':0}]);self.assertLessEqual(len(json.dumps(sent[0]).encode()),MAX_INPUT_BYTES)
  self.assertEqual(ai.audit['input_tokens'],100)
 def test_same_snapshot_reuses_saved_cache_but_changed_evidence_does_not(self):
  calls=[]
  def provider(p):calls.append(p);return {'selections':[]},{}
  ai=self.worker(provider);args=('details',URL,'Cloud',['summary'],[{'id':0,'value':'Conference'}])
  ai.choose(*args);ai2=self.worker(provider,ai.audit);ai2.choose(*args)
  self.assertEqual(len(calls),1);self.assertEqual(ai2.audit['cache_hits'],1)
  ai2.choose(*args[:-1],[{'id':0,'value':'Updated conference'}]);self.assertEqual(len(calls),2)
 def test_three_calls_per_item_and_worker_limit_across_items(self):
  calls=[]
  def provider(p):calls.append(p);return {'selections':[]},{}
  budget=WorkerBudget(4,200000);ai=self.worker(provider,budget=budget)
  for i in range(6):ai.choose(str(i),URL,'Cloud',['country'],[{'id':0,'value':'France'}])
  self.assertEqual(len(calls),3)
  ai=self.worker(provider,budget=budget)
  for i in range(6):ai.choose(str(i),URL,'Cloud',['country'],[{'id':0,'value':'France'}])
  self.assertEqual(len(calls),4)
 def test_quota_failure_stops_worker_without_retry_or_raw_error(self):
  def provider(p):raise RuntimeError('provider_http_429')
  budget=WorkerBudget();ai=self.worker(provider,budget=budget)
  for i in range(3):ai.choose(str(i),URL,'Cloud',['country'],[{'id':0,'value':'France'}])
  self.assertEqual(ai.audit['calls'],1);self.assertEqual(budget.stopped,'provider_http_429')
 def test_disabled_never_calls_provider(self):
  with patch.dict(os.environ,{'AI_ENRICHMENT_ENABLED':'0'}):
   ai=self.worker(lambda p:self.fail('Must not call AI'))
   self.assertEqual(ai.choose('x',URL,'Cloud',['country'],[{'id':0,'value':'France'}]),[])
 def test_known_facts_and_dates_spend_zero_tokens(self):
  ai=self.worker(lambda p:self.fail('Must not call AI'))
  for kind,fields in [('event_date',{}),('event_location',{'country':'France','city':'Paris','venue':'Hall'})]:
   r=[{'fields':fields}];self.assertEqual(enrich_pass(kind,{URL:BODY},SOURCE,{},r,ai)[0],r)
 def test_catalog_never_borrows_another_events_location(self):
  ai=self.worker(lambda p:self.fail('Must not call AI'));source={**SOURCE,'selectors':{}}
  self.assertEqual(enrich_pass('event_location',{URL:BODY},source,{},[],ai)[0],[])
 def test_literal_country_selection_maps_region_with_page_evidence(self):
  def provider(p):
   node=next(n for n in p['nodes'] if n['value']=='France')
   return {'selections':[{'field':'country','node_id':node['id']}]},{}
  ai=self.worker(provider);rows,_=enrich_pass('event_location',{URL:BODY},SOURCE,{},[],ai)
  self.assertEqual(rows[0]['fields'],{'country':'France','region':'Europe'})
  self.assertEqual(rows[0]['evidence']['method'],'ai_observed_nodes')
  self.assertEqual(rows[0]['evidence']['page_url'],URL)
 def test_selected_attendance_text_is_normalized_by_rules(self):
  def provider(p):
   node=next(n for n in p['nodes'] if n['value']=='Hybrid conference')
   return {'selections':[{'field':'format','node_id':node['id']}]},{}
  rows,_=enrich_pass('event_stats',{URL:BODY},SOURCE,{},[],self.worker(provider))
  self.assertEqual(rows[0]['fields']['format'],'hybrid')
 def test_ambiguous_field_selections_are_not_applied(self):
  def provider(p):return {'selections':[{'field':'country','node_id':n['id']} for n in p['nodes'][:2]]},{}
  self.assertEqual(enrich_pass('event_location',{URL:BODY},SOURCE,{},[],self.worker(provider))[0],[])
 def test_discovery_can_only_return_real_allowed_links_for_review(self):
  page={'url':'https://conferencemonkey.org/conference/cloud','html':'<a href="https://organizer.org/event">Learn about Cloud</a><a href="https://facebook.com/ads">Cloud</a>'}
  def provider(p):return {'selections':[{'field':'website','node_id':0},{'field':'website','node_id':999}]},{}
  rows=discover_links(page,'https://conferencemonkey.org',self.worker(provider))
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['url'],URL);self.assertTrue(rows[0]['payload']['evidence']['requires_review'])
 def test_secret_forms_scripts_footer_are_not_sent(self):
  body=BODY+b'<footer><p>Country USA</p></footer><form><p>location private</p><input value="secret"></form><script>secret</script>'
  nodes,_=field_nodes(body,'event_location');text=json.dumps(nodes)
  self.assertNotIn('secret',text);self.assertNotIn('Country USA',text);self.assertNotIn('private',text)
 def test_empty_budget_does_not_discard_deterministic_results(self):
  ai=self.worker(lambda p:self.fail('Must not call AI'),budget=WorkerBudget(0,0));r=[{'fields':{'venue':'Hall'}}]
  rows,_=enrich_pass('event_location',{URL:BODY},SOURCE,{},r,ai)
  self.assertEqual(rows,r)
 def test_time_budget_leaves_deterministic_result_intact(self):
  ai=self.worker(lambda p:self.fail('Must not call AI'));ai.deadline=time.monotonic()+2
  self.assertEqual(ai.choose('location',URL,'Cloud',['country'],[{'id':0,'value':'France'}]),[])
  self.assertEqual(ai.audit['passes'][0]['reason'],'time_budget_exhausted')
 def test_saved_country_reaches_assembled_event_with_provenance(self):
  body=BODY.replace(b'<p>Event location</p>',b'')
  def provider(p):
   node=next(n for n in p['nodes'] if n['value']=='France')
   return {'selections':[{'field':'country','node_id':node['id']}]},{}
  location,_=enrich_pass('event_location',{URL:body},SOURCE,{},[],self.worker(provider))
  report={kind:{'results':extract_pass(kind,{URL:body},SOURCE,{}),'status':'success','errors':[]} for kind in PASS_TYPES}
  report['event_location']['results']+=location
  events=assemble(report,SOURCE)
  self.assertEqual(events[0]['region'],'Europe')
  proof=events[0]['extraction']['metadata_enrichment']['evidence']['region']
  self.assertEqual(proof['method'],'ai_observed_nodes');self.assertEqual(proof['page_url'],URL)

if __name__=='__main__':unittest.main()

