"""Read-only, resumable evidence collection for an explicitly selected backlog.

No database writes, paid AI, security overrides, or inferred approval decisions.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor,as_completed
from datetime import date,datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from collector.fetch import Fetcher
from collector.automatic import parse_page
from collector.recovery import EXTRACT
from collector.source_validation import bad_page,observed_links
from collector.search import result_url

LOCK=threading.Lock();HOSTS={};FETCHERS={};CACHE={}
PARSERS=None
UA='CrowdStage-Audit/1.0 (+https://github.com/Richxpierre/crowd-stage-ingestion)'

def analyze(body,final,row):
    result={}
    soup=BeautifulSoup(body,'lxml')
    result['page_title']=soup.title.get_text(' ',strip=True)[:240] if soup.title else ''
    result['headings']=[n.get_text(' ',strip=True)[:200] for n in soup.select('h1,h2')[:10]]
    for n in soup.select('script,style,nav,footer,header'):n.decompose()
    text=soup.get_text(' ',strip=True)
    result['text_excerpt']=text[:2600]
    result['date_contexts']=list(dict.fromkeys(text[max(0,m.start()-90):m.end()+130] for m in re.finditer(r'\b20(?:2[4-9]|30)\b',text)))[:12]
    page={'url':final,'html':body.decode('utf-8','replace')}
    result['bad_page']=bad_page(page)
    result['observed_event_links']=[{'url':u,'label':label} for _,u,label in observed_links(page,final)][:8]
    source={'url':row['url'],'name':row.get('title') or urlsplit(final).hostname,'publisher':urlsplit(final).hostname,'kind':'auto','is_official_source':True,'selectors':{'website_only':True}}
    try:
        records=parse_page(body,source,final)
        result['events']=[{k:r.get(k) for k in ('title','start_date','end_date','event_url','source_url','venue','city','country','format','status')} for r in records][:30]
        result['future_count']=sum(r.get('end_date','')>=date.today().isoformat() for r in records)
    except Exception as exc:
        import traceback
        result['parser_error']=str(exc)[:400];result['parser_traceback']=traceback.format_exc()[-2500:]
    return result

def inspect(row,out):
    original=row['url'];url=result_url(original)
    result={'id':row['id'],'original_url':original,'checked_at':datetime.now(timezone.utc).isoformat()}
    if not url:return {**result,'status':'unsafe_url'}
    host=urlsplit(url).hostname;domain=EXTRACT(host).top_domain_under_public_suffix or host
    with LOCK:mutex=HOSTS.setdefault(domain,threading.Lock())
    with mutex:
        key=url.rstrip('/')
        if key in CACHE:return {**CACHE[key],'id':row['id'],'original_url':original,'cached_from':CACHE[key]['id']}
        with LOCK:fetcher=FETCHERS.setdefault(host,Fetcher(UA,min_delay_seconds=1.5,timeout=10))
        fetcher.deadline=time.monotonic()+28
        try:
            code,body,headers=fetcher.fetch(url)
            final=headers.get('X-CrowdStage-Final-URL',url)
            result.update(status='loaded',http_status=code,final_url=final,bytes=len(body))
            result.update(PARSERS.submit(analyze,body,final,row).result())
            # Keep exact evidence for manual checks and deterministic parser regression tests.
            name=hashlib.sha256(key.encode()).hexdigest()[:24]+'.html'
            (out/'pages'/name).write_bytes(body);result['snapshot']=name
        except Exception as exc:result.update(status='fetch_failed',error_type=type(exc).__name__,error=str(exc)[:600])
        CACHE[key]=result.copy()
    return result

def main():
    global PARSERS
    parser=argparse.ArgumentParser();parser.add_argument('--inventory',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--workers',type=int,default=24);parser.add_argument('--parsers',type=int,default=4);parser.add_argument('--limit',type=int)
    args=parser.parse_args();args.out.mkdir(exist_ok=True,parents=True);(args.out/'pages').mkdir(exist_ok=True)
    path=args.out/'live-evidence.jsonl';done=set()
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:done.add(json.loads(line)['id'])
            except ValueError:pass
    rows=[r for r in json.loads(args.inventory.read_text(encoding='utf-8')) if r['id'] not in done]
    # Spread large shared hosts across the run while preserving every unique row.
    rows.sort(key=lambda r:hashlib.sha256(r['id'].encode()).hexdigest())
    if args.limit:rows=rows[:args.limit]
    print(json.dumps({'pending':len(rows),'already_recorded':len(done)}),flush=True)
    with ProcessPoolExecutor(max_workers=max(1,min(args.parsers,6))) as processes,path.open('a',encoding='utf-8') as output,ThreadPoolExecutor(max_workers=max(1,min(args.workers,32))) as pool:
        PARSERS=processes
        futures=[pool.submit(inspect,r,args.out) for r in rows]
        for i,future in enumerate(as_completed(futures),1):
            output.write(json.dumps(future.result(),ensure_ascii=True)+'\n');output.flush()
            if i%100==0:print(json.dumps({'completed':i,'total':len(rows)}),flush=True)

if __name__=='__main__':main()
