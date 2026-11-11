"""Publish non-secret worker readiness to the admin dashboard, never event data."""
import argparse
import json
import os
from collector.client import Client
from collector.manus_backup import ManusClient,ManusError,readiness as manus_readiness
from collector.claude_client import ClaudeClient, configured as claude_configured

def publish(db,authenticate_manus=False,authenticate_claude=True):
    active=os.environ.get('AI_ENRICHMENT_ENABLED')=='1'
    key=bool(os.environ.get('OPENAI_API_KEY'))
    reports={
        'openai':{
            'configured':active,'credential_available':key,'authenticated':None,
            'task_creation_allowed':active and key,
            'reason':'authentication_not_checked' if active and key else 'openai_disabled' if not active else 'missing_key'
        },
        'manus':manus_readiness()
    }

    # Report Claude Core Brain
    claude = ClaudeClient()
    try:
        reports['claude'] = claude.authenticate() if authenticate_claude else {
            'provider': 'claude',
            'configured': claude_configured(),
            'credential_available': bool(os.environ.get('ANTHROPIC_API_KEY')),
            'authenticated': None,
            'model': claude.model,
            'task_creation_allowed': claude_configured(),
            'reason': 'active_core_brain' if claude_configured() else 'missing_key'
        }
    finally:
        claude.close()

    if authenticate_manus:
        client=ManusClient()
        try:reports['manus']=client.authenticate()
        except ManusError as exc:reports['manus'].update(authenticated=False,reason=str(exc))
        finally:client.close()

    for provider,report in reports.items():
        if provider=='manus' and not authenticate_manus:continue
        try:
            db.request('POST','rpc/report_enrichment_provider',json={'p_provider':provider,'p_report':report})
        except Exception:
            pass
    return reports

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--authenticate-manus',action='store_true')
    p.add_argument('--authenticate-claude',action='store_true',default=True)
    args=p.parse_args()
    print(json.dumps(publish(Client(),args.authenticate_manus,args.authenticate_claude)))

if __name__=='__main__':main()
