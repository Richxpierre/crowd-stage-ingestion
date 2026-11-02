"""Read-only integration probe: no database connection and no source admission."""
import asyncio
import json
import os
import time
from pathlib import Path
from collector.source_validation import validate_website
from collector.crawler_reader import Crawl4AIReader
from collector.search import SearXNG

async def main():
 deadline=time.monotonic()+180
 search=SearXNG(deadline=deadline)
 try:
  async with Crawl4AIReader(os.environ.get('COLLECTOR_USER_AGENT') or 'CrowdStageValidator (+https://github.com/Richxpierre/crowd-stage-ingestion)',deadline=deadline) as reader:
   result=await validate_website({'url':'https://amef.uom.gr/','evidence':{'method':'read_only_probe'}},reader,search)
 finally:search.close()
 Path('validator-probe.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 summary={'status':result['status'],'reason':result['reason'],'pages':len(result['report'].get('pages',[])),
  'crawl_attempts':len(result['report']['attempts']),'searches':len(result['report']['searches']),
  'search_matches':result['report'].get('search_matches',0),'mapped_passes':list(result.get('parse_settings',{}).get('parse_passes',{})),
  'source_writes':0,'event_writes':0}
 print(json.dumps(summary))
 if not result['report']['attempts']:raise RuntimeError('Crawl4AI probe did not attempt a website')
 # Review is a correct outcome when public search engines cannot provide evidence.
 if result['status'] not in ('validated','needs_review'):raise RuntimeError('Unexpected validation state')
if __name__=='__main__':asyncio.run(main())
