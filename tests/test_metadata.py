import json
import time
import unittest
from collector.metadata import infer,region_for
from collector.parse_passes import extract_pass,assemble,PASS_TYPES
from collector.metadata_worker import enrich
S={'name':'Example Conference','url':'https://example.org/event','publisher':'test','selectors':{'single_event':True}}
def html(text='',title='Example Conference'):return f'<main><h1>{title}</h1><p>{text}</p></main>'.encode()
class MetadataTests(unittest.TestCase):
 def test_region_country_codes_and_aliases(self):
  for country,region in [('US','North America'),('Mexico','LATAM'),('DE','Europe'),('FRA','Europe'),('Türkiye','MEA'),('Singapore','APAC')]:self.assertEqual(region_for(country),region)
 def test_structured_address_provides_macro_region_and_format(self):
  n={'@type':'Event','name':'Example Conference','startDate':'2027-06-01','url':S['url'],'location':{'@type':'Place','name':'Hall','address':{'addressCountry':'US','addressRegion':'California','addressLocality':'San Francisco'}}}
  body=('<script type="application/ld+json">'+json.dumps(n)+'</script>').encode()
  report={p:{'results':extract_pass(p,{S['url']:body},S,{}),'status':'success'} for p in PASS_TYPES}
  rec=assemble(report,S)[0];self.assertEqual(rec['region'],'North America');self.assertEqual(rec['format'],'in_person');self.assertEqual(rec['event_kind'],'conference')
 def test_beijing_venue_prose(self):
  result=infer(html('The conference will be held in Beijing, China.'),S['url'],'Example Conference')
  self.assertEqual(result['fields']['region'],'APAC');self.assertEqual(result['fields']['country'],'China')
 def test_country_domain_is_suggestion_not_fact(self):
  result=infer(html(), 'https://example.de/event','Example Conference')
  self.assertNotIn('region',result['fields']);self.assertEqual(result['suggestions']['region']['value'],'Europe')
 def test_generic_country_domains_do_not_assign_venue(self):
  for suffix in ['co','me','io','com','org']:
   self.assertFalse(infer(html(),f'https://example.{suffix}/','Example Conference')['suggestions'])
 def test_footer_office_address_ignored(self):
  result=infer(html()+b'<footer>Location: France. Online conference</footer>',S['url'],'Example Conference')
  self.assertNotIn('region',result['fields']);self.assertNotIn('format',result['fields'])
 def test_virtual_hybrid_and_physical(self):
  for text,fmt in [('An online conference','online'),('A hybrid conference','hybrid'),('An in-person conference','in_person')]:self.assertEqual(infer(html(text),S['url'],'Example Conference')['fields']['format'],fmt)
 def test_recordings_and_registration_are_not_virtual_attendance(self):
  self.assertNotIn('format',infer(html('Register online. Watch recordings online.'),S['url'],'Example Conference')['fields'])
 def test_catalog_does_not_lend_country_or_format(self):
  result=infer(html('Location: France. Online conference','All conferences'),S['url'],'Example Conference');self.assertNotIn('region',result['fields']);self.assertNotIn('format',result['fields'])
 def test_kind_classification(self):
  for title,kind in [('Energy Trade Show','trade-show'),('Cloud Expo','expo'),('Climate Summit','summit'),('Science Conference','conference')]:self.assertEqual(infer(html(title=title),S['url'],title)['fields']['event_kind'],kind)
 def test_conflicting_countries_not_auto_assigned(self):
  self.assertNotIn('region',infer(html('Location: France and Germany'),S['url'],'Example Conference')['fields'])
 def test_negated_online_and_registration_not_formats(self):
  for text in ['This is not a virtual conference.','Online conference registration is now open.']:
   self.assertNotIn('format',infer(html(text),S['url'],'Example Conference')['fields'])
 def test_mirdec_hybrid_without_h1(self):
  r=infer(b'<h4>MIRDEC Conference Barcelona Spain</h4><h2>ON-SITE &amp; ONLINE CONFERENCE</h2>',S['url'],'MIRDEC Conference Barcelona Spain',single_event=True)
  self.assertEqual(r['fields']['format'],'hybrid');self.assertEqual(r['fields']['region'],'Europe')
 def test_hybrid_heading_after_large_rendered_wrapper(self):
  body=('<main><h4>MIRDEC Conference Barcelona Spain</h4>'+('menu '*10000)+'<h2>ON-SITE &amp; ONLINE CONFERENCE</h2></main>').encode()
  self.assertEqual(infer(body,S['url'],'MIRDEC Conference Barcelona Spain',single_event=True)['fields']['format'],'hybrid')
 def test_virtual_location(self):
  r=infer(b'',S['url'],'Example Conference',node={'location':{'@type':'VirtualLocation'}});self.assertEqual(r['fields']['format'],'online')
class Reader:
 def __init__(self,body):self.body=body;self.calls=[]
 async def read(self,url):self.calls.append(url);return {'url':url,'html':self.body.decode()}
class Search:
 def __init__(self):self.calls=[]
 def search(self,q):self.calls.append(q);return {'results':[{'url':'https://unrelated.example/France'}]}
class WorkerTests(unittest.IsolatedAsyncioTestCase):
 async def test_browser_proof_is_used_search_snippets_are_not(self):
  record={'title':'Example Conference','start_date':'2027-06-01','end_date':'2027-06-01','source_record_id':'one','event_url':S['url'],'format':'unknown'}
  reader=Reader(html('A hybrid conference. Location: Singapore.'));search=Search()
  records,audit=await enrich([record],S,reader,search,time.monotonic()+10)
  self.assertEqual(records[0]['region'],'APAC');self.assertEqual(records[0]['format'],'hybrid');self.assertEqual(len(reader.calls),1);self.assertFalse(search.calls)
 async def test_search_unrelated_urls_are_not_fetched(self):
  record={'title':'Example Conference','start_date':'2027-06-01','source_record_id':'one','event_url':S['url'],'format':'unknown'}
  reader=Reader(html());search=Search()
  records,audit=await enrich([record],S,reader,search,time.monotonic()+10)
  self.assertLessEqual(len(reader.calls),3);self.assertLessEqual(len(search.calls),2);self.assertNotIn('region',record);self.assertEqual(reader.calls,[S['url']])
if __name__=='__main__':unittest.main()
