"""Restartable, at-least-once page discovery with transactional source registration."""
from datetime import datetime,timezone
import asyncio
import hashlib
import os
import time
from bs4 import BeautifulSoup
from .aggregators import extract,task
from .crawler_reader import Crawl4AIReader,same_owner
from .fetch import Fetcher,UnsafeDestination,RobotsPathDenied


class StaticFirstReader:
    def __init__(self,user_agent,deadline=None,skip_robots_for=None):
        self.deadline=deadline;self.seed=skip_robots_for;self.user_agent=user_agent
        self.fetcher=Fetcher(user_agent,timeout=15,respect_robots=not bool(skip_robots_for));self.fetcher.deadline=deadline
        self.browser=None;self.stats={'http_pages':0,'browser_pages':0}
    async def __aenter__(self):return self
    async def __aexit__(self,*args):
        if self.browser:await self.browser.__aexit__(*args)
    async def read(self,url):
        if self.seed and not same_owner(url,self.seed):raise UnsafeDestination('Aggregator reader left its approved host')
        try:
            _,body,headers=await asyncio.to_thread(self.fetcher.fetch,url)
            actual=headers.get('X-CrowdStage-Final-URL',url)
            if BeautifulSoup(body,'html.parser').select_one('a[href]'):
                self.stats['http_pages']+=1
                return {'url':actual,'html':body.decode('utf-8','replace'),'links':[],'markdown':''}
        except (UnsafeDestination,RobotsPathDenied):raise
        except Exception:
            # A normal robots-denied page is not retried through an override browser.
            if not self.seed:raise
        return await self.read_rendered(url)
    async def read_rendered(self,url):
        if not self.browser:
            self.browser=Crawl4AIReader(self.user_agent,deadline=self.deadline,skip_robots_for=self.seed)
            await self.browser.__aenter__()
        self.stats['browser_pages']+=1
        return await self.browser.read(url)


async def run_bulk_job(db,job,reader_factory=StaticFirstReader):
    config=job['config'];settings=config.get('settings',{});seed=config['url'];adapter=settings['adapter']
    generation=hashlib.sha256((seed+'|'+adapter+'|v1|'+str(settings.get('dashboard_reset_at',''))).encode()).hexdigest()[:24]
    config={**config,'bulk_generation':generation}
    db.update_crawl_job(job['id'],{'config':config})
    db.enqueue_frontier(job,generation,[task(seed,phase='listing')])
    stats={'pages_scanned':0,'links_found':0,'sources_added':0,'duplicates':0,'needs_review':0,'rejected':0}
    errors=[];cancelled=False;processed=0;deadline=time.monotonic()+max(1,min(float(config.get('bulk_batch_seconds',180)),360))
    page_limit=max(1,min(int(config.get('max_pages',50)),100));source_limit=max(1,min(int(config.get('max_sources',50)),50))
    def heartbeat():
        s=db.crawl_job(job['id'])
        if not s or s.get('status')!='running' or s.get('cancel_requested'):return False
        db.update_crawl_job(job['id'],{**stats,'heartbeat_at':datetime.now(timezone.utc).isoformat()});return True
    async with reader_factory(os.environ.get('COLLECTOR_USER_AGENT',''),deadline=deadline,
        skip_robots_for=seed if settings.get('skip_robots') is True else None) as reader:
        while time.monotonic()<deadline and processed<source_limit:
            if not heartbeat():cancelled=True;break
            sources=db.claim_frontier(job['id'],'source',min(20,source_limit-processed))
            if sources:
                # The server checks cancellation and commits each source with its frontier acknowledgement.
                results=db.complete_frontier_sources(job['id'],[x['id'] for x in sources])
                for result in results:
                    processed+=1;stats['links_found']+=1
                    key={'added':'sources_added','existing':'duplicates','needs_review':'needs_review','rejected':'rejected'}.get(result['status'])
                    if key:stats[key]+=1
                    else:errors.append({'url':result.get('url',seed),'message':result.get('error','Source registration failed')[:400]})
                continue
            if stats['pages_scanned']>=page_limit:break
            pages=db.claim_frontier(job['id'],'page',1)
            if not pages:break
            item=pages[0];stats['pages_scanned']+=1
            try:
                if not same_owner(item['url'],seed):raise UnsafeDestination('Frontier page leaves approved aggregator')
                page=await reader.read(item['url'])
                try:children=extract(page,adapter,seed)
                except ValueError:
                    if not hasattr(reader,'read_rendered'):raise
                    page=await reader.read_rendered(item['url'])
                    children=extract(page,adapter,seed)
                if not heartbeat():cancelled=True;break
                db.enqueue_frontier(job,generation,children)
                db.finish_frontier(job['id'],item['id'])
            except Exception as exc:
                errors.append({'url':item['url'],'message':str(exc)[:400]})
                db.fail_frontier(job['id'],item,str(exc)[:400])
        progress=db.frontier_progress(job['id'])
        progress.update(getattr(reader,'stats',{}));config['bulk_progress']=progress
    status='cancelled' if cancelled else 'partial' if errors or progress['pending_pages'] or progress['pending_sources'] else 'completed'
    message=f"Bulk discovery: {stats['pages_scanned']} pages, {stats['sources_added']} added, {stats['duplicates']} existing; {progress['pending_pages']} pages and {progress['pending_sources']} links saved for continuation"
    db.update_crawl_job(job['id'],{**stats,'config':config,'status':status,'errors':errors[-50:],'summary':message,
        'completed_at':datetime.now(timezone.utc).isoformat()})
    return {**stats,'status':status,'bulk_progress':progress}
