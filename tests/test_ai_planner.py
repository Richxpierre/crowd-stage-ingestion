import asyncio
import json
import os
import unittest
from unittest.mock import patch
from collector.ai_planner import plan,propose,selected,run_mapped_job,page_evidence

SEED='https://directory.org/'
def page(url,html):return {'url':url,'html':html}
def step(follow='',website='',next=''):
    return {'follow_selector':follow,'website_selector':website,'pagination_selector':next,'explanation':'Observed links'}

class Reader:
    pages={}
    def __init__(self,*a,**kw):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*a):pass
    async def read(self,url):return self.pages[url]

class DB:
    def __init__(self):self.records=[];self.updates=[]
    def crawl_job(self,id):return {'status':'running','cancel_requested':False}
    def update_crawl_job(self,id,value):self.updates.append(value)
    def record_discovery(self,*a):self.records.append(a);return {'status':'needs_review'}

class PlannerTests(unittest.TestCase):
    def setUp(self):
        Reader.pages={SEED:page(SEED,'<a class="speaker" href="/speaker">Speaker</a>'),SEED+'speaker':page(SEED+'speaker','<section id="sessions"><a href="https://talk.org/">Talk</a></section><section id="events"><a href="https://conf.org/">Conference 2024</a><a href="https://youtube.com/video">Video</a></section>')}
    def test_profile_event_path_is_tested_not_invented(self):
        ask=lambda p,h:step(follow='a.speaker') if not h else step(website='#events a')
        report=asyncio.run(plan(Reader(),SEED,lambda:True,ask))
        self.assertTrue(report['validated']);self.assertEqual(len(report['steps']),2)
        self.assertEqual(report['examples'],[{'url':'https://conf.org/','label':'Conference 2024'}])
    def test_missing_selector_never_claims_validation(self):
        report=asyncio.run(plan(Reader(),SEED,lambda:True,lambda p,h:step(follow='.missing')))
        self.assertFalse(report['validated'])
    def test_cancellation_and_bad_selector(self):
        with self.assertRaisesRegex(RuntimeError,'cancelled'):asyncio.run(plan(Reader(),SEED,lambda:False))
        with self.assertRaises(Exception):selected(Reader.pages[SEED],'[',SEED)
    def test_nonanchor_and_foreign_navigation_ignored(self):
        p=page(SEED,'<div data-url="https://evil.org/">x</div><a href="https://evil.org/">out</a><a href="http://127.0.0.1/">private</a>')
        self.assertEqual(selected(p,'*',SEED),[])
        self.assertEqual(selected(p,'div',SEED,True),[])
    def test_scrub_page_evidence(self):
        p=page(SEED,'<p>Private biography</p><form><input value="secret"/></form><script>secret</script><div class="events"><a href="/event">Conference</a></div>')
        evidence=json.dumps(page_evidence(p))
        self.assertNotIn('secret',evidence);self.assertNotIn('Private biography',evidence);self.assertIn('Conference',evidence)
    def test_response_api_and_key_not_in_payload(self):
        class Response:
            status_code=200
            def json(self):return {'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(step(follow='a.speaker'))}]}]}
        class Session:
            def post(self,url,**kw):
                self.kw=kw;return Response()
        session=Session()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test-key'}):propose(Reader.pages[SEED],[],session)
        self.assertFalse(session.kw['json']['store']);self.assertTrue(session.kw['json']['text']['format']['strict'])
        self.assertNotIn('test-key',json.dumps(session.kw['json']))
    def test_execution_uses_sequence_and_only_reviews_sources(self):
        mapping={'validated':True,'start_url':SEED,'steps':[step(follow='a.speaker'),step(website='#events a')]}
        db=DB();job={'id':'job','config':{'url':SEED,'max_pages':4,'max_sources':3,'settings':{'crawl_plan':mapping}}}
        result=asyncio.run(run_mapped_job(db,job,Reader))
        self.assertEqual(result['needs_review'],1);self.assertEqual(db.records[0][2],'https://conf.org/')
        self.assertEqual(db.records[0][3],'needs_review')
    def test_quota_and_temporary_rate_limits_are_distinguished(self):
        class Response:
            status_code=429
            def __init__(self,code):self.code=code
            def json(self):return {'error':{'code':self.code,'message':'untrusted provider text'}}
        class Session:
            def __init__(self,code):self.code=code
            def post(self,*a,**kw):return Response(self.code)
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test-key'}):
            for code,message in [('insufficient_quota','quota unavailable'),('credit_balance_exhausted','quota unavailable'),('project_spend_limit_exceeded','quota unavailable'),('rate_limit_exceeded','temporary rate limit')]:
                with self.assertRaisesRegex(RuntimeError,message):propose(Reader.pages[SEED],[],Session(code))

if __name__=='__main__':unittest.main()
