import json
import unittest
from collector.automatic import parse_page
from collector.source_validation import validate_website
from collector.validation_recovery import scope_url,BoundedSearch
from test_source_validation import Reader,Search,BODY,URL,ROOT

SOURCE={'url':URL,'name':'Conference','publisher':'Organizer','kind':'auto','is_official_source':True,'selectors':{'website_only':True}}
class EvidenceTests(unittest.TestCase):
 def test_concurrent_cached_searches_do_not_multiply_network_requests(self):
  from concurrent.futures import ThreadPoolExecutor
  import time
  class SlowSearch:
   calls=0
   def search(self,query):
    self.calls+=1;time.sleep(0.01);return {'results':[]}
  search=SlowSearch();bounded=BoundedSearch(search)
  with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(bounded.search,['same query']*8))
  self.assertEqual(search.calls,1);self.assertEqual(bounded.requests,1)
 def dates(self,html):return {(r['start_date'],r['end_date']) for r in parse_page(html.encode(),SOURCE,URL)}
 def test_article_with_submission_deadline_uses_explicit_conference_sentence(self):
  html='<main><h1>IOS-Jahrestagung 2027: Recentering the Margins of the World Economy</h1><p>The submission deadline is December 15, 2026. The conference will take place from May 20 to 21, 2027 in Regensburg, Germany.</p></main>'
  self.assertEqual(self.dates(html),{('2027-05-20','2027-05-21')})
 def test_lis_article_without_hero_date_is_parsed(self):
  html='<main><h1>2027-3rd III/LIS Comparative Economic Inequality Conference</h1><p>LIS invites scholars to the 3rd III/LIS Comparative Economic Inequality Conference on 11-12 February 2027 at the London School of Economics (LSE).</p><h3>Submissions</h3><p>Submission deadline: November 30, 2026</p></main>'
  self.assertEqual(self.dates(html),{('2027-02-11','2027-02-12')})
 def test_h3_page_builder_heading_and_explicit_prose(self):
  html='<h3>10th Asia-Pacific Conference on Economics and Finance</h3><p>APEF2026 will be a hybrid conference happening in Singapore from December 10-11, 2026!</p>'
  self.assertEqual(self.dates(html),{('2026-12-10','2026-12-11')})
 def test_deadlines_alone_are_not_event_dates(self):
  self.assertEqual(self.dates('<main><h1>Cloud Conference 2027</h1><p>Submission deadline May 20, 2027.</p><p>Registration closes May 21, 2027.</p></main>'),set())
 def test_catalog_does_not_join_two_cards(self):
  self.assertEqual(self.dates('<main><h1>Conferences</h1><article><h2>Cloud Conference</h2><p>The conference will be held May 20, 2027.</p></article><article><h2>Security Conference</h2><p>The conference will be held June 10, 2027.</p></article></main>'),set())
 def test_shared_hosted_tenants_are_never_collapsed_to_parent(self):
  self.assertFalse(scope_url('https://sciencesconf.org','https://event.sciencesconf.org'))
  self.assertFalse(scope_url('https://other.sciencesconf.org','https://event.sciencesconf.org'))
  self.assertFalse(scope_url('https://sites.google.com/view/other','https://sites.google.com/view/our-event'))

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
 async def test_generated_date_selector_cannot_override_conference_with_deadline(self):
  html='<h4>MIRDEC International Academic Conference 24-25 November 2026, Barcelona</h4><h2>Submission deadline</h2><p>13 November 2026</p>'
  result=await validate_website({'url':URL},Reader({URL:html}),Search(results=[]))
  self.assertEqual(result['status'],'validated')
  self.assertEqual({(r['start_date'],r['end_date']) for r in result['report']['event_evidence']},{('2026-11-24','2026-11-25')})
  self.assertNotIn('start_date',result['parse_settings']['parse_passes']['event_date']['selectors'])
 async def test_root_then_observed_event_url_is_third_and_final_strategy(self):
  target=ROOT+'/2027/annual-conference'
  reader=Reader({URL:RuntimeError('HTTP 404'),ROOT:'<h1>Organizer</h1><p>Find our conferences and upcoming scientific meetings here.</p><a href="'+target+'">Annual conference 2027</a>',target:BODY})
  result=await validate_website({'url':URL},reader,Search(results=[]))
  self.assertEqual(result['status'],'validated')
  self.assertLessEqual(len(result['report']['strategies']),3)
  self.assertEqual(result['canonical_url'],ROOT)
  self.assertIn(target,result['report']['pages'])
 async def test_search_hints_can_find_working_organizer_path_after_404(self):
  target=ROOT+'/conference-2027'
  reader=Reader({URL:RuntimeError('HTTP 404'),ROOT:RuntimeError('HTTP 404'),target:BODY})
  result=await validate_website({'url':URL},reader,Search(results=[{'url':target,'title':'Cloud Conference 2027'}]))
  self.assertEqual(result['status'],'validated');self.assertEqual(len(result['report']['strategies']),3)
 async def test_rate_limit_does_not_cascade_to_more_urls(self):
  reader=Reader({URL:RuntimeError('HTTP 429')})
  result=await validate_website({'url':URL},reader,Search())
  self.assertEqual(result['report']['failure_code'],'rate_limited');self.assertEqual(len(reader.calls),1)
 async def test_404_and_dns_failures_share_global_limits(self):
  for error in (RuntimeError('HTTP 404'),RuntimeError('Temporary failure in name resolution')):
   reader=Reader({URL:error,ROOT:error,'https://www.organizer.org':error})
   result=await validate_website({'url':URL},reader,Search(results=[]))
   self.assertLessEqual(len(result['report']['strategies']),3)
   self.assertLessEqual(result['report']['navigations_used'],8)
   self.assertLessEqual(result['report']['searches_used'],3)
 async def test_unknown_fields_do_not_block_event_identity(self):
  node={'@type':'Event','name':'Cloud Conference 2027','url':URL,'startDate':'2027-06-12','endDate':'2027-06-13'}
  body='<h1>Cloud Conference 2027</h1>'+''.join('<script type="application/ld+json">'+json.dumps({**node,'description':text})+'</script>' for text in ('First official summary','Short official summary'))
  result=await validate_website({'url':URL},Reader({URL:body}),Search(results=[]))
  self.assertEqual(result['status'],'validated')
  self.assertTrue(any(w['code']=='optional_conflicts' for w in result['report']['warnings']))
 async def test_conflicting_location_never_becomes_validated_via_unrelated_root(self):
  node={'@type':'Event','name':'Cloud Conference 2027','url':URL,'startDate':'2027-06-12','endDate':'2027-06-13'}
  body=''.join('<script type="application/ld+json">'+json.dumps({**node,'location':{'name':city,'address':{'addressLocality':city}}})+'</script>' for city in ('Paris','London'))
  result=await validate_website({'url':URL},Reader({URL:body}),Search(results=[]))
  self.assertEqual(result['status'],'needs_review')

