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
EXPECTED=[('2027-02-11','2027-02-12'),('2026-12-10','2026-12-11'),('2026-11-24','2026-11-25')]
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
 for result,dates in zip(results,EXPECTED):
  if result['status']=='validated':
   observed={(event['start_date'],event['end_date']) for event in result['report'].get('event_evidence',[])}
   if observed!={dates}:raise RuntimeError('Extracted dates disagree with the independently researched event dates')
  elif result['report'].get('failure_code') not in ('access_blocked','policy_unavailable','dns_failure','timeout','server_error'):
   raise RuntimeError('A readable confirmed event page still needs parser investigation')
 if sum(r['status']=='validated' for r in results)<2:raise RuntimeError('Too few live pages were accessible to verify the strategy implementation')
if __name__=='__main__':asyncio.run(main())

