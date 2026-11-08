"""Deterministic discovery adapters. These return observed links, never event facts."""
import re
import calendar
from datetime import date
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from .search import result_url
from .crawler_reader import same_owner

class NoOrganizerLink(ValueError):
    def __init__(self,title):
        self.title=title
        super().__init__('Conference listing has no organizer website button')


def historical_date_hint(text,today=None):
    """Only explicit profile date metadata, never a guessed date from a title."""
    from .automatic import date_range,MONTHS
    today=today or date.today();text=' '.join((text or '').split())
    dates=date_range(text)
    if dates:return date.fromisoformat(dates[1])<today
    match=re.fullmatch(r'([A-Za-z]+)\.?\s+(20\d{2})',text)
    if match and match[1].lower() in MONTHS:
        month=MONTHS[match[1].lower()];year=int(match[2])
        return date(year,month,calendar.monthrange(year,month)[1])<today
    return bool(re.fullmatch(r'20\d{2}',text) and int(text)<today.year)


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
        heading=soup.find('h1') if adapter=='conference_monkey' else None
        title=heading.get_text(' ',strip=True)[:240] if heading else label
        children.append(task(target,'source',listing_url=url,title=title,
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
                card=node.find_parent(class_='c-s-event');date_node=card.select_one('.c-s-event__meta--date') if card else None
                date_hint=date_node.get_text(' ',strip=True) if date_node else None
                if historical_date_hint(date_hint):
                    audit=page.setdefault('discovery_filter_report',{'historical_skipped':0,'examples':[]})
                    audit['historical_skipped']+=1
                    if len(audit['examples'])<5:audit['examples'].append({'url':link(node),'title':node.get_text(' ',strip=True)[:160],'date_hint':date_hint})
                    continue
                location=card.select_one('.c-s-event__meta--location') if card else None
                website(node,'sessionize_profile_event_link',section='events',selector='#events .c-s-event a.c-s-event__name[href]',
                    date_hint=date_hint,location_hint=location.get_text(' ',strip=True) if location else None)
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
