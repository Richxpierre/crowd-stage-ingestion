"""Offline discovery snapshot importer. Stores only unverified leads; NO URLs fetched.

Supports Sessionize SQLite snapshot, CSV with `url` column, JSON/JSONL URLs.
Set --upload to put leads in the private CrowdStage review queue (service role).
"""
from __future__ import annotations
import argparse
import csv
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit
from collector.parsers import clean_url


def load_urls(path: Path):
    ext = path.suffix.lower()
    if ext in ('.db','.sqlite','.sqlite3'):
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
            for (url,) in db.execute('select url from sessionize_event_urls where is_listed=1'):
                yield url
    elif ext == '.csv':
        with path.open(newline='',encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                yield row.get('url') or row.get('event_url') or row.get('official_url')
    elif ext == '.jsonl':
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                value=json.loads(line)
                yield value if isinstance(value,str) else (value.get('url') or value.get('event_url'))
    elif ext == '.json':
        records=json.loads(path.read_text(encoding='utf-8'))
        for value in records:
            yield value if isinstance(value,str) else (value.get('url') or value.get('event_url'))
    else:
        raise ValueError('Use SQLite, CSV, JSON, or JSONL')


def candidates(path, provenance):
    seen=set()
    for raw in load_urls(path):
        url=clean_url(raw)
        if not url or url in seen: continue
        seen.add(url)
        yield {'url':url,'discovered_from':provenance,'review_state':'needs_review'}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',required=True)
    ap.add_argument('--provenance',required=True,help='e.g. sessionize-2026-10-15, association-sitemap')
    ap.add_argument('--output',default='url_candidates_review.csv')
    ap.add_argument('--upload',action='store_true',help='Requires a server-side Supabase key; never publishes events')
    args=ap.parse_args()
    rows=list(candidates(Path(args.input),args.provenance))
    with open(args.output,'w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['url','discovered_from','review_state'])
        writer.writeheader();writer.writerows(rows)
    print(f'Wrote {len(rows)} REVIEW-ONLY URLs to {args.output}')
    if args.upload:
        from collector.client import Client
        db=Client()
        for offset in range(0,len(rows),100):
            db.upsert_candidates(rows[offset:offset+100])
        print(f'Uploaded {len(rows)} candidates; none are approved or published')

if __name__=='__main__':
    main()
