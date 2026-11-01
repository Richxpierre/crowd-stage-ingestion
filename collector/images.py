"""Pick a real event image URL; never download images or invent a fallback."""
from __future__ import annotations
import ipaddress
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from bs4 import BeautifulSoup


def image_url(value, page_url):
    if not isinstance(value,str) or not value.strip() or len(value)>4000:
        return None
    target=urljoin(page_url,value.strip())
    try:
        p=urlsplit(target)
        host=(p.hostname or '').lower().rstrip('.')
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443):
            return None
        if '.' not in host or host.endswith(('.local','.localhost','.internal','.test','.invalid')):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        return urlunsplit((p.scheme,p.netloc,p.path,p.query,''))
    except ValueError:
        return None


def _values(value):
    if isinstance(value,list):
        for item in value: yield from _values(item)
    elif isinstance(value,dict):
        yield from _values(value.get('contentUrl') or value.get('url') or value.get('@id'))
    elif isinstance(value,str):
        yield value


def _img_src(node):
    srcset=node.get('srcset') or node.get('data-srcset') or ''
    candidates=[]
    for entry in srcset.split(','):
        parts=entry.strip().split()
        if parts:
            size=re.match(r'(\d+(?:\.\d+)?)[wx]$',parts[-1]) if len(parts)>1 else None
            candidates.append((float(size[1]) if size else 0,parts[0]))
    if candidates: return max(candidates)[1]
    for attr in ('data-src','data-lazy-src','src'):
        value=node.get(attr)
        if value and not value.startswith('data:'):
            return value
    return None


def _banner(soup,page_url,card=False):
    candidates=[]
    for node in soup.find_all(['img','source']):
        value=_img_src(node)
        url=image_url(value,page_url)
        if not url: continue
        context=' '.join([str(node.get('alt','')),str(node.get('class','')),str(node.get('id','')),url]).lower()
        if re.search(r'logo|sponsor|avatar|icon|tracking|pixel|badge',context): continue
        width=int(str(node.get('width','0'))) if str(node.get('width','0')).isdigit() else 0
        height=int(str(node.get('height','0'))) if str(node.get('height','0')).isdigit() else 0
        if (width and width<300) or (height and height<120): continue
        semantic=bool(re.search(r'hero|banner|cover|header|key.?visual',context))
        if card or semantic or (width>=600 and height>=200 and width/height>=1.4):
            candidates.append((int(semantic)*10000000+width*height,url))
    for node in soup.select('[style]'):
        context=' '.join([str(node.get('class','')),str(node.get('id',''))]).lower()
        if not re.search(r'hero|banner|cover',context): continue
        match=re.search(r'background(?:-image)?\s*:[^;]*url\([\'"]?([^\)\'\"]+)',node['style'],re.I)
        url=image_url(match[1],page_url) if match else None
        if url: candidates.append((10000000,url))
    return max(candidates)[1] if candidates else None


def choose_image(body_or_soup,page_url,event_image=None,card=None,selectors=None):
    soup=BeautifulSoup(body_or_soup,'html.parser') if isinstance(body_or_soup,(bytes,str)) else body_or_soup
    def result(value,method):
        url=image_url(value,page_url)
        return {'cover_url':url,'image_source_url':page_url,'image_method':method} if url else None
    css=(selectors or {}).get('cover_image')
    if isinstance(css,str) and css:
        node=soup.select_one(css)
        selected=result(node.get('content') or _img_src(node) or node.get('href'),'selector') if node else None
        if selected: return selected
    for value in _values(event_image):
        selected=result(value,'jsonld_event')
        if selected: return selected
    if card is not None:
        selected=result(_banner(card,page_url,card=True),'event_card')
        if selected: return selected
    for key in ('og:image:secure_url','og:image','twitter:image','twitter:image:src'):
        for node in soup.find_all('meta'):
            if str(node.get('property') or node.get('name') or '').lower()==key:
                selected=result(node.get('content'), 'og_image' if key.startswith('og:') else 'twitter_image')
                if selected: return selected
    for node in soup.find_all('link',rel='image_src'):
        selected=result(node.get('href'),'link_image')
        if selected: return selected
    return result(_banner(soup,page_url),'hero_banner') or {}


def enrich_missing_images(fetcher,source,records):
    """Calendar/custom parsers may link to a dedicated page without embedding an image."""
    from .automatic import relevant_url
    from .parsers import _finish
    cache={}
    for record in records:
        url=record.get('event_url')
        if record.get('cover_url') or not url or not relevant_url(url,source['url']): continue
        if url not in cache:
            if len(cache)>=5: continue
            cache[url]={}
            try:
                _,body,headers=fetcher.fetch(url)
                cache[url]=choose_image(body,headers.get('X-CrowdStage-Final-URL',url),selectors=source.get('selectors'))
            except Exception:
                continue  # An image lookup must not discard a valid conference.
        record.update(cache[url])
        _finish(record,source['publisher'],record['source_record_id'])
    return records
