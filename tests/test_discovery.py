import asyncio
import os
import unittest
from collector.discovery import classify_links,run_job,page_links
from collector.crawler_reader import same_owner,Crawl4AIReader

SEED='https://directory.org/'
OFFICIAL='https://organizer.org/'
def page(url,html):return {'url':url,'html':html,'links':[],'markdown':''}
LISTING=page(SEED,'<a href="https://organizer.org/">Official website</a>')

class Reader:
    pages={SEED:LISTING}
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
        return {'status':('existing' if self.existing else 'added') if args[3]=='linked' else args[3]}

class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        Reader.pages={SEED:LISTING};Reader.calls=[]
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
    def test_markdown_images_and_navigation_are_not_organizers(self):
        p=page(SEED,'<a href="/top/conferences">Conferences</a><a href="/conference/real-2027">Conference</a>')
        p['markdown']='![Website](https://cdn.org/banner.png) [Official website](https://organizer.org/) [Website](https://cdn.org/logo.svg)'
        internal,official=classify_links(p,SEED,{})
        self.assertEqual(internal[0],SEED+'conference/real-2027')
        self.assertEqual([x['url'] for x in official],[OFFICIAL])
    def test_dated_news_and_cancelled_headings_do_not_create_events(self):
        from collector.automatic import parse_page
        source={'url':OFFICIAL,'name':'Cloud Conference','publisher':'organizer.org','selectors':{}}
        self.assertEqual(parse_page(b'<h1>Cloud Conference, November 18, 2027</h1>',source,OFFICIAL+'news/announcement'),[])
        self.assertEqual(parse_page(b'<h1>Cancelled Cloud Conference, November 18, 2027</h1>',source,OFFICIAL),[])
    def test_button_links_and_duplicate_counts(self):
        for existing in [False,True]:
            db=DB();db.existing=existing
            result=asyncio.run(run_job(db,self.job,Reader))
            self.assertEqual(result['duplicates' if existing else 'sources_added'],1)
            self.assertEqual(db.records[0][3],'linked')
            self.assertEqual(result['status'],'completed')
    def test_organizer_unavailability_does_not_block_source_url(self):
        Reader.pages[OFFICIAL]=PermissionError('Organizer unavailable')
        db=DB();result=asyncio.run(run_job(db,self.job,Reader))
        self.assertEqual(result['sources_added'],1)
        self.assertEqual(result['needs_review'],0)
        self.assertEqual(result['status'],'completed')
        self.assertNotIn(OFFICIAL,Reader.calls)
    def test_listing_detail_visit_conference_button_is_all_that_is_required(self):
        detail=SEED+'conference/cloud'
        Reader.pages={SEED:page(SEED,'<a href="/conference/cloud">Cloud conference</a>'),detail:page(detail,'<h1>Cloud Conference</h1><a href="https://organizer.org/">Visit conference</a>')}
        db=DB();result=asyncio.run(run_job(db,self.job,Reader))
        self.assertEqual(Reader.calls,[SEED,detail])
        self.assertEqual(result['sources_added'],1)
        self.assertEqual(db.records[0][2:7],(OFFICIAL,'linked','Cloud Conference',None,None))
        self.assertEqual(db.records[0][7]['method'],'aggregator_website_link')
    def test_explicit_website_button_wins_over_nearby_registration_text(self):
        html='<p>Website: <a href="https://organizer.org/">organizer.org</a> Apply: <a href="https://organizer.org/registration-form/">Registration</a></p><a href="https://organizer.org/">Visit Website</a>'
        _,links=classify_links(page(SEED+'conference/cloud',html),SEED,{})
        self.assertEqual([item['url'] for item in links],[OFFICIAL])
        self.assertTrue(links[0]['label'].startswith('Visit Website'))
    def test_listing_pagination_does_not_consume_event_detail_depth(self):
        Reader.pages={SEED:page(SEED,'<a href="?page=2">Next page</a>'),SEED+'?page=2':page(SEED+'?page=2','<a href="?page=3">Next page</a>'),SEED+'?page=3':page(SEED+'?page=3','<a href="/conference/cloud">Cloud conference</a>'),SEED+'conference/cloud':LISTING}
        self.job['config'].update(max_depth=1,max_pages=4)
        result=asyncio.run(run_job(DB(),self.job,Reader))
        self.assertEqual(result['sources_added'],1)
        self.assertIn(SEED+'conference/cloud',Reader.calls)
    def test_cancel_stops_before_fetch_or_insert(self):
        db=DB();db.cancel=True
        result=asyncio.run(run_job(db,self.job,Reader))
        self.assertEqual(result['status'],'cancelled');self.assertEqual(Reader.calls,[]);self.assertEqual(db.records,[])
    def test_failed_seed_uses_public_index(self):
        Reader.pages={SEED:RuntimeError('403'),SEED+'llms.txt':LISTING}
        self.job['config']['settings']={'fallback_seeds':[SEED+'llms.txt','https://evil.org/']}
        db=DB();result=asyncio.run(run_job(db,self.job,Reader))
        self.assertEqual(result['sources_added'],1);self.assertNotIn('https://evil.org/',Reader.calls)

@unittest.skipUnless(os.environ.get('RUN_CRAWLER_TESTS')=='1','Requires Crawl4AI and Chromium')
class Crawl4AIIntegrationTests(unittest.TestCase):
    def test_direct_browser_mode_extracts_without_http_prefetch(self):
        body=('<html><head><title>Conference directory</title></head><body><h1>Upcoming conferences</h1><p>'+('Browse public conference listings and official organizer websites. '*40)+'</p><script>document.body.insertAdjacentHTML("beforeend",\'<a href="https://organizer.org/">Official website</a>\');fetch("https://foreign.org/private");</script></body></html>')
        class FixtureReader(Crawl4AIReader):
            async def _guard_context(self,page,context,**kwargs):
                await super()._guard_context(page,context,**kwargs)
                await context.route(SEED,lambda route:route.fulfill(status=200,body=body,content_type='text/html'))
        async def run():
            async with FixtureReader('CrowdStage test',skip_robots_for=SEED) as reader:
                reader.policy_fetcher.fetch=lambda *args: (_ for _ in ()).throw(AssertionError('HTTP prefetch ran'))
                reader.direct_fetcher.fetch=reader.policy_fetcher.fetch
                return await reader.read(SEED)
        from unittest.mock import patch
        with patch('collector.crawler_reader.host_of',return_value='directory.org'):
            result=asyncio.run(run())
        self.assertEqual([x['url'] for x in classify_links(result,SEED,{})[1]],[OFFICIAL])

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
