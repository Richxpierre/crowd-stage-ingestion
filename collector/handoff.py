"""Request a bounded continuation in this same repository; never log credentials."""
import os
import re
import uuid
import requests


def dispatch_bulk_continuation(db,job,next_id,session=None):
    count=int(job['config'].get('bulk_worker_handoffs',0))
    maximum=max(0,min(int(job['config'].get('settings',{}).get('bulk_max_handoffs',2)),5))
    token=os.environ.get('BULK_WORKFLOW_TOKEN')
    repo=os.environ.get('GITHUB_REPOSITORY')
    ref=os.environ.get('GITHUB_REF_NAME','main')
    if count>=maximum or not token or repo!='Richxpierre/crowd-stage-ingestion':return False
    uuid.UUID(next_id)
    if not re.fullmatch(r'[A-Za-z0-9._/-]+',ref):raise ValueError('Invalid worker ref')
    if not db.prepare_bulk_handoff(next_id,count+1):return False
    own=session is None;client=session or requests.Session();client.trust_env=False
    try:
        response=client.post('https://api.github.com/repos/Richxpierre/crowd-stage-ingestion/actions/workflows/discover-organizer-websites.yml/dispatches',
            headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'},
            json={'ref':ref,'inputs':{'job_id':next_id}},timeout=15,allow_redirects=False)
        if response.status_code!=204:raise RuntimeError(f'Continuation dispatch failed (HTTP {response.status_code}); work remains queued')
        return True
    finally:
        if own:client.close()
