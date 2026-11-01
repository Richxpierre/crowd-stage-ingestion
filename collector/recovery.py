"""Recover a failed feed/page by discovering the organizer's actual website."""
from __future__ import annotations
import re
import time
from urllib.parse import urlsplit,urlunsplit
import tldextract
from .parsers import clean_url,parse_ics,parse_jsonld,_finish
from .official import parse_official_html
from .automatic import collect_automatic
from .feeds import feed_body,parse_feed
from .fetch import UnsafeDestination,RobotsPathDenied

# Use the bundled list, including hosted-service tenant boundaries. No PSL network calls.
EXTRACT=tldextract.TLDExtract(suffix_list_urls=(),cache_dir=None,include_psl_private_domains=True)
FEED_PREFIXES={'feed','feeds','rss','calendar','ical'}


def fallback_urls(source):
    original=clean_url(source['url'])
    if not original:return []
    p=urlsplit(original)
    host=(p.hostname or '').encode('idna').decode('ascii').lower()
    root=urlunsplit(('https',p.netloc,'/','',''))
    configured=(source.get('selectors') or {}).get('fallback_url')
    urls=[]
    if configured:
        target=clean_url(configured)
        if not target:raise ValueError('Fallback website must be a public HTTPS URL')
        urls.append(target)  # Explicit administrator setting; normal fetch guards still apply.
    urls.append(root)
    if host.split('.')[0] in FEED_PREFIXES:
        parent=host.split('.',1)[1]
        one,two=EXTRACT(host),EXTRACT(parent)
        if one.suffix and one.top_domain_under_public_suffix==two.top_domain_under_public_suffix:
            urls.append('https://'+parent+'/')
    return list(dict.fromkeys(u for u in urls if u!=original))[:3]


def _http_status(exc):
    return getattr(getattr(exc,'response',None),'status_code',None)


def _terminal(exc):
    return (isinstance(exc,PermissionError) and not isinstance(exc,RobotsPathDenied)) or isinstance(exc,UnsafeDestination) or _http_status(exc)==429


def collect_source(fetcher,source):
    """Return records, HTTP result and a persisted primary/fallback attempt report."""
    fetcher.deadline=time.monotonic()+150
    report={'mode':'primary','attempts':[],'errors':[]}
    code,headers=200,{}
    try:
        if source['kind']=='auto':
            records,details=collect_automatic(fetcher,source)
            report.update(details)
        else:
            old=source.get('discovery_report') or {}
            cache_ok=old.get('image_extraction_version')==1 and old.get('mode')!='website_fallback'
            code,body,headers=fetcher.fetch(source['url'],source.get('etag') if cache_ok else None,
                                          source.get('last_modified') if cache_ok else None)
            if code==304:return [],code,headers,report
            if (source.get('selectors') or {}).get('website_only') and (feed_body(body) or body.lstrip().startswith(b'BEGIN:VCALENDAR')):
                raise ValueError('Expected an organizer website page, received a feed')
            records=(parse_feed(body,source) if feed_body(body) else
                     parse_ics(body,source) if source['kind']=='ics' else
                     parse_official_html(body,source) if source['kind']=='official_html' else parse_jsonld(body,source))
        report['attempts'].append({'url':source['url'],'method':source['kind'],'status':'success' if records else 'empty'})
        if records:return records,code,headers,report
        if any(issue.get('http_status')==429 for issue in report.get('errors',[])):
            raise PermissionError('Website rate limit reached; retry after cooldown')
        detail=report.get('errors',[])
        raise ValueError('No valid Event records with explicit event dates'+(': '+detail[0]['message'] if detail else ''))
    except Exception as exc:
        if _terminal(exc):
            report.update(primary_error=str(exc)[:500],primary_http_status=_http_status(exc))
            report['attempts'].append({'url':source['url'],'method':source['kind'],'status':'blocked','message':str(exc)[:500]})
            exc.report=report
            raise
        primary_error=str(exc)[:500]
        report['primary_error']=primary_error
        report['primary_http_status']=_http_status(exc) or next((issue.get('http_status') for issue in report.get('errors',[]) if issue.get('http_status')),None)
        attempt={'url':source['url'],'method':source['kind'],'status':'failed','message':primary_error}
        if report['attempts'] and report['attempts'][-1]['url']==source['url']:
            report['attempts'][-1]=attempt
        else:report['attempts'].append(attempt)
    if (source.get('selectors') or {}).get('website_fallback',True) is False:
        raise RecoveryFailure('No valid Event records. Primary collection failed; website fallback is disabled: '+primary_error,report)
    try:candidates=fallback_urls(source)
    except ValueError as exc:raise RecoveryFailure(str(exc),report) from exc
    options=source.get('selectors') or {}
    fallback_selectors={key:options[key] for key in ('max_pages','render','cover_image','website_only') if key in options}
    fallback_selectors.update(options.get('fallback_selectors') or {})
    for url in candidates:
        fallback={**source,'url':url,'kind':'auto','selectors':fallback_selectors}
        try:
            records,details=collect_automatic(fetcher,fallback)
            report['attempts'].append({'url':url,'method':'automatic_website','status':'success' if records else 'empty'})
            report['errors'].extend(details.get('errors',[]))
            if records:
                report.update({key:val for key,val in details.items() if key!='errors'})
                report.update(mode='website_fallback',fallback_url=url,configured_url=source['url'])
                return records,200,{},report
        except Exception as exc:
            if _terminal(exc):
                report['attempts'].append({'url':url,'status':'blocked','message':str(exc)[:250]})
                # Robots or unsafe destinations cannot be worked around with another host.
                raise RecoveryFailure('Website recovery blocked: '+str(exc),report) from exc
            report['attempts'].append({'url':url,'status':'failed','message':str(exc)[:250]})
    raise RecoveryFailure('No valid Event records. Feed/page failed and website discovery found no usable events. '+primary_error,report)


class RecoveryFailure(ValueError):
    def __init__(self,message,report):
        super().__init__(message)
        self.report=report


def reconcile_identities(db,source,records):
    """A fallback page and a recovered feed must update the same observed edition."""
    observations=db.observations(source['id'])
    by_identity={obs['source_record_id']:obs for obs in observations}
    normalize=lambda text:re.sub(r'\s+',' ',text or '').strip().casefold()
    keys=[(clean_url(r.get('event_url')),r['start_date'][:4]) for r in records]
    adopted=set()
    for record,key in zip(records,keys):
        if record['source_record_id'] in by_identity:continue
        matches=[obs for obs in observations if key[0] and
                 clean_url(obs['payload'].get('event_url'))==key[0] and
                 str(obs['payload'].get('start_date',''))[:4]==key[1]]
        if not matches:
            # Some calendars omit the event URL. Exact title and dates still identify
            # an edition if there is only one compatible observation in this source.
            matches=[obs for obs in observations if normalize(obs['payload'].get('title'))==normalize(record['title'])
                     and obs['payload'].get('start_date')==record['start_date']
                     and obs['payload'].get('end_date')==record['end_date']]
        ambiguous=len(matches)>1
        if len(matches)>1 or keys.count(key)>1:
            matches=[obs for obs in matches if normalize(obs['payload'].get('title'))==normalize(record['title'])
                     and obs['payload'].get('start_date')==record['start_date']]
        identities={obs['source_record_id'] for obs in matches}
        if len(identities)>1 or (ambiguous and not identities):
            raise ValueError('Ambiguous feed/website edition identity; review duplicates before retrying')
        if identities:
            identity=identities.pop()
            if identity in adopted:raise ValueError('Several discovered events match one existing feed record; review source settings')
            adopted.add(identity)
            _finish(record,source['publisher'],identity)
    return records
