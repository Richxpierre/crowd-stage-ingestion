"""Durable async Manus mini tasks. Only the worker submits paid agent requests."""
import hashlib
import json
import time
from datetime import datetime,timezone
from .client import Client
from .manus_backup import ManusClient,ManusError,configured,prepare_task,validate_selection

def enqueue(payload,db=None):
    if not configured():raise ManusError('manus_disabled')
    prepare_task(payload)  # Reject unusable excerpts before creating a queue row.
    key=hashlib.sha256(('manus-mini-v2|'+json.dumps(payload,sort_keys=True)).encode()).hexdigest()
    row=(db or Client()).request('POST','rpc/queue_manus_enrichment',json={'p_fingerprint':key,'p_payload':payload})
    return row

def patch(db,job,values):
    return db.request('PATCH','conference_manus_jobs',params={'id':'eq.'+job['id'],'claim_token':'eq.'+job['claim_token']},json=values)

def wake_consumers(db,job):
    url=job['payload'].get('url')
    if not url:return
    # Reuse normal validation/publication gates. Never apply generated facts here.
    db.request('PATCH','conference_sources',params={'url':'eq.'+url,'is_active':'eq.true','ingestion_allowed':'eq.true'},json={'next_check_at':datetime.now(timezone.utc).isoformat()})
    db.request('PATCH','conference_source_validations',params={'url':'eq.'+url,'status':'eq.needs_review'},
               json={'status':'queued','reason':'Manus mini enrichment is ready; recheck direct page evidence','completed_at':None})

def reconcile(db,client,job):
    state=client.task_status(job['task_id'])
    patch(db,job,{'credits_used':state['credits_used'],'checked_at':datetime.now(timezone.utc).isoformat()})
    age=time.time()-datetime.fromisoformat(job['started_at'].replace('Z','+00:00')).timestamp()
    try:
        usage=client.usage()
        low=usage['available']<=max(50,usage['allowance']*0.2) or usage['spent']>=min(usage['allowance'],2000)*0.8
        db.request('PATCH','conference_manus_gate',params={'id':'eq.true'},json={**usage,'checked_at':datetime.now(timezone.utc).isoformat(),
            'reason':'manus_daily_buffer_reached' if low else 'manus_task_in_flight' if state['status']=='running' else 'manus_ready'})
    except ManusError:
        low=True
    if low and (state['status'] in ('running','pending') or state['background_running']):
        if job.get('reason')!='manus_stop_requested':
            client.stop(job['task_id']);patch(db,job,{'reason':'manus_stop_requested'})
        return 'pending'
    if state['status']=='waiting' or age>180 and (state['status'] in ('running','pending') or state['background_running']):
        if job.get('reason')!='manus_stop_requested':
            client.stop(job['task_id']);patch(db,job,{'reason':'manus_stop_requested'})
        return 'pending'
    if state['status'] in ('stopped','completed') and not state['background_running']:
        result=client.result(job['task_id'],job['payload'])
        if result is not None:
            patch(db,job,{'status':'completed','result':result,'completed_at':datetime.now(timezone.utc).isoformat(),'reason':None})
            wake_consumers(db,job);return 'completed'
        if age>240:
            patch(db,job,{'status':'failed','reason':'manus_no_verified_result','completed_at':datetime.now(timezone.utc).isoformat()})
            return 'failed'
    if state['status'] in ('error','failed','deleted'):
        patch(db,job,{'status':'failed','reason':'manus_task_failed','completed_at':datetime.now(timezone.utc).isoformat()});return 'failed'
    return 'pending'

def tick(db,client):
    if not configured():return {'reason':'manus_disabled'}
    active=db.request('GET','conference_manus_jobs',params={'status':'eq.running','select':'*','order':'started_at','limit':'1'})
    if active:
        try:return {'reason':reconcile(db,client,active[0]),'id':active[0]['id']}
        except ManusError as exc:
            if str(exc) in ('manus_invalid_result','manus_unobserved_selection','manus_structured_output_failed'):
                client.stop(active[0]['task_id']);patch(db,active[0],{'status':'failed','reason':str(exc),'completed_at':datetime.now(timezone.utc).isoformat()})
            return {'reason':str(exc)}
    usage=client.usage()
    job=db.request('POST','rpc/claim_manus_enrichment',json={
        'p_allowance':usage['allowance'],'p_available':usage['available'],'p_spent':usage['spent'],'p_reset_at':usage['reset_at']})
    if not job.get('id'):return job
    try:
        task=client.create(job['payload'],job)
    except ManusError as exc:
        # No automatic resubmission: a transport timeout may have created a task.
        certain=str(exc) in ('manus_unauthenticated','manus_permission_denied','manus_resource_exhausted','manus_invalid_argument','manus_insufficient_evidence')
        patch(db,job,{'status':'failed' if certain else 'unknown','reason':str(exc),**({'credits_used':0} if certain else {})})
        return {'reason':str(exc),'id':job['id']}
    patch(db,job,{'task_id':task,'status':'running','reason':'manus_pending'})
    return {'reason':'manus_pending','id':job['id'],'task_id':task}

def settle_finished(db,client):
    from datetime import timedelta
    rows=db.request('GET','conference_manus_jobs',params={'status':'in.(completed,failed)','credits_used':'is.null',
        'task_id':'not.is.null','started_at':'gte.'+(datetime.now(timezone.utc)-timedelta(days=7)).isoformat(),
        'select':'*','order':'checked_at.asc.nullsfirst','limit':'5'})
    for job in rows:
        state=client.task_status(job['task_id'])
        patch(db,job,{'credits_used':state['credits_used'],'checked_at':datetime.now(timezone.utc).isoformat()})

def main():
    db=Client();client=ManusClient();deadline=time.monotonic()+150
    try:
        settle_finished(db,client)
        while time.monotonic()<deadline:
            result=tick(db,client);print(json.dumps(result))
            if result['reason'] not in ('manus_pending','pending'):break
            time.sleep(5)
    finally:client.close()

if __name__=='__main__':main()

