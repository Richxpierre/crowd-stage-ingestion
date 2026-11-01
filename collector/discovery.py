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


async def run_job(db,job,reader_factory=Crawl4AIReader):
    config=job['config'];settings=config.get('settings') or {};seed=config['url']
    page_limit=max(1,min(int(config.get('max_pages',10)),100))
    source_limit=max(1,min(int(config.get('max_sources',10)),50))
    depth_limit=max(1,min(int(config.get('max_depth',2)),4))
    deadline=time.monotonic()+360
    stats={'pages_scanned':0,'links_found':0,'sources_added':0,'duplicates':0,'needs_review':0,'rejected':0}
    errors=[];queue=[(seed,0)];visited=set();successful=[];outbound=set();fallback_seeds_added=False;cancelled=False
    db.update_crawl_job(job['id'],{'config':{**config,'settings':{**settings,'discovery_mode':'website_links'}}})
    recent=db.recent_crawled_pages(job['target_id'])
    def heartbeat(**extra):
        state=db.crawl_job(job['id'])
        if not state or state.get('status')!='running' or state.get('cancel_requested'):return False
        db.update_crawl_job(job['id'],{**stats,'errors':errors[-50:],'visited_urls':successful,'heartbeat_at':datetime.now(timezone.utc).isoformat(),**extra})
        return True
    async with reader_factory(os.environ.get('COLLECTOR_USER_AGENT',''),deadline=deadline,
        skip_robots_for=seed if settings.get('skip_robots') is True else None) as reader:
        while queue and stats['pages_scanned']<page_limit and stats['sources_added']<source_limit and time.monotonic()<deadline:
            if not heartbeat():cancelled=True;break
            url,depth=queue.pop(0)
            if url in visited or not same_owner(url,seed):continue
            if depth>0 and url in recent and not re.search(r'\.xml$|[?&]page=|/top/|/page/',url):continue
            visited.add(url);stats['pages_scanned']+=1
            try:
                page=await reader.read(url)
                successful.append(url)
                internal,candidates=classify_links(page,seed,settings)
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
                        result=db.record_discovery(job['id'],page['url'],candidate['url'],'linked',title,None,None,
                            {'method':'aggregator_website_link','link_label':candidate['label'][:300]},
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
    db.update_crawl_job(job['id'],{**stats,'errors':errors[-50:],'status':status,'summary':message,
        'visited_urls':successful,'completed_at':datetime.now(timezone.utc).isoformat()})
    return {**stats,'status':status}


def main():
    parser=argparse.ArgumentParser(description='Process one queued Crawl4AI aggregator discovery job')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--claim-only',help='Claim a job and write its snapshot for the worker')
    group.add_argument('--job-file',help='Process a previously claimed job snapshot')
    parser.add_argument('--job-id',help='Claim only this queued job for a scoped manual run')
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
    try:LOG.info('Discovery result: %s',asyncio.run(run_job(db,job)))
    except Exception as exc:
        db.update_crawl_job(job['id'],{'status':'failed','summary':str(exc)[:1000],
            'completed_at':datetime.now(timezone.utc).isoformat()})
        raise

if __name__=='__main__':main()
