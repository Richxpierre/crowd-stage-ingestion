"""Queued aggregator discovery using Crawl4AI; save organizer website-button URLs as sources."""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime,timezone
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin,urlsplit,parse_qs
from xml.etree import ElementTree
from bs4 import BeautifulSoup
from .client import Client
from .crawler_reader import Crawl4AIReader,same_owner
from .parsers import clean_url
from .search import SearXNG,search_queries,conference_page

LOG=logging.getLogger('crowdstage-discovery')
EVENT_HINT=re.compile(r'conference|congress|symposium|convention|summit|expo|trade.?show|trade.?fair|workshop|con\b',re.I)
OFFICIAL_HINT=re.compile(r'official|\bwebsite\b|\bhomepage\b|event\s*site|visit\s*(?:the\s+)?(?:site|conference)',re.I)
DENY_HOSTS={'facebook.com','instagram.com','linkedin.com','twitter.com','x.com','youtube.com','youtu.be','tiktok.com','maps.google.com','accounts.google.com','play.google.com','apps.apple.com','eventbrite.com','meetup.com','inomics.com','10times.com','conferencemonkey.org','sessionize.com'}
SKIP=re.compile(r'/(?:login|signup|register|user|privacy|terms|legal|speaker|speakers|sessions?|schedule|blog|news|publish|contact|about|help)(?:/|$)',re.I)


def excluded(url):
    host=(urlsplit(url).hostname or '').lower().removeprefix('www.')
    return any(host==h or host.endswith('.'+h) for h in DENY_HOSTS)


def normalized_link(value,base):
    target=urljoin(base,value or '')
    if target.startswith('http://'):target='https://'+target[7:]
    return clean_url(target)


def page_links(page):
    url=page['url'];links=[]
    if urlsplit(url).path.endswith('.xml'):
        if re.search(r'<!\s*(DOCTYPE|ENTITY)',page['html'],re.I):raise ValueError('Unsupported XML entities')
        root=ElementTree.fromstring(page['html'])
        return [{'href':el.text,'text':'sitemap event'} for el in root.iter() if el.tag.rsplit('}',1)[-1]=='loc' and el.text][:10000]
    for item in page.get('links',[]):
        if item.get('href'):links.append({'href':urljoin(url,item['href']),'text':item.get('text') or item.get('title') or ''})
    soup=BeautifulSoup(page['html'],'html.parser')
    for node in soup.select('a[href], [data-url], [data-href]'):
        target=node.get('href') or node.get('data-url') or node.get('data-href')
        label=' '.join([node.get_text(' ',strip=True),str(node.get('title','')),str(node.get('aria-label','')),str(node.get('class',''))])
        parent=node.parent
        for _ in range(2):
            if parent is None:break
            context=parent.get_text(' ',strip=True)
            if len(context)<400 and (re.search(r'website|homepage',context,re.I) or EVENT_HINT.search(context)):label+=' '+context
            parent=parent.parent
        links.append({'href':urljoin(url,target),'text':label})
    for label,urlmatch in re.findall(r'(?<!!)\[([^\]]+)\]\((https://[^\s\)]+)\)',page.get('markdown','')):
        links.append({'href':urlmatch,'text':label})
    for script in soup.find_all('script',type='application/ld+json'):
        try:payload=json.loads(script.string or script.get_text())
        except ValueError:continue
        def visit(value):
            if isinstance(value,list):
                for item in value:yield from visit(item)
            elif isinstance(value,dict):
                types=value.get('@type',[]);types=[types] if isinstance(types,str) else types
                if any(str(t).endswith('Event') for t in types):
                    if isinstance(value.get('url'),str):yield {'href':urljoin(url,value['url']),'text':'official event website'}
                    org=value.get('organizer')
                    if isinstance(org,dict) and isinstance(org.get('url'),str):yield {'href':urljoin(url,org['url']),'text':'organizer website'}
                for key in ('@graph','itemListElement','item'):
                    if key in value:yield from visit(value[key])
        links.extend(visit(payload))
    return links[:3000]


def classify_links(page,seed,settings):
    internal=[];official=[]
    pattern=re.compile(settings.get('listing_pattern') or r'conference|expo|trade|event|/e/|[?&]page=|/page/',re.I)
    for item in page_links(page):
        target=normalized_link(item['href'],page['url'])
        if not target:continue
        if re.search(r'\.(?:png|jpe?g|gif|webp|svg|ico|avif|pdf|css|js|zip)(?:$|[?])',target,re.I):continue
        if target==clean_url(page['url']):continue
        if same_owner(target,seed):
            # Some directories expose an explicit outbound redirect target in the link.
            args=parse_qs(urlsplit(target).query)
            redirected=next((clean_url(args[key][0]) for key in ('url','target','redirect','redirect_url') if args.get(key)),None)
            if redirected and not excluded(redirected) and OFFICIAL_HINT.search(item['text']):
                official.append({'url':redirected,'label':item['text']})
            elif not SKIP.search(urlsplit(target).path) and pattern.search(target+' '+item['text']):
                internal.append(target)
        elif not excluded(target) and not SKIP.search(urlsplit(target).path) and OFFICIAL_HINT.search(item['text']):
            official.append({'url':target,'label':item['text']})
    internal=list(dict.fromkeys(internal))
    # Reach actual listings before navigation/category pages on large catalogs.
    internal.sort(key=lambda u:0 if re.search(r'/conference/|/e/|20\d{2}',u) else 1)
    return internal,list({item['url']:item for item in official}.values())


async def run_job(db,job,reader_factory=Crawl4AIReader,search_factory=SearXNG):
    if job['config'].get('settings',{}).get('adapter') in ('sessionize','conference_monkey'):
        from .bulk_discovery import run_bulk_job
        return await run_bulk_job(db,job)
    config=job['config'];settings=config.get('settings') or {};seed=config['url']
    page_limit=max(1,min(int(config.get('max_pages',10)),100))
    source_limit=max(1,min(int(config.get('max_sources',10)),50))
    depth_limit=max(1,min(int(config.get('max_depth',2)),4))
    deadline=time.monotonic()+360
    stats={'pages_scanned':0,'links_found':0,'sources_added':0,'duplicates':0,'needs_review':0,'rejected':0}
    errors=[];queue=[(seed,0)];visited=set();successful=[];outbound=set();fallback_seeds_added=False;cancelled=False
    config={**config,'settings':{**settings,'discovery_mode':'website_links'}}
    from .mini_enrichment import MiniEnricher,discover_links as ai_discover
    ai=MiniEnricher(config.get('ai_enrichment'),deadline=deadline)
    config['ai_enrichment']=ai.audit
    search_candidates={};search_report={'enabled':settings.get('search_enabled') is True,'requests':0,'results':0,'pages_checked':0,'warnings':[]}
    config['search_report']=search_report
    db.update_crawl_job(job['id'],{'config':config})
    recent=db.recent_crawled_pages(job['target_id'])
    def heartbeat(**extra):
        state=db.crawl_job(job['id'])
        if not state or state.get('status')!='running' or state.get('cancel_requested'):return False
        db.update_crawl_job(job['id'],{**stats,'config':config,'errors':errors[-50:],'visited_urls':successful,'heartbeat_at':datetime.now(timezone.utc).isoformat(),**extra})
        return True
    if search_report['enabled'] and heartbeat():
        search=None
        try:
            search=search_factory(deadline=min(deadline,time.monotonic()+45))
            for query in search_queries(seed,config.get('name'),settings):
                if not heartbeat():cancelled=True;break
                search_report['requests']+=1
                response=await asyncio.to_thread(search.search,query)
                if response.get('warnings'):search_report['warnings'].append(response['warnings'])
                for item in response['results']:
                    target=item['url']
                    if target in search_candidates or excluded(target) and not same_owner(target,seed):continue
                    if re.search(r'\.(?:xml|rss|ics|pdf|png|jpe?g|svg|zip)(?:$|[?])',target,re.I):continue
                    search_candidates[target]=item
                    if len(search_candidates)>=20:break
                if len(search_candidates)>=20:break
        except Exception as exc:
            errors.append({'url':seed,'message':'Search assistance: '+str(exc)[:450]})
        finally:
            if search:search.close()
            search_report['results']=len(search_candidates)
            # Retain successful earlier searches even if a later query fails.
            queue.extend((url,0) for url in search_candidates if url!=seed)
            if search_report['warnings']:
                errors.append({'url':seed,'message':'Some search engines were unavailable: '+str(search_report['warnings'])[:400]})
            heartbeat()
    async with reader_factory(os.environ.get('COLLECTOR_USER_AGENT',''),deadline=deadline,
        skip_robots_for=seed if settings.get('skip_robots') is True else None) as reader:
        while queue and stats['pages_scanned']<page_limit and stats['sources_added']<source_limit and time.monotonic()<deadline:
            if cancelled or not heartbeat():cancelled=True;break
            url,depth=queue.pop(0)
            search_hit=search_candidates.get(url)
            external=bool(search_hit and not same_owner(url,seed))
            if url in visited or not(same_owner(url,seed) or external):continue
            if depth>0 and url in recent and not re.search(r'\.xml$|[?&]page=|/top/|/page/',url):continue
            visited.add(url);stats['pages_scanned']+=1
            try:
                page=await reader.read(url)
                successful.append(url)
                if search_hit:search_report['pages_checked']+=1
                if external:
                    final=normalized_link(page['url'],url)
                    if not final or excluded(final):raise ValueError('Search candidate redirected to an excluded destination')
                    title=conference_page(page)
                    if title and final not in outbound:
                        if not heartbeat():cancelled=True;break
                        outbound.add(final);stats['links_found']+=1
                        result=db.record_discovery(job['id'],seed,final,'needs_review',title,None,None,
                            {'method':'searxng_crawl4ai','query':search_hit['query'],'engines':search_hit['engines'],
                             'search_result_url':url,'verified_page':final,'relevance':'event page signal; official ownership unconfirmed'},
                            'Search-discovered website inspected with Crawl4AI; review before adding to Sources')
                        stats[{'needs_review':'needs_review','existing':'duplicates','rejected':'rejected','added':'sources_added'}[result['status']]]+=1
                    else:stats['rejected']+=1
                    continue
                internal,candidates=classify_links(page,seed,settings)
                if not internal and not candidates:
                    proposed=await asyncio.to_thread(ai_discover,page,seed,ai)
                    internal=[x['url'] for x in proposed if x['task_kind']=='page']
                    candidates=[{'url':x['url'],'label':x['payload'].get('title',''),'ai':True} for x in proposed if x['task_kind']=='source']
                if depth<depth_limit:
                    queue.extend((target,depth+1) for target in internal if target not in visited and (target not in recent or re.search(r'\.xml$|[?&]page=|/top/|/page/',target)) and target not in {u for u,_ in queue})
                    queue=queue[:500]
                for candidate in candidates:
                    if candidate['url'] in outbound:continue
                    if len(outbound)>=source_limit*3 or stats['sources_added']>=source_limit or time.monotonic()>=deadline:break
                    if not heartbeat():cancelled=True;break
                    outbound.add(candidate['url']);stats['links_found']+=1
                    try:
                        title_node=BeautifulSoup(page['html'],'html.parser').find('h1')
                        title=title_node.get_text(' ',strip=True)[:240] if title_node else None
                        result=db.record_discovery(job['id'],page['url'],candidate['url'],'needs_review' if candidate.get('ai') else 'linked',title,None,None,
                            {'method':'ai_observed_link' if candidate.get('ai') else 'aggregator_website_link','requires_review':bool(candidate.get('ai')),'link_label':candidate['label'][:300]},
                            'Organizer website button URL saved; event parsing is queued separately')
                        key={'added':'sources_added','existing':'duplicates','needs_review':'needs_review','rejected':'rejected'}[result['status']]
                        stats[key]+=1
                    except Exception as exc:
                        errors.append({'url':candidate['url'],'message':str(exc)[:500]})
                        if not heartbeat():cancelled=True;break
            except Exception as exc:
                errors.append({'url':url,'message':str(exc)[:500]})
                if not fallback_seeds_added:
                    queue.extend((target,0) for target in settings.get('fallback_seeds',[]) if same_owner(target,seed) and target not in visited)
                    fallback_seeds_added=True
            if cancelled:break
    limited=bool(queue) and (stats['pages_scanned']>=page_limit or stats['sources_added']>=source_limit or time.monotonic()>=deadline)
    status='cancelled' if cancelled else 'partial' if errors or limited else 'completed'
    if errors and stats['pages_scanned'] and not any(stats[k] for k in ('sources_added','duplicates','needs_review')):status='failed'
    message=('Cancelled by administrator' if cancelled else 'Crawl limits reached; run again or increase limits' if limited else
             'Completed with some blocked or failed pages' if errors else 'Discovery completed')
    if search_report['enabled']:message+=f"; search: {search_report['requests']} queries, {search_report['results']} links, {search_report['pages_checked']} checked"
    db.update_crawl_job(job['id'],{**stats,'config':config,'errors':errors[-50:],'status':status,'summary':message,
        'visited_urls':successful,'completed_at':datetime.now(timezone.utc).isoformat()})
    return {**stats,'status':status}


def main():
    parser=argparse.ArgumentParser(description='Process one queued Crawl4AI aggregator discovery job')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--claim-only',help='Claim a job and write its snapshot for the worker')
    group.add_argument('--job-file',help='Process a previously claimed job snapshot')
    parser.add_argument('--job-id',help='Claim only this queued job for a scoped manual run')
    parser.add_argument('--drain-bulk',action='store_true',help='Continue bulk batches for up to ten minutes, leaving unfinished work queued')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.job_file and args.job_id:parser.error('--job-id cannot be combined with --job-file')
    db=Client();job=json.loads(Path(args.job_file).read_text(encoding='utf-8')) if args.job_file else db.claim_crawl_job(args.job_id)
    if args.claim_only:
        if job and job.get('id'):Path(args.claim_only).write_text(json.dumps(job,ensure_ascii=True),encoding='utf-8')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as output:output.write('has_job='+('true' if job and job.get('id') else 'false')+'\n')
        return
    if not job or not job.get('id'):
        LOG.info('No queued or scheduled crawl jobs');return
    try:
        worker_deadline=time.monotonic()+600
        while True:
            bulk=job['config'].get('settings',{}).get('adapter') in ('sessionize','conference_monkey')
            if args.drain_bulk and bulk:job['config']['bulk_batch_seconds']=max(1,min(180,worker_deadline-time.monotonic()))
            result=asyncio.run(run_job(db,job));LOG.info('Discovery result: %s',result)
            if not(args.drain_bulk and bulk) or result.get('status')=='cancelled' or job['config'].get('bulk_probe') is True:break
            next_id=db.continue_bulk_crawl(job['id'])
            if not next_id:break
            if time.monotonic()>=worker_deadline:
                from .handoff import dispatch_bulk_continuation
                try:LOG.info('Bulk handoff requested: %s',dispatch_bulk_continuation(db,job,next_id))
                except Exception as exc:LOG.error('%s',exc)
                break
            job=db.claim_crawl_job()  # Oldest queued/due target wins; continuation remains durable.
            if not job:break
    except Exception as exc:
        db.update_crawl_job(job['id'],{'status':'failed','summary':str(exc)[:1000],
            'completed_at':datetime.now(timezone.utc).isoformat()})
        raise

if __name__=='__main__':main()
