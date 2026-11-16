import unittest
from unittest.mock import patch
from tests.test_source_validation import Reader,Search,ROOT as URL
from collector.source_validation import validate_website
from collector.parse_passes import PASS_TYPES,FIELDS

class ReviewedValidation(unittest.IsolatedAsyncioTestCase):
 async def test_admin_recipe_reaches_validation_and_stays_guarded(self):
  body='<main><h1>Cloud Conference 2027</h1><p>Conference edition 2027 is June twelve and thirteen in Paris, France.</p></main>'
  plan={}
  fields={'start_date':'2027-06-12','end_date':'2027-06-13','format':'in_person','country':'FR','city':'Paris','categories':['Cloud'],'event_kind':'conference'}
  for kind in PASS_TYPES:
   plan[kind]={'page_urls':[URL],'require_verified':True,'verified_events':[{'title':'Cloud Conference 2027','edition':'2027','start_date':'2027-06-12','page_url':URL,'event_url':URL,
    'require_text':['Cloud Conference 2027','Conference edition 2027 is June twelve and thirteen in Paris, France.'],'fields':{k:v for k,v in fields.items() if k in FIELDS[kind]}}]}
  candidate={'url':URL,'title':'Cloud Conference 2027','evidence':{'admin_reviewed':True},'parse_settings':{'parse_passes':plan}}
  with patch('collector.manus_research.research_job',return_value=None):
   class SlashReader(Reader):
    async def read(self,url):
     page=await super().read(url);page['url']=url.rstrip('/')+'/';return page
   result=await validate_website(candidate,SlashReader({URL:body}),Search(results=[]))
   self.assertEqual(result['status'],'validated',result['reason'])
   self.assertTrue(result['parse_settings']['parse_passes']['event_date']['require_verified'])
   changed=await validate_website(candidate,Reader({URL:body.replace('June twelve and thirteen','New dates coming soon')}),Search(results=[]))
   self.assertEqual(changed['status'],'needs_review')
