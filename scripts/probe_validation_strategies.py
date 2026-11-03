"""Real Crawl4AI, read-only probe of previously held organizer pages. No database client."""
import asyncio
import json
import os
import time
from pathlib import Path
from collector.crawler_reader import Crawl4AIReader
from collector.source_validation import validate_website
URLS=[
 'https://www.lisdatacenter.org/news-and-events/events/conferences/2027-3rd-iii-lis-comparative-economic-inequality-conference/',
 'https://apef.ear.com.sg/','https://www.mirdec.com/barca2026home',
]
class NoIndex:
 def search(self,query):return {'results':[],'warnings':'Probe intentionally tests direct page evidence without indexing'}
async def main():
 results=[]
 async with Crawl4AIReader(os.environ.get('COLLECTOR_USER_AGENT') or 'CrowdStageValidator (+https://github.com/Richxpierre/crowd-stage-ingestion)',deadline=time.monotonic()+600) as reader:
  for url in URLS:
   reader.deadline=time.monotonic()+160
   result=await validate_website({'url':url},reader,NoIndex());results.append(result)
   print(json.dumps({'url':url,'status':result['status'],'reason':result['reason'],'strategies':len(result['report']['strategies']),
     'navigations':result['report']['navigations_used'],'searches':result['report']['searches_used'],'events':result['report'].get('event_evidence'),'database_writes':0}),flush=True)
 Path('validator-strategy-probe.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
 if any(r['status']!='validated' for r in results):raise RuntimeError('A confirmed event page still needs parser investigation')
if __name__=='__main__':asyncio.run(main())

