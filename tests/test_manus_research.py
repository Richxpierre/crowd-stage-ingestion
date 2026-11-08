import asyncio
import copy
import os
import unittest
from unittest.mock import Mock,patch
from collector.manus_research import prepare,validate,verify,add_results,research_job
from collector.manus_backup import prepare_task
from collector.parse_passes import assemble,PASS_TYPES

URL='https://organizer.org/conference'
TITLE='Cloud Conference 2027'
BODY='<main><h1>'+TITLE+'</h1><p>Conference dates June 12-13, 2027</p><p>Venue country France</p><p>Hybrid conference</p></main>'
SOURCE={'url':URL,'name':TITLE,'publisher':'Cloud','selectors':{'single_event':True}}
PAYLOAD={'stage':'research','url':URL,'event':TITLE,'fields':['start_date','end_date','country','format'],'known':{}}
def row(field,value,quote):return {'field':field,'value':value,'quote':quote,'page_url':URL,'event_title':TITLE}
EVIDENCE={'evidence':[row('start_date','June 12-13, 2027','Conference dates June 12-13, 2027'),
 row('end_date','June 12-13, 2027','Conference dates June 12-13, 2027'),row('country','France','Venue country France'),
 row('format','Hybrid conference','Hybrid conference')]}
class Reader:
 def __init__(self,body=BODY):self.body=body;self.calls=[]
 async def read(self,url):self.calls.append(url);return {'url':url,'html':self.body}

class ResearchTests(unittest.TestCase):
 def test_agent_can_browse_and_receives_only_missing_fields(self):
  task=prepare_task(PAYLOAD);prompt=task['message']['content']
  self.assertIn(URL,prompt);self.assertIn('Browse',prompt);self.assertIn('three pages',prompt)
  self.assertNotIn('Do not browse',prompt);self.assertEqual(task['share_visibility'],'private')
  self.assertLess(len(prompt.encode()),2200)
 def test_untrusted_or_unsubstantiated_urls_and_values_are_not_returned(self):
  for replacement in ({'page_url':'https://attacker.org/'},{'value':'Germany'},{'field':'password'}):
   proposal={'evidence':[{**EVIDENCE['evidence'][2],**replacement}]}
   self.assertEqual(validate(proposal,PAYLOAD),{'evidence':[]})
 def test_shared_host_cannot_cross_tenants(self):
  proposal={'evidence':[{**EVIDENCE['evidence'][2],'page_url':'https://other.wordpress.com/conference'}]}
  self.assertEqual(validate(proposal,{**PAYLOAD,'url':'https://owner.wordpress.com/conference'})['evidence'],[])
 def test_crawl4ai_independently_checks_quote_and_event_identity_once(self):
  reader=Reader();result=asyncio.run(verify(EVIDENCE,PAYLOAD,reader))
  self.assertEqual(len(result['evidence']),4);self.assertEqual(reader.calls,[URL])
  self.assertEqual(result['verification'],'crawl4ai_direct_organizer')
  for body in (BODY.replace('France','Canada'),BODY.replace(TITLE,'Other Conference 2027')):
   result=asyncio.run(verify({'evidence':[EVIDENCE['evidence'][2]]},PAYLOAD,Reader(body)))
   self.assertEqual(result['evidence'],[])
 def test_footer_and_catalog_evidence_stay_in_review(self):
  for body in ('<main><h1>'+TITLE+'</h1></main><footer>Venue country France</footer>',
               BODY.replace(TITLE,'Upcoming events')):
   self.assertEqual(asyncio.run(verify({'evidence':[EVIDENCE['evidence'][2]]},PAYLOAD,Reader(body)))['evidence'],[])
 def test_verified_research_fills_four_pass_assembly_without_model_facts(self):
  job={'id':'job','status':'completed','payload':PAYLOAD,'result':EVIDENCE}
  report={kind:{'results':add_results(kind,{URL:BODY.encode()},SOURCE,[],job),'errors':[],'status':'success'} for kind in PASS_TYPES}
  events=assemble(report,SOURCE)
  self.assertEqual(len(events),1);event=events[0]
  self.assertEqual((event['start_date'],event['end_date']),('2027-06-12','2027-06-13'))
  self.assertEqual((event['country'],event['region'],event['format']),('France','Europe','hybrid'))
  self.assertEqual(report['event_location']['results'][0]['evidence']['method'],'manus_crawl4ai_verified')
 def test_changed_page_discards_stale_research(self):
  job={'id':'job','payload':PAYLOAD,'result':EVIDENCE}
  self.assertEqual(add_results('event_location',{URL:BODY.replace('France','Canada').encode()},SOURCE,[],job),[])
 def test_known_fields_and_edition_are_not_replaced(self):
  job={'id':'job','payload':{**PAYLOAD,'known':{'start_date':'2028-06-12'}},'result':EVIDENCE}
  self.assertEqual(add_results('event_date',{URL:BODY.encode()},SOURCE,[],job),[])
  existing=[{'fields':{'country':'Canada','region':'north_america'}}]
  self.assertEqual(add_results('event_location',{URL:BODY.encode()},SOURCE,existing,job),existing)
 def test_complete_source_needs_no_paid_research(self):
  with patch.dict(os.environ,{'MANUS_API_KEY':'test','MANUS_BACKUP_ENABLED':'1'}),patch('collector.manus_queue.enqueue') as enqueue:
   db=Mock();db.request.return_value=[]
   results={'event_date':{'results':[{'fields':{f:'known' for f in ('start_date','end_date','country','format','event_kind')}}]}}
   self.assertIsNone(research_job(SOURCE,{},results,db));enqueue.assert_not_called()

if __name__=='__main__':unittest.main()
