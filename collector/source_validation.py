"""Crawl4AI + SearXNG admission gate. Search snippets are never event facts."""
from __future__ import annotations
import argparse
import asyncio
from datetime import date,datetime,timezone
import json
import os
import re
import time
from urllib.parse import urlsplit,urlunsplit,urljoin
from bs4 import BeautifulSoup
from .crawler_reader import Crawl4AIReader,same_owner
from .search import SearXNG,result_url
from .automatic import parse_page,date_range,EVENT_WORD
from .parse_passes import PASS_TYPES,extract_pass,assemble,event_nodes
from .parsers import clean_url
from .fetch import UnsafeDestination

def website_root(url):
    """Keep tenant/subdomain ownership. Never turn a tenant into its hosting provider."""
    p=urlsplit(url)
    return urlunsplit(('https',p.netloc,'','',''))

def http_status(exc):
    code=getattr(getattr(exc,'response',None),'status_code',None)
    if code:return code
    match=re.search(r'\bHTTP\s*(403|404)\b',str(exc),re.I)
    return int(match[1]) if match else None

def bad_page(page):
    soup=BeautifulSoup(page.get('html',''),'html.parser')
    title=' '.join(n.get_text(' ',strip=True) for n in soup.select('title,h1'))
    text=soup.get_text(' ',strip=True)[:2000]
    if re.search(r'page not found|404 not found|access denied|just a moment|verify you are human|domain (?:is )?for sale|website (?:is )?parked',title+' '+text[:500],re.I):return True
    return len(text.strip())<30 and not list(event_nodes(page.get('html','').encode()))

def observed_links(page,root):
    soup=BeautifulSoup(page.get('html',''),'html.parser');output=[]
    for a in soup.select('a[href]'):
        url=clean_url(urljoin(page['url'],a['href']))
        if not url or not same_owner(url,root):continue
        label=a.get_text(' ',strip=True)+' '+urlsplit(url).path
        if re.search(r'login|privacy|terms|\.pdf$|\.xml$|\.ics$',label,re.I):continue
        score=sum(bool(re.search(pattern,label,re.I)) for pattern in [r'event|conference|edition|20\d\d',r'about|venue|location|visit|attend',r'ticket|registration|price'])
        if score:output.append((score,url,label[:180]))
    return sorted(output,key=lambda x:(-x[0],x[1]))

def safe_selector(soup,node):
    """Use observed, unique selectors only, never invented website mappings."""
    if node.get('id') and re.fullmatch(r'[A-Za-z_][\w-]*',node['id']):
        selector='#'+node['id']
    else:
        parts=[];current=node
        for _ in range(8):
            if not current or current.name in ('[document]','html'):break
            siblings=current.parent.find_all(current.name,recursive=False) if current.parent else [current]
            parts.insert(0,current.name+(f':nth-of-type({siblings.index(current)+1})' if len(siblings)>1 else ''))
            current=current.parent
        selector=' > '.join(parts)
    matches=soup.select(selector)
    return selector if len(matches)==1 and matches[0] is node else None

def proposed_selectors(kind,body):
    soup=BeautifulSoup(body,'html.parser');mapping={}
    fields={
      'event_date':{'start_date':'[itemprop="startDate"],time[datetime]','end_date':'[itemprop="endDate"]'},
      'event_stats':{'price_from':'[itemprop="price"]','price_currency':'[itemprop="priceCurrency"]'},
      'event_location':{'city':'[itemprop="addressLocality"]','region':'[itemprop="addressRegion"]','country':'[itemprop="addressCountry"]','venue':'[itemprop="location"] [itemprop="name"]'},
      'event_details':{'summary':'meta[name="description"]','cover_url':'meta[property="og:image"]','categories':'meta[name="keywords"]'},
    }[kind]
    for field,selector in fields.items():
        nodes=soup.select(selector)
        if len(nodes)==1:
            exact=safe_selector(soup,nodes[0])
            if exact:mapping[field]=exact
    # A visible, explicit date range can supply both date fields on a dedicated page.
    if kind=='event_date' and not mapping:
        nodes=[n for n in soup.select('main time,main p,section time,section p')
               if len(n.get_text(' ',strip=True))<200 and date_range(n.get('datetime') or n.get_text(' ',strip=True))
               and not re.search(r'deadline|submission|copyright|updated|registration',n.get_text(),re.I)]
        if len(nodes)==1:
            exact=safe_selector(soup,nodes[0])
            if exact:mapping={'start_date':exact,'end_date':exact}
    return mapping

async def validate_website(candidate,reader,search):
    original=clean_url(candidate['url'])
    if not original:raise ValueError('Use a public HTTPS organizer URL')
    audit={'original_url':original,'attempts':[],'searches':[],'pages':[],'errors':[]}
    outcome={'status':'needs_review','canonical_url':original,'report':audit,'parse_settings':{}}
    async def read(url):
        try:
            page=await reader.read(url)
            if bad_page(page):
                if re.search(r'404|page not found',page.get('html','')[:2000],re.I):raise RuntimeError('HTTP 404: page-not-found content')
                raise ValueError('Page is empty, blocked or parked')
            audit['attempts'].append({'url':url,'status':'loaded'})
            return page
        except Exception as exc:
            audit['attempts'].append({'url':url,'status':'failed','http_status':http_status(exc),'message':str(exc)[:500]})
            raise
    try:
        from .discovery import excluded
        if excluded(original):
            outcome['reason']='This URL belongs to an aggregator or excluded platform, not an organizer website';return outcome
        root_retry=False
        try:first=await read(original)
        except Exception as exc:
            root=website_root(original)
            if isinstance(exc,UnsafeDestination) or http_status(exc) not in (403,404) or original.rstrip('/')==root:raise
            # A single bounded retry, preserving the host/tenant and all network guards.
            outcome['canonical_url']=root
            root_retry=True
            first=await read(root)
        canonical=outcome['canonical_url']
        if not same_owner(first['url'],canonical):raise ValueError('Organizer ownership changed during navigation')
        # Store the working redirected hostname, keeping root retries path-free.
        canonical=website_root(first['url']) if root_retry else clean_url(first['url'])
        outcome['canonical_url']=canonical
        pages={first['url']:first['html'].encode()}
        seed_host=urlsplit(canonical).hostname
        queries=[f'site:{seed_host} conference {date.today().year} {date.today().year+1}',
                 f'site:{seed_host} conference dates venue tickets']
        urls=[];search_failed=False;search_matches=0
        for query in queries:
            try:
                found=await asyncio.to_thread(search.search,query)
                same_site_results=[r for r in found['results'] if same_owner(r['url'],canonical)]
                search_matches+=len(same_site_results)
                audit['searches'].append({'query':query,'results':same_site_results[:10],'warnings':found.get('warnings')})
                urls.extend(r['url'] for r in same_site_results)
            except Exception as exc:
                search_failed=True;audit['errors'].append({'stage':'search','message':str(exc)[:500]})
        urls=[u for _,u,_ in observed_links(first,canonical)]+urls
        for url in list(dict.fromkeys(urls))[:5]:
            if url in pages:continue
            try:
                page=await read(url)
                if same_owner(page['url'],canonical):pages[page['url']]=page['html'].encode()
            except Exception as exc:
                audit['errors'].append({'stage':'crawl','url':url,'message':str(exc)[:500]})
        audit['pages']=list(pages)
        source={'url':canonical,'name':candidate.get('title') or seed_host,'publisher':seed_host,
                'kind':'auto','is_official_source':True,'selectors':{'website_only':True,'render':True}}
        raw=[r for url,body in pages.items() for r in parse_page(body,source,url)]
        today=date.today().isoformat()
        future=[r for r in raw if r['end_date']>=today and int(r['start_date'][:4])<=date.today().year+2
                and r.get('status')!='cancelled' and EVENT_WORD.search(r['title'])]
        editions={(r['start_date'],r['end_date'],re.sub(r'\W+',' ',r['title']).casefold()) for r in future}
        all_editions={(r['start_date'],r['end_date'],re.sub(r'\W+',' ',r['title']).casefold()) for r in raw}
        single=len(editions)==1 and len(all_editions)==1
        source['selectors']['single_event']=single
        plan={};results={}
        for kind in PASS_TYPES:
            initial=extract_pass(kind,pages,source,{})[:50]
            targets=list(dict.fromkeys(r['evidence']['page_url'] for r in initial))
            config={'enabled':True,'page_urls':targets or list(pages)[:3],'selectors':{}}
            if single:
                mappings=[proposed_selectors(kind,pages[u]) for u in config['page_urls']]
                # A selector is safe across configured pages only when every page
                # independently offers the same unique field mapping.
                if mappings and all(m==mappings[0] for m in mappings):config['selectors']=mappings[0]
            extracted=extract_pass(kind,{u:pages[u] for u in config['page_urls']},source,config)[:50]
            plan[kind]=config
            results[kind]={'status':'success' if extracted else 'missing','results':extracted,'errors':[]}
        assembled=assemble(results,source)
        current=[r for r in assembled if r['end_date']>=today and int(r['start_date'][:4])<=date.today().year+2]
        audit.update(event_evidence=[{'title':r['title'],'start_date':r['start_date'],'end_date':r['end_date'],'url':r['event_url']} for r in current][:20],
                     pass_results=results,search_matches=search_matches)
        outcome['parse_settings']={'website_only':True,'render':True,'max_pages':8,'single_event':single,'parse_passes':plan}
        if search_failed:outcome['reason']='Search assistance failed; review the crawl evidence and retry'
        elif not search_matches:outcome['reason']='Search returned no same-website evidence; manual review required'
        elif not future or not current:outcome['reason']='No unambiguous upcoming conference edition found on the crawled website'
        elif any(r.get('extraction',{}).get('conflicting_fields') for r in current):outcome['reason']='Conflicting event facts require review'
        elif (candidate.get('evidence') or {}).get('requires_review') and not (candidate.get('evidence') or {}).get('admin_reviewed'):
            outcome['reason']='External search discovery requires organizer review before admission'
        else:outcome.update(status='validated',reason='Crawled website and same-site search evidence confirm an upcoming conference; four-pass settings verified')
        return outcome
    except Exception as exc:
        outcome['reason']='Website could not be validated: '+str(exc)[:500]
        audit['errors'].append({'stage':'validation','http_status':http_status(exc),'message':str(exc)[:500]})
        return outcome

async def run(limit=5,validation_id=None,dry_run=False):
    from .client import Client
    db=Client()
    for _ in range(max(1,min(limit,10))):
        candidate=db.request('POST','rpc/claim_conference_source_validation',json={'p_id':validation_id})
        if not candidate:break
        deadline=time.monotonic()+180
        try:
            search=SearXNG(deadline=deadline)
            try:
                async with Crawl4AIReader(os.environ.get('COLLECTOR_USER_AGENT',''),deadline=deadline) as reader:
                    result=await validate_website(candidate,reader,search)
            finally:search.close()
        except Exception as exc:
            result={'status':'needs_review','canonical_url':candidate['url'],'parse_settings':{},'report':{'errors':[{'message':str(exc)[:750]}]},'reason':'Validator setup failed; retry after checking the worker'}
        if dry_run:
            result.update(status='needs_review',reason='Evidence-only validation preview: '+result['reason'])
        db.request('POST','rpc/finish_conference_source_validation',json={'p_id':candidate['id'],'p_result':result,'p_claim':candidate['claim_token']})
        if validation_id:break

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=5);parser.add_argument('--validation-id');parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();asyncio.run(run(args.limit,args.validation_id,args.dry_run))
if __name__=='__main__':main()
