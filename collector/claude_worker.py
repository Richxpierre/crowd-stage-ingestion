"""Claude Core Brain Enrichment Worker for CrowdStage.
Offloads research and attribute extraction from Manus, saving paid credits and latency.
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import requests

from .client import Client
from .claude_client import ClaudeClient, configured as claude_configured
from .manus_queue import wake_consumers

LOG = logging.getLogger('crowdstage-claude-worker')

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def clean_page_text(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'noscript', 'svg']):
        tag.decompose()
    text = soup.get_text(' ', strip=True)
    return re.sub(r'\s+', ' ', text)

def fetch_page(url: str, timeout: int = 15) -> str | None:
    try:
        headers = {'User-Agent': os.environ.get('COLLECTOR_USER_AGENT') or 'CrowdStage-Intelligence/1.0'}
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
        LOG.warning('Failed to fetch %s: HTTP %d', url, resp.status_code)
    except Exception as exc:
        LOG.warning('Fetch error for %s: %s', url, exc)
    return None

def process_manus_queue(db: Client, claude: ClaudeClient, limit: int = 5) -> int:
    """Claim and resolve queued Manus research tasks with Claude."""
    processed = 0
    try:
        # Check for queued jobs in conference_manus_jobs
        jobs = db.request('GET', 'conference_manus_jobs', params={
            'status': 'eq.queued',
            'select': '*',
            'order': 'created_at.asc',
            'limit': str(limit)
        })
    except Exception as exc:
        LOG.warning('Could not query conference_manus_jobs: %s', exc)
        return 0

    for job in jobs:
        jid = job['id']
        payload = job.get('payload') or {}
        stage = payload.get('stage')
        url = payload.get('url')
        event_title = payload.get('event') or 'Conference'
        fields = payload.get('fields') or ['country', 'format', 'city']

        LOG.info('Claude taking job %s (%s) for %s', jid, stage, event_title)

        # Claim the job
        claim_token = f'claude-{int(time.time())}'
        try:
            db.request('PATCH', 'conference_manus_jobs', params={'id': 'eq.' + jid, 'status': 'eq.queued'}, json={
                'status': 'running',
                'claim_token': claim_token,
                'started_at': utc_now(),
                'reason': 'claude_processing'
            })
        except Exception as exc:
            LOG.warning('Failed to claim job %s: %s', jid, exc)
            continue

        if not url:
            db.request('PATCH', 'conference_manus_jobs', params={'id': 'eq.' + jid}, json={
                'status': 'failed',
                'reason': 'missing_url',
                'completed_at': utc_now()
            })
            continue

        html = fetch_page(url)
        if not html:
            db.request('PATCH', 'conference_manus_jobs', params={'id': 'eq.' + jid}, json={
                'status': 'failed',
                'reason': 'target_fetch_failed',
                'completed_at': utc_now()
            })
            continue

        text = clean_page_text(html)
        evidence, usage = claude.research_fields(event_title, url, fields, text)

        if evidence:
            result = {'evidence': evidence}
            LOG.info('Claude successfully extracted %d verified fields for %s', len(evidence), event_title)
            db.request('PATCH', 'conference_manus_jobs', params={'id': 'eq.' + jid}, json={
                'status': 'completed',
                'result': result,
                'credits_used': 0,
                'completed_at': utc_now(),
                'reason': 'claude_resolved'
            })
            job['payload'] = payload
            wake_consumers(db, job)
            processed += 1
        else:
            LOG.info('Claude found no verifiable evidence for %s on %s', event_title, url)
            db.request('PATCH', 'conference_manus_jobs', params={'id': 'eq.' + jid}, json={
                'status': 'completed',
                'result': {'evidence': []},
                'credits_used': 0,
                'completed_at': utc_now(),
                'reason': 'claude_no_supported_evidence'
            })
            processed += 1

    return processed

def process_pending_events(db: Client, claude: ClaudeClient, limit: int = 10) -> int:
    """Directly enrich events in pending_review that have missing attributes."""
    enriched = 0
    try:
        events = db.request('GET', 'events', params={
            'status': 'eq.pending_review',
            'select': 'id,slug,name,external_url,country,city,venue,format,start_date,end_date,metadata_report',
            'order': 'created_at.desc',
            'limit': str(limit)
        })
    except Exception as exc:
        LOG.warning('Could not query pending events: %s', exc)
        return 0

    for ev in events:
        missing = []
        if not ev.get('country'): missing.append('country')
        if not ev.get('city'): missing.append('city')
        if not ev.get('format'): missing.append('format')
        if not ev.get('venue'): missing.append('venue')

        url = ev.get('external_url')
        if not missing or not url:
            continue

        LOG.info('Claude enriching event %s (missing: %s)', ev.get('name'), missing)
        html = fetch_page(url)
        if not html:
            continue

        text = clean_page_text(html)
        evidence, _ = claude.research_fields(ev.get('name', 'Conference'), url, missing, text)

        if evidence:
            patch = {}
            meta = ev.get('metadata_report') or {}
            existing_evidence = meta.get('evidence') or {}

            for item in evidence:
                field = item.get('field')
                val = item.get('value')
                if field and val and field in missing:
                    patch[field] = val
                    existing_evidence[field] = {
                        'value': val,
                        'method': 'claude_core_brain',
                        'confidence': 'high',
                        'page_url': url,
                        'text': item.get('quote')
                    }

            if patch:
                patch['metadata_report'] = {**meta, 'evidence': existing_evidence}
                try:
                    db.request('PATCH', 'events', params={'id': 'eq.' + ev['id']}, json=patch)
                    LOG.info('Enriched %s with %s', ev.get('name'), list(patch.keys()))
                    enriched += 1
                except Exception as exc:
                    LOG.warning('Failed to patch event %s: %s', ev['id'], exc)

    return enriched

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s] %(message)s')
    if not claude_configured():
        LOG.info('Claude enrichment is not configured (missing ANTHROPIC_API_KEY). Skipping.')
        return

    db = Client()
    claude = ClaudeClient()
    LOG.info('Starting Claude Core Brain Worker with model %s', claude.model)

    try:
        q_count = process_manus_queue(db, claude, limit=5)
        ev_count = process_pending_events(db, claude, limit=8)
        LOG.info('Claude Core Brain finished: resolved %d queued jobs, enriched %d events.', q_count, ev_count)
    finally:
        claude.close()

if __name__ == '__main__':
    main()
