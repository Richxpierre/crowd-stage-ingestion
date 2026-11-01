import asyncio
from datetime import date,timedelta
import json
import os
import unittest
from collector.discovery import classify_links,verify_organizer,run_job,page_links
from collector.crawler_reader import same_owner,Crawl4AIReader

SEED='https://directory.org/'
OFFICIAL='https://organizer.org/'
FUTURE=(date.today()+timedelta(days=90)).isoformat()
EVENT={'@type':'Event','name':'Cloud Conference','startDate':FUTURE,'endDate':FUTURE,'url':OFFICIAL}
def page(url,html):return {'url':url,'html':html,'links':[],'markdown':''}
LISTING=page(SEED,'<a href="https://organizer.org/">Official website</a>')
def eventpage(event=EVENT):return page(OFFICIAL,'<script type="application/ld+json">'+json.dumps(event)+'</script>')

class Reader:
    pages={SEED:LISTING,OFFICIAL:eventpage()}
    calls=[]
    def __init__(self,*args,**kwargs):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def read(self,url):
        self.calls.append(url)
        value=self.pages[url]
        if isinstance(value,Exception):raise value
        return value

class DB:
    def __init__(self):self.updates=[];self.records=[];self.cancel=False;self.existing=False
    def recent_crawled_pages(self,target):return set()
    def crawl_job(self,id):return {'status':'running','cancel_requested':self.cancel}
    def update_crawl_job(self,id,record):self.updates.append(record)
    def record_discovery(self,*args):
        self.records.append(args)
        return {'status':('existing' if self.existing else 'added') if args[3]=='verified' else args[3]}

class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        Reader.pages={SEED:LISTING,OFFICIAL:eventpage()};Reader.calls=[]
        self.job={'id':'job','target_id':'target','config':{'url':SEED,'max_pages':3,'max_sources':2}}
    def test_host_boundaries_and_private_tenants(self):
        self.assertTrue(same_owner('https://www.organizer.co.uk/a','https://organizer.co.uk/'))
        for url in ['https://evil.org/','https://directory.org.evil.org/','http://directory.org/','https://a:b@directory.org/','https://directory.org:444/']:
            self.assertFalse(same_owner(url,SEED))
        self.assertFalse(same_owner('https://other.blogspot.com/','https://one.blogspot.com/'))
    def test_listing_links_are_not_official_sources(self):
        internal,external=classify_links(page(SEED,'<a href="/conference/cloud">Cloud conference</a><a href="https://facebook.com/cloud">Official website</a><a href="https://organizer.org/">Official website</a>'),SEED,{})
        self.assertEqual(internal,[SEED+'conference/cloud'])
        self.assertEqual([x['url'] for x in external],[OFFICIAL])
    def test_sitemap_and_xml_entities(self):
        p=page(SEED+'events.xml','<urlset><url><loc>https://directory.org/cloud</loc></url></urlset>')
        self.assertEqual(classify_links(p,SEED,{'listing_pattern':'.*'})[0],[SEED+'cloud'])
        with self.assertRaises(ValueError):page_links({**p,'html':'<!DOCTYPE bad><urlset/>'})
    def test_upcoming_organizer_verified(self):
        self.assertEqual(asyncio.run(verify_organizer(Reader(),OFFICIAL))['event']['title'],'Cloud Conference')
    def test_past_and_cancelled_not_verified(self):
        for change in [{'startDate':'2020-01-01','endDate':'2020-01-02'},{'eventStatus':'https://schema.org/EventCancelled'}]:
            Reader.pages[OFFICIAL]=eventpage({**EVENT,**change})
            self.assertIsNone(asyncio.run(verify_organizer(Reader(),OFFICIAL)))
    def test_verified_sources_and_duplicate_counts(self):
        for existing in [False,True]:
            db=DB();db.existing=existing
            result=asyncio.run(run_job(db,self.job,Reader))
            self.assertEqual(result['duplicates' if existing else 'sources_added'],1)
            self.assertEqual(db.records[0][3],'verified')
            self.assertEqual(result['status'],'completed')
    def test_blocked_organizer_goes_to_review(self):
        Reader.pages[OFFICIAL]=PermissionError('robots denied')
        db=DB();result=asyncio.run(run_job(db,self.job,Reader))
        self.assertEqual(result['needs_review'],1)
        self.assertEqual(db.records[0][3],'needs_review')
        self.assertEqual(result['status'],'partial')
    def test_cancel_stops_before_fetch_or_insert(self):
        db=DB();db.cancel=True
        result=asyncio.run(run_job(db,self.job,Reader))
        self.assertEqual(result['status'],'cancelled');self.assertEqual(Reader.calls,[]);self.assertEqual(db.records,[])
    def test_failed_seed_uses_public_index(self):
        Reader.pages={SEED:RuntimeError('403'),SEED+'llms.txt':LISTING,OFFICIAL:eventpage()}
        self.job['config']['settings']={'fallback_seeds':[SEED+'llms.txt','https://evil.org/']}
        db=DB();result=asyncio.run(run_job(db,self.job,Reader))
        self.assertEqual(result['sources_added'],1);self.assertNotIn('https://evil.org/',Reader.calls)

@unittest.skipUnless(os.environ.get('RUN_CRAWLER_TESTS')=='1','Requires Crawl4AI and Chromium')
class Crawl4AIIntegrationTests(unittest.TestCase):
    def test_real_sdk_extracts_rendered_links_and_blocks_foreign_requests(self):
        body=('''<html><head><title>Conference directory</title></head><body><h1>Upcoming conferences</h1><p>'''+
            'Explore upcoming technology conferences and trade shows with their official organizer websites. '*30+
            '''</p><div id="events"></div><script>document.querySelector('#events').innerHTML='<a href="https://organizer.org/">Official website</a>';fetch('https://foreign.org/private');fetch('https://127.0.0.1/private');</script></body></html>''').encode()
        class Fetcher:
            calls=[]
            def fetch(self,url):
                self.calls.append(url)
                if url!=SEED:raise AssertionError('Unexpected network request: '+url)
                return 200,body,{'Content-Type':'text/html'}
        fetcher=Fetcher()
        async def run():
            async with Crawl4AIReader('CrowdStage test',fetcher=fetcher) as reader:return await reader.read(SEED)
        result=asyncio.run(run())
        self.assertEqual([x['url'] for x in classify_links(result,SEED,{})[1]],[OFFICIAL])
        self.assertEqual(fetcher.calls,[SEED])
