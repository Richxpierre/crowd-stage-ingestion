"""Fair, leased mini-enrichment. Unsupported evidence stays in review."""
import asyncio
import hashlib
import json
import logging
import os
import time
from bs4 import BeautifulSoup
from .client import Client,DatabaseError
from .claude_client import ClaudeClient,configured
from .enrichment_values import normalize_evidence,normalized,scope_issue
from .pipeline_control import configuration
from .fetch import Fetcher,UnsafeDestination
from .parsers import clean_url

LOG=logging.getLogger('crowdstage-claude-worker')

def read_page(url):
    fetcher=Fetcher(os.environ.get('COLLECTOR_USER_AGENT') or 'CrowdStage-Enrichment/1.0 (+https://github.com/Richxpierre/crowd-stage-ingestion)',timeout=10)
    fetcher.deadline=time.monotonic()+35
    try:
        _,body,headers=fetcher.fetch(url)
        final=headers.get('X-CrowdStage-Final-URL',url)
        soup=BeautifulSoup(body,'html.parser')
        for node in soup.select('script,style,nav,header,footer,aside,noscript,svg'):node.decompose()
        text=soup.get_text(' ',strip=True)
        if len(text)>=80:return final,text[:16000],hashlib.sha256(body).hexdigest(),body
    except UnsafeDestination:
        raise
    except Exception:
        pass
    # Browser fallback remains guarded and bounded. A blocked page is a retry,
    # never proof that an event does not exist.
    from .crawler_reader import Crawl4AIReader
    async def rendered():
        async with Crawl4AIReader(os.environ.get('COLLECTOR_USER_AGENT','CrowdStage-Enrichment/1.0'),deadline=time.monotonic()+35) as reader:
            page=await asyncio.wait_for(reader.read(url),timeout=35)
            body=(page.get('html') or '').encode()
            soup=BeautifulSoup(body,'html.parser')
            for node in soup.select('script,style,nav,footer,noscript'):node.decompose()
            return page['url'],soup.get_text(' ',strip=True)[:16000],hashlib.sha256(body).hexdigest(),body
    return asyncio.run(rendered())

def retryable_provider(exc):
    return any(x in str(exc) for x in ('provider_','verification_timeout','missing_key'))

def process_research(db,claude,limit,deadline):
    count=0
    from .mini_enrichment import INSTRUCTIONS
    from .manus_queue import wake_consumers
    for _ in range(limit):
        if time.monotonic()>deadline-45 or not configuration(db,'claude')['can_run']:break
        job=db.request('POST','rpc/claim_claude_research',json={})
        if not job:break
        payload=job.get('payload') or {};result={};error=None
        try:
            if payload.get('stage')=='research':
                url,text,digest,_=read_page(payload['url'])
                evidence,_=claude.research_fields(payload.get('event','Conference'),url,payload.get('fields',[]),text)
                accepted=[]
                for row in evidence:
                    if not isinstance(row,dict):continue
                    value,quote=row.get('value'),row.get('quote')
                    if not isinstance(value,str) or not isinstance(quote,str) or not 3<=len(quote)<=600:continue
                    if row.get('field') not in payload.get('fields',[]):continue
                    if normalized(row.get('event_title'))!=normalized(payload.get('event')) or clean_url(row.get('page_url'))!=clean_url(url):continue
                    if normalized(quote) not in normalized(text) or normalized(value) not in normalized(quote):continue
                    accepted.append({k:row[k] for k in ('field','value','quote','page_url','event_title')})
                result={'evidence':accepted[:7]}
            else:
                picked,_=claude.select_evidence(payload,INSTRUCTIONS)
                nodes={n['id'] for n in payload.get('nodes',[]) if isinstance(n,dict) and 'id' in n}
                result={'selections':[r for r in picked.get('selections',[]) if isinstance(r,dict) and r.get('field') in payload.get('fields',[]) and type(r.get('node_id')) is int and r['node_id'] in nodes][:8]}
        except DatabaseError:raise
        except Exception as exc:
            error=str(exc)[:500];LOG.warning('Research job did not finish: %s',error)
        db.request('POST','rpc/finish_claude_research',json={'p_id':job['id'],'p_claim':job['claim_token'],'p_result':result,'p_error':error})
        count+=1
        if not error:
            if result.get('evidence') or result.get('selections'):wake_consumers(db,job)
        if error and retryable_provider(error):raise RuntimeError(error)
    return count

def process_events(db,claude,limit,deadline):
    count=0
    for _ in range(limit):
        if time.monotonic()>deadline-45 or not configuration(db,'claude')['can_run']:break
        job=db.request('POST','rpc/claim_conference_event_enrichment',json={})
        if not job:break
        event=job['event'];patch={};report={};error=None;outcome='completed';fatal=False
        try:
            issue=scope_issue(event['name'])
            if issue:
                outcome='review';report={'reason':issue}
            else:
                missing=[f for f in ('format','region') if not event.get(f)]
                if not event.get('vertical_slug'):missing.append('industry_research')
                if missing:missing += [f for f in ('country','city','venue') if not event.get(f)]
                # Optional unknown price/attendance do not block publication or
                # trigger endless paid requests after required facts are complete.
                if missing:
                    url,text,digest,body=read_page(event['external_url'])
                    from .metadata import infer
                    deterministic=infer(body,url,event['name'],event,single_event=False)
                    for field in ('country','region','format'):
                        if field in missing and deterministic['fields'].get(field):
                            value=deterministic['fields'][field]
                            patch[field]='in-person' if value=='in_person' else 'virtual' if value=='online' else value
                    missing=[f for f in missing if f not in patch]
                    evidence=[]
                    if missing:evidence,_=claude.research_fields(event['name'],url,missing,text)
                    fields,proof,warnings=normalize_evidence(evidence,text,event['name'],url,{**event,**patch})
                    patch.update(fields)
                    report={'evidence':proof,'warnings':warnings,'page_url':url,'snapshot_sha256':digest,'method':'quoted_organizer_page'}
                    for field in patch:
                        if field not in report['evidence'] and field in deterministic.get('evidence',{}):report['evidence'][field]=deterministic['evidence'][field]
        except DatabaseError:raise
        except Exception as exc:
            outcome='retry';error=str(exc)[:500];fatal=retryable_provider(exc)
            report={'reason':'Page or enrichment temporarily unavailable; event preserved in Pending Review'}
        result=db.request('POST','rpc/finish_conference_event_enrichment',json={'p_id':job['id'],'p_claim':job['claim_token'],'p_patch':patch,'p_report':report,'p_outcome':outcome,'p_error':error})
        LOG.info('Event %s: %s',event['id'],json.dumps(result))
        if outcome=='completed':count+=1
        if fatal:raise RuntimeError(error)
    return count

def main():
    logging.basicConfig(level=logging.INFO)
    db=Client();controls=configuration(db,'claude')
    if not controls['can_run']:
        LOG.info('Claude is paused by admin control');return
    if not configured():raise RuntimeError('Claude credential is not configured')
    claude=ClaudeClient();deadline=time.monotonic()+420
    try:
        research_limit=min(4,controls['batch_size'])
        researched=process_research(db,claude,research_limit,deadline)
        enriched=process_events(db,claude,min(8,max(0,controls['batch_size']-researched)),deadline)
        LOG.info('Saved %d research jobs and processed %d event jobs',researched,enriched)
    except Exception as exc:
        db.request('POST','rpc/record_conference_pipeline_failure',json={'p_pipeline':'claude','p_error':str(exc)[:750]})
        raise
    finally:claude.close()

if __name__=='__main__':main()

