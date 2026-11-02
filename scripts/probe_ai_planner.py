"""Read-only live API/Crawl4AI check; never connects to the event database."""
import asyncio
import json
import os
import time
from pathlib import Path
from collector.ai_planner import plan
from collector.crawler_reader import Crawl4AIReader

async def main():
    if not os.environ.get('OPENAI_API_KEY'):raise RuntimeError('OPENAI_API_KEY is not available to the test worker')
    async with Crawl4AIReader('CrowdStage AI integration test',deadline=time.monotonic()+300) as reader:
        result=await plan(reader,'https://conferencemonkey.org/top/conferences',lambda:True)
    Path('ai-plan-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('AI_PLAN_RESULT='+json.dumps(result,ensure_ascii=True))
    if not result['validated']:raise RuntimeError('AI plan did not verify a complete path; review the report')

asyncio.run(main())
