"""Publish non-secret worker readiness to the admin dashboard, never event data."""
import argparse
import json
import os
from collector.client import Client
from collector.manus_backup import ManusClient,ManusError,readiness

def publish(db,authenticate_manus=False):
    active=os.environ.get('AI_ENRICHMENT_ENABLED')=='1'
    key=bool(os.environ.get('OPENAI_API_KEY'))
    reports={'openai':{'configured':active,'credential_available':key,'authenticated':None,
        'task_creation_allowed':active and key,'reason':'authentication_not_checked' if active and key else 'openai_disabled' if not active else 'missing_key'},
        'manus':readiness()}
    if authenticate_manus:
        client=ManusClient()
        try:reports['manus']=client.authenticate()
        except ManusError as exc:reports['manus'].update(authenticated=False,reason=str(exc))
        finally:client.close()
    for provider,report in reports.items():
        # The Manus card is the last explicit authentication check, with its own
        # checked_at. A configuration-only heartbeat must not erase that result.
        if provider=='manus' and not authenticate_manus:continue
        db.request('POST','rpc/report_enrichment_provider',json={'p_provider':provider,'p_report':report})
    return reports

def main():
    p=argparse.ArgumentParser();p.add_argument('--authenticate-manus',action='store_true');args=p.parse_args()
    print(json.dumps(publish(Client(),args.authenticate_manus)))
if __name__=='__main__':main()

