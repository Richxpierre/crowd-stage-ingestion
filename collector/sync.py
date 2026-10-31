"""Recurring worker: ONLY registered official conference websites/feeds are fetched.
Discovery sites are never polled here. New records go to CrowdStage pending_review.
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .client import Client
from .fetch import Fetcher
from .official import parse_official_html
from .parsers import parse_ics, parse_jsonld

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


def sync_one(db, source):
    sid = source['id']
    counts = {'fetched_count':0,'created_count':0,'updated_count':0,'unchanged_count':0,'skipped_count':0}
    run_id = db.create_run(sid)
    try:
        if not source.get('is_official_source') or not source.get('ingestion_allowed') or not source.get('permission_reviewed_at') or len(str(source.get('permission_basis') or '').strip()) < 8:
            raise PermissionError('Only reviewed, approved, organizer-hosted direct sources may be refreshed')
        if is_directory_target(source['url']):
            raise PermissionError('Sessionize and ConferenceGrid are discovery sources, not recurring refresh targets')
        if source.get('kind') not in ('ics','jsonld','official_html'):
            raise ValueError('Unsupported direct URL kind')
        fetcher = Fetcher(os.environ.get('COLLECTOR_USER_AGENT',''),
                          min_delay_seconds=source.get('min_delay_seconds',2))
        code, body, headers = fetcher.fetch(source['url'], source.get('etag'), source.get('last_modified'))
        if code == 304:
            db.update_source(sid, {'last_success_at':utc_now(),'last_attempt_at':utc_now(),
                                   'last_error':None,'next_check_at':next_check(source)})
            db.update_run(run_id, {'status':'unchanged','completed_at':utc_now(),'http_status':304})
            return counts
        kind = source['kind']
        records = (parse_ics(body, source) if kind == 'ics'
                   else parse_jsonld(body, source) if kind == 'jsonld'
                   else parse_official_html(body, source))
        if not records:
            raise ValueError('No valid Event records parsed; retaining prior events')
        # Same feed UID/record ID is a single source observation.
        records = list({r['source_record_id']:r for r in records}.values())
        counts['fetched_count'] = len(records)
        for i in range(0,len(records),100):
            batch = db.ingest(sid, records[i:i+100])
            for key in ('created','updated','unchanged','skipped'):
                counts[key+'_count'] += int(batch.get(key,0))
        db.update_source(sid, {'etag':headers.get('ETag'), 'last_modified':headers.get('Last-Modified'),
                              'last_success_at':utc_now(), 'last_attempt_at':utc_now(),
                              'next_check_at':next_check(source),'last_error':None})
        db.update_run(run_id, {**counts, 'status':'success','http_status':code,'completed_at':utc_now()})
        return counts
    except Exception as exc:
        LOG.exception('Source %s failed',source.get('slug'))
        db.update_run(run_id,{**counts,'status':'failed','completed_at':utc_now(),'error_message':str(exc)[:750]})
        db.update_source(sid,{'last_attempt_at':utc_now(), 'last_error':str(exc)[:750],
                              'next_check_at':next_check(source,6)})
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
    for s in sources:
        try:
            LOG.info('%s %s',s['slug'],json.dumps(sync_one(db,s)))
        except Exception:
            failures += 1
    if failures:
        LOG.error('%d of %d sources failed',failures,len(sources))
        sys.exit(1)

if __name__ == '__main__':
    main()
