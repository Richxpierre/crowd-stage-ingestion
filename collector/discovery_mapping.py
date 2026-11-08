"""Declarative mappings built from observed anchors, never executable model output."""
from fnmatch import fnmatchcase
import json
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from .crawler_reader import same_owner

ASSET=re.compile(r'\.(?:png|jpe?g|gif|webp|svg|pdf|css|js|zip|ics)(?:$|[?])',re.I)
UNRELATED=re.compile(r'\b(?:login|sign in|privacy|terms|sponsor|advertis|facebook|linkedin|twitter)\b',re.I)


def observed_nodes(page,seed):
    from .discovery import normalized_link,excluded
    soup=BeautifulSoup(page.get('html',''),'html.parser');rows=[];seen=set()
    for a in soup.select('a[href]')[:3000]:
        url=normalized_link(a.get('href'),page['url'])
        label=a.get_text(' ',strip=True) or a.get('aria-label','') or a.get('title','')
        if not url or url in seen or url==page['url'] or ASSET.search(url) or UNRELATED.search(label):continue
        if not same_owner(url,seed) and excluded(url):continue
        seen.add(url)
        classes=[c for c in a.get('class',[]) if re.fullmatch(r'[A-Za-z_][\w-]{1,60}',c)]
        # Class selectors generalize across editions; href selectors remain literal evidence.
        selector='a.'+'.'.join(classes[:3]) if classes else 'a[href='+json.dumps(a['href'])+']'
        parent_classes=[c for c in (a.parent.get('class',[]) if a.parent else []) if re.fullmatch(r'[A-Za-z_][\w-]{1,60}',c)]
        if not classes and same_owner(url,seed):
            path=urlsplit(a['href']).path
            prefix=path.rsplit('/',1)[0]+'/'
            if prefix.startswith('/') and prefix!='/' and len(prefix)>2:selector='a[href^='+json.dumps(prefix)+']'
            elif parent_classes:selector='.'+'.'.join(parent_classes[:2])+' a[href]'
        scope=a.find_parent(id=True)
        if scope and re.fullmatch(r'[A-Za-z_][\w-]{0,60}',scope.get('id','')):selector='#'+scope['id']+' '+selector
        hint='website' if not same_owner(url,seed) and re.search(r'website|homepage|official|visit.*conference',label,re.I) else 'pagination' if re.fullmatch(r'next(?: page)?|[›»→]',label,re.I) or 'next' in a.get('rel',[]) else 'follow' if same_owner(url,seed) and re.search(r'conference|event|summit|expo|directory|speaker|/e/',urlsplit(url).path+' '+label,re.I) else ''
        parent_class=' '.join(a.parent.get('class',[])) if a.parent else ''
        if not hint and same_owner(url,seed) and re.search(r'directory.*title|post-link|event.*title',parent_class+' '+' '.join(classes),re.I):hint='follow'
        rows.append({'url':url,'selector':selector,'label':label[:100],'hint':hint,
                     'value':urlsplit(url).path[:80],'context':('internal ' if same_owner(url,seed) else 'external ')+parent_class[:60]})
    rows.sort(key=lambda n:0 if n['hint']=='website' else 1 if n['hint']=='follow' else 2 if n['hint']=='pagination' else 3)
    return [dict(n,id=i) for i,n in enumerate(rows[:20])]


def path_pattern(url):
    path=urlsplit(url).path or '/'
    parts=path.rstrip('/').split('/')
    if len(parts)>2 and parts[-1] and not parts[-1].isdigit():return '/'.join(parts[:-1])+'/*'
    return path


def mapped_links(page,seed,steps):
    from .discovery import normalized_link,excluded,OFFICIAL_HINT
    if not isinstance(steps,list) or len(steps)>12:raise ValueError('Invalid discovery steps')
    soup=BeautifulSoup(page.get('html',''),'html.parser');inside=[];outside=[]
    for step in steps:
        if not isinstance(step,dict):raise ValueError('Invalid discovery step')
        pattern=step.get('match_path','')
        if not isinstance(pattern,str) or len(pattern)>250 or not pattern.startswith('/'):raise ValueError('Invalid discovery path')
        if not fnmatchcase(urlsplit(page['url']).path or '/',pattern):continue
        for field in ('follow_selector','pagination_selector','website_selector'):
            selector=step.get(field)
            if not selector:continue
            if not isinstance(selector,str) or len(selector)>500:raise ValueError('Invalid link selector')
            for a in soup.select(selector)[:1000]:
                url=normalized_link(a.get('href'),page['url'])
                if not url or ASSET.search(url) or url==page['url'] or UNRELATED.search(a.get_text(' ',strip=True)):continue
                if field=='website_selector':
                    if step.get('website_require_label') is True and not OFFICIAL_HINT.search(a.get_text(' ',strip=True)):continue
                    if not same_owner(url,seed) and not excluded(url):outside.append({'url':url,'label':a.get_text(' ',strip=True)[:300]})
                elif same_owner(url,seed):inside.append(url)
    return list(dict.fromkeys(inside)),list({n['url']:n for n in outside}.values())
