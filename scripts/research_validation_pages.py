"""Bounded, read-only organizer-page research. No database writes or search snippets as facts."""
import concurrent.futures
import json
import time
from pathlib import Path
from collector.fetch import Fetcher
from collector.automatic import parse_page
URLS=[
 'https://www.lisdatacenter.org/news-and-events/events/conferences/2027-3rd-iii-lis-comparative-economic-inequality-conference/',
 'https://www.mirdec.com/barca2026home','https://apef.ear.com.sg/',
 'https://leibniz-ios.de/wissen-vermitteln/aktuelles/newsdetail/ios-jahrestagung-2027-recentering-the-margins-of-the-world-economy',
 'https://www.forensoc.org/','https://www.aasle.org/beijing2026-conference-info',
 'https://fufifest.org/registration','https://international-pension-conference.com/',
]
def inspect(url):
 fetcher=Fetcher('CrowdStageValidationResearch (+https://github.com/Richxpierre/crowd-stage-ingestion)',timeout=10)
 fetcher.deadline=time.monotonic()+35
 try:
  code,body,headers=fetcher.fetch(url)
  source={'url':url,'name':'Organizer conference','publisher':'Organizer','kind':'auto','is_official_source':True,'selectors':{'website_only':True}}
  records=parse_page(body,source,headers.get('X-CrowdStage-Final-URL',url))
  return {'url':url,'http_status':code,'bytes':len(body),'events':[{'title':r['title'],'start':r['start_date'],'end':r['end_date']} for r in records][:8]}
 except Exception as error:return {'url':url,'error':str(error)[:500]}
def main():
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(inspect,URLS))
 Path('validation-page-research.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
 for result in results:print(json.dumps(result))
if __name__=='__main__':main()

