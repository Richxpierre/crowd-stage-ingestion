"""Four independent, evidence-preserving website extraction passes.

Optional passes never need a date. Only the assembler requires an explicit date;
facts join by event name, URL and edition, never by the source alone.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import re
import time
import unicodedata
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from .automatic import parse_page, discover_links, same_site, date_range, EVENT_WORD
from .parsers import _ld_nodes, _is_event, _extract_location, clean_url, plain, date_part, _finish
from .images import choose_image
from urllib.parse import urlsplit,urlunsplit

PASS_TYPES = ('event_date', 'event_stats', 'event_location', 'event_details')
FIELDS = {
    'event_date': ('start_date','end_date','timezone','status'),
    'event_stats': ('price_from','price_currency','attendees','exhibitors','format'),
    'event_location': ('venue','city','region','country'),
    'event_details': ('event_kind','summary','categories','cover_url','image_method','image_source_url','registration_url','cfp_url','cfp_deadline'),
}

def now(): return datetime.now(timezone.utc).isoformat()
def identity(title): return re.sub(r'\W+', ' ', title or '').strip().casefold()
def canonical_page(url):
    p=urlsplit(url)
    path=re.sub(r'/(?:index|default)\.(?:html?|php|aspx?)$', '/', p.path,flags=re.I)
    return urlunsplit((p.scheme,p.netloc,path or '/',p.query,''))

def event_nodes(body):
    soup = BeautifulSoup(body, 'html.parser')
    for script in soup.find_all('script', type=re.compile(r'^application/ld\+json', re.I)):
        try: payload = json.loads(script.string or script.get_text())
        except (ValueError, TypeError): continue
        for node in _ld_nodes(payload):
            if _is_event(node): yield node

def nonempty(data):
    return {k:v for k,v in data.items() if v is not None and v != '' and v != [] and v != 'unknown'}

def number(value, integer=False):
    try:
        if isinstance(value, bool): return None
        n = float(str(value).replace(',', ''))
        if n < 0 or n == float('inf') or n != n: return None
        return int(n) if integer and n.is_integer() else None if integer else n
    except (ValueError, TypeError): return None

def node_fields(kind, node, body, url, source):
    if kind == 'event_location':
        venue,city,region,country = _extract_location(node.get('location'))
        return nonempty(dict(venue=venue,city=city,region=region,country=country))
    if kind == 'event_stats':
        attendance = str(node.get('eventAttendanceMode',''))
        fmt = next((value for key,value in [('Mixed','hybrid'),('Offline','in_person'),('Online','online')]
                    if key+'EventAttendanceMode' in attendance), None)
        offers = node.get('offers') or []
        if isinstance(offers,dict): offers=[offers]
        # Different currencies cannot be collapsed into one minimum price.
        prices=[(number(o.get('price',o.get('lowPrice'))),plain(o.get('priceCurrency'),3))
                for o in offers if isinstance(o,dict)]
        prices=[p for p in prices if p[0] is not None and p[1]]
        price=min(prices) if prices and len({p[1] for p in prices})==1 else (None,None)
        return nonempty(dict(format=fmt,price_from=price[0],price_currency=price[1],
                             attendees=number(node.get('attendeeCount'),True),exhibitors=number(node.get('exhibitorCount'),True)))
    if kind == 'event_details':
        tags=node.get('keywords') or []
        if isinstance(tags,str): tags=re.split(r'[,;]',tags)
        if not isinstance(tags,list): tags=[]
        result=nonempty(dict(summary=plain(node.get('description'),1200),categories=[plain(x,60) for x in tags if isinstance(x,str)][:15]))
        result.update(choose_image(body,url,event_image=node.get('image'),selectors=source.get('selectors')))
        offers=node.get('offers') or []
        if isinstance(offers,dict): offers=[offers]
        for offer in offers:
            if isinstance(offer,dict) and clean_url(offer.get('url')):
                result['registration_url']=clean_url(offer['url']);break
        return result
    raise ValueError('Unsupported pass')

def overrides(kind, body, config, url):
    """Explicit admin CSS mappings can extract optional facts without any dates."""
    soup=BeautifulSoup(body,'html.parser'); values={}
    mapping=config.get('selectors') or {}
    if not isinstance(mapping,dict): raise ValueError('Pass selectors must be an object')
    for field,selector in mapping.items():
        if field not in FIELDS[kind] or not isinstance(selector,str) or not selector.strip(): continue
        node=soup.select_one(selector)  # Invalid CSS fails just this pass.
        if not node: continue
        value=node.get('datetime') or node.get('content') or node.get_text(' ',strip=True)
        if field.endswith('_url'):
            value=clean_url(urljoin(url,node.get('href') or node.get('src') or value))
        elif field in ('start_date','end_date','cfp_deadline'):
            span=date_range(value);value=span[0 if field!='end_date' else 1] if span else date_part(value)
        elif field in ('attendees','exhibitors','price_from'): value=number(value,field!='price_from')
        elif field=='categories': value=[x.strip()[:60] for x in re.split(r'[,;]',value) if x.strip()][:15]
        elif field=='format': value=value if value in ('in_person','online','hybrid') else None
        else: value=plain(value,1200 if field=='summary' else 240)
        if value is not None: values[field]=value
    return nonempty(values)


def verified_event_values(kind, body, config, url):
    """Replay an admin's edition-specific facts only against the inspected page.

    This is declarative data, never Python/JavaScript or instructions from a page.
    A date recipe can fail closed when its evidence changes, requiring re-review.
    """
    recipes=config.get('verified_events') or []
    if not isinstance(recipes,list) or len(recipes)>100:
        raise ValueError('verified_events must be a list of at most 100 reviewed editions')
    if not recipes:
        if config.get('require_verified'):raise ValueError('Reviewed edition evidence changed or is missing; recheck this source before ingestion')
        return []
    def norm(value):
        value=unicodedata.normalize('NFKC',str(value)).casefold()
        return re.sub(r'[^\w]+',' ',value).strip()
    soup=BeautifulSoup(body,'html.parser')
    structured=[]
    for script in soup.find_all('script',type=re.compile(r'^application/ld\+json',re.I)):
        try:structured.extend(n for n in _ld_nodes(json.loads(script.string or script.get_text())) if _is_event(n))
        except (ValueError,TypeError):continue
    for noisy in soup.select('script,style,noscript'):noisy.decompose()
    visible=norm(soup.get_text(' ',strip=True))
    matched=[]
    for recipe in recipes:
        if not isinstance(recipe,dict):raise ValueError('Invalid reviewed event recipe')
        if canonical_page(str(recipe.get('page_url','')))!=canonical_page(url):continue
        proofs=recipe.get('require_text') or []
        edition=str(recipe.get('edition',''))
        event_guard=recipe.get('require_event')
        schema_match=False
        if event_guard:
            if not isinstance(event_guard,dict) or not re.fullmatch(r'[0-9a-f]{64}',str(event_guard.get('sha256',''))):
                raise ValueError('Reviewed structured event needs a SHA-256 evidence fingerprint')
            schema_match=any(
                str(node.get('name',''))==event_guard.get('name') and
                date_part(node.get('startDate'))==event_guard.get('start_date') and
                date_part(node.get('endDate') or node.get('startDate'))==event_guard.get('end_date') and
                hashlib.sha256(json.dumps(node,sort_keys=True,separators=(',',':')).encode()).hexdigest()==event_guard['sha256']
                for node in structured)
        if not re.fullmatch(r'\d{4}',edition) or not isinstance(proofs,list) or not (0 if event_guard else 2)<=len(proofs)<=12:
            raise ValueError('Reviewed event needs an edition and 2-12 visible evidence excerpts')
        if not all(isinstance(p,str) and 3<=len(p)<=600 for p in proofs):
            raise ValueError('Invalid reviewed event evidence excerpt')
        if event_guard and not schema_match:continue
        if not event_guard and not any(edition in p for p in proofs):continue
        if not all(norm(p) in visible for p in proofs):continue
        title=plain(recipe.get('title'),240)
        start=date_part(recipe.get('start_date'))
        if not title or not start or start[:4]!=edition:raise ValueError('Reviewed event identity does not match its edition')
        if event_guard and event_guard.get('start_date')!=start:raise ValueError('Reviewed structured evidence does not match event dates')
        fields={k:v for k,v in (recipe.get('fields') or {}).items() if k in FIELDS[kind]}
        for key,value in list(fields.items()):
            if key.endswith('_url'):
                fields[key]=clean_url(value)
            elif key in ('start_date','end_date','cfp_deadline'):
                fields[key]=date_part(value)
            elif key in ('attendees','exhibitors','price_from'):
                fields[key]=number(value,key!='price_from')
        if fields.get('format') not in (None,'in_person','online','hybrid'):raise ValueError('Invalid reviewed format')
        if kind=='event_date':
            if fields.get('start_date')!=start:raise ValueError('Reviewed dates do not match event identity')
            if event_guard and fields.get('end_date',start)!=event_guard.get('end_date'):raise ValueError('Reviewed structured evidence does not match event dates')
            fields['source_record_id']=recipe.get('source_record_id') or canonical_page(recipe.get('event_url') or url)+'|'+edition
        fields=nonempty(fields)
        if fields:matched.append((recipe,title,start,fields))
    if config.get('require_verified') and not matched:
        raise ValueError('Reviewed edition evidence changed or is missing; recheck this source before ingestion')
    return matched

def extract_pass(kind, pages, source, config, on_page=None):
    results=[]
    for url,body in pages.items():
        digest=hashlib.sha256(body).hexdigest()
        def add(title,start,fields,method,event_url=None,metadata=None):
            fields=nonempty(fields)
            original=event_url or url;canonical=canonical_page(original)
            if 'source_record_id' in fields and fields['source_record_id'].startswith(original+'|'):
                fields['source_record_id']=canonical+fields['source_record_id'][len(original):]
            if fields: results.append(dict(title=title,start_date=start,event_url=canonical,
                fields=fields,evidence={'page_url':url,'snapshot_sha256':digest,'method':method,**({'metadata':metadata} if metadata else {})}))
        custom=overrides(kind,body,config,url)
        dated=[]
        if kind=='event_date':
            dated=parse_page(body,source,url)
            for record in dated:
                if len(dated)==1 and (source.get('selectors') or {}).get('single_event'):
                    record.update(custom)
                add(record['title'],record['start_date'],{**{k:record.get(k) for k in FIELDS[kind]},
                    'source_record_id':record['source_record_id']},'date_parser',record.get('event_url'))
        else:
            for node in event_nodes(body):
                fields=node_fields(kind,node,body,url,source)
                from .metadata import infer,GROUPS
                venue,city,_,country=_extract_location(node.get('location'))
                meta=infer(body,url,plain(node.get('name'),240) or '',dict(venue=venue,city=city,country=country),node)
                if kind=='event_location' and fields.get('region') not in GROUPS:fields.pop('region',None)
                for key,value in meta['fields'].items():
                    if key in FIELDS[kind]:fields.setdefault(key,value)
                add(plain(node.get('name'),240),date_part(node.get('startDate')),
                    fields,'schema_event',clean_url(node.get('url')),meta)
            soup=BeautifulSoup(body,'html.parser')
            # HTML-only extraction stays useful even when dates are missing.
            headings=soup.find_all('h1')
            title=plain(headings[0].get_text(' ',strip=True),240) if len(headings)==1 else None
            if title and EVENT_WORD.search(title) and not list(event_nodes(body)):
                fields={}
                if kind=='event_details':
                    meta=soup.find('meta',attrs={'name':'description'}) or soup.find('meta',property='og:description')
                    tags=soup.find('meta',attrs={'name':'keywords'})
                    fields=nonempty(dict(summary=plain(meta.get('content'),1200) if meta else None,
                        categories=[x.strip()[:60] for x in tags.get('content','').split(',') if x.strip()][:15] if tags else []))
                    fields.update(choose_image(body,url,selectors=source.get('selectors')))
                elif kind=='event_location':
                    def prop(name):
                        n=soup.select_one('[itemprop="'+name+'"]')
                        return plain(n.get('content') or n.get_text(' ',strip=True),240) if n else None
                    fields=nonempty(dict(city=prop('addressLocality'),region=prop('addressRegion'),country=prop('addressCountry')))
                elif kind=='event_stats':
                    for field,label in [('attendees',r'attendees|participants'),('exhibitors',r'exhibitors')]:
                        matches=re.findall(r'\b([\d,]+)\s+(?:'+label+r')\b',soup.get_text(' ',strip=True),re.I)
                        values={number(m,True) for m in matches};values.discard(None)
                        if len(values)==1:fields[field]=values.pop()
                add(title,None,fields,'html_event_page')
        # Infer only on a uniquely dated event page; never borrow catalog/footer facts.
        if kind!='event_date' and not list(event_nodes(body)):
            from .metadata import infer
            dated_records=parse_page(body,source,url)
            if len(dated_records)==1:
                rec=dated_records[0];meta=infer(body,url,rec['title'],rec,single_event=True)
                add(rec['title'],rec['start_date'],{k:v for k,v in meta['fields'].items() if k in FIELDS[kind]},
                    'metadata_rules',rec.get('event_url'),meta)
        # Unscoped HTML overrides are usable on a dedicated source only. Catalogs
        # must use Event structured nodes, avoiding facts leaking between editions.
        if custom and (source.get('selectors') or {}).get('single_event') and not (kind=='event_date' and dated):
            title=plain((source.get('selectors') or {}).get('title_value') or source['name'],240)
            if kind=='event_date' and custom.get('start_date'):
                custom.setdefault('end_date',custom['start_date'])
                custom['source_record_id']=source['url']+'|'+custom['start_date'][:4]
            add(title,custom.get('start_date'),custom,'admin_selectors',source['url'])
        if kind=='event_details' and (source.get('selectors') or {}).get('single_event'):
            image=choose_image(body,url,selectors=source.get('selectors'))
            add(source['name'],None,image,'page_image',source['url'])
        for recipe,title,start,fields in verified_event_values(kind,body,config,url):
            event_url=canonical_page(recipe.get('event_url') or source['url'])
            if kind=='event_date':
                results[:]=[row for row in results if not (
                    row['fields'].get('source_record_id')==fields['source_record_id'] or
                    (row['event_url']==event_url and ((source.get('selectors') or {}).get('single_event') or identity(row['title'])==identity(title))))]
            add(title,start,fields,'admin_selectors',event_url,
                {'manual_review':{'edition':recipe['edition'],'evidence':recipe['require_text'],'reviewed_at':recipe.get('reviewed_at')}})
        if on_page:on_page()
    return results

def collect_passes(fetcher, source, selected=None, on_result=None):
    from .mini_enrichment import MiniEnricher,enrich_pass
    ai=MiniEnricher((source.get('discovery_report') or {}).get('ai_enrichment') or (source.get('selectors') or {}).get('ai_enrichment'))
    selected=tuple(selected or PASS_TYPES)
    if any(p not in PASS_TYPES for p in selected): raise ValueError('Unknown parse pass')
    options=(source.get('selectors') or {}).get('parse_passes') or {}
    pages={}; errors={}; report={}; started=now();rendered_count=0;stop_fetching=False
    root=clean_url(source['url'])
    if not root: raise ValueError('Source must be HTTPS')
    fetcher.deadline=time.monotonic()+150
    ai.deadline=fetcher.deadline
    max_pages=max(1,min(int((source.get('selectors') or {}).get('max_pages',8)),20))
    live={kind:dict(status='running',started_at=now(),results=[],errors=[],
        progress_stage='collecting',pages_done=0,pages_total=max_pages,heartbeat_at=now()) for kind in selected}
    def publish_collection():
        for kind,item in live.items():
            item.update(pages_done=len(set(pages)|set(errors)),heartbeat_at=now())
            if on_result:on_result(kind,dict(item))
    publish_collection()
    def load(url):
        nonlocal rendered_count,stop_fetching
        if url in pages or url in errors:return
        try:
            if not same_site(url,root): raise ValueError('Pass page must belong to the organizer website')
            code,body,headers=fetcher.fetch(url)
            if code==304: raise ValueError('No page body; retry a full refresh')
            if (source.get('selectors') or {}).get('render',True) and rendered_count<2 and b'<script' in body.lower() and not parse_page(body,source,url):
                from .render import render_html
                rendered_count+=1
                try:
                    rendered=render_html(fetcher,url,body)
                    if rendered:body=rendered
                except Exception as exc:
                    # Rendering is an enhancement; keep optional facts from the
                    # static body even when browser startup/navigation fails.
                    errors[url]='Rendering failed: '+str(exc)[:400]
            pages[url]=body
        except Exception as exc:
            errors[url]=str(exc)[:500]
            from .fetch import UnsafeDestination
            if isinstance(exc,UnsafeDestination) or getattr(getattr(exc,'response',None),'status_code',None)==429:
                stop_fetching=True
        publish_collection()
    from .manus_research import research_job,add_results as add_research
    research=None
    try:research=research_job(source,{},enqueue_missing=False)
    except Exception:pass  # Optional provider queue cannot break deterministic extraction.
    load(root)
    # Shared crawl snapshot, fetched once. Do not stop after finding dates:
    # linked venue/about/ticket pages can supply the other passes.
    queue=discover_links(pages[root],root,root,website_only=True) if root in pages else []
    from .recovery import fallback_urls
    for target in fallback_urls(source):
        if target not in queue:queue.append(target)
    if research:
        for row in (research.get('result') or {}).get('evidence',[]):
            if row['page_url'] not in queue:queue.insert(0,row['page_url'])
    for kind in selected:
        cfg=options.get(kind) or {}
        for url in cfg.get('page_urls') or []:
            target=clean_url(url)
            if target and target not in queue:queue.insert(0,target)
    while queue and not stop_fetching and len(set(pages)|set(errors))<max_pages:
        url=queue.pop(0)
        if url in pages or url in errors:continue
        load(url)
    for kind in selected:
        cfg=options.get(kind) or {}; t=time.monotonic()
        item={**live[kind],'progress_stage':'extracting','pages_done':0,'pages_total':0,'heartbeat_at':now()}
        if on_result:on_result(kind,item)
        try:
            if cfg.get('enabled',True) is False:item['status']='disabled'
            else:
                targets=list(dict.fromkeys(clean_url(u) for u in cfg.get('page_urls',[]) if clean_url(u))) or list(dict.fromkeys([*pages,*errors]))
                chosen={u:pages[u] for u in targets if u in pages}
                item['pages_total']=len(chosen)
                item['errors']=[{'page_url':u,'message':errors.get(u,'Page was not fetched within the page budget')} for u in targets if u not in pages]
                def page_done():
                    item.update(pages_done=item['pages_done']+1,heartbeat_at=now())
                    if on_result:on_result(kind,dict(item))
                item['results']=extract_pass(kind,chosen,source,cfg,on_page=page_done)
                item['results']=add_research(kind,pages,source,item['results'],research)
                item['results'],_=enrich_pass(kind,chosen,source,cfg,item['results'],ai)
                if kind=='event_date' and len(item['results'])==1:
                    source={**source,'_ai_event_identity':item['results'][0]}
                item['ai_enrichment']={k:v for k,v in ai.audit.items() if k!='cache'}
                item['status']='partial' if item['results'] and item['errors'] else 'success' if item['results'] else 'failed' if item['errors'] else 'missing'
        except Exception as exc:
            item.update(status='failed',errors=[{'message':str(exc)[:500]}])
        item.update(completed_at=now(),duration_ms=round((time.monotonic()-t)*1000),progress_stage='complete',heartbeat_at=now())
        report[kind]=item
        if on_result:on_result(kind,item)
    try:
        if not research:research=research_job(source,pages,report)
        if research:ai.audit['research']={k:research.get(k) for k in ('id','status','reason')}
    except Exception:ai.audit['research']={'status':'unavailable'}
    return report,dict(mode='four_pass',started_at=started,pages=list(pages),pages_checked=len(pages),
                       errors=[{'url':u,'message':m} for u,m in errors.items()],parse_passes=report,ai_enrichment=ai.audit)

def assemble(report, source):
    """Join independent facts only when there is one matching edition candidate."""
    records=[]
    date_items=report.get('event_date',{}).get('results',[])
    for item in date_items:
        dates=item['fields'];start=dates.get('start_date')
        if not item.get('title') or not start:continue
        record=dict(title=item['title'],event_url=item['event_url'],official_url=item['event_url'],
                    source_url=item['evidence']['page_url'],format='unknown',categories=[],**dates)
        evidence={'event_date':item['evidence']};conflicts=[]
        for kind in PASS_TYPES[1:]:
            candidates=[]
            for extra in report.get(kind,{}).get('results',[]):
                same_name=identity(extra.get('title'))==identity(item['title'])
                same_url=extra.get('event_url')==item['event_url']
                dedicated=(source.get('selectors') or {}).get('single_event') and len({(d['start_date'],d['fields'].get('end_date')) for d in date_items})==1
                if extra.get('start_date') and extra['start_date']!=start:continue
                ambiguous=not extra.get('start_date') and len({d['start_date'] for d in date_items
                    if identity(d.get('title'))==identity(item['title']) and d.get('event_url')==item['event_url']})>1
                if not ambiguous and (same_name and same_url or dedicated):candidates.append(extra)
            for field in FIELDS[kind]:
                specified=[c for c in candidates if c['evidence']['method']=='admin_selectors' and field in c['fields']]
                values=[c['fields'][field] for c in (specified or candidates) if field in c['fields']]
                unique={json.dumps(v,sort_keys=True) for v in values}
                if len(unique)==1:record[field]=values[0]
                elif len(unique)>1:
                    conflicts.append(field)
                    report[kind]['status']='partial'
                    report[kind].setdefault('errors',[]).append({'message':'Conflicting evidence for '+field+'; field left unknown'})
            if candidates:evidence[kind]=[c['evidence'] for c in candidates]
        metadata={'version':1,'evidence':{},'fields':{},'suggestions':{},'warnings':[]}
        for extra in [x for kind in PASS_TYPES[1:] for x in report.get(kind,{}).get('results',[])]:
            if extra.get('event_url')!=item['event_url'] or extra.get('start_date') not in (None,start):continue
            meta=extra.get('evidence',{}).get('metadata',{})
            for key,proof in meta.get('evidence',{}).items():
                if record.get(key)==proof['value']:metadata['evidence'][key]=proof;metadata['fields'][key]=proof['value']
            metadata['suggestions'].update(meta.get('suggestions',{}));metadata['warnings']+=meta.get('warnings',[])
        record['extraction']={'method':'four_pass','passes':evidence,'conflicting_fields':conflicts,'metadata_enrichment':metadata}
        try:records.append(_finish(record,source['publisher'],dates['source_record_id']))
        except (ValueError,KeyError):continue
    # An edition's disagreement about dates needs review; do not import two dates
    # under one identity and let last-write-win choose the event dates.
    grouped={}
    for record in records:grouped.setdefault(record['source_record_id'],[]).append(record)
    output=[]
    for items in grouped.values():
        if len({(r['start_date'],r['end_date']) for r in items})==1:output.append(items[0])
        else:
            report['event_date']['status']='partial'
            report['event_date'].setdefault('errors',[]).append({'message':'Conflicting dates for one event identity; no event assembled'})
    return output

def retain_known_optional(records, observations, publisher):
    """A failed/missing optional extraction must not erase a previously known fact."""
    for record in records:
        old=[o['payload'] for o in observations if o.get('source_record_id')==record['source_record_id']
             and o['payload'].get('start_date')==record['start_date'] and o['payload'].get('end_date')==record['end_date']]
        if len(old)!=1:continue
        retained=[]
        for fields in list(FIELDS.values())[1:]:
            for field in fields:
                if field in record['extraction']['conflicting_fields']:continue
                if record.get(field) in (None,'',[], 'unknown') and old[0].get(field) not in (None,'',[],'unknown'):
                    record[field]=old[0][field];retained.append(field)
        record['extraction']['retained_previous_fields']=retained
        _finish(record,publisher,record['source_record_id'])
    return records
