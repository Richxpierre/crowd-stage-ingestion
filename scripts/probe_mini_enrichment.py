"""One paid evidence-selection request on a public page; no database access."""
import asyncio
import json
import os
import time
from pathlib import Path
from collector.bulk_discovery import StaticFirstReader
from collector.mini_enrichment import MiniEnricher,WorkerBudget,discover_links

async def main():
    if not os.environ.get('OPENAI_API_KEY'):
        raise RuntimeError('OPENAI_API_KEY is not available to the read-only probe')
    seed='https://conferencemonkey.org/top/conferences'
    deadline=time.monotonic()+90
    async with StaticFirstReader('CrowdStage enrichment integration probe',deadline=deadline) as reader:
        page=await reader.read(seed)
    ai=MiniEnricher(budget=WorkerBudget(calls=1,tokens=15000),deadline=deadline)
    links=await asyncio.to_thread(discover_links,page,seed,ai)
    report={k:v for k,v in ai.audit.items() if k!='cache'}
    report.update(observed_links_selected=len(links),examples=[x['url'] for x in links[:5]])
    Path('mini-enrichment-probe.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
    if ai.audit['calls']!=1 or not links:raise RuntimeError('Read-only AI probe did not select a usable observed link; see its report')

if __name__=='__main__':asyncio.run(main())

