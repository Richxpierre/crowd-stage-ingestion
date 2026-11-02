import json
import unittest
from collector.source_validation import validate_website,website_root
from collector.fetch import UnsafeDestination

URL='https://organizer.org/conference'
ROOT='https://organizer.org'
BODY='<h1>Cloud Conference 2027</h1><script type="application/ld+json">'+json.dumps({'@type':'Event','name':'Cloud Conference 2027','startDate':'2027-06-12','endDate':'2027-06-13','url':URL,'keywords':'cloud, infrastructure','location':{'name':'Event Hall','address':{'addressLocality':'Paris'}},'offers':{'price':25,'priceCurrency':'EUR'}})+'</script>'
class Reader:
 def __init__(self,pages):self.pages=pages;self.calls=[]
 async def read(self,url):
  self.calls.append(url);value=self.pages.get(url,self.pages.get(url.rstrip('/')))
  if isinstance(value,Exception):raise value
  if value is None:raise RuntimeError('HTTP 404')
  return {'url':url,'html':value,'links':[],'markdown':''}
class Search:
 def __init__(self,results=None,error=None):self.results=results if results is not None else [{'url':URL,'title':'Cloud Conference','query':'site','engines':['test']}];self.error=error
 def search(self,query):
  if self.error:raise self.error
  return {'results':self.results,'warnings':None}
class ValidationTests(unittest.IsolatedAsyncioTestCase):
 async def test_accepts_crawled_event_with_search_and_four_pass_plan(self):
  result=await validate_website({'url':URL},Reader({URL:BODY}),Search())
  self.assertEqual(result['status'],'validated')
  self.assertEqual(set(result['parse_settings']['parse_passes']),{'event_date','event_stats','event_location','event_details'})
  self.assertTrue(result['report']['event_evidence'])
 async def test_404_retries_root_once_and_keeps_original_audit(self):
  reader=Reader({URL:RuntimeError('HTTP 404'),ROOT:BODY})
  result=await validate_website({'url':URL},reader,Search(results=[{'url':ROOT,'title':'Conference'}]))
  self.assertEqual(result['status'],'validated');self.assertEqual(result['canonical_url'],ROOT)
  self.assertEqual(result['report']['original_url'],URL);self.assertEqual(reader.calls[:2],[URL,ROOT])
 async def test_persistent_403_is_review_not_validation(self):
  reader=Reader({URL:PermissionError('HTTP 403'),ROOT:PermissionError('HTTP 403')})
  result=await validate_website({'url':URL},reader,Search())
  self.assertEqual(result['status'],'needs_review');self.assertEqual(reader.calls,[URL,ROOT])
 async def test_search_outage_or_empty_search_never_autovalidates(self):
  for search in (Search(error=RuntimeError('Search unavailable')),Search(results=[])):
   result=await validate_website({'url':URL},Reader({URL:BODY}),search)
   self.assertEqual(result['status'],'needs_review')
 async def test_future_dates_cannot_come_from_search_snippet(self):
  result=await validate_website({'url':URL},Reader({URL:'<h1>Cloud Conference</h1><p>Our annual conference is coming soon, please check back for details.</p>'}),Search())
  self.assertEqual(result['status'],'needs_review')
 async def test_soft_404_is_held(self):
  result=await validate_website({'url':URL},Reader({URL:'<title>Page not found</title><h1>404 not found</h1>'}),Search())
  self.assertEqual(result['status'],'needs_review')
 async def test_external_discoveries_stay_in_review_until_admin_reviewed(self):
  result=await validate_website({'url':URL,'evidence':{'requires_review':True}},Reader({URL:BODY}),Search())
  self.assertEqual(result['status'],'needs_review')
 async def test_unsafe_network_destination_does_not_trigger_root_retry(self):
  reader=Reader({URL:UnsafeDestination('Refusing non-public DNS destination')})
  result=await validate_website({'url':URL},reader,Search())
  self.assertEqual(result['status'],'needs_review');self.assertEqual(reader.calls,[URL])
 def test_root_does_not_strip_a_hosted_tenant(self):
  self.assertEqual(website_root('https://conference.example.org/2026/event?x=1'),'https://conference.example.org')
