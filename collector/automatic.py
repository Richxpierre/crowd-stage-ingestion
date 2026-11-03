"""Bounded, evidence-based extraction from an organizer URL, without site mappings.

Prefer explicit Event data and calendar feeds; then inspect dated HTML sections.
Unknown location/format/prices remain unknown. Every result retains its evidence.
"""
from __future__ import annotations

from datetime import date
import json
import re
import time
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from .parsers import _finish, clean_url, parse_ics, parse_jsonld, plain
from .official import parse_official_html
from .images import choose_image

MONTHS = {name.lower(): n for n, names in enumerate([
    ('January','Jan'),('February','Feb'),('March','Mar'),('April','Apr'),
    ('May',),('June','Jun'),('July','Jul'),('August','Aug'),
    ('September','Sep','Sept'),('October','Oct'),('November','Nov'),('December','Dec')
], 1) for name in names}
M = '(?:' + '|'.join(sorted(MONTHS, key=len, reverse=True)) + r')\.?'
D = r'\d{1,2}(?:st|nd|rd|th)?'
DATE_PATTERNS = [
    re.compile(fr'(?P<d>{D})\s+(?P<m>{M})\s+(?P<y1>20\d{{2}})\s*(?:-|–|—|to)\s*(?P<d2>{D})\s+(?P<m2>{M})\s+(?P<y>20\d{{2}})', re.I),
    re.compile(fr'(?P<m>{M})\s+(?P<d>{D})(?:\s*,?\s*(?P<y1>20\d{{2}}))?\s*(?:-|–|—|to|through)\s*(?:(?P<m2>{M})\s+)?(?P<d2>{D})\s*,?\s*(?P<y>20\d{{2}})', re.I),
    re.compile(fr'(?P<d>{D})\s*(?:-|–|—|to)\s*(?P<d2>{D})\s+(?P<m>{M})\s*,?\s*(?P<y>20\d{{2}})', re.I),
    re.compile(fr'(?P<m>{M})\s+(?P<d>{D})\s*,?\s*(?P<y>20\d{{2}})', re.I),
    re.compile(fr'(?P<d>{D})\s+(?P<m>{M})\s*,?\s*(?P<y>20\d{{2}})', re.I),
]
SKIP_PATH = re.compile(r'/(?:schedule|sessions?|speakers?|news|blog|press|privacy|legal|terms|exit|login|signup|sponsors?|exhibitors?)(?:/|$)', re.I)
EVENT_WORD = re.compile(r'conference|congress|symposium|convention|\bexpo\b|summit|trade.?show|festival|con\b|\bevent|jahrestagung', re.I)


def date_range(text):
    text = re.sub(r'\s+', ' ', text or '').strip()
    text = re.sub(r'\bFeber\b','February',text,flags=re.I)
    text = re.sub(r'\b(?:Mo|Di|Mi|Do|Fr|Sa|So)\.\s*,?\s*','',text)
    text = re.sub(fr'(\d{{1,2}})\.\s+(?={M})',r'\1 ',text,flags=re.I)
    # ISO ranges, including time datetime attributes; reject ambiguous numeric dates.
    iso = re.findall(r'\b20\d{2}-\d{2}-\d{2}(?!\d)', text)
    if iso:
        try:
            start, end = date.fromisoformat(iso[0]), date.fromisoformat(iso[1] if len(iso)>1 else iso[0])
            return (start.isoformat(), end.isoformat()) if 0 <= (end-start).days <= 31 else None
        except ValueError:
            return None
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        g = match.groupdict()
        num = lambda value: int(re.match(r'\d+', value)[0])
        try:
            start = date(int(g.get('y1') or g['y']), MONTHS[g['m'].lower().rstrip('.')], num(g['d']))
            end = date(int(g['y']), MONTHS[(g.get('m2') or g['m']).lower().rstrip('.')], num(g.get('d2') or g['d']))
            if 0 <= (end-start).days <= 31:
                return start.isoformat(), end.isoformat()
        except ValueError:
            pass
    return None


def same_site(url, root):
    p, base = urlsplit(url), urlsplit(root)
    if not (p.scheme == 'https' and not p.username and not p.password and p.port in (None,443) and p.hostname):return False
    a,b=p.hostname.lower().rstrip('.'),(base.hostname or '').lower().rstrip('.')
    if a==b:return True
    from .recovery import EXTRACT
    left,right=EXTRACT(a),EXTRACT(b)
    return bool(left.suffix and left.top_domain_under_public_suffix and left.top_domain_under_public_suffix==right.top_domain_under_public_suffix)


def relevant_url(url, root):
    p = urlsplit(url)
    return same_site(url, root) and not p.query and not SKIP_PATH.search(p.path) and not re.search(
        r'\.(?:jpg|png|svg|webp|pdf|zip|js|css|woff2?|mp4)$', p.path, re.I)


def _brand(soup, source):
    meta = soup.find('meta', property='og:site_name') or soup.find('meta', property='og:title')
    title = meta.get('content') if meta else soup.title.get_text(' ',strip=True) if soup.title else source['name']
    return plain(re.split(r'\s+[|–—]\s+', title or source['name'])[0], 120)


def _evidence(record, source, method, text):
    record['extraction'] = {'method':method, 'evidence':plain(text,1500), 'page_url':record['source_url']}
    return _finish(record, source['publisher'], record['source_record_id'])


def parse_sections(body, source, page_url):
    soup = BeautifulSoup(body, 'html.parser')
    for node in soup(['script','style','nav','footer','header']):
        node.decompose()
    brand = _brand(BeautifulSoup(body,'html.parser'),source)
    if not EVENT_WORD.search(brand + ' ' + (soup.h1.get_text(' ',strip=True) if soup.h1 else '')):
        return []
    seeds = list(soup.find_all('time')) + list(soup.select('[itemprop="startDate"]'))
    # Common landing-page layouts have dates in paragraphs/spans rather than <time>.
    seeds += [n for n in soup.find_all(['p','span','h1','h2','h3','h4'])
              if len(n.get_text(' ',strip=True)) < 200 and date_range(n.get_text(' ',strip=True))]
    records = []
    for seed in seeds[:100]:
        dates = date_range(seed.get('datetime') or seed.get('content') or seed.get_text(' ',strip=True))
        if not dates:
            continue
        context = seed
        for _ in range(5):
            if context.find(['h1','h2','h3','h4']) and (context.find('address') or context.select_one('[itemprop="location"]')):
                break
            if not context.parent or context.parent.name in ('body','html'):
                break
            context = context.parent
        text = context.get_text(' ',strip=True)
        # Never merge dates from multiple cards into one event.
        ranges = {date_range(t.get('datetime') or t.get_text(' ',strip=True)) for t in context.find_all('time')}
        ranges.discard(None)
        if len(ranges)>1 or len(text)>5000:
            continue
        heading = context.find(['h1','h2','h3','h4'])
        location = context.find('address') or context.select_one('[itemprop="location"]')
        if not heading or not location:
            continue
        title = plain(heading.get_text(' ',strip=True),240)
        loc = plain(location.get_text(' ',strip=True),240)
        if not title or not loc or date_range(title):
            continue
        if not EVENT_WORD.search(title):
            title = f'{brand} {title}'
        # Prefer the surrounding event card to find its dedicated event URL.
        link_context = context
        if not context.find('a',href=True) and context.parent and len(context.parent.get_text(' ',strip=True)) < 5000:
            link_context = context.parent
        links = [clean_url(urljoin(page_url,a['href'])) for a in link_context.find_all('a',href=True)]
        links = [u for u in links if u and relevant_url(u,source['url']) and urlsplit(u).path != '/']
        event_url = links[0] if links else page_url
        # The text is preserved as a venue. Only split city/country when explicitly comma-separated.
        parts = [p.strip() for p in loc.split(',')]
        city = parts[0] if len(parts)==2 else None
        country = parts[1] if len(parts)==2 else None
        record = dict(title=title,start_date=dates[0],end_date=dates[1],timezone=None,
            venue=loc,city=city,country=country,region=None,format='unknown',categories=[],
            summary=plain((context.find('p').get_text(' ',strip=True) if context.find('p') else None)),
            event_url=event_url,official_url=event_url,registration_url=None,cfp_url=None,
            cfp_deadline=None,status='upcoming',source_url=page_url)
        card=context if context.name in ('article','figure') else context.find_parent(['article','figure']) or link_context
        record.update(choose_image(soup,page_url,card=card,selectors=source.get('selectors')))
        identity = f'{event_url}|{dates[0][:4]}|{title.lower()}'
        try:
            record = _finish(record,source['publisher'],identity)
            records.append(_evidence(record,source,'dated_html',text))
        except ValueError:
            continue
    return records


def parse_microdata(body, source, page_url):
    soup = BeautifulSoup(body,'html.parser')
    out=[]
    for event in soup.select('[itemscope][itemtype]'):
        if not str(event.get('itemtype','')).endswith('Event'):
            continue
        node={'@type':'Event'}
        for key in ('name','startDate','endDate','description','url'):
            el=event.select_one(f'[itemprop="{key}"]')
            if el:
                node[key]=el.get('content') or el.get('datetime') or el.get('href') or el.get_text(' ',strip=True)
        loc=event.select_one('[itemprop="location"]')
        if loc:
            node['location']=loc.get_text(' ',strip=True)
        image=event.select_one('[itemprop="image"]')
        if image:
            node['image']=image.get('content') or image.get('src') or image.get('href')
        wrapper=('<script type="application/ld+json">'+json.dumps(node).replace('</','<\\/')+'</script>').encode()
        out += parse_jsonld(wrapper,source,page_url)
    return out


def parse_dated_heading(body,source,url):
    """An edition's own H1 can explicitly name both conference and dates.

    Do not require address markup or infer a location from unstructured text.
    Deadlines and catalog headings never qualify as event dates.
    """
    if SKIP_PATH.search(urlsplit(url).path):return []
    soup=BeautifulSoup(body,'html.parser')
    headings=soup.find_all('h1')
    if not headings:
        # Some page builders use H4 for the full event title/date block.
        headings=[h for h in soup.find_all(['h2','h3','h4'])
                  if date_range(h.get_text(' ',strip=True)) and
                  re.search(r'conference|congress|symposium|convention|summit|expo|trade.?show',h.get_text(' ',strip=True),re.I)]
    if len(headings)!=1:return []
    title=headings[0].get_text(' ',strip=True)
    dates=date_range(title)
    if not dates or len(title)>240 or not EVENT_WORD.search(title) or re.search(r'deadline|submission|call for|early.?bird|registration|calendar|directory|upcoming events|cancelled|canceled',title,re.I):return []
    record=dict(title=title,start_date=dates[0],end_date=dates[1],timezone=None,
        venue=None,city=None,country=None,region=None,format='unknown',categories=[],summary=None,
        event_url=url,official_url=url,registration_url=None,cfp_url=None,cfp_deadline=None,
        status='upcoming',source_url=url)
    record.update(choose_image(soup,url,selectors=source.get('selectors')))
    record=_finish(record,source['publisher'],url+'|'+dates[0][:4])
    return [_evidence(record,source,'dated_event_heading',title)]


def parse_event_hero(body,source,url):
    """A single event heading and an unambiguous nearby date, without invented location."""
    if SKIP_PATH.search(urlsplit(url).path):return []
    soup=BeautifulSoup(body,'html.parser')
    for node in soup.select('nav,header,footer,script,style,aside'):node.decompose()
    headings=soup.find_all('h1')
    if len(headings)!=1:return []
    heading=headings[0];title=plain(heading.get_text(' ',strip=True),240)
    if not EVENT_WORD.search(title) or re.search(r'cancelled|canceled|call for|deadline|registration',title,re.I):return []
    context=heading
    for _ in range(4):
        if not context.parent or context.parent.name in ('body','html'):break
        context=context.parent
        text=context.get_text(' ',strip=True)
        if len(text)>1800 or len(context.find_all('h1'))!=1:break
        # The hero must be free of submission/registration/publication date ambiguity.
        if re.search(r'deadline|submission|registration|published|updated|copyright|call for',text,re.I):continue
        dates=date_range(text)
        if not dates:continue
        # All independently dated short elements must agree or be endpoints of this range.
        observed=[]
        for n in context.find_all(['p','span','time','div']):
            value=n.get('datetime') or n.get('content') or n.get_text(' ',strip=True)
            if len(value)>250:continue
            found=date_range(value)
            if found:observed.append(found)
        if any(d!=dates and not(d[0]==d[1] and d[0] in dates) for d in observed):continue
        record=dict(title=title,start_date=dates[0],end_date=dates[1],timezone=None,
            venue=None,city=None,country=None,region=None,format='unknown',categories=[],summary=None,
            event_url=url,official_url=url,registration_url=None,cfp_url=None,cfp_deadline=None,status='upcoming',source_url=url)
        record.update(choose_image(soup,url,selectors=source.get('selectors')))
        record=_finish(record,source['publisher'],url+'|'+dates[0][:4])
        return [_evidence(record,source,'dated_event_hero',text)]
    return []


def parse_event_prose(body,source,url):
    """Explicit event-date sentences in article content; never infer a year or a venue."""
    soup=BeautifulSoup(body,'html.parser')
    for node in soup.select('nav,header,footer,aside,script,style'):node.decompose()
    scope=soup.find('main') or soup.find('article') or soup
    headings=[h for h in scope.find_all(['h1','h2','h3','h4']) if EVENT_WORD.search(h.get_text(' ',strip=True))
              and not re.search(r'^(?:upcoming|past|our|all)?\s*(?:events|conferences|news|calendar)\s*$',h.get_text(' ',strip=True),re.I)]
    primary=[h for h in headings if h.name=='h1']
    if len(primary)>1:return []
    heading=primary[0] if primary else headings[0] if headings else None
    if not heading:return []
    title=plain(heading.get_text(' ',strip=True),240)
    spans=[];evidence=[]
    for node in scope.find_all(['p','time','h2','h3','h4']):
        text=node.get('datetime') or node.get_text(' ',strip=True)
        if len(text)>1600:continue
        for sentence in re.split(r'(?<=[.!?])\s+',text):
            if re.search(r'deadline|submission|registration|published|copyright|updated|notification|acceptance',sentence,re.I):continue
            # Explicitly tie prose dates to the event, not arbitrary dates elsewhere on a page.
            contextual=EVENT_WORD.search(sentence) and re.search(r'held|take place|takes place|happen|conference\s+on\b|from\b|conference.*\bon\b',sentence,re.I)
            nearby=not SKIP_PATH.search(urlsplit(url).path) and node in heading.find_next_siblings(limit=4) and len(sentence)<250
            if not(contextual or nearby):continue
            dates=date_range(sentence)
            if dates:spans.append(dates);evidence.append(sentence)
    if not spans or len(set(spans))!=1:return []
    start,end=spans[0]
    record=dict(title=title,start_date=start,end_date=end,timezone=None,venue=None,city=None,country=None,region=None,
        format='unknown',categories=[],summary=None,event_url=url,official_url=url,registration_url=None,cfp_url=None,
        cfp_deadline=None,status='upcoming',source_url=url)
    record.update(choose_image(soup,url,selectors=source.get('selectors')))
    return [_evidence(_finish(record,source['publisher'],url+'|'+start[:4]),source,'explicit_event_prose',' | '.join(evidence)[:1200])]


def parse_page(body, source, url):
    from .feeds import feed_body,parse_feed
    if (source.get('selectors') or {}).get('website_only') and (feed_body(body) or body.lstrip().startswith(b'BEGIN:VCALENDAR')):
        return []
    if feed_body(body):
        records,method=parse_feed(body,{**source,'url':url}),'xml_event_feed'
    elif body.lstrip().startswith(b'BEGIN:VCALENDAR'):
        records,method=parse_ics(body,{**source,'url':url}),'ics'
    else:
        records,method=parse_official_html(body,source,url),'structured_event'
        if not records:
            records,method=parse_microdata(body,source,url),'microdata'
        if not records:
            records,method=parse_sections(body,source,url),'dated_html'
        if not records:
            records,method=parse_dated_heading(body,source,url),'dated_event_heading'
        if not records:
            records,method=parse_event_hero(body,source,url),'dated_event_hero'
        if not records:
            records,method=parse_event_prose(body,source,url),'explicit_event_prose'
    return [_evidence(r,source,method,r.get('extraction',{}).get('evidence') or
                      f"{r['title']} | {r['start_date']} – {r['end_date']} | {r.get('venue') or ''}") for r in records]


def is_feed_url(url):
    parsed=urlsplit(url)
    return bool(re.search(r'\.(?:ics|rss|atom|xml|json)$|/(?:feed|feeds|rss|ical|icalendar)(?:/|$)',parsed.path,re.I) or re.search(r'(?:^|&)(?:ical|format)=(?:1|ics|rss|xml|ical)',parsed.query,re.I))


def discover_links(body, url, root, website_only=False):
    from .feeds import feed_body,feed_links
    if feed_body(body):
        if website_only:return []
        return list(dict.fromkeys(target for target in feed_links(body,url)
                                  if relevant_url(target,root) and target!=url))
    soup=BeautifulSoup(body,'html.parser')
    found=[]
    for el in soup.find_all(['a','link']):
        href=el.get('href')
        # A missing href is text, never a relative URL such as /More or /Download.
        if not isinstance(href,str) or not href.strip() or href.startswith(('#','mailto:','tel:','javascript:')):continue
        if website_only and re.search(r'calendar|rss|atom|xml',str(el.get('type','')),re.I):continue
        target=clean_url(urljoin(url,href))
        if not target or not relevant_url(target,root) or target==url:
            continue
        if website_only and is_feed_url(target) and 'sitemap' not in urlsplit(target).path.lower():continue
        label=el.get_text(' ',strip=True)+' '+target
        # Calendar links first; favor edition/visit/event detail pages over general links.
        score=10 if target.lower().endswith('.ics') else 5 if re.search(r'20\d{2}|events?|conference|convention|expo|summit|about|attend|visit|location',label,re.I) else 1
        found.append((score,target))
    return [u for _,u in sorted(set(found),key=lambda pair:(-pair[0],pair[1]))]


def collect_automatic(fetcher, source):
    root=clean_url(source['url'])
    selectors=source.get('selectors') or {}
    website_only=selectors.get('website_only') is True
    if website_only and is_feed_url(root):raise ValueError('Website-only collection needs the main organizer website, not a feed URL')
    limit=max(1,min(int(selectors.get('max_pages',8)),20))
    queue=[root]; visited=set(); records=[]; page_images={}
    fetcher.deadline=min(getattr(fetcher,'deadline',None) or float('inf'),time.monotonic()+120)
    rendered_count=0
    report={'pages':[], 'errors':[], 'methods':[], 'limit':limit,'website_only':website_only}
    # Fetch each page unconditionally: homepage 304 does not mean linked events are unchanged.
    while queue and len(visited)<limit:
        url=queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            code,body,headers=fetcher.fetch(url)
            actual=headers.get('X-CrowdStage-Final-URL',url)
            report['pages'].append(actual)
            page_images[url]=page_images[actual]=choose_image(body,actual,selectors=source.get('selectors'))
            parsed=parse_page(body,source,actual)
            if not parsed and selectors.get('render',True) and rendered_count<2 and b'<script' in body.lower():
                from .render import render_html
                rendered_count+=1
                rendered=render_html(fetcher,actual,body)
                if rendered:
                    parsed=parse_page(rendered,source,actual)
                    if parsed:
                        report['methods'].append('rendered_html')
                        body=rendered
            records.extend(parsed)
            report['methods'].extend(r['extraction']['method'] for r in parsed)
            if len(parsed)==1 and parsed[0]['extraction']['method'] in ('dated_event_hero','dated_event_heading'):
                # A complete single-edition header is enough; do not spend the remaining
                # budget crawling registration, programme and footer pages for the same event.
                break
            for link in discover_links(body,actual,root,website_only=website_only):
                if link not in visited and link not in queue:
                    queue.append(link)
            if len(visited)==1:
                # Only use sitemap when the landing page has no useful links or event facts.
                if not parsed and not queue:
                    queue.append(urljoin(root,'/sitemap.xml'))
            if urlsplit(actual).path.endswith('.xml'):
                tree=ElementTree.fromstring(body)
                for node in tree.iter():
                    if node.tag.rsplit('}',1)[-1]=='loc' and node.text:
                        target=clean_url(node.text)
                        if target and relevant_url(target,root) and target not in visited and target not in queue and (not website_only or not is_feed_url(target) or 'sitemap' in urlsplit(target).path.lower()):
                            queue.append(target)
                            if len(queue)>=50:
                                break
        except Exception as exc:
            from .fetch import UnsafeDestination
            status=getattr(getattr(exc,'response',None),'status_code',None)
            if url==root and (isinstance(exc,(PermissionError,UnsafeDestination)) or status==429):
                raise
            report['errors'].append({'url':url,'message':str(exc)[:250],'http_status':status})
            if status==429:break
    report['methods']=sorted(set(report['methods']))
    report['pages_checked']=len(visited)
    # Repeated cards/details represent one edition, but distinct cities/editions stay separate.
    unique={}
    for r in records:
        # A dedicated event page's share image is more relevant than a catalog's generic image.
        page_image=page_images.get(r.get('event_url'),{})
        if page_image and (not r.get('cover_url') or r.get('image_method') not in ('jsonld_event','event_card','selector')):
            r.update(page_image)
        _finish(r,source['publisher'],r['source_record_id'])
        location=re.sub(r'\W+',' ',r.get('city') or r.get('venue') or '').strip().lower()
        key=(r['start_date'],r['end_date'],location,re.sub(r'\W+',' ',r['title']).strip().lower())
        if key not in unique:
            unique[key]=r
        elif not unique[key].get('cover_url') and r.get('cover_url'):
            unique[key].update({k:r[k] for k in ('cover_url','image_source_url','image_method')})
            _finish(unique[key],source['publisher'],unique[key]['source_record_id'])
    report['events_found']=len(unique)
    return list(unique.values()),report

