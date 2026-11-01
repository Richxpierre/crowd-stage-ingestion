"""RSS/Atom adapters: explicit event dates only, never publication timestamps."""
from __future__ import annotations
import json
import re
from urllib.parse import urljoin
from xml.etree import ElementTree as ET
from .parsers import parse_jsonld, _finish, clean_url,plain


def feed_body(body):
    return bool(re.search(br'<(?:\w+:)?(?:rss|feed|RDF)(?:\s|>)',body[:8192],re.I))


def feed_links(body,page_url):
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b',body,re.I):return []
    try:root=ET.fromstring(body)
    except ET.ParseError:return []
    links=[]
    for el in root.iter():
        if el.tag.rsplit('}',1)[-1].lower()=='link' and el.get('rel','alternate')=='alternate':
            url=clean_url(urljoin(page_url,el.get('href') or el.text or ''))
            if url:links.append(url)
        if len(links)>=1000:break
    return links


def parse_feed(body,source):
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b',body,re.I):
        raise ValueError('Feed contains unsupported XML entities')
    root=ET.fromstring(body)
    local=lambda tag: tag.rsplit('}',1)[-1].lower()
    if local(root.tag) not in ('rss','feed','rdf'):
        return []
    records=[]
    for item in (el for el in root.iter() if local(el.tag) in ('item','entry')):
        def value(*names):
            return next((''.join(el.itertext()).strip() for el in item.iter()
                         if local(el.tag) in names and ''.join(el.itertext()).strip()),None)
        links=[el for el in item.iter() if local(el.tag)=='link']
        page=next((clean_url(urljoin(source['url'],el.get('href') or el.text or ''))
                   for el in links if el.get('rel','alternate')=='alternate'),None)
        fragment=value('description','summary','content','encoded') or ''
        rows=parse_jsonld(fragment.encode('utf-8'),source,page or source['url']) if fragment.strip() else []
        start=value('startdate','start_date','dtstart')
        if not rows and start:
            node={'@type':'Event','name':value('title','name'),'startDate':start,
                  'endDate':value('enddate','end_date','dtend') or start,
                  'description':fragment,'url':page,'location':value('location','venue')}
            rows=parse_jsonld(('<script type="application/ld+json">'+json.dumps(node).replace('</','<\\/')+'</script>').encode(),source,source['url'])
        uid=plain(value('guid','id'),500)
        for record in rows:
            record['source_url']=source['url']
            if page and record.get('event_url')==source['url']:
                record['event_url']=record['official_url']=page
            records.append(_finish(record,source['publisher'],uid or record['source_record_id']))
        if len(records)>=1000:break
    return records[:1000]
