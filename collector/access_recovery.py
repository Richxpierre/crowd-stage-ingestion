"""Recover public alternate routes, without bypassing access challenges."""
import asyncio
import re
import time
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from .crawler_reader import same_owner
from .search import SearXNG,result_url
from .mini_enrichment import MiniEnricher
from .manus_backup import configured


class AccessBlocked(RuntimeError):pass


def block_kind(error):
    text=str(error).lower()
    if any(x in text for x in ('robots','certificate','unsafe','private address','ssrf')):return None
    if re.search(r'\b429\b|too many requests|rate.limit',text):return 'rate_limited'
    if re.search(r'\b403\b|cloudflare|captcha|access denied|bot verification|anti.bot|challenge page',text):return 'anti_bot'
    return None


def challenge_page(page):
    soup=BeautifulSoup(page.get('html',''),'html.parser')
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    return bool(re.fullmatch(r'just a moment[.!… ]*|attention required(?:!|[ |\-].*cloudflare.*)?|access denied[.! ]*|verify (?:you are|that you are) (?:a )?human[.! ]*|security check[.! ]*',title.strip(),re.I)
                or soup.select_one('#challenge-form, #cf-challenge-running'))


class AccessRecovery:
    def __init__(self,db,job,config,deadline,search_factory=SearXNG):
        self.db=db;self.job=job;self.config=config;self.deadline=deadline;self.search_factory=search_factory
        self.report=config.setdefault('access_recovery',{})
        self.used=False

    def live(self):
        row=self.db.crawl_job(self.job['id'])
        return bool(row and row.get('status')=='running' and not row.get('cancel_requested'))

    @property
    def waiting(self):return self.report.get('status')=='awaiting_enrichment'

    async def read(self,reader,url):
        if self.used and self.report.get('blocked_url')==url:raise AccessBlocked(self.report.get('reason','Access recovery already attempted'))
        if self.report.get('blocked_url')==url and self.report.get('manus_job_id') and not self.used:
            return await self.recover(reader,url,'anti_bot')
        try:
            page=await reader.read(url)
            if challenge_page(page):raise AccessBlocked('Anti-bot challenge page')
            return page
        except Exception as exc:
            kind=block_kind(exc)
            if not kind:raise
            self.report['original_error']=str(exc)[:160]
            return await self.recover(reader,url,kind)

    async def recover(self,reader,url,kind):
        if self.used:raise AccessBlocked('Access block; automatic recovery already attempted for this run')
        self.used=True
        if not self.live():raise AccessBlocked('Recovery cancelled')
        if self.config.get('settings',{}).get('access_recovery_enabled') is False:
            self.report.update(status='disabled',blocked_url=url,kind=kind)
            raise AccessBlocked('Access block; automatic public-route recovery disabled')
        if kind=='rate_limited':
            self.report.update(status='cooldown',blocked_url=url,kind=kind,reason='Rate limited; wait for the next scheduled run. No immediate retries or paid task.')
            raise AccessBlocked(self.report['reason'])
        self.report.update(blocked_url=url,kind=kind,max_probes=3,status='checking')
        if time.monotonic()+20>=self.deadline:
            self.report.update(status='needs_review',reason='Insufficient run time for public-route recovery')
            raise AccessBlocked(self.report['reason'])
        try:
            choices=[];nodes=self.report.get('candidates',[])
            if self.report.get('manus_job_id'):
                rows=self.db.request('GET','conference_manus_jobs',params={'id':'eq.'+self.report['manus_job_id'],'select':'status,result,reason','limit':'1'})
                queued=rows[0] if rows else {'status':'failed','reason':'Missing mini task'}
            else:
                query='site:'+urlsplit(self.config['url']).hostname+' conferences events directory'
                self.report.update(status='searching',query=query)
                search=self.search_factory(deadline=min(self.deadline,time.monotonic()+15))
                try:found=await asyncio.to_thread(search.search,query)
                finally:search.close()
                nodes=[];seen={result_url(url)}
                for item in found.get('results',[]):
                    candidate=result_url(item.get('url'))
                    if not candidate or candidate in seen or not same_owner(candidate,self.config['url']):continue
                    if re.search(r'\.(?:pdf|png|jpe?g|zip|xml|rss|ics)(?:$|[?])',candidate,re.I):continue
                    seen.add(candidate);nodes.append({'id':len(nodes),'url':candidate,'value':urlsplit(candidate).path[:100],
                        'label':str(item.get('title') or '')[:100],'context':'Public search result on aggregator domain'})
                    if len(nodes)>=8:break
                self.report['candidates']=nodes
                if not nodes:raise AccessBlocked('Search found no alternate public pages on this aggregator')
                if not self.live():raise AccessBlocked('Recovery cancelled')
                payload_nodes=[{k:n[k] for k in ('id','value','label','context')} for n in nodes]
                ai=MiniEnricher(deadline=self.deadline)
                choices=await asyncio.to_thread(ai.choose,'access_recovery',url,'Find an accessible conference listing route',['follow'],payload_nodes)
                self.report['ai']=ai.audit
                queued={'status':'completed','result':{'selections':choices}}
                if not choices and configured():
                    if not self.live():raise AccessBlocked('Recovery cancelled')
                    from .manus_queue import enqueue
                    queued=enqueue({'stage':'access_recovery','url':url,'event':'Aggregator recovery '+self.job['target_id'],
                        'fields':['follow'],'nodes':payload_nodes},self.db)
                    self.report.update(manus_job_id=queued['id'],provider='manus')
            if queued['status'] in ('queued','running'):
                self.report.update(status='awaiting_enrichment',reason='Access blocked. Waiting for mini enrichment to select public alternate routes; shared credit buffer applies.')
                raise AccessBlocked(self.report['reason'])
            if queued['status']!='completed':raise AccessBlocked('Mini enrichment could not identify an alternate route')
            allowed={n['id']:n['url'] for n in nodes};urls=[]
            for choice in (queued.get('result') or {}).get('selections',[]):
                target=allowed.get(choice.get('node_id'))
                if choice.get('field')=='follow' and target and target not in urls:urls.append(target)
            attempts=[];self.report['attempts']=attempts
            for target in urls[:3]:
                if not self.live():raise AccessBlocked('Recovery cancelled')
                if time.monotonic()+8>=self.deadline:break
                try:
                    page=await reader.read(target)  # No recursion, proxy rotation, or CAPTCHA solving.
                    if not same_owner(page['url'],self.config['url']) or challenge_page(page):raise AccessBlocked('Alternate route is blocked or left the aggregator')
                    if not BeautifulSoup(page.get('html',''),'html.parser').select_one('a[href]'):raise AccessBlocked('Alternate route has no usable listing links')
                    attempts.append({'url':target,'status':'accessible'})
                    self.report.update(status='recovered',recovered_url=page['url'],reason='Mini-selected public route loaded. Normal discovery and validation gates still apply.')
                    return page
                except Exception as exc:attempts.append({'url':target,'status':'failed','message':str(exc)[:200]})
            raise AccessBlocked('No accessible mini-selected route. Review access requirements or use an approved feed/API.')
        except Exception as exc:
            if not self.waiting:self.report.update(status='needs_review',reason=str(exc)[:300])
            raise AccessBlocked(self.report.get('original_error','Access blocked')+'; recovery: '+self.report['reason']) from None

    def resume_if_ready(self):
        if self.waiting and self.report.get('manus_job_id'):
            self.db.request('POST','rpc/resume_conference_access_recovery',json={'p_manus_job_id':self.report['manus_job_id']})
