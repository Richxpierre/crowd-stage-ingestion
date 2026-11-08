"""Real organizer parsing + one bounded Manus research task; never publish events."""
import json
import time
from pathlib import Path
from collector.client import Client
from collector.sync import sync_one
from collector.manus_queue import enqueue,tick
from collector.manus_backup import ManusClient

def main():
    db=Client();slug='events-linuxfoundation-org-ba3f90dd33'
    sources=db.sources(slug)
    if not sources:raise RuntimeError('The approved organizer probe source is unavailable')
    source=sources[0]
    import os
    from unittest.mock import patch
    with patch.dict(os.environ,{'MANUS_BACKUP_ENABLED':'0'}):sync_one(db,source,parse_only=True)
    payload={'stage':'research','url':source['url'],'event':'Open Source Summit Japan',
             'fields':['country','format'],'known':{'start_date':'2026-12-07','end_date':'2026-12-09'},'period':'2026-11'}
    job=enqueue(payload,db);client=ManusClient()
    try:
        deadline=time.monotonic()+150
        while job['status'] not in ('completed','failed','unknown','review') and time.monotonic()<deadline:
            result=tick(db,client);print(json.dumps(result))
            job=db.request('GET','conference_manus_jobs',params={'id':'eq.'+job['id'],'select':'*'})[0]
            if job['status'] not in ('completed','failed','unknown','review'):time.sleep(5)
        report={'job_id':job['id'],'status':job['status'],'reason':job.get('reason'),
                'verified_fields':sorted({r['field'] for r in (job.get('result') or {}).get('evidence',[])}),
                'verified_evidence':(job.get('result') or {}).get('evidence',[])}
        if job['status']=='completed':
            from collector.fetch import Fetcher
            from collector.manus_research import add_results
            from collector.parse_passes import PASS_TYPES
            _,body,_=Fetcher(os.environ['COLLECTOR_USER_AGENT']).fetch(source['url'])
            normalized={k:v for kind in PASS_TYPES for r in add_results(kind,{source['url']:body},source,[],job) for k,v in r['fields'].items()}
            report['parser_fields']=normalized
            if normalized.get('country')!='Japan' or normalized.get('format')!='in_person':
                raise RuntimeError('Verified citations did not reach parser field normalization')
        Path('ingestion-repair-probe.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print('REPAIR_PROBE='+json.dumps(report))
        if job['status']=='failed':raise RuntimeError('Manus research probe failed: '+str(job.get('reason')))
    finally:client.close()

if __name__=='__main__':main()
