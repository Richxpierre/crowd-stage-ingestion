"""Generate local four-pass previews from saved direct-page evidence; no DB writes."""
import argparse
import asyncio
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from collector.source_validation import _validate_site
from collector.parsers import clean_url
from collector.source_validation import website_root
from collector.crawler_reader import same_owner

class NoSearch:
    def search(self,query):return {'results':[],'warnings':'Manual direct-page review; indexing is not evidence'}

class SnapshotReader:
    def __init__(self,row,body):self.row=row;self.body=body
    async def read(self,url):
        allowed={clean_url(self.row['original_url']),clean_url(self.row['final_url'])}
        if clean_url(url) not in allowed:raise ValueError('Additional page not collected in this direct snapshot review')
        return {'url':self.row['final_url'],'html':self.body,'links':[],'markdown':''}

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);parser.add_argument('--revision',type=int,default=1);args=parser.parse_args()
    os.environ['AI_ENRICHMENT_ENABLED']='0';os.environ['MANUS_BACKUP_ENABLED']='0'
    inventory={r['id']:r for r in json.loads((args.out/'backlog-inventory.json').read_text())}
    path=args.out/('four-pass-previews.jsonl' if args.revision==1 else 'four-pass-previews-v'+str(args.revision)+'.jsonl');done=set()
    if path.exists():done={json.loads(s)['id'] for s in path.read_text().splitlines()}
    rows=[json.loads(s) for s in (args.out/'live-evidence.jsonl').read_text().splitlines()]
    replay=args.out/'reparsed-evidence.jsonl'
    updates={json.loads(s)['id']:json.loads(s) for s in replay.read_text().splitlines()} if replay.exists() else {}
    rows=[{**r,**updates.get(r['id'],{})} for r in rows]
    rows=[r for r in rows if r.get('future_count') and r['id'] not in done]
    print(json.dumps({'pending_previews':len(rows)}),flush=True)
    with path.open('a',encoding='utf-8') as output:
        for index,row in enumerate(rows,1):
            candidate=inventory[row['id']]
            body=(args.out/'pages'/row['snapshot']).read_text(encoding='utf-8')
            result=await _validate_site(candidate,SnapshotReader(row,body),NoSearch())
            canonical=urlsplit(result['canonical_url'])
            if canonical.path in ('','/') and not canonical.query:result['canonical_url']=website_root(result['canonical_url'])
            report=result['report'];stamp=datetime.now(timezone.utc).isoformat()
            report.update(validation_version=2,navigations_used=1,searches_used=0,strategy_limit=3,navigation_limit=8,search_limit=3,
                observation_backend='guarded_http_snapshot_manual_preview',collected_at=row['checked_at'],
                strategies=[{'strategy':'manual_direct_http_snapshot','url':row['original_url'],'status':result['status'],
                    'reason':result['reason'],'started_at':row['checked_at'],'completed_at':stamp}],
                manual_review={'status':'proposed','original_title':candidate.get('title'),'snapshot':row['snapshot']})
            report['attempts']=[a for a in report.get('attempts',[]) if a['status']=='loaded']
            flags=[]
            if re.search(r'/(?:sessions?|speakers?|agenda|schedule|talks?)(?:/|$)',urlsplit(row['original_url']).path,re.I):flags.append('ancillary_page')
            if any(not same_owner(e.get('url') or '',row['original_url']) for e in report.get('event_evidence',[])):flags.append('event_url_outside_owner')
            if row.get('bad_page'):flags.append('bad_page')
            output.write(json.dumps({'id':row['id'],'url':row['original_url'],'expected_updated_at':candidate['updated_at'],
                'flags':flags,'result':result},ensure_ascii=True)+'\n');output.flush()
            print(json.dumps({'preview':index,'total':len(rows),'url':row['original_url'],'status':result['status'],'flags':flags}),flush=True)

if __name__=='__main__':asyncio.run(main())
