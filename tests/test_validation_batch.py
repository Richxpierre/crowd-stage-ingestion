import asyncio
import unittest
from unittest.mock import AsyncMock,MagicMock,patch
from collector.source_validation import run

class ValidationBatchTests(unittest.TestCase):
 def test_batch_reuses_browser_and_finishes_each_claim(self):
  rows=[{'id':'one','url':'https://one.example.org','claim_token':'a'},
        {'id':'two','url':'https://two.example.org','claim_token':'b'},None]
  db=MagicMock()
  db.request.side_effect=lambda method,path,**kwargs: rows.pop(0) if path.endswith('claim_conference_source_validation') else {}
  browser=MagicMock();browser.__aenter__=AsyncMock(return_value=browser);browser.__aexit__=AsyncMock()
  validate=AsyncMock(return_value={'status':'needs_review','reason':'fixture'})
  with patch('collector.client.Client',return_value=db),patch('collector.source_validation.Crawl4AIReader',return_value=browser) as factory,patch('collector.source_validation.SearXNG'),patch('collector.source_validation.validate_website',validate):
   asyncio.run(run(50))
  self.assertEqual(factory.call_count,1)
  self.assertEqual(validate.await_count,2)
  finishes=[c for c in db.request.call_args_list if c.args[1].endswith('finish_conference_source_validation')]
  self.assertEqual([c.kwargs['json']['p_claim'] for c in finishes],['a','b'])
 def test_scoped_validation_does_not_drain_other_candidates(self):
  db=MagicMock();db.request.return_value={'id':'one','url':'https://one.example.org','claim_token':'a'}
  browser=MagicMock();browser.__aenter__=AsyncMock(return_value=browser);browser.__aexit__=AsyncMock()
  with patch('collector.client.Client',return_value=db),patch('collector.source_validation.Crawl4AIReader',return_value=browser),patch('collector.source_validation.SearXNG'),patch('collector.source_validation.validate_website',AsyncMock(return_value={'status':'needs_review','reason':'fixture'})) as validate:
   asyncio.run(run(50,validation_id='one'))
  self.assertEqual(validate.await_count,1)

