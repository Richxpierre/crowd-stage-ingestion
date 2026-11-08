"""Bounded Crawl4AI/SearXNG enrichment before ingestion. No AI or auto-publication."""
import asyncio
from html import unescape
import os
import time
from urllib.parse import urlsplit
from .metadata import infer
from .parsers import _finish

async def enrich(records,source,reader,search,deadline,db=None):
 from .validation_recovery import scope_url
 from .parse_passes import event_nodes
 audit={'version':1,'navigations':[],'searches':[],'errors':[],'max_navigations':3,'max_searches':2}
 cache={};searched=0;reads=0
 for record in records:
  if time.monotonic()>=deadline:break
  if record.get('format') not in (None,'unknown','') and record.get('region') and record.get('_industry_resolved') is True:continue
  url=record.get('official_url') or record.get('event_url') or source['url']
  scope=lambda target:scope_url(target,url)
  targets=[url];proposals=[]
  while targets and reads<3 and time.monotonic()<deadline:
   target=targets.pop(0)
   if not scope(target):continue
   try:
    if target not in cache:
     reads+=1;page=await asyncio.wait_for(reader.read(target),timeout=max(1,min(25,deadline-time.monotonic())))
     if not scope(page['url']):raise ValueError('Enrichment redirect left organizer scope')
     cache[target]=page;audit['navigations'].append({'url':target,'final_url':page['url'],'status':'loaded'})
    page=cache[target];body=page['html'].encode()
    # Only the directly matched event may provide structured evidence.
    matched=[n for n in event_nodes(body) if unescape(str(n.get('name',''))).strip().casefold()==unescape(record['title']).strip().casefold() and str(n.get('startDate',''))[:10]==record['start_date']]
    node=matched[0] if len(matched)==1 else None
    from .parsers import _extract_location
    known=dict(record)
    if node:
     venue,city,_,country=_extract_location(node.get('location'))
     known.update({k:v for k,v in dict(venue=venue,city=city,country=country).items() if v})
    # Search pages require matching edition proof, never snippets or unrelated catalog text.
    if target==url or node:
     proposals.append(infer(body,page['url'],record['title'],known,node,single_event=bool((source.get('selectors') or {}).get('single_event') and target==url)))
    if proposals and all(any(p['fields'].get(k) for p in proposals) or record.get(k) not in (None,'unknown','') for k in ('format','region','industry_text')):break
   except Exception as exc:
    audit['errors'].append({'url':target,'message':str(exc)[:400]})
    from .fetch import UnsafeDestination
    if isinstance(exc,UnsafeDestination) or getattr(getattr(exc,'response',None),'status_code',None)==429:return records,audit
   if not targets and searched<2 and reads<3 and time.monotonic()<deadline:
    query=f'site:{urlsplit(url).hostname} "{record["title"][:100]}" {record["start_date"][:4]} venue location attendance conference topics industry'
    searched+=1
    try:
     found=await asyncio.wait_for(asyncio.to_thread(search.search,query),timeout=max(1,min(16,deadline-time.monotonic())))
     audit['searches'].append({'query':query,'results':len(found.get('results',[]))})
     targets=[x['url'] for x in found.get('results',[]) if scope(x['url']) and x['url'] not in cache][:3-reads]
    except Exception as exc:audit['errors'].append({'stage':'search','message':str(exc)[:400]})
  import copy
  result=copy.deepcopy(record.get('extraction',{}).get('metadata_enrichment') or {'version':1,'fields':{},'evidence':{},'suggestions':{},'warnings':[]})
  for p in proposals:
   result['suggestions'].update(p['suggestions']);result['warnings']+=p['warnings']
  for key in ('format','region','country','city','event_kind','industry_text'):
   values={p['fields'][key] for p in proposals if key in p['fields']}
   if len(values)>1:result['warnings'].append('Conflicting '+key+' evidence; value not assigned.');continue
   if len(values)==1:
    value=values.pop();result['fields'][key]=value
    result['evidence'][key]=next(p['evidence'][key] for p in proposals if p['fields'].get(key)==value)
    if record.get(key) in (None,'','unknown'):record[key]=value
  record.setdefault('extraction',{})['metadata_enrichment']=result
  # Recheck industry after free evidence extraction, then reuse one bounded research job.
  if db is not None and time.monotonic()+15<deadline:
   try:
    from .industry import classify
    await asyncio.wait_for(asyncio.to_thread(classify,db,[record]),timeout=min(10,deadline-time.monotonic()))
    if record.get('_industry_resolved') is False:
     from .manus_research import research_job,add_results
     core={k:record.get(k) for k in ('start_date','end_date','country','format','event_kind')}
     reports={'event_date':{'results':[{'title':record['title'],'fields':core}]},'event_details':{'results':[{'fields':{k:record.get(k) for k in ('summary','categories','industry_text')}}]}}
     job=await asyncio.wait_for(asyncio.to_thread(research_job,source,{},reports,db),timeout=min(10,deadline-time.monotonic()))
     if job and job.get('status')=='completed':
      for citation in (job.get('result') or {}).get('evidence',[]):
       target=citation.get('page_url','')
       if target not in cache and reads<3 and scope(target) and time.monotonic()+2<deadline:
        reads+=1
        try:
         page=await asyncio.wait_for(reader.read(target),timeout=min(15,deadline-time.monotonic()))
         if scope(page['url']):cache[target]=page
        except Exception:continue
      fresh={p['url']:p['html'].encode() for p in cache.values()}
      rows=add_results('event_details',fresh,source,[],job)
      for row in rows:
       if row['title'].casefold()==record['title'].casefold() and row['fields'].get('industry_research'):
        record['industry_research']=row['fields']['industry_research']
        result['evidence']['industry_research']=row['evidence']
   except Exception as exc:audit['errors'].append({'stage':'industry_research','message':str(exc)[:300]})

  _finish(record,source['publisher'],record['source_record_id'])
 return records,audit

async def enrich_with_browser(records,source,deadline,db=None):
 from .crawler_reader import Crawl4AIReader
 from .search import SearXNG
 deadline=min(deadline,time.monotonic()+90)
 search=SearXNG(deadline=deadline)
 try:
  async with Crawl4AIReader(os.environ.get('COLLECTOR_USER_AGENT',''),deadline=deadline) as reader:
   return await asyncio.wait_for(enrich(records,source,reader,search,deadline,db=db),timeout=max(1,deadline-time.monotonic()))
 finally:search.close()
