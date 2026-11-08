"""Service-only Supabase REST client. Never import this in frontend code."""
from __future__ import annotations
import os
from datetime import datetime, timezone
import requests

class DatabaseError(RuntimeError):
    """A database/transport failure must not be mistaken for a bad source page."""
    pass

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
        try:
            response = self.session.request(method, self.base + '/' + path, timeout=50, **kwargs)
        except requests.RequestException:
            raise DatabaseError(f'Supabase transport failed: {method} {path.split("?")[0]}') from None
        if not response.ok:
            raise DatabaseError(f'Supabase {method} {path.split("?")[0]}: {response.status_code} {response.text[:300]}')
        try:return response.json() if response.content else None
        except ValueError:raise DatabaseError(f'Supabase returned invalid JSON: {method} {path.split("?")[0]}') from None

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

    def save_parse_pass(self, run_id, source_id, kind, result):
        self.request('POST','conference_source_parse_results',params={'on_conflict':'run_id,pass_type'},
            json={'run_id':run_id,'source_id':source_id,'pass_type':kind,**result},
            headers={'Prefer':'resolution=merge-duplicates,return=minimal'})

    def claim_parse_request(self):
        return self.request('POST','rpc/claim_conference_source_parse',json={})

    def finish_parse_request(self, request_id, status, message=None):
        self.request('PATCH','conference_source_parse_requests',params={'id':'eq.'+request_id},
            json={'status':status,'completed_at':datetime.now(timezone.utc).isoformat(),'error_message':message})

    def upsert_candidates(self, rows):
        return self.request('POST', 'conference_url_candidates', params={'on_conflict':'url'},
            json=rows, headers={'Prefer':'resolution=ignore-duplicates,return=minimal'})

    def claim_crawl_job(self, job_id=None):
        if not job_id:return self.request('POST','rpc/claim_conference_crawl',json={})
        # An explicitly selected queued job is claimed atomically without touching other targets.
        now=datetime.now(timezone.utc)
        rows=self.request('PATCH','conference_crawl_jobs',params={'id':'eq.'+job_id,'status':'eq.queued','cancel_requested':'eq.false'},
            json={'status':'running','started_at':now.isoformat(),'heartbeat_at':now.isoformat()},headers={'Prefer':'return=representation'})
        if not rows:return None
        from datetime import timedelta
        job=rows[0]
        self.request('PATCH','conference_crawler_targets',params={'id':'eq.'+job['target_id']},
            json={'next_crawl_at':(now+timedelta(hours=job['config'].get('interval_hours',24))).isoformat()})
        return job

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

    def enqueue_frontier(self,job,generation,tasks):
        for offset in range(0,len(tasks),100):
            rows=[{**item,'target_id':job['target_id'],'generation':generation} for item in tasks[offset:offset+100]]
            self.request('POST','conference_crawl_frontier',params={'on_conflict':'target_id,generation,url,task_kind'},json=rows,
                headers={'Prefer':'resolution=ignore-duplicates,return=minimal'})

    def claim_frontier(self,job_id,kind,limit):
        return self.request('POST','rpc/claim_conference_frontier',json={'p_job_id':job_id,'p_kind':kind,'p_limit':limit})

    def complete_frontier_sources(self,job_id,ids):
        return self.request('POST','rpc/complete_conference_frontier_sources',json={'p_job_id':job_id,'p_task_ids':ids})

    def finish_frontier(self,job_id,task_id):
        from datetime import timedelta
        now=datetime.now(timezone.utc)
        return self.request('PATCH','conference_crawl_frontier',params={'id':'eq.'+task_id,'lease_job_id':'eq.'+job_id,'status':'eq.leased'},
            json={'status':'done','completed_at':now.isoformat(),'available_at':(now+timedelta(days=7)).isoformat(),'lease_until':None})

    def fail_frontier(self,job_id,item,error):
        from datetime import timedelta
        return self.request('PATCH','conference_crawl_frontier',params={'id':'eq.'+item['id'],'lease_job_id':'eq.'+job_id,'status':'eq.leased'},
            json={'status':'error' if item['attempts']>=3 else 'queued','last_error':error,
                  'available_at':(datetime.now(timezone.utc)+timedelta(minutes=5*item['attempts'])).isoformat(),'lease_until':None})

    def frontier_progress(self,job_id):
        return self.request('POST','rpc/conference_frontier_progress',json={'p_job_id':job_id})

    def frontier_errors(self,target_id,generation):
        return self.request('GET','conference_crawl_frontier',params={'target_id':'eq.'+target_id,'generation':'eq.'+generation,
            'status':'eq.error','select':'url,last_error','limit':'50'})

    def continue_bulk_crawl(self,job_id):
        return self.request('POST','rpc/continue_conference_bulk_crawl',json={'p_job_id':job_id})

    def prepare_bulk_handoff(self,job_id,count):
        rows=self.request('GET','conference_crawl_jobs',params={'id':'eq.'+job_id,'status':'eq.queued','select':'config'})
        if not rows:return False
        return bool(self.request('PATCH','conference_crawl_jobs',params={'id':'eq.'+job_id,'status':'eq.queued'},
            json={'config':{**rows[0]['config'],'bulk_worker_handoffs':count}},headers={'Prefer':'return=representation'}))

