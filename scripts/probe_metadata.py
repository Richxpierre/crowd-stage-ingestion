"""Read-only CLI: python -m scripts.probe_metadata [--url ... --title ... --start-date YYYY-MM-DD]."""
import argparse,asyncio,json,os,time
from collector.crawler_reader import Crawl4AIReader
from collector.metadata_worker import enrich
from collector.search import SearXNG
async def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url');p.add_argument('--title');p.add_argument('--start-date');args=p.parse_args()
 if args.url and not(args.title and args.start_date):p.error('--url needs --title and --start-date')
 cases=[(args.url,args.title,args.start_date,None,None)] if args.url else [
 ('https://www.aasle.org/beijing2026-conference-info','AASLE 2026 CONFERENCE','2026-12-10','APAC','in_person'),
 ('https://www.mirdec.com/barca2026home','MIRDEC Conference Barcelona Spain','2026-11-24','Europe','hybrid'),
 ('https://wapsociety.org/event/wap3','3rd Welfare & Policy conference','2027-05-10','Europe','in_person')]
 async with Crawl4AIReader(os.environ.get('COLLECTOR_USER_AGENT',''),deadline=time.monotonic()+300) as reader:
  verified=0
  for url,title,start,region,fmt in cases:
   deadline=time.monotonic()+90;reader.deadline=deadline;search=SearXNG(deadline=deadline)
   try:
    records,audit=await enrich([{'title':title,'start_date':start,'end_date':start,'event_url':url,'source_record_id':url,'format':'unknown'}],{'url':url,'publisher':'probe','selectors':{'single_event':True}},reader,search,deadline)
   finally:search.close()
   record=records[0];print(json.dumps({'url':url,'region':record.get('region'),'format':record.get('format'),'evidence':record.get('extraction',{}),'audit':audit,'database_writes':0}),flush=True)
   if record.get('region') and record.get('format') not in (None,'unknown'):
    if region and (record['region'],record['format'])!=(region,fmt):raise RuntimeError('Wrong live metadata classification')
    verified+=1
  if not args.url and verified<2:raise RuntimeError('Too few live sites classified for meaningful verification')
if __name__=='__main__':asyncio.run(main())
