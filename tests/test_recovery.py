import json
import unittest
from unittest.mock import patch
import requests
from collector.recovery import collect_source,fallback_urls,reconcile_identities,RecoveryFailure
from collector.feeds import parse_feed
from collector.fetch import UnsafeDestination,RobotsPathDenied
from collector.sync import sync_one
from test_ingestion import SOURCE,FakeDB

ROOT='https://official.org/'
FEED=ROOT+'calendar.xml'
SOURCE_FEED={**SOURCE,'url':FEED,'kind':'auto','name':'Cloud Conference','selectors':{'render':False,'max_pages':3}}
EVENT={'@type':'Event','name':'Cloud Conference','startDate':'2027-06-02','endDate':'2027-06-03','url':ROOT+'event-2027/'}
PAGE=('<script type="application/ld+json">'+json.dumps(EVENT)+'</script>').encode()

class FakeFetcher:
    def __init__(self,pages):self.pages=pages;self.calls=[]
    def fetch(self,url,*cache):
        self.calls.append((url,cache))
        value=self.pages.get(url,RuntimeError('404 not found'))
        if isinstance(value,Exception):raise value
        return 200,value,{}

class RecoveryDB(FakeDB):
    def observations(self,source_id):return []

class RecoveryTests(unittest.TestCase):
    def test_missing_feed_falls_back_to_website(self):
        fetcher=FakeFetcher({FEED:RuntimeError('404 feed missing'),ROOT:PAGE})
        records,code,_,report=collect_source(fetcher,SOURCE_FEED)
        self.assertEqual(len(records),1)
        self.assertEqual(report['mode'],'website_fallback')
        self.assertEqual(report['fallback_url'],ROOT)
        self.assertEqual(report['configured_url'],FEED)
        self.assertIn('404',report['primary_error'])
        self.assertEqual(code,200)

    def test_malformed_rss_and_xml_trigger_discovery(self):
        for body in (b'<rss><channel><item>broken',b'<events><bad',b'<rss><channel/></rss>'):
            with self.subTest(body=body):
                rows,_,_,report=collect_source(FakeFetcher({FEED:body,ROOT:PAGE}),SOURCE_FEED)
                self.assertEqual(len(rows),1)
                self.assertEqual(report['mode'],'website_fallback')

    def test_direct_ics_failure_also_recovers(self):
        source={**SOURCE_FEED,'kind':'ics','url':ROOT+'calendar.ics'}
        rows,_,_,report=collect_source(FakeFetcher({source['url']:requests.Timeout('feed timeout'),ROOT:PAGE}),source)
        self.assertEqual(rows[0]['start_date'],'2027-06-02')
        self.assertEqual(report['mode'],'website_fallback')

    def test_broken_linked_feed_does_not_hide_valid_root_event(self):
        fetcher=FakeFetcher({ROOT:PAGE+b'<a href="/broken.xml">Event calendar</a>'})
        rows,_,_,report=collect_source(fetcher,{**SOURCE_FEED,'url':ROOT})
        self.assertEqual(len(rows),1)
        self.assertEqual(report['mode'],'primary')

    def test_rss_follows_actual_event_links_not_publication_dates(self):
        rss=b'<rss><channel><item><title>Conference announcement</title><pubDate>Tue, 01 Jun 2027 00:00:00 GMT</pubDate><link>https://official.org/event-2027/</link></item></channel></rss>'
        self.assertEqual(parse_feed(rss,SOURCE_FEED),[])
        rows,_,_,report=collect_source(FakeFetcher({FEED:rss,ROOT+'event-2027/':PAGE}),SOURCE_FEED)
        self.assertEqual(rows[0]['start_date'],'2027-06-02')
        self.assertEqual(report['mode'],'primary')

    def test_explicit_rss_and_atom_event_dates(self):
        rss=b'<rss><channel><item><guid>one</guid><title>Cloud Conference</title><startDate>2027-06-02</startDate><endDate>2027-06-03</endDate><link>https://official.org/event-2027/</link></item></channel></rss>'
        atom=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>one</id><title>Cloud Conference</title><startDate>2027-06-02</startDate><endDate>2027-06-03</endDate><link href="https://official.org/event-2027/"/></entry></feed>'
        for body in (rss,atom):
            rows=parse_feed(body,SOURCE_FEED)
            self.assertEqual(rows[0]['source_record_id'],'one')
            self.assertEqual(rows[0]['end_date'],'2027-06-03')

    def test_domain_boundaries_and_feed_subdomains(self):
        for domain in ('conference.com','conference.org','conference.net','conference.co.uk'):
            urls=fallback_urls({**SOURCE_FEED,'url':'https://feeds.'+domain+'/rss'})
            self.assertEqual(urls,['https://feeds.'+domain+'/','https://'+domain+'/'])
        self.assertEqual(fallback_urls({**SOURCE_FEED,'url':'https://feeds.blogspot.com/rss'}),['https://feeds.blogspot.com/'])
        self.assertEqual(fallback_urls({**SOURCE_FEED,'url':'https://conference.org/'}),[])
        self.assertNotIn('https://conference.com/',fallback_urls({**SOURCE_FEED,'url':'https://conference.org/rss'}))

    def test_explicit_fallback_and_disable(self):
        source={**SOURCE_FEED,'selectors':{'fallback_url':'https://conference.net/events'}}
        self.assertEqual(fallback_urls(source)[0],'https://conference.net/events')
        fetcher=FakeFetcher({FEED:RuntimeError('broken')})
        with self.assertRaises(RecoveryFailure):
            collect_source(fetcher,{**SOURCE_FEED,'selectors':{'website_fallback':False}})
        self.assertEqual([u for u,_ in fetcher.calls],[FEED])

    def test_robots_and_private_destinations_cannot_trigger_recovery(self):
        for error in (PermissionError('Robots denied'),UnsafeDestination('Private network blocked')):
            fetcher=FakeFetcher({FEED:error,ROOT:PAGE})
            with self.assertRaises(type(error)):collect_source(fetcher,SOURCE_FEED)
            self.assertEqual(len(fetcher.calls),1)

    def test_rate_limit_waits_for_cooldown(self):
        response=requests.Response();response.status_code=429
        fetcher=FakeFetcher({FEED:requests.HTTPError('Rate limited',response=response),ROOT:PAGE})
        with self.assertRaises(requests.HTTPError):collect_source(fetcher,{**SOURCE_FEED,'kind':'ics'})
        self.assertEqual(len(fetcher.calls),1)

    def test_a_disallowed_feed_path_can_use_a_permitted_public_homepage(self):
        fetcher=FakeFetcher({FEED:RobotsPathDenied('Feed path disallowed'),ROOT:PAGE})
        rows,_,_,report=collect_source(fetcher,SOURCE_FEED)
        self.assertEqual(len(rows),1)
        self.assertEqual(report['mode'],'website_fallback')

    def test_both_paths_fail_retains_diagnostics(self):
        with self.assertRaises(RecoveryFailure) as failure:
            collect_source(FakeFetcher({}),SOURCE_FEED)
        self.assertIn('primary_error',failure.exception.report)
        self.assertGreaterEqual(len(failure.exception.report['attempts']),2)

    def test_recovery_preserves_existing_feed_identity(self):
        class DB:
            def observations(self,_):return [{'source_record_id':'original-calendar-uid','payload':{
                'title':'Cloud Conference','event_url':ROOT+'event-2027/','start_date':'2027-06-02','end_date':'2027-06-03'}}]
        rows,_,_,_=collect_source(FakeFetcher({ROOT:PAGE}),SOURCE_FEED)
        self.assertEqual(reconcile_identities(DB(),SOURCE_FEED,rows)[0]['source_record_id'],'original-calendar-uid')

    def test_returning_feed_matches_fallback_edition_without_url(self):
        class DB:
            def observations(self,_):return [{'source_record_id':'website-edition-id','payload':{
                'title':'Cloud Conference','event_url':ROOT+'event-2027/','start_date':'2027-06-02','end_date':'2027-06-03'}}]
        row={'source_record_id':'new-feed-uid','title':'Cloud Conference','start_date':'2027-06-02','end_date':'2027-06-03','event_url':None}
        self.assertEqual(reconcile_identities(DB(),SOURCE_FEED,[row])[0]['source_record_id'],'website-edition-id')

    def test_worker_records_recovery_without_changing_registered_url(self):
        fetcher=FakeFetcher({FEED:RuntimeError('feed broken'),ROOT:PAGE})
        db=RecoveryDB()
        with patch('collector.sync.Fetcher',return_value=fetcher):
            result=sync_one(db,SOURCE_FEED)
        self.assertEqual(result['created_count'],1)
        self.assertEqual(db.runs[-1]['status'],'success')
        self.assertEqual(db.sources[-1]['discovery_report']['mode'],'website_fallback')
        self.assertIsNone(db.sources[-1]['last_error'])
        self.assertFalse(any('url' in update for update in db.sources))

    def test_failed_recovery_preserves_events_and_reports_attention(self):
        db=RecoveryDB();db.events=[{'existing':'untouched'}]
        with patch('collector.sync.Fetcher',return_value=FakeFetcher({})):
            with self.assertRaises(RecoveryFailure):sync_one(db,SOURCE_FEED)
        self.assertEqual(db.events,[{'existing':'untouched'}])
        self.assertEqual(db.runs[-1]['status'],'failed')
        self.assertIn('attempts',db.sources[-1]['discovery_report'])

    def test_primary_feed_is_probed_again_after_recovery(self):
        source={**SOURCE_FEED,'kind':'ics','etag':'old-feed-tag','discovery_report':{
            'mode':'website_fallback','image_extraction_version':1}}
        ics=b'BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:one\nSUMMARY:Cloud Conference\nDTSTART:20270602\nEND:VEVENT\nEND:VCALENDAR'
        fetcher=FakeFetcher({FEED:ics})
        _,_,_,report=collect_source(fetcher,source)
        self.assertEqual(fetcher.calls,[(FEED,(None,None))])
        self.assertEqual(report['mode'],'primary')

    def test_ambiguous_edition_mapping_does_not_create_a_duplicate(self):
        class DB:
            def observations(self,_):return [{'source_record_id':uid,'payload':{
                'title':'Other conference','event_url':ROOT+'event-2027/','start_date':'2027-06-02','end_date':'2027-06-03'}} for uid in ['one','two']]
        rows,_,_,_=collect_source(FakeFetcher({ROOT:PAGE}),SOURCE_FEED)
        with self.assertRaisesRegex(ValueError,'Ambiguous'):
            reconcile_identities(DB(),SOURCE_FEED,rows)

    def test_successful_feed_does_not_visit_website_fallback(self):
        ics=b'BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:one\nSUMMARY:Cloud Conference\nDTSTART:20270602\nEND:VEVENT\nEND:VCALENDAR'
        fetcher=FakeFetcher({FEED:ics})
        rows,_,_,report=collect_source(fetcher,{**SOURCE_FEED,'kind':'ics'})
        self.assertEqual(len(rows),1)
        self.assertEqual(len(fetcher.calls),1)
        self.assertEqual(report['mode'],'primary')

if __name__=='__main__':unittest.main()
