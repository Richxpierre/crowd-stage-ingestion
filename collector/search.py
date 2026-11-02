"""Bounded SearXNG search transport. Crawl4AI, not snippets, inspects results."""
from __future__ import annotations
from datetime import datetime, timezone
import ipaddress
import json
import os
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup
from .fetch import host_of
from .parsers import clean_url


def result_url(value):
    try:
        p=urlsplit(value)
        if p.scheme not in ('https','http') or p.username or p.password or p.port not in (None,80,443):return None
        host=(p.hostname or '').lower().rstrip('.')
        if '.' not in host or host.endswith(('.local','.localhost','.internal','.test','.invalid')):return None
        try:ipaddress.ip_address(host);return None
        except ValueError:pass
        query=urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('gclid','fbclid')])
        return clean_url(urlunsplit(('https',host,p.path or '/',query,'')))
    except (TypeError,ValueError):return None


def search_queries(seed,name,settings):
    custom=settings.get('search_queries') or []
    if not isinstance(custom,list) or any(not isinstance(q,str) for q in custom):raise ValueError('Search queries must be a list of strings')
    if len(custom)>3 or any(len(q)>240 for q in custom):raise ValueError('Use at most three search queries of 240 characters each')
    year=datetime.now(timezone.utc).year
    defaults=[f'site:{urlsplit(seed).hostname} conferences {year}',f'"{str(name or urlsplit(seed).hostname)[:100]}" conference official website']
    return list(dict.fromkeys(q.strip() for q in (custom or defaults) if q.strip()))[:3]


class SearXNG:
    def __init__(self,deadline=None,session=None):
        # Trusted worker configuration only: never accept an endpoint from target JSON.
        self.base=os.environ.get('SEARXNG_URL','').rstrip('/')
        p=urlsplit(self.base)
        local=p.scheme=='http' and p.hostname in ('127.0.0.1','localhost') and p.port==8888
        if p.username or p.password or p.query or p.fragment or p.path not in ('','/') or not(local or p.scheme=='https' and p.port in (None,443)):
            raise ValueError('Configure SEARXNG_URL as the private worker service or a public HTTPS origin')
        if not local:host_of(self.base)
        self.local=local
        self.deadline=deadline or time.monotonic()+45
        self.session=session or requests.Session()
        self.session.trust_env=False

    def close(self):self.session.close()

    def search(self,query,page=1):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Search time budget reached')
        if not self.local:host_of(self.base)
        headers={'Accept':'application/json','User-Agent':'CrowdStage-Discovery/1.0'}
        if os.environ.get('SEARXNG_TOKEN'):headers['Authorization']='Bearer '+os.environ['SEARXNG_TOKEN']
        with self.session.get(self.base+'/search',params={'q':query,'format':'json','categories':'general','pageno':page,'safesearch':1},
                              headers=headers,timeout=min(15,remaining),allow_redirects=False,stream=True) as response:
            if response.status_code==403:raise RuntimeError('SearXNG rejected JSON search; enable search.formats: [html, json]')
            if response.status_code==429:raise RuntimeError('SearXNG rate limited search; retry on the next scheduled run')
            if response.status_code!=200:raise RuntimeError(f'SearXNG search failed (HTTP {response.status_code})')
            data=bytearray()
            for chunk in response.iter_content(8192):
                if time.monotonic()>=self.deadline:raise TimeoutError('Search time budget reached')
                data.extend(chunk)
                if len(data)>2*1024*1024:raise ValueError('SearXNG response exceeds 2 MB')
        try:payload=json.loads(data)
        except ValueError as exc:raise ValueError('SearXNG returned invalid JSON') from exc
        if not isinstance(payload,dict) or not isinstance(payload.get('results'),list):raise ValueError('SearXNG returned an unsupported result shape')
        results=[];seen=set()
        for item in payload['results'][:100]:
            if not isinstance(item,dict):continue
            url=result_url(item.get('url'))
            if not url or url in seen:continue
            seen.add(url)
            engines=item.get('engines') or [item.get('engine')]
            results.append({'url':url,'title':str(item.get('title') or '')[:240],'query':query,
                            'engines':[str(e)[:80] for e in engines[:10] if e] if isinstance(engines,list) else []})
        warnings=payload.get('unresponsive_engines') or []
        return {'results':results[:20],'warnings':str(warnings)[:500] if warnings else None}


def conference_page(page):
    """A relevance check for review candidates, not proof of official ownership."""
    import re
    soup=BeautifulSoup(page['html'],'html.parser')
    title=' '.join(n.get_text(' ',strip=True) for n in soup.select('title,h1,h2')[:12])
    hint=re.search(r'conference|congress|symposium|convention|summit|expo|trade[ -]?show|trade[ -]?fair|\bcon\b',title,re.I)
    if hint:return title[:240]
    def event(value):
        if isinstance(value,list):return any(event(v) for v in value)
        if not isinstance(value,dict):return False
        types=value.get('@type',[]);types=[types] if isinstance(types,str) else types
        return any(str(t).endswith('Event') for t in types) or any(event(value[k]) for k in ('@graph','itemListElement','item') if k in value)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            if event(json.loads(script.get_text())):return title[:240] or 'Event website candidate'
        except ValueError:pass
    return None
