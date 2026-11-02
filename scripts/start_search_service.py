"""Start an isolated, loopback-only SearXNG service on a Linux Docker runner."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import requests

# Official GHCR multi-platform manifest resolved 2026-11-01. Update deliberately.
IMAGE='ghcr.io/searxng/searxng@sha256:63f50f92d266cd2859193ed46c10de9f39d4e44fc24344b2f2f1d2a80ba3689c'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-file',type=Path)
    args=parser.parse_args()
    if args.job_file:
        job=json.loads(args.job_file.read_text())
        if job.get('config',{}).get('settings',{}).get('search_enabled') is not True:
            print('Search assistance disabled for this job');return
    config=Path(tempfile.mkdtemp(prefix='crowdstage-search-'))
    (config/'settings.yml').write_text('use_default_settings: true\nserver:\n  secret_key: "'+secrets.token_hex(32)+'"\n  limiter: false\n  image_proxy: false\nsearch:\n  formats: [html, json]\n',encoding='utf-8')
    subprocess.run(['docker','run','--detach','--name','crowdstage-search','--publish','127.0.0.1:8888:8080',
                    '--memory','768m','--cpus','1.5','--pids-limit','256',
                    '--volume',str(config)+':/etc/searxng:rw',IMAGE],check=True,timeout=180)
    session=requests.Session();session.trust_env=False
    for _ in range(30):
        try:
            response=session.get('http://127.0.0.1:8888/healthz',timeout=2)
            if response.status_code==200:
                print('Private SearXNG worker service ready');return
        except requests.RequestException:pass
        time.sleep(2)
    raise RuntimeError('SearXNG startup timed out; ordinary crawling can continue without search')

if __name__=='__main__':main()
