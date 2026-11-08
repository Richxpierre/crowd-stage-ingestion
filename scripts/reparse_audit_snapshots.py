"""Re-evaluate saved snapshots after parser fixes; never refetch or write the DB."""
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import date
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from collector.automatic import parse_page,date_range

def parse_one(arg):
    row,folder=arg
    body=(Path(folder)/'pages'/row['snapshot']).read_bytes()
    source={'url':row['original_url'],'name':row.get('page_title') or urlsplit(row['original_url']).hostname,'publisher':urlsplit(row['original_url']).hostname,'kind':'auto','selectors':{'website_only':True}}
    try:
        records=parse_page(body,source,row['final_url'])
        return {'id':row['id'],'events':[{k:r.get(k) for k in ('title','start_date','end_date','event_url','source_url','venue','city','country','format','status')} for r in records][:30],
                'future_count':sum(r['end_date']>=date.today().isoformat() for r in records),'parser_revision':2}
    except Exception as exc:return {'id':row['id'],'parser_error':str(exc),'parser_revision':2}

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    rows=[json.loads(s) for s in (args.out/'live-evidence.jsonl').read_text().splitlines()]
    candidates=[]
    for r in rows:
        if r.get('status')!='loaded':continue
        text=' '.join([r.get('page_title',''),*r.get('headings',[]),*r.get('date_contexts',[])])
        if r.get('future_count') or r.get('parser_error') or re.search(r'2027|2028',text) or any((dates:=date_range(s)) and dates[1]>=date.today().isoformat() for s in r.get('date_contexts',[])):
            candidates.append(r)
    print(json.dumps({'reparse_candidates':len(candidates)}),flush=True)
    with (args.out/'reparsed-evidence.jsonl').open('w') as output,ProcessPoolExecutor(max_workers=6) as pool:
        for i,future in enumerate(as_completed([pool.submit(parse_one,(r,str(args.out))) for r in candidates]),1):
            output.write(json.dumps(future.result())+'\n');output.flush()
            if i%100==0:print(json.dumps({'reparsed':i,'total':len(candidates)}),flush=True)

if __name__=='__main__':main()
