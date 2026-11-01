"""Crawl4AI browser extraction with all HTTP traffic mediated by our public-site fetcher."""
from __future__ import annotations
import asyncio
import os
import tempfile
import time
from urllib.parse import urlsplit
from .fetch import Fetcher
from .parsers import clean_url
from .recovery import EXTRACT


def same_owner(url,base):
    one,two=urlsplit(url),urlsplit(base)
    if one.scheme!='https' or one.username or one.password or one.port not in (None,443):return False
    a=(one.hostname or '').encode('idna').decode().lower().rstrip('.')
    b=(two.hostname or '').encode('idna').decode().lower().rstrip('.')
    if a==b:return True
    left,right=EXTRACT(a),EXTRACT(b)
    return bool(left.suffix and left.top_domain_under_public_suffix and left.top_domain_under_public_suffix==right.top_domain_under_public_suffix)


class Crawl4AIReader:
    def __init__(self,user_agent,deadline=None,fetcher=None,skip_robots_for=None):
        self.user_agent=user_agent
        self.policy_fetcher=fetcher or Fetcher(user_agent,timeout=15,min_delay_seconds=2)
        self.direct_fetcher=(Fetcher(user_agent,timeout=15,min_delay_seconds=2,respect_robots=False)
            if skip_robots_for and not fetcher else self.policy_fetcher)
        self.skip_robots_for=skip_robots_for
        self.fetcher=self.policy_fetcher
        self.deadline=deadline or time.monotonic()+360
        self.lock=asyncio.Lock()
        self.current_url=None
        self.initial=None
        self.requests=0

    async def __aenter__(self):
        os.environ.setdefault('CRAWL4_AI_BASE_DIRECTORY',os.path.join(tempfile.gettempdir(),'crowdstage-crawler'))
        os.environ.setdefault('CRAWL4AI_NO_CLOUD_NOTICE','1')
        from crawl4ai import AsyncWebCrawler,BrowserConfig,CrawlerRunConfig,CacheMode
        browser=BrowserConfig(headless=True,user_agent=self.user_agent,ignore_https_errors=False,
            accept_downloads=False,enable_stealth=False,text_mode=True,verbose=False,
            init_scripts=["if(navigator.serviceWorker){navigator.serviceWorker.register=()=>Promise.reject(new Error('Background workers disabled'));}"])
        self.crawler=AsyncWebCrawler(config=browser,base_directory=os.environ['CRAWL4_AI_BASE_DIRECTORY'])
        self.config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS,page_timeout=25000,
            check_robots_txt=False,  # Policy belongs to the guarded fetcher, including explicit admin overrides.
            wait_until='domcontentloaded',delay_before_return_html=0.3,word_count_threshold=0,
            exclude_social_media_links=True,keep_data_attributes=True,verbose=False,
            process_iframes=False,magic=False,simulate_user=False,override_navigator=False)
        self.crawler.crawler_strategy.set_hook('on_page_context_created',self._guard_context)
        await self.crawler.start()
        return self

    async def __aexit__(self,*args):
        await self.crawler.close()

    async def _guard_context(self,page,context,**kwargs):
        await context.route_web_socket('**/*',lambda ws:ws.close())
        async def route_request(route):
            request=route.request
            if (request.method!='GET' or not same_owner(request.url,self.current_url) or self.requests>=12
                or request.resource_type in ('image','media','font') or
                (request.is_navigation_request() and clean_url(request.url)!=clean_url(self.current_url))):
                return await route.abort()
            self.requests+=1
            if clean_url(request.url)==clean_url(self.current_url):
                code,body,headers=self.initial
            else:
                try:
                    async with self.lock:
                        if time.monotonic()>=self.resource_deadline:return await route.abort()
                        self.fetcher.deadline=min(self.deadline,self.resource_deadline)
                        self.fetcher.timeout=3
                        code,body,headers=await asyncio.to_thread(self.fetcher.fetch,request.url)
                except Exception:return await route.abort()
            await route.fulfill(status=code,body=body,content_type=headers.get('Content-Type',headers.get('content-type','text/html')))
        await context.route('**/*',route_request)
        page.on('popup',lambda popup:asyncio.create_task(popup.close()))
        return page

    async def read(self,url):
        if time.monotonic()>=self.deadline:raise TimeoutError('Crawl time budget reached')
        self.fetcher=(self.direct_fetcher if self.skip_robots_for and same_owner(url,self.skip_robots_for) else self.policy_fetcher)
        self.fetcher.deadline=min(self.deadline,time.monotonic()+50)
        self.fetcher.timeout=15
        self.initial=await asyncio.to_thread(self.fetcher.fetch,url)
        _,body,headers=self.initial
        self.current_url=headers.get('X-CrowdStage-Final-URL',url)
        self.requests=0
        self.resource_deadline=min(self.deadline,time.monotonic()+10)
        if urlsplit(self.current_url).path.endswith('.xml'):
            return {'url':self.current_url,'html':body.decode('utf-8-sig','replace'),'links':[],'markdown':''}
        result=await asyncio.wait_for(self.crawler.arun(url=self.current_url,config=self.config),timeout=55)
        if not result.success:raise RuntimeError('Crawl4AI: '+str(result.error_message)[:400])
        links=[]
        for group in ('internal','external'):
            links.extend(result.links.get(group,[]))
        markdown=getattr(result.markdown,'raw_markdown',str(result.markdown or ''))
        return {'url':self.current_url,'html':result.html,'links':links,'markdown':markdown}
