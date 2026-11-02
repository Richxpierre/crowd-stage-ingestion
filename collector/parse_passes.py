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
    'event_details': ('summary','categories','cover_url','image_method','image_source_url','registration_url','cfp_url','cfp_deadline'),
}

def now(): return datetime.now(timezone.utc).isoformat()
def identity(title): return re.sub(r'\W+', ' ', title or '').strip().casefold()
def canonical_page(url):
    p=urlsplit(url)
    path=re.sub(r'/(?:index|default)\.(?:html?|php|aspx?)$', '/', p.path,flags=re.I)
    return urlunsplit((p.scheme,p.netloc,path,p.query,''))

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

def extract_pass(kind, pages, source, config, on_page=None):
    results=[]
    for url,body in pages.items():
        digest=hashlib.sha256(body).hexdigest()
        def add(title,start,fields,method,event_url=None):
            fields=nonempty(fields)
            original=event_url or url;canonical=canonical_page(original)
            if 'source_record_id' in fields and fields['source_record_id'].startswith(original+'|'):
                fields['source_record_id']=canonical+fields['source_record_id'][len(original):]
            if fields: results.append(dict(title=title,start_date=start,event_url=canonical,
                fields=fields,evidence={'page_url':url,'snapshot_sha256':digest,'method':method}))
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
                add(plain(node.get('name'),240),date_part(node.get('startDate')),
                    node_fields(kind,node,body,url,source),'schema_event',clean_url(node.get('url')))
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
        if on_page:on_page()
    return results

def collect_passes(fetcher, source, selected=None, on_result=None):
    selected=tuple(selected or PASS_TYPES)
    if any(p not in PASS_TYPES for p in selected): raise ValueError('Unknown parse pass')
    options=(source.get('selectors') or {}).get('parse_passes') or {}
    pages={}; errors={}; report={}; started=now();rendered_count=0;stop_fetching=False
    root=clean_url(source['url'])
    if not root: raise ValueError('Source must be HTTPS')
    fetcher.deadline=time.monotonic()+150
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
    load(root)
    # Shared crawl snapshot, fetched once. Do not stop after finding dates:
    # linked venue/about/ticket pages can supply the other passes.
    queue=discover_links(pages[root],root,root,website_only=True) if root in pages else []
    from .recovery import fallback_urls
    for target in fallback_urls(source):
        if target not in queue:queue.append(target)
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
                targets=cfg.get('page_urls') or list(dict.fromkeys([*pages,*errors]))
                chosen={u:pages[u] for u in targets if u in pages}
                item['pages_total']=len(chosen)
                item['errors']=[{'page_url':u,'message':errors.get(u,'Page was not fetched within the page budget')} for u in targets if u not in pages]
                def page_done():
                    item.update(pages_done=item['pages_done']+1,heartbeat_at=now())
                    if on_result:on_result(kind,dict(item))
                item['results']=extract_pass(kind,chosen,source,cfg,on_page=page_done)
                item['status']='partial' if item['results'] and item['errors'] else 'success' if item['results'] else 'failed' if item['errors'] else 'missing'
        except Exception as exc:
            item.update(status='failed',errors=[{'message':str(exc)[:500]}])
        item.update(completed_at=now(),duration_ms=round((time.monotonic()-t)*1000),progress_stage='complete',heartbeat_at=now())
        report[kind]=item
        if on_result:on_result(kind,item)
    return report,dict(mode='four_pass',started_at=started,pages=list(pages),pages_checked=len(pages),
                       errors=[{'url':u,'message':m} for u,m in errors.items()],parse_passes=report)

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
        record['extraction']={'method':'four_pass','passes':evidence,'conflicting_fields':conflicts}
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
