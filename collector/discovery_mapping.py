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
    soup=BeautifulSoup(page.get('html',''),'html.parser');rows=[]
    for a in soup.select('a[href]')[:3000]:
        url=normalized_link(a.get('href'),page['url'])
        label=a.get_text(' ',strip=True) or a.get('aria-label','') or a.get('title','')
        if not url or url==page['url'] or ASSET.search(url) or UNRELATED.search(label):continue
        if not same_owner(url,seed) and excluded(url):continue
        classes=[c for c in a.get('class',[]) if re.fullmatch(r'[A-Za-z_][\w-]{1,60}',c)]
        # Class selectors generalize across editions; href selectors remain literal evidence.
        selector='a.'+'.'.join(classes[:3]) if classes else 'a[href='+json.dumps(a['href'])+']'
        parent_classes=[c for c in (a.parent.get('class',[]) if a.parent else []) if re.fullmatch(r'[A-Za-z_][\w-]{1,60}',c)]
        if not classes and same_owner(url,seed):
            path=urlsplit(a['href']).path
            prefix=path.rsplit('/',1)[0]+'/'
            if prefix.startswith('/') and prefix!='/' and len(prefix)>2:selector='a[href^='+json.dumps(prefix)+']'
            elif parent_classes:selector='.'+'.'.join(parent_classes[:2])+' a[href]'
        scope=next((p for p in a.parents if p.get('id') and re.search(r'(?:^|[-_])(?:main|content|events?|directory|listing|results)(?:$|[-_])',p['id'],re.I) and not re.search(r'\d{3,}',p['id'])),None)
        if scope and re.fullmatch(r'[A-Za-z_][\w-]{0,60}',scope.get('id','')):selector='#'+scope['id']+' '+selector
        hint='website' if not same_owner(url,seed) and re.search(r'website|homepage|official|visit.*conference',label,re.I) else 'pagination' if re.fullmatch(r'next(?: page)?|[›»→]',label,re.I) or 'next' in a.get('rel',[]) else 'follow' if same_owner(url,seed) and re.search(r'conference|event|summit|expo|directory|speaker|/e/',urlsplit(url).path+' '+label,re.I) else ''
        website_parent=next((c for c in parent_classes if re.search(r'website|webpage|official|organizer',c,re.I)),None)
        if hint=='website' and website_parent:
            selector=(('#'+scope['id']+' ') if scope else '')+'.'+website_parent+' a[href]'
        parent_class=' '.join(a.parent.get('class',[])) if a.parent else ''
        if not hint and same_owner(url,seed) and re.search(r'directory.*title|post-link|event.*title',parent_class+' '+' '.join(classes),re.I):hint='follow'
        navigation=bool(a.find_parent(['nav','header','footer']) or re.search(r'nav|footer|sidebar|reminder',str(scope.get('id','')) if scope else '',re.I))
        if hint=='website' and navigation:continue
        path=urlsplit(url).path
        detail=bool(re.search(r'/conference/|/events?/[^/]+|/e/[^/]+',path,re.I) or re.search(r'directory.*title|event.*title',parent_class+' '+' '.join(classes),re.I))
        priority=0 if hint=='website' else 1 if hint=='pagination' else 2 if detail and not navigation else 3 if hint=='follow' and not navigation else 4 if re.search(r'conferences|speakers-directory',path,re.I) else 5 if hint else 6
        rows.append({'url':url,'selector':selector,'label':label[:100],'hint':hint,
                     'value':path[:80],'context':('internal ' if same_owner(url,seed) else 'external ')+parent_class[:60], '_priority':priority})
    rows.sort(key=lambda n:n['_priority'])
    # A plain "More information" link can precede an explicit Website button with
    # the same URL. Deduplicate after ranking so the explicit button wins.
    unique={}
    for n in rows:unique.setdefault(n['url'],n)
    rows=list(unique.values())
    # Use the strongest observed link family rather than combining unrelated navigation.
    best=min((n['_priority'] for n in rows if n['hint']=='follow'),default=99)
    for n in rows:
        if n['hint']=='follow' and n['_priority']>best:n['hint']=''
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
