"""Bounded read-only probe of actual adapters; no database connection or writes."""
import asyncio
import json
import time
from pathlib import Path
from collector.aggregators import extract
from collector.bulk_discovery import StaticFirstReader

async def main():
    output=[]
    for adapter,seed in [('conference_monkey','https://conferencemonkey.org/top/conferences'),('sessionize','https://sessionize.com/speakers-directory')]:
        queue=[seed];visited=set();sources={};errors=[]
        async with StaticFirstReader('CrowdStage discovery validation',deadline=time.monotonic()+150) as reader:
            while queue and len(visited)<6:
                url=queue.pop(0)
                if url in visited:continue
                visited.add(url)
                try:
                    page=await reader.read(url);children=extract(page,adapter,seed)
                    for item in children:
                        if item['task_kind']=='source':sources.setdefault(item['url'],item['payload'])
                    pages=[x for x in children if x['task_kind']=='page']
                    pages.sort(key=lambda x:x['payload'].get('phase')!='detail')
                    queue.extend(x['url'] for x in pages if x['url'] not in visited and x['url'] not in queue)
                except Exception as exc:errors.append({'url':url,'error':str(exc)[:400]})
            output.append({'adapter':adapter,'pages':len(visited),'candidate_urls':len(sources),'transport':reader.stats,'examples':list(sources)[:8],'errors':errors})
    Path('bulk-probe-report.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps(output,ensure_ascii=True))
    if any(x['candidate_urls']==0 for x in output):raise RuntimeError('One adapter found no source links')

asyncio.run(main())
