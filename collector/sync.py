"""Recurring worker: ONLY registered official conference websites/feeds are fetched.
Discovery sites are never polled here. New records go to CrowdStage pending_review.
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .client import Client
from .fetch import Fetcher
from .parsers import _finish, clean_url
from .images import enrich_missing_images
from .recovery import collect_source,reconcile_identities

LOG = logging.getLogger('crowdstage-ingestion')
BLOCKED_DIRECTORY_HOSTS = {'conferencegrid.com','sessionize.com'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def next_check(source, hours_override=None):
    hours = max(6, min(int(hours_override or source.get('refresh_hours') or 24), 720))
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def is_directory_target(url):
    host = (urlsplit(url).hostname or '').lower().removeprefix('www.')
    return any(host == blocked or host.endswith('.' + blocked) for blocked in BLOCKED_DIRECTORY_HOSTS)


def preserve_existing_identity(db, source, records):
    """Keep the original UUID when an existing dedicated event page is renamed.

    This also adopts identities written by the older title-based parser, avoiding
    duplicate records when a source upgrades to the new parser.
    """
    if len(records) != 1 or not source.get('selectors', {}).get('single_event'):
        return records
    record = records[0]
    matches = [obs for obs in db.observations(source['id'])
               if clean_url(obs['payload'].get('event_url')) == clean_url(record.get('event_url'))
               and str(obs['payload'].get('start_date',''))[:4] == record['start_date'][:4]]
    identities = {obs['source_record_id'] for obs in matches}
    if len(identities) > 1:
        raise ValueError('Ambiguous event identities on a dedicated source; review duplicates first')
    if identities:
        return [_finish(record, source['publisher'], identities.pop())]
    return records


def sync_one(db, source):
    sid = source['id']
    counts = {'fetched_count':0,'created_count':0,'updated_count':0,'unchanged_count':0,'skipped_count':0}
    run_id = db.create_run(sid)
    report = None
    try:
        if not source.get('is_official_source') or not source.get('ingestion_allowed') or not source.get('permission_reviewed_at') or len(str(source.get('permission_basis') or '').strip()) < 8:
            raise PermissionError('Only reviewed, approved, organizer-hosted direct sources may be refreshed')
        if source.get('record_scope') != 'conference':
            raise PermissionError('Source must be verified as a conference or conference catalog, not news or sessions')
        if '/schedule/' in urlsplit(source['url']).path:
            raise ValueError('Session schedules must not be imported as conferences')
        if is_directory_target(source['url']):
            raise PermissionError('Sessionize and ConferenceGrid are discovery sources, not recurring refresh targets')
        if source.get('kind') not in ('auto','ics','jsonld','official_html'):
            raise ValueError('Unsupported direct URL kind')
        fetcher = Fetcher(os.environ.get('COLLECTOR_USER_AGENT',''),
                          min_delay_seconds=source.get('min_delay_seconds',2))
        records,code,headers,report=collect_source(fetcher,source)
        if code == 304:
            db.update_source(sid, {'last_success_at':utc_now(),'last_attempt_at':utc_now(),
                                   'last_error':None,'next_check_at':next_check(source)})
            db.update_run(run_id, {'status':'unchanged','completed_at':utc_now(),'http_status':304})
            return counts
        recovered=report.get('mode')=='website_fallback'
        # Historical calendars must not refill the site with expired sessions/events.
        # Keep cancellation observations so existing listings can still be archived.
        current_date = datetime.now(timezone.utc).date().isoformat()
        current_records = [r for r in records if r['end_date'] >= current_date or r.get('status') == 'cancelled']
        counts['skipped_count'] = len(records) - len(current_records)
        records = current_records
        if source['kind'] != 'auto' and not recovered:
            fetcher.deadline=min(getattr(fetcher,'deadline',None) or float('inf'),time.monotonic()+60)
            records=enrich_missing_images(fetcher,source,records)
        # Same feed UID/record ID is a single source observation.
        records = list({r['source_record_id']:r for r in records}.values())
        if recovered or (source.get('discovery_report') or {}).get('mode')=='website_fallback':
            records=reconcile_identities(db,source,records)
        records = preserve_existing_identity(db, source, records)
        counts['fetched_count'] = len(records)
        image_report=report
        image_report.update(image_extraction_version=1,images_found=sum(bool(r.get('cover_url')) for r in records))
        for i in range(0,len(records),100):
            batch = db.ingest(sid, records[i:i+100])
            for key in ('created','updated','unchanged','skipped'):
                counts[key+'_count'] += int(batch.get(key,0))
        db.update_source(sid, {'etag':headers.get('ETag'), 'last_modified':headers.get('Last-Modified'),
                              'discovery_report':image_report,
                              'last_success_at':utc_now(), 'last_attempt_at':utc_now(),
                              'next_check_at':next_check(source,6 if recovered else None),'last_error':None})
        if recovered:
            LOG.warning('Source %s recovered with automatic website discovery at %s; primary feed/page will be retried',source.get('slug'),report['fallback_url'])
        db.update_run(run_id, {**counts, 'status':'success' if records else 'unchanged','http_status':code,'completed_at':utc_now()})
        return counts
    except Exception as exc:
        LOG.exception('Source %s failed',source.get('slug'))
        db.update_run(run_id,{**counts,'status':'failed','completed_at':utc_now(),'error_message':str(exc)[:750]})
        failed={'last_attempt_at':utc_now(), 'last_error':str(exc)[:750],'next_check_at':next_check(source,6)}
        failure_report=getattr(exc,'report',None) or report
        if failure_report is not None:failed['discovery_report']=failure_report
        db.update_source(sid,failed)
        raise


def main():
    parser = argparse.ArgumentParser(description='Refresh CrowdStage conferences from official URLs only')
    parser.add_argument('--slug',help='Force refresh a single approved source, even if not yet due')
    parser.add_argument('--limit-sources',type=int,default=int(os.environ.get('MAX_SOURCES','100')))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    db = Client()
    sources = db.sources(slug=args.slug)[:max(0,args.limit_sources)]
    LOG.info('%d direct sources selected',len(sources))
    failures = 0
    run_deadline=time.monotonic()+max(150,min(int(os.environ.get('MAX_RUN_SECONDS','600')),1800))
    for index,s in enumerate(sources):
        if time.monotonic()>=run_deadline:
            LOG.warning('Run time budget reached; %d due sources deferred to the next scheduled run',len(sources)-index)
            break
        try:
            LOG.info('%s %s',s['slug'],json.dumps(sync_one(db,s)))
        except Exception:
            failures += 1
    if failures:
        LOG.error('%d of %d sources failed',failures,len(sources))
        sys.exit(1)

if __name__ == '__main__':
    main()

