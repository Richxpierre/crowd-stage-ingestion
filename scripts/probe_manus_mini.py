"""One real queued evidence task, gated by live Manus credits. No event mutation."""
import json
import time
from collector.fetch import Fetcher
from collector.parse_passes import event_nodes
from collector.mini_enrichment import field_nodes
from collector.manus_backup import ManusClient
from collector.manus_queue import enqueue,tick
from collector.client import Client

def main():
    url='https://events.linuxfoundation.org/kubecon-cloudnativecon-north-america/'
    _,body,_=Fetcher('CrowdStage mini-enrichment verification').fetch(url)
    nodes,selectors=field_nodes(body,'event_location')
    if not nodes:raise RuntimeError('Official page provided no location evidence; no AI task created')
    payload={'stage':'event_location','url':url,'event':'KubeCon + CloudNativeCon North America',
             'fields':['country'],'nodes':nodes[:12]}
    db=Client();row=enqueue(payload,db)
    if row['status'] in ('completed','failed','unknown','review'):
        print('MANUS_MINI_PROBE='+json.dumps(row));return
    client=ManusClient();created=False
    try:
        # One task at most. Never submit another job after completion in this probe.
        deadline=time.monotonic()+140
        while time.monotonic()<deadline:
            result=tick(db,client);print(json.dumps(result))
            if result.get('task_id'):created=True
            if result['reason'] not in ('manus_pending','pending'):break
            time.sleep(5)
        rows=db.request('GET','conference_manus_jobs',params={'id':'eq.'+row['id'],'select':'id,status,task_id,credits_used,reason,result'})
        print('MANUS_MINI_PROBE='+json.dumps(rows))
    finally:client.close()

if __name__=='__main__':main()

