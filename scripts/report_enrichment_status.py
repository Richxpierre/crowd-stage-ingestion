"""Publish non-secret worker readiness and live token tracking to admin dashboard."""
import argparse
import json
import os
from collector.client import Client
from collector.manus_backup import ManusClient, ManusError, readiness as manus_readiness
from collector.claude_client import ClaudeClient, configured as claude_configured

def publish(db, authenticate_manus=False, authenticate_claude=False):
    # OpenAI is currently NOT connected
    has_openai = bool(os.environ.get('OPENAI_API_KEY')) and os.environ.get('AI_ENRICHMENT_ENABLED') == '1'
    reports = {
        'openai': {
            'provider': 'openai',
            'configured': has_openai,
            'credential_available': bool(os.environ.get('OPENAI_API_KEY')),
            'authenticated': None,
            'task_creation_allowed': False,
            'reason': 'openai_not_connected',
            'live_tokens': {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
        },
        'manus': manus_readiness()
    }

    # Claude Core Brain (live & active)
    claude = ClaudeClient()
    try:
        active = claude_configured()
        has_key = bool(os.environ.get('ANTHROPIC_API_KEY'))
        stats = claude.tracker.get_stats()
        reports['claude'] = {
            'provider': 'claude',
            'configured': active,
            'credential_available': has_key,
            'authenticated': True if has_key else False,
            'task_creation_allowed': active and has_key,
            'reason': 'active_core_brain' if active and has_key else 'claude_missing_key',
            'model': claude.model,
            'live_tokens': stats
        }
    finally:
        claude.close()

    if authenticate_manus:
        client = ManusClient()
        try:
            reports['manus'] = client.authenticate()
        except ManusError as exc:
            reports['manus'].update(authenticated=False, reason=str(exc))
        finally:
            client.close()

    for provider, report in reports.items():
        if provider == 'manus' and not authenticate_manus:
            continue
        try:
            db.request('POST', 'rpc/report_enrichment_provider', json={'p_provider': provider, 'p_report': report})
        except Exception:
            pass

    return reports

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--authenticate-manus', action='store_true')
    p.add_argument('--authenticate-claude', action='store_true')
    args = p.parse_args()
    print(json.dumps(publish(Client(), args.authenticate_manus, args.authenticate_claude)))

if __name__ == '__main__':
    main()
