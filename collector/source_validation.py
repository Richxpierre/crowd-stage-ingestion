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
    match=re.search(r'\bHTTP\s*(\d{3})\b',str(exc),re.I)
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
        if re.search(r'login|privacy|terms|contact|about.lis|contributors|institutional.policies|\.pdf$|\.xml$|\.ics$',label,re.I):continue
        score=sum(bool(re.search(pattern,label,re.I)) for pattern in [r'event|conference|edition|20\d\d',r'about|venue|location|visit|attend',r'ticket|registration|price'])
        if score:
            # Prefer links tied to this edition over site-wide menu entries.
            tokens=set(re.findall(r'[a-z]{4,}|20\d\d',urlsplit(page['url']).path.lower()))-{'conference','conferences','event','events','home','registration'}
            score+=3*len(tokens & set(re.findall(r'[a-z]{4,}|20\d\d',urlsplit(url).path.lower())))
            output.append((score,url,label[:180]))
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

async def _validate_site(candidate,reader,search):
    original=clean_url(candidate['url'])
    if not original:raise ValueError('Use a public HTTPS organizer URL')
    audit={'original_url':original,'attempts':[],'searches':[],'pages':[],'errors':[]}
    outcome={'status':'needs_review','canonical_url':original,'report':audit,'parse_settings':{}}
    async def read(url):
        try:
            page=await reader.read(url)
            if bad_page(page):
                if re.search(r'404|page not found',page.get('html','')[:2000],re.I):raise RuntimeError('HTTP 404: page-not-found content')
                if re.search(r'domain (?:is )?for sale|website (?:is )?parked',page.get('html','')[:2000],re.I):raise ValueError('Domain is parked or for sale')
                if re.search(r'access denied|just a moment|verify you are human',page.get('html','')[:2000],re.I):raise PermissionError('HTTP 403: access challenge')
                raise ValueError('Empty rendered page')
            audit['attempts'].append({'url':url,'status':'loaded'})
            return page
        except Exception as exc:
            audit['attempts'].append({'url':url,'status':'failed','http_status':http_status(exc),'message':str(exc)[:500]})
            raise
    try:
        from .discovery import excluded
        if excluded(original):
            outcome['reason']='This URL belongs to an aggregator or excluded platform, not an organizer website';return outcome
        first=await read(original)
        root_retry=False
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
        initial_source={'url':canonical,'name':candidate.get('title') or seed_host,'publisher':seed_host,'kind':'auto','is_official_source':True,'selectors':{'website_only':True}}
        initial_records=parse_page(first['html'].encode(),initial_source,first['url'])
        initial_future=[r for r in initial_records if r['end_date']>=date.today().isoformat() and r.get('status')!='cancelled']
        urls=[u for _,u,_ in observed_links(first,canonical)]+urls
        # An event's own complete page wins over unrelated catalog/navigation pages.
        for url in ([] if initial_future else list(dict.fromkeys(urls))[:2]):
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
        from .mini_enrichment import MiniEnricher,enrich_pass
        ai=candidate.get('_mini_enricher') or MiniEnricher((candidate.get('report') or {}).get('ai_enrichment'))
        for kind in PASS_TYPES:
            initial=extract_pass(kind,pages,source,{})[:50]
            targets=list(dict.fromkeys(r['evidence']['page_url'] for r in initial))
            config={'enabled':True,'page_urls':targets or list(pages)[:3],'selectors':{}}
            if single:
                mappings=[proposed_selectors(kind,pages[u]) for u in config['page_urls']]
                # A selector is safe across configured pages only when every page
                # independently offers the same unique field mapping.
                if mappings and all(m==mappings[0] for m in mappings):config['selectors']=mappings[0]
            if config['selectors']:
                trial=extract_pass(kind,{u:pages[u] for u in config['page_urls']},source,config)[:50]
                # A unique CSS node can still be a deadline or footer address.
                # Generated overrides must reproduce independently extracted facts.
                for field in list(config['selectors']):
                    expected={json.dumps(r['fields'][field],sort_keys=True) for r in initial if field in r['fields']}
                    proposed={json.dumps(r['fields'][field],sort_keys=True) for r in trial if field in r['fields']}
                    if len(expected)!=1 or proposed!=expected:config['selectors'].pop(field)
            extracted=extract_pass(kind,{u:pages[u] for u in config['page_urls']},source,config)[:50]
            extracted,config=await asyncio.to_thread(enrich_pass,kind,{u:pages[u] for u in config['page_urls']},source,config,extracted,ai)
            plan[kind]=config
            results[kind]={'status':'success' if extracted else 'missing','results':extracted,'errors':[]}
        assembled=assemble(results,source)
        audit['ai_enrichment']=ai.audit
        current=[r for r in assembled if r['end_date']>=today and int(r['start_date'][:4])<=date.today().year+2 and r.get('status')!='cancelled']
        audit.update(event_evidence=[{'title':r['title'],'start_date':r['start_date'],'end_date':r['end_date'],'url':r['event_url'],'page_url':r['source_url']} for r in current][:20],
                     pass_results=results,search_matches=search_matches)
        audit['past_events']=[{'title':r['title'],'start_date':r['start_date'],'end_date':r['end_date']} for r in raw if r['end_date']<today][:5]
        outcome['parse_settings']={'website_only':True,'render':True,'max_pages':8,'single_event':single,'parse_passes':plan,'ai_enrichment':ai.audit}
        critical={'city','country','region','venue'}
        optional=sorted({field for r in current for field in r.get('extraction',{}).get('conflicting_fields',[]) if field not in critical})
        audit['warnings']=([{'code':'optional_conflicts','fields':optional,'message':'Conflicting optional values stay unknown; they do not invalidate the website.'}] if optional else [])
        if search_failed or not search_matches:audit['warnings'].append({'code':'search_unavailable' if search_failed else 'not_indexed','message':'Search could not corroborate this URL; admission requires directly crawled event evidence.'})
        if not future or not current:outcome['reason']='No unambiguous upcoming conference edition found on the crawled website'
        elif any(critical.intersection(r.get('extraction',{}).get('conflicting_fields',[])) for r in current):outcome['reason']='Conflicting event location facts require review'
        elif (candidate.get('evidence') or {}).get('requires_review') and not (candidate.get('evidence') or {}).get('admin_reviewed'):
            outcome['reason']='External search discovery requires organizer review before admission'
        else:
            audit['evidence_basis']='direct_organizer_event'
            outcome.update(status='validated',reason='Direct organizer-page evidence confirms an upcoming conference; four-pass settings verified')
        return outcome
    except Exception as exc:
        outcome['reason']='Website could not be validated: '+str(exc)[:500]
        audit['errors'].append({'stage':'validation','http_status':http_status(exc),'message':str(exc)[:500]})
        audit['terminal']=isinstance(exc,UnsafeDestination)
        if getattr(exc,'recovery_url',None):audit['recovery_url']=exc.recovery_url
        return outcome


async def validate_website(candidate,reader,search):
    from .validation_recovery import validate_with_recovery
    from .mini_enrichment import MiniEnricher
    candidate={**candidate,'_mini_enricher':MiniEnricher((candidate.get('report') or {}).get('ai_enrichment'))}
    async def evaluate(item,bounded,finder):
        # The recovery controller allows 45 seconds per strategy. Optional AI
        # must leave time for deterministic results to be returned and saved.
        item['_mini_enricher'].deadline=time.monotonic()+40
        return await _validate_site(item,bounded,finder)
    return await validate_with_recovery(candidate,reader,search,evaluate)

async def run(limit=5,validation_id=None,dry_run=False):
    from .client import Client
    db=Client()
    worker_deadline=time.monotonic()+max(180,min(int(os.environ.get('VALIDATION_BATCH_SECONDS','600')),900))
    async with Crawl4AIReader(os.environ.get('COLLECTOR_USER_AGENT',''),deadline=worker_deadline) as reader:
      for _ in range(max(1,min(limit,50))):
        if time.monotonic()>=worker_deadline-15:break
        candidate=db.request('POST','rpc/claim_conference_source_validation',json={'p_id':validation_id})
        if not candidate:break
        deadline=min(worker_deadline,time.monotonic()+180)
        reader.deadline=deadline
        try:
            search=SearXNG(deadline=deadline)
            try:
                result=await asyncio.wait_for(validate_website(candidate,reader,search),timeout=max(1,deadline-time.monotonic()))
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


