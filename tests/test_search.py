import asyncio
import json
import os
import unittest
from unittest.mock import patch
from collector.search import SearXNG,result_url,search_queries,conference_page
from collector.discovery import run_job

SEED='https://directory.org/'
def page(url,html):return {'url':url,'html':html,'markdown':'','links':[]}

class Response:
    status_code=200
    def __init__(self,payload):self.body=json.dumps(payload).encode()
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,*args):yield self.body

class Session:
    def __init__(self,response):self.response=response;self.calls=[]
    def get(self,*args,**kwargs):self.calls.append((args,kwargs));return self.response
    def close(self):pass

class SearchTests(unittest.TestCase):
    def test_json_contract_dedup_and_credentials_never_redirect(self):
        session=Session(Response({'results':[{'url':'http://conf.org/?utm_source=x#bio','title':'Conference','engines':['bing']},{'url':'https://conf.org/','title':'Same'},{'url':'https://127.0.0.1/'}]}))
        with patch.dict(os.environ,{'SEARXNG_URL':'http://127.0.0.1:8888'}):
            client=SearXNG(session=session);r=client.search('cloud conference')
        self.assertEqual(len(r['results']),1)
        self.assertEqual(r['results'][0]['url'],'https://conf.org/')
        self.assertEqual(session.calls[0][1]['params']['format'],'json')
        self.assertFalse(session.calls[0][1]['allow_redirects'])

    def test_bad_formats_size_rate_limit_and_warnings(self):
        for code,message in [(403,'enable search.formats'),(429,'rate limited'),(302,'HTTP 302')]:
            response=Response({});response.status_code=code
            with patch.dict(os.environ,{'SEARXNG_URL':'http://127.0.0.1:8888'}):
                with self.assertRaisesRegex(RuntimeError,message):SearXNG(session=Session(response)).search('query')
        with patch.dict(os.environ,{'SEARXNG_URL':'http://127.0.0.1:8888'}):
            response=Response({});response.body=b'x'*(2*1024*1024+1)
            with self.assertRaisesRegex(ValueError,'2 MB'):SearXNG(session=Session(response)).search('query')
            with self.assertRaisesRegex(ValueError,'result shape'):SearXNG(session=Session(Response({'results':None}))).search('query')
            r=SearXNG(session=Session(Response({'results':[],'unresponsive_engines':[['bing','timeout']]}))).search('query')
            self.assertIn('timeout',r['warnings'])

    def test_endpoint_and_result_restrictions(self):
        for endpoint in ['http://private.internal','http://127.0.0.1:9000','https://user:password@search.org','https://search.org/redirect']:
            with patch.dict(os.environ,{'SEARXNG_URL':endpoint}):
                with self.assertRaises(ValueError):SearXNG()
        for url in ['javascript:alert(1)','http://127.0.0.1/','https://private.local/','https://u:p@conf.org/','https://conf.org:444/']:
            self.assertIsNone(result_url(url))

    def test_queries_and_relevance_not_snippet_verification(self):
        self.assertIn('site:directory.org',search_queries(SEED,'Directory',{})[0])
        self.assertEqual(search_queries(SEED,'Directory',{'search_queries':[' cloud expo ','cloud expo']}),['cloud expo'])
        with self.assertRaises(ValueError):search_queries(SEED,'Directory',{'search_queries':'not a list'})
        self.assertIsNone(conference_page(page('https://conf.org/','<h1>Hosting account suspended</h1>')))
        self.assertTrue(conference_page(page('https://conf.org/','<h1>Cloud Conference</h1>')))

class Reader:
    calls=[];pages={}
    def __init__(self,*a,**kw):self.options=kw
    async def __aenter__(self):return self
    async def __aexit__(self,*a):pass
    async def read(self,url):
        self.calls.append(url)
        p=self.pages[url]
        if isinstance(p,Exception):raise p
        return p

class DB:
    def __init__(self,cancel=False):self.records=[];self.updates=[];self.cancel=cancel
    def recent_crawled_pages(self,*a):return set()
    def crawl_job(self,*a):return {'status':'running','cancel_requested':self.cancel}
    def update_crawl_job(self,id,record):self.updates.append(record)
    def record_discovery(self,*args):self.records.append(args);return {'status':'added' if args[3]=='linked' else args[3]}

class Search:
    calls=[];failure=False
    def __init__(self,**kw):pass
    def close(self):pass
    def search(self,q):
        self.calls.append(q)
        if self.failure:raise RuntimeError('provider unavailable')
        return {'results':[{'url':SEED+'conference/cloud','title':'Cloud','query':q,'engines':['bing']},{'url':'https://newconf.org/','title':'New','query':q,'engines':['bing']}],'warnings':None}

class HybridTests(unittest.TestCase):
    def setUp(self):
        Reader.calls=[];Search.calls=[];Search.failure=False
        Reader.pages={SEED:page(SEED,'<h1>Directory</h1>'),SEED+'conference/cloud':page(SEED+'conference/cloud','<h1>Cloud conference</h1><a href="https://official.org/">Official website</a>'),'https://newconf.org/':page('https://newconf.org/','<h1>New Conference</h1>')}
        self.job={'id':'job','target_id':'target','config':{'url':SEED,'max_pages':4,'max_sources':3,'settings':{'search_enabled':True,'search_queries':['cloud conference']}}}
    def test_search_navigates_aggregator_and_reviews_external_after_visit(self):
        db=DB();r=asyncio.run(run_job(db,self.job,Reader,Search))
        self.assertEqual(r['sources_added'],1);self.assertEqual(r['needs_review'],1)
        external=[x for x in db.records if x[2]=='https://newconf.org/'][0]
        self.assertEqual(external[3],'needs_review')
        self.assertEqual(external[7]['method'],'searxng_crawl4ai')
        self.assertIn('https://newconf.org/',Reader.calls)
        self.assertNotIn('https://official.org/',Reader.calls)
    def test_search_failure_preserves_regular_crawling(self):
        Search.failure=True;Reader.pages[SEED]=Reader.pages[SEED+'conference/cloud']
        db=DB();r=asyncio.run(run_job(db,self.job,Reader,Search))
        self.assertEqual(r['sources_added'],1)
        self.assertIn('provider unavailable',db.updates[-1]['errors'][0]['message'])
    def test_disabled_or_cancelled_never_searches(self):
        self.job['config']['settings']['search_enabled']=False
        asyncio.run(run_job(DB(),self.job,Reader,Search));self.assertEqual(Search.calls,[])
        self.job['config']['settings']['search_enabled']=True;Reader.calls=[]
        r=asyncio.run(run_job(DB(cancel=True),self.job,Reader,Search))
        self.assertEqual(r['status'],'cancelled');self.assertEqual(Search.calls,[]);self.assertEqual(Reader.calls,[])
    def test_irrelevant_or_blocked_external_hit_not_registered(self):
        for response in [page('https://newconf.org/','<h1>Parked domain</h1>'),PermissionError('Blocked')]:
            Reader.pages['https://newconf.org/']=response
            db=DB();asyncio.run(run_job(db,self.job,Reader,Search))
            self.assertFalse(any(r[2]=='https://newconf.org/' for r in db.records))

@unittest.skipUnless(os.environ.get('RUN_SEARXNG_TESTS')=='1','Requires private SearXNG container')
class ServiceSmokeTests(unittest.TestCase):
    def test_actual_container_json_api(self):
        client=SearXNG()
        try:
            result=client.search('conference official website')
            self.assertIsInstance(result['results'],list)
            # External engines may be blocked on CI. Empty results must remain empty.
            print('SearXNG live search:',len(result['results']),'results; warnings:',result['warnings'])
        finally:client.close()

if __name__=='__main__':unittest.main()
