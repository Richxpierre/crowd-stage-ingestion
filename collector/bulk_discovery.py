"""Restartable, at-least-once page discovery with transactional source registration."""
from datetime import datetime,timezone
import asyncio
import hashlib
import os
import time
from bs4 import BeautifulSoup
from .aggregators import extract,task,NoOrganizerLink
from .crawler_reader import Crawl4AIReader,same_owner
from .fetch import Fetcher,UnsafeDestination,RobotsPathDenied
from .search import SearXNG,result_url,conference_page

async def recover_organizer(db,job,page,title,deadline,remaining,report,
                            search_factory=SearXNG,reader_factory=Crawl4AIReader):
    """One search / at most three crawled results. Suggestions always require review."""
    from .discovery import excluded
    if report['requests']>=3 or remaining<=0 or time.monotonic()+20>=deadline:return []
    query='"'+title[:160]+'" official conference website'
    search=search_factory(deadline=min(deadline,time.monotonic()+15));report['requests']+=1
    try:found=await asyncio.to_thread(search.search,query)
    except Exception:
        report['warnings'].append('Organizer search unavailable for '+page['url']);return []
    finally:search.close()
    candidates=[]
    for item in found['results']:
        url=result_url(item['url'])
        if url and not excluded(url) and not same_owner(url,job['config']['url']) and url not in candidates:candidates.append(url)
        if len(candidates)>=min(3,remaining):break
    report['results']+=len(candidates)
    results=[]
    if not candidates:return results
    try:
        async with reader_factory(os.environ.get('COLLECTOR_USER_AGENT',''),deadline=min(deadline,time.monotonic()+45)) as reader:
            for url in candidates:
                if time.monotonic()+8>=deadline:break
                try:
                    candidate=await reader.read(url);report['pages_checked']+=1
                    if not conference_page(candidate):continue
                    state=db.crawl_job(job['id'])
                    if not state or state.get('status')!='running' or state.get('cancel_requested'):break
                    results.append(db.record_discovery(job['id'],page['url'],candidate['url'],'needs_review',title,
                        None,None,{'method':'searxng_organizer_recovery','requires_review':True,'query':query,
                            'verified_page':candidate['url']},'Search-located organizer candidate; ownership review required'))
                except Exception:continue
    except Exception:report['warnings'].append('Organizer candidate crawling unavailable for '+page['url'])
    return results


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
    from .mini_enrichment import MiniEnricher,discover_links as ai_discover
    ai=MiniEnricher(config.get('ai_enrichment'))
    db.update_crawl_job(job['id'],{'config':config})
    db.enqueue_frontier(job,generation,[task(seed,phase='listing')])
    stats={'pages_scanned':0,'links_found':0,'sources_added':0,'duplicates':0,'needs_review':0,'rejected':0}
    errors=[];cancelled=False;processed=0;deadline=time.monotonic()+max(1,min(float(config.get('bulk_batch_seconds',180)),360))
    search_report={'enabled':settings.get('search_enabled') is True,'requests':0,'results':0,'pages_checked':0,'warnings':[]}
    config['search_report']=search_report;without_website=[]
    ai.deadline=deadline
    from .access_recovery import AccessRecovery
    recovery=AccessRecovery(db,job,config,deadline)
    page_limit=max(1,min(int(config.get('max_pages',50)),100));source_limit=max(1,min(int(config.get('max_sources',50)),50))
    def heartbeat(stage='Discovering organizer links'):
        s=db.crawl_job(job['id'])
        if not s or s.get('status')!='running' or s.get('cancel_requested'):return False
        config['progress_stage']=stage
        db.update_crawl_job(job['id'],{**stats,'config':config,'errors':errors[-50:],'heartbeat_at':datetime.now(timezone.utc).isoformat()});return True
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
            if not heartbeat('Fetching next aggregator page'):cancelled=True;break
            pages=db.claim_frontier(job['id'],'page',1)
            if not pages:break
            item=pages[0];stats['pages_scanned']+=1
            try:
                if not same_owner(item['url'],seed):raise UnsafeDestination('Frontier page leaves approved aggregator')
                page=await recovery.read(reader,item['url'])
                try:children=extract(page,adapter,seed)
                except ValueError:
                    if hasattr(reader,'read_rendered'):
                        page=await reader.read_rendered(item['url'])
                    try:children=extract(page,adapter,seed)
                    except ValueError:
                        children=await asyncio.to_thread(ai_discover,page,seed,ai)
                        if not children:raise
                if not heartbeat():cancelled=True;break
                # AI-selected links need ownership review; the deterministic bulk
                # admission RPC intentionally only accepts known adapter evidence.
                safe_children=[]
                for child in children:
                    evidence=child.get('payload',{}).get('evidence',{})
                    if child['task_kind']=='source' and evidence.get('method')=='ai_observed_link':
                        if not heartbeat():cancelled=True;break
                        result=db.record_discovery(job['id'],page['url'],child['url'],'needs_review',
                            child['payload'].get('title'),None,None,evidence,'AI-selected observed website link; organizer review required')
                        stats['links_found']+=1
                        if result['status']=='needs_review':stats['needs_review']+=1
                        elif result['status']=='existing':stats['duplicates']+=1
                    else:safe_children.append(child)
                if cancelled:break
                db.enqueue_frontier(job,generation,safe_children)
                db.finish_frontier(job['id'],item['id'])
            except NoOrganizerLink as exc:
                without_website.append(item['url'])
                if search_report['enabled'] and heartbeat('Searching for the missing organizer website'):
                    recovered=await recover_organizer(db,job,page,exc.title,deadline,source_limit-processed,search_report)
                    for result in recovered:
                        processed+=1;stats['links_found']+=1
                        if result['status']=='existing':stats['duplicates']+=1
                        elif result['status']=='needs_review':stats['needs_review']+=1
                # Re-fetching the same complete listing cannot create a missing button.
                db.finish_frontier(job['id'],item['id'])
                config['listings_without_website']=without_website[-20:]
            except Exception as exc:
                errors.append({'url':item['url'],'message':str(exc)[:400]})
                db.fail_frontier(job['id'],item,str(exc)[:400])
                if recovery.waiting:break
        progress=db.frontier_progress(job['id'])
        config['progress_stage']='Batch finished; continuation saved'
        progress.update(getattr(reader,'stats',{}));config['bulk_progress']=progress
        config['ai_enrichment']=ai.audit
        if progress.get('failed_tasks'):
            errors.extend({'url':item['url'],'message':item.get('last_error') or 'Saved task exhausted retries'}
                          for item in db.frontier_errors(job['target_id'],generation))
    status='cancelled' if cancelled else 'partial' if errors or progress['pending_pages'] or progress['pending_sources'] else 'completed'
    message=f"Bulk discovery: {stats['pages_scanned']} pages, {stats['sources_added']} added, {stats['duplicates']} existing; {progress['pending_pages']} pages and {progress['pending_sources']} links saved for continuation"
    if progress.get('failed_tasks'):message+=f"; {progress['failed_tasks']} failed tasks require review"
    if without_website:message+=f"; {len(without_website)} listings lacked a website button (search suggestions require review)"
    db.update_crawl_job(job['id'],{**stats,'config':config,'status':status,'errors':errors[-50:],'summary':message,
        'completed_at':datetime.now(timezone.utc).isoformat()})
    recovery.resume_if_ready()
    return {**stats,'status':status,'bulk_progress':progress,'awaiting_enrichment':recovery.waiting}
