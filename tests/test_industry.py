import asyncio
import time
import unittest
from unittest.mock import Mock,patch
from collector.industry import extract,classify
from collector.manus_research import research_job,add_results
class IndustryTests(unittest.TestCase):
 def test_scoped_prose_without_meta_keywords(self):
  body=b'<main><h1>Community Conference 2027</h1><p>An ethical hacking conference for security professionals.</p></main><footer>Sponsored by finance partners</footer>'
  self.assertIn('ethical hacking',extract(body,'Community Conference 2027'))
  self.assertNotIn('finance',extract(body,'Community Conference 2027'))
  self.assertEqual(extract(body,'Other Conference 2027'),'')
 def test_preview_keeps_metadata_shape_for_browser_enrichment(self):
  db=Mock();db.request.return_value=[{'status':'needs_review'}]
  record={'title':'Event'};classify(db,[record])
  self.assertEqual(record['extraction']['metadata_enrichment']['warnings'],[])
  self.assertEqual(record['extraction']['metadata_enrichment']['evidence'],{})
 def test_catalog_cannot_lend_industry(self):
  self.assertEqual(extract(b'<h1>Upcoming events</h1><p>Cybersecurity software and banking events.</p>','Upcoming events',True),'')
 def test_missing_industry_queues_even_with_complete_core_facts(self):
  fields={'start_date':'2027-06-12','end_date':'2027-06-13','country':'France','format':'in_person','event_kind':'conference'}
  results={'event_date':{'results':[{'title':'Community Conference 2027','fields':fields}]}}
  db=Mock();db.request.side_effect=[[],[{'status':'needs_review'}]]
  with patch('collector.manus_backup.configured',return_value=True),patch('collector.manus_queue.enqueue',return_value={'status':'queued'}) as enqueue:
   research_job({'url':'https://organizer.org','name':'Community Conference 2027'}, {},results,db)
   self.assertEqual(enqueue.call_args.args[0]['fields'],['categories'])
   self.assertEqual(enqueue.call_args.args[0]['research_version'],2)
 def test_complete_geography_still_reads_missing_industry_then_skips_paid_research(self):
  from collector.metadata_worker import enrich
  url='https://organizer.org';title='Community Conference 2027'
  class Reader:
   calls=0
   async def read(self,target):
    self.calls+=1
    return {'url':target,'html':f'<main><h1>{title}</h1><p>An ethical hacking conference for cybersecurity professionals.</p></main>'}
  reader=Reader();search=Mock();search.search.side_effect=AssertionError('No search needed')
  db=Mock();db.request.return_value=[{'status':'matched','slug':'security'}]
  record={'title':title,'start_date':'2027-06-12','end_date':'2027-06-13','event_url':url,'official_url':url,'source_url':url,'source_record_id':'one','format':'in_person','region':'Europe','country':'France','city':'Paris','categories':[],'_industry_resolved':False}
  with patch('collector.manus_research.research_job') as research:
   rows,audit=asyncio.run(enrich([record],{'url':url,'publisher':'Organizer','selectors':{'single_event':True}},reader,search,time.monotonic()+30,db=db))
   self.assertEqual(reader.calls,1);self.assertIn('ethical hacking',rows[0]['industry_text']);research.assert_not_called();search.search.assert_not_called()
 def test_failed_research_is_reused_not_requeued(self):
  db=Mock();db.request.return_value=[{'status':'failed','id':'known'}]
  with patch('collector.manus_backup.configured',return_value=True),patch('collector.manus_queue.enqueue') as enqueue:
   self.assertEqual(research_job({'url':'https://organizer.org'}, {},{},db)['status'],'failed');enqueue.assert_not_called()
 def test_verified_topic_keeps_existing_categories(self):
  url='https://organizer.org';title='Community Conference 2027';body=f'<main><h1>{title}</h1><p>An ethical hacking conference.</p></main>'.encode()
  job={'id':'job','status':'completed','payload':{'event':title,'known':{'start_date':'2027-06-12'}},'result':{'evidence':[{'field':'categories','value':'ethical hacking','quote':'An ethical hacking conference.','page_url':url,'event_title':title}]}}
  existing=[{'fields':{'categories':['Technology']}}]
  rows=add_results('event_details',{url:body},{'url':url},existing,job)
  self.assertEqual(rows[0]['fields']['categories'],['Technology']);self.assertEqual(rows[1]['fields']['industry_research'],'ethical hacking')
if __name__=='__main__':unittest.main()
