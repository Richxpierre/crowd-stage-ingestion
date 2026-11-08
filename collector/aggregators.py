"""Deterministic discovery adapters. These return observed links, never event facts."""
import re
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from .search import result_url
from .crawler_reader import same_owner

class NoOrganizerLink(ValueError):
    def __init__(self,title):
        self.title=title
        super().__init__('Conference listing has no organizer website button')


def task(url,kind='page',**payload):
    return {'url':url,'task_kind':kind,'payload':payload}


def extract(page,adapter,seed):
    from .discovery import excluded
    soup=BeautifulSoup(page['html'],'html.parser');url=page['url'];children=[]
    def link(node):return result_url(urljoin(url,node.get('href','')))
    def internal(node,phase):
        target=link(node)
        if target and target!=url and same_owner(target,seed):children.append(task(target,phase=phase))
    def website(node,method,**evidence):
        target=link(node)
        if not target or same_owner(target,seed) or excluded(target):return
        if re.search(r'\.(?:pdf|png|jpe?g|gif|webp|svg|zip|xml|rss|ics)$',urlsplit(target).path,re.I):return
        label=node.get_text(' ',strip=True)[:240]
        children.append(task(target,'source',listing_url=url,title=label,
            evidence={'method':method,'link_label':label,**evidence}))
    if adapter=='sessionize':
        path=urlsplit(url).path
        if path.rstrip('/')=='/speakers-directory' or path.startswith('/speakers-directory/'):
            for node in soup.select('.c-directory-list__title a[href]'):
                if urlsplit(link(node) or '').path.startswith('/speakers-directory/'):internal(node,'listing')
            for node in soup.select('.c-entry--speaker .c-entry__title a[href]'):internal(node,'detail')
            for node in soup.select('.c-nav-page a[href]'):
                if node.get_text(' ',strip=True).lower()=='next':internal(node,'listing')
            if not children:raise ValueError('Sessionize directory selectors returned no links; inspect markup')
        elif soup.select_one('#speaker .c-s-speaker-info__name'):
            for node in soup.select('#events .c-s-event a.c-s-event__name[href]'):
                website(node,'sessionize_profile_event_link',section='events',selector='#events .c-s-event a.c-s-event__name[href]')
        else:raise ValueError('Unrecognized Sessionize profile; no generic outbound-link fallback')
    elif adapter=='conference_monkey':
        if urlsplit(url).path.startswith('/conference/'):
            for node in soup.select('a[href]'):
                label=node.get_text(' ',strip=True)
                if re.match(r'^visit\s*(?:the\s+)?(?:conference|website|site)\b',label,re.I):website(node,'aggregator_website_link')
            if not children:
                from .automatic import EVENT_WORD
                headings=soup.select('h1')
                title=headings[0].get_text(' ',strip=True) if len(headings)==1 else ''
                if EVENT_WORD.search(title) and len(soup.get_text(' ',strip=True))>150:
                    raise NoOrganizerLink(title[:240])
                raise ValueError('Conference Monkey detail has no explicit website button')
        else:
            for node in soup.select('a.post-link[href]'):
                if urlsplit(link(node) or '').path.startswith('/conference/'):internal(node,'detail')
            for node in soup.select('a[href]'):
                label=node.get_text(' ',strip=True).lower()
                if node.get('rel') and 'next' in node.get('rel') or label in ('next','next page','›','»'):
                    internal(node,'listing')
            if not children:raise ValueError('Conference Monkey listing selectors returned no links')
    else:raise ValueError('Unknown bulk discovery adapter')
    return list({(x['url'],x['task_kind']):x for x in children}.values())
