"""Three non-recursive validation strategies sharing one network/search budget."""
from __future__ import annotations
import asyncio
from datetime import date,datetime,timezone
import re
import time
from urllib.parse import urlsplit
from .parsers import clean_url
from .crawler_reader import same_owner
from .recovery import EXTRACT

MAX_STRATEGIES=3
MAX_READS=8
MAX_SEARCHES=3
SHARED_HOSTS={'sciencesconf.org','sites.google.com','wordpress.com','wixsite.com','github.io','webflow.io','notion.site'}
STOP={'https','www','conference','conferences','event','events','home','website','visit','registration','annual','international','about','index','html','news','detail','details'}

def scope_url(url,base):
    if not clean_url(url) or not same_owner(url,base):return False
    one,two=urlsplit(url),urlsplit(base)
    host=(two.hostname or '').removeprefix('www.')
    if host=='sites.google.com':
        tenant='/'.join(two.path.strip('/').split('/')[:2])
        return (one.hostname or '').removeprefix('www.')==host and one.path.strip('/').startswith(tenant+'/') or (one.hostname or '').removeprefix('www.')==host and one.path.strip('/')==tenant
    if any(host==shared or host.endswith('.'+shared) for shared in SHARED_HOSTS):
        return (one.hostname or '').removeprefix('www.')==host
    return True

def tokens(value):
    return {word for word in re.findall(r'[a-z]{4,}',value.lower()) if word not in STOP}

def recovery_identity(original):
    host=(urlsplit(original).hostname or '').removeprefix('www.')
    parts=EXTRACT(host)
    anchors=tokens(urlsplit(original).path)
    if parts.subdomain:anchors|=tokens(parts.subdomain)
    return host,parts,anchors

def failure_code(result):
    reason=result.get('reason','').lower()
    errors=[e for e in result.get('report',{}).get('errors',[]) if e.get('stage') in ('validation','crawl','strategy')]
    text=reason+' '+' '.join(str(e.get('message','')).lower() for e in errors)
    if result.get('report',{}).get('terminal'):return 'unsafe_destination'
    if 'requires organizer review' in text:return 'organizer_review_required'
    if 'aggregator or excluded' in text:return 'excluded_platform'
    if '429' in text or 'rate limit' in text:return 'rate_limited'
    if 'robots policy denies access' in text:return 'policy_denied'
    if 'robots.txt blocks' in text:return 'robots_path_denied'
    if 'for sale' in text or 'parked' in text:return 'parked_or_empty'
    if '403' in text or 'cloudflare' in text or 'blocked' in text or 'access denied' in text:return 'access_blocked'
    if 'robots' in text:return 'policy_unavailable'
    if 'empty rendered' in text:return 'empty_page'
    if '404' in text or '410' in text or 'not-found' in text:return 'missing_page'
    if 'name resolution' in text or 'getaddrinfo' in text or 'dns' in text:return 'dns_failure'
    if 'timeout' in text or 'timed out' in text or 'time budget' in text:return 'timeout'
    if re.search(r'\b50[0234]\b',text):return 'server_error'
    if 'conflict' in text:return 'conflicting_evidence'
    if result.get('report',{}).get('past_events'):return 'past_edition'
    return 'missing_event_evidence'

class BoundedReader:
    def __init__(self,reader,original):self.reader=reader;self.original=original;self.cache={};self.reads=0;self.counts={};self.force=False;self.audit=[]
    async def read(self,url):
        from .source_validation import bad_page
        from .fetch import UnsafeDestination
        if not scope_url(url,self.original):raise UnsafeDestination('Recovery URL is outside organizer ownership')
        key=url.rstrip('/')
        if key in self.cache and not self.force:return self.cache[key]
        self.force=False
        if self.reads>=MAX_READS or self.counts.get(key,0)>=2:raise RuntimeError('Validation navigation budget exhausted')
        self.reads+=1;self.counts[key]=self.counts.get(key,0)+1
        from .source_validation import http_status
        entry={'url':url,'status':'running'};self.audit.append(entry)
        try:
            page=await self.reader.read(url)
            entry['status']='loaded'
        except BaseException as exc:
            entry.update(status='failed',message=str(exc)[:500] or 'Strategy time budget exhausted',http_status=http_status(exc));raise
        if not scope_url(page['url'],self.original):raise UnsafeDestination('Redirect changed organizer ownership')
        if not bad_page(page):self.cache[key]=page
        return page

class BoundedSearch:
    def __init__(self,search):self.searcher=search;self.cache={};self.requests=0;self.results=[];self.audit=[]
    def search(self,query):
        if query in self.cache:return self.cache[query]
        if self.requests>=MAX_SEARCHES:raise RuntimeError('Validation search budget exhausted')
        self.requests+=1
        result=self.searcher.search(query)
        self.cache[query]=result;self.results.extend(result.get('results',[]))
        return result

def recovery_links(reader,search,original):
    from .source_validation import observed_links
    _,_,anchors=recovery_identity(original)
    choices=[]
    for page in reader.cache.values():choices.extend((u,score) for score,u,_ in observed_links(page,original))
    choices.extend((r['url'],1) for r in search.results if r.get('url'))
    host=(urlsplit(original).hostname or '').removeprefix('www.')
    output=[]
    for url,score in choices:
        url=clean_url(url)
        if not url or not scope_url(url,original) or url.rstrip('/') in reader.cache:continue
        if re.search(r'\.(?:pdf|xml|ics|rss|jpg|png|zip)(?:$|\?)|/(?:privacy|login|terms|contact)(?:/|$)',url,re.I):continue
        match=len(anchors & tokens(urlsplit(url).path))
        # A different institutional subdomain/parent must match an observed event identity.
        if (urlsplit(url).hostname or '').removeprefix('www.')!=host and (not anchors or not match):continue
        if reader.counts.get(url.rstrip('/'),0)>=1:continue
        output.append((score+match*5,url))
    return [url for _,url in sorted(set(output),key=lambda x:(-x[0],len(x[1]),x[1]))]

async def validate_with_recovery(candidate,reader,search,evaluate):
    from .source_validation import website_root
    original=clean_url(candidate['url'])
    if not original:raise ValueError('Use a public HTTPS organizer URL')
    bounded=BoundedReader(reader,original);finder=BoundedSearch(search)
    strategies=[];attempts=[];errors=[];searches=[];pages=[]
    selected=original;name='original_event_page';baseline=[];outcome=None
    started=time.monotonic()
    for index in range(MAX_STRATEGIES):
        tick=time.monotonic();stamp=datetime.now(timezone.utc).isoformat()
        remaining=150-(time.monotonic()-started)
        if remaining<1:break
        try:outcome=await asyncio.wait_for(evaluate({**candidate,'url':selected},bounded,finder),timeout=min(45,remaining))
        except asyncio.TimeoutError:
            outcome={'status':'needs_review','canonical_url':selected,'parse_settings':{},'reason':'Validation strategy timed out',
                'report':{'errors':[{'stage':'strategy','message':'Strategy time budget exhausted'}]}}
        report=outcome['report'];code=None if outcome['status']=='validated' else failure_code(outcome)
        if index==0:baseline=report.get('event_evidence',[])
        if outcome['status']=='validated' and baseline:
            dates={(r['start_date'],r['end_date']) for r in baseline}
            if not dates.intersection((r['start_date'],r['end_date']) for r in report.get('event_evidence',[])):
                outcome.update(status='needs_review',reason='Recovery found a different event edition; organizer review required')
                code='identity_conflict'
        strategies.append({'strategy':name,'url':selected,'status':outcome['status'],'failure_code':code,'reason':outcome.get('reason'),
            'started_at':stamp,'completed_at':datetime.now(timezone.utc).isoformat(),'duration_ms':round((time.monotonic()-tick)*1000)})
        attempts.extend(report.get('attempts',[]));errors.extend(report.get('errors',[]));searches.extend(report.get('searches',[]));pages.extend(report.get('pages',[]))
        if outcome['status']=='validated' or code in ('unsafe_destination','organizer_review_required','excluded_platform','rate_limited','parked_or_empty','policy_denied','identity_conflict'):break
        if bounded.reads>=MAX_READS or time.monotonic()-started>=150:break
        hint=clean_url(report.get('recovery_url'))
        if hint and scope_url(hint,original) and hint.rstrip('/') not in bounded.counts:
            selected=hint;name='observed_redirect_target';continue
        # Search is still attempted after an unreadable first page. Results are navigation hints only.
        if finder.requests<MAX_SEARCHES:
            host,parts,anchors=recovery_identity(original)
            shared=any(host==shared or host.endswith('.'+shared) for shared in SHARED_HOSTS)
            search_host=parts.top_domain_under_public_suffix if parts.subdomain and not shared else host
            query=f'site:{search_host} '+ ' '.join(sorted(anchors)[:8])+f' conference {date.today().year} {date.today().year+1}'
            try:
                found=await asyncio.to_thread(finder.search,query)
                searches.append({'query':query,'results':[r for r in found.get('results',[]) if scope_url(r['url'],original)][:10],'warnings':found.get('warnings')})
            except Exception as exc:errors.append({'stage':'recovery_search','message':str(exc)[:500]})
        root=website_root(original)
        if index==0 and original.rstrip('/')!=root and not (urlsplit(original).hostname or '').endswith('sites.google.com'):
            selected=root;name='organizer_root';continue
        links=recovery_links(bounded,finder,original)
        if links:selected=links[0];name='observed_or_search_event_page';continue
        # www changes are scoped to the same organizer; no .com/.org/.net guessing.
        p=urlsplit(original);host=p.hostname or ''
        alias='https://'+(host[4:] if host.startswith('www.') else 'www.'+host)
        if code in ('dns_failure','missing_page','policy_unavailable') and scope_url(alias,original) and alias.rstrip('/') not in bounded.counts:
            selected=alias;name='verified_host_alias';continue
        if code in ('dns_failure','timeout','server_error','empty_page') and bounded.counts.get(original.rstrip('/'),0)<2:
            selected=original;name='bounded_transport_retry';bounded.force=True
            if hasattr(reader,'validation_wait_seconds'):reader.validation_wait_seconds=1.5
            continue
        break
    final=outcome['report']
    canonical=urlsplit(outcome['canonical_url'])
    if canonical.path in ('','/') and not canonical.query:outcome['canonical_url']=website_root(outcome['canonical_url'])
    final.update(original_url=original,validation_version=2,strategies=strategies,strategy_limit=MAX_STRATEGIES,
        navigation_limit=MAX_READS,search_limit=MAX_SEARCHES,navigations_used=bounded.reads,searches_used=finder.requests,
        attempts=bounded.audit,errors=errors,searches=searches,pages=list(dict.fromkeys(pages)),
        failure_code=None if outcome['status']=='validated' else code)
    return outcome

