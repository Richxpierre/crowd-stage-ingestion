import unittest
from pathlib import Path
from unittest.mock import patch
from collector.sync import sync_one, is_directory_target
from collector.official import parse_official_html
from scripts.import_url_candidates import candidates

FIXTURE = Path(__file__).parent / 'fixtures'
SOURCE = dict(id='s-01',slug='approved',publisher='official.org',kind='ics',
    record_scope='conference',
    url='https://official.org/conferences.ics',is_official_source=True,
    ingestion_allowed=True,permission_reviewed_at='2026-10-30T00:00:00Z',
    permission_basis='Approved collection of organizer calendar')

class FakeDB:
    def __init__(self):
        self.events=[];self.runs=[];self.sources=[]
    def create_run(self,source_id):return 'r1'
    def update_run(self,run_id,data):self.runs.append(data)
    def update_source(self,source_id,data):self.sources.append(data)
    def ingest(self,source_id,records):
        self.events.extend(records)
        return {'created':len(records),'updated':0,'unchanged':0,'skipped':0}

class Tests(unittest.TestCase):
    def test_approved_direct_feed(self):
        class FakeFetcher:
            def __init__(self,*a,**kw):pass
            def fetch(self,*a,**kw):return 200,(FIXTURE/'feed.ics').read_bytes(),{'ETag':'"abc"'}
        db=FakeDB()
        with patch('collector.sync.Fetcher',FakeFetcher),patch.dict('os.environ',{'COLLECTOR_USER_AGENT':'CrowdStageBot (+mailto:hello@example.org)'}):
            result=sync_one(db,SOURCE)
        self.assertEqual(result['created_count'],2)
        self.assertEqual(db.runs[-1]['status'],'success')
        self.assertEqual(db.sources[-1]['etag'],'"abc"')

    def test_refuses_directories_and_inactive_permissions(self):
        self.assertTrue(is_directory_target('https://www.sessionize.com/event'))
        self.assertTrue(is_directory_target('https://conferencegrid.com/conferences'))
        db=FakeDB()
        with self.assertRaises(PermissionError):sync_one(db,{**SOURCE,'url':'https://sessionize.com/e/x'})
        with self.assertRaises(PermissionError):sync_one(db,{**SOURCE,'permission_reviewed_at':None})
        self.assertFalse(db.events)

    def test_css_page_identity_stable_after_rename(self):
        src={'url':'https://official.org/event-2027','publisher':'official.org','selectors':{'title':'h1','start_date':'time'}}
        a=parse_official_html(b'<h1>Sample One</h1><time datetime="2027-05-11">11 May</time>',src)
        b=parse_official_html(b'<h1>Sample Two</h1><time datetime="2027-06-02">2 June</time>',src)
        self.assertEqual(a[0]['dedupe_key'],b[0]['dedupe_key'])
        self.assertNotEqual(a[0]['content_hash'],b[0]['content_hash'])

    def test_csv_candidates_do_not_resolve_automatically(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as d:
            file=Path(d)/'leads.csv';file.write_text('url\nhttps://sessionize.com/test\nhttps://sessionize.com/test\n')
            leads=list(candidates(file,'sessionize-snapshot'))
            self.assertEqual(len(leads),1)
            self.assertEqual(leads[0]['review_state'],'needs_review')
            self.assertNotIn('official_url',leads[0])

if __name__=='__main__':unittest.main()

class DiscoveryTests(unittest.TestCase):
    def test_one_time_discovery_has_no_watch_argument(self):
        import subprocess,sys
        result=subprocess.run([sys.executable,'-m','scripts.one_time_discovery','--help'],
                              text=True,capture_output=True,check=True)
        self.assertNotIn('--watch',result.stdout)

    def test_direct_source_pipeline_rejects_discovery_host_subdomains(self):
        self.assertTrue(is_directory_target('https://events.sessionize.com/xyz'))
        self.assertTrue(is_directory_target('https://www.conferencegrid.com/calendar'))

    def test_relative_registration_url(self):
        src={'url':'https://official.org/conference/2027','publisher':'official.org',
             'selectors':{'title':'h1','start_date':'time','registration_url':'.register'}}
        body=b'<h1>Conference 2027</h1><time datetime="2027-05-11">May</time><a class="register" href="/tickets">Register</a>'
        event=parse_official_html(body,src)[0]
        self.assertEqual(event['registration_url'],'https://official.org/tickets')

