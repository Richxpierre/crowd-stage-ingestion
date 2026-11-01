"""Service-only Supabase REST client. Never import this in frontend code."""
from __future__ import annotations
import os
from datetime import datetime, timezone
import requests

class Client:
    def __init__(self):
        base = os.environ['SUPABASE_URL'].rstrip('/')
        if not base.startswith('https://'):
            raise ValueError('SUPABASE_URL must use HTTPS')
        self.base = base + '/rest/v1'
        key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_SECRET_KEY')
        if not key:
            raise RuntimeError('Set SUPABASE_SERVICE_ROLE_KEY or SUPABASE_SECRET_KEY (server only)')
        self.session = requests.Session()
        self.session.headers.update({'apikey': key, 'Content-Type': 'application/json'})
        if not key.startswith('sb_secret_'):
            self.session.headers['Authorization'] = 'Bearer ' + key

    def request(self, method, path, **kwargs):
        response = self.session.request(method, self.base + '/' + path, timeout=50, **kwargs)
        if not response.ok:
            raise RuntimeError(f'Supabase {method} {path.split("?")[0]}: {response.status_code} {response.text[:300]}')
        return response.json() if response.content else None

    def sources(self, slug=None):
        params = {
            'select': '*', 'is_active': 'eq.true', 'is_official_source': 'eq.true',
            'ingestion_allowed': 'eq.true', 'record_scope':'eq.conference',
            'order': 'next_check_at.asc', 'limit': '1000'
        }
        if slug:
            params['slug'] = 'eq.' + slug  # manual runs can force a not-yet-due source
        else:
            params['next_check_at'] = 'lte.' + datetime.now(timezone.utc).isoformat()
        return self.request('GET', 'conference_sources', params=params)

    def create_run(self, source_id):
        return self.request('POST', 'conference_ingestion_runs', json={
            'source_id': source_id, 'status': 'running'
        }, headers={'Prefer':'return=representation'})[0]['id']

    def observations(self, source_id):
        return self.request('GET', 'conference_event_observations', params={
            'select':'source_record_id,payload', 'source_id':'eq.'+source_id,
            'order':'last_seen_at.desc', 'limit':'100'
        })

    def update_run(self, run_id, record):
        self.request('PATCH', 'conference_ingestion_runs', params={'id':'eq.'+run_id}, json=record)

    def update_source(self, source_id, record):
        self.request('PATCH', 'conference_sources', params={'id':'eq.'+source_id}, json=record)

    def ingest(self, source_id, records):
        return self.request('POST', 'rpc/ingest_crowdstage_conference_batch', json={
            'p_source_id':source_id, 'p_records':records
        })

    def upsert_candidates(self, rows):
        return self.request('POST', 'conference_url_candidates', params={'on_conflict':'url'},
            json=rows, headers={'Prefer':'resolution=ignore-duplicates,return=minimal'})

    def claim_crawl_job(self):
        return self.request('POST','rpc/claim_conference_crawl',json={})

    def crawl_job(self, job_id):
        rows=self.request('GET','conference_crawl_jobs',params={'id':'eq.'+job_id,'select':'id,status,cancel_requested'})
        return rows[0] if rows else None

    def update_crawl_job(self, job_id, record):
        return self.request('PATCH','conference_crawl_jobs',params={'id':'eq.'+job_id,'status':'eq.running'},json=record)

    def recent_crawled_pages(self, target_id):
        from datetime import timedelta
        since=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
        rows=self.request('GET','conference_crawl_jobs',params={'target_id':'eq.'+target_id,'select':'visited_urls',
            'completed_at':'gte.'+since,'config->settings->>discovery_mode':'eq.website_links','order':'completed_at.desc','limit':'50'})
        return {url for row in rows for url in row.get('visited_urls',[])}

    def record_discovery(self, job_id, listing, official, status, title, start, end, evidence, reason):
        return self.request('POST','rpc/record_conference_discovery',json={'p_job_id':job_id,'p_listing_url':listing,
            'p_official_url':official,'p_status':status,'p_title':title,'p_start':start,'p_end':end,'p_evidence':evidence,'p_reason':reason})

