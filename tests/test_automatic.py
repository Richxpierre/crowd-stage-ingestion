import json
import unittest
from unittest.mock import patch
from collector.automatic import date_range, parse_page, collect_automatic, discover_links, same_site
from collector.sync import sync_one
from test_ingestion import SOURCE, FakeDB

ROOT='https://organizer.org/'
AUTO={**SOURCE,'name':'DemoCon','url':ROOT,'kind':'auto','selectors':{'render':False}}
# Representative ordinary HTML, matching the homepage layout inspected at twitchcon.com.
CARDS=b'''<title>TwitchCon</title>
<figure><div><time>November 13-15, 2026</time><address>San Diego Convention Center</address><h3>San Diego</h3></div><a href="/san-diego-2026/">Explore</a></figure>
<figure><time>22-23 May 2027</time><address>Berlin, Germany</address><h3>Berlin</h3></figure>'''

class AutomaticTests(unittest.TestCase):
    def test_empty_h1_is_missing_evidence_instead_of_a_type_error(self):
        self.assertEqual(parse_page(b'<html><h1></h1><p>Welcome to our archive.</p></html>',AUTO,ROOT),[])
    def test_explicit_localized_months_and_semantic_event_header(self):
        self.assertEqual(date_range('08. – 10. Juni 2027'),('2027-06-08','2027-06-10'))
        self.assertEqual(date_range('7–9 avril 2027'),('2027-04-07','2027-04-09'))
        self.assertEqual(date_range('12-13 noviembre 2027'),('2027-11-12','2027-11-13'))
        self.assertIsNone(date_range('11/12/2027'))
        rows=parse_page(b'<title>HICSS | Hawaii International Conference</title><header><h1>HICSS-60</h1><p>January 5-8, 2027</p></header>',AUTO,ROOT)
        self.assertEqual([(r['start_date'],r['end_date']) for r in rows],[('2027-01-05','2027-01-08')])
    def test_date_formats_and_invalid_dates(self):
        cases={'November 13-15, 2026':('2026-11-13','2026-11-15'),
               '22–23 May 2027':('2027-05-22','2027-05-23'),
               'October 30 – November 2, 2027':('2027-10-30','2027-11-02'),
               'June 2, 2027':('2027-06-02','2027-06-02'),
               '2027-06-02T09:00:00Z':('2027-06-02','2027-06-02')}
        for text,expected in cases.items():
            with self.subTest(text=text):self.assertEqual(date_range(text),expected)
        for text in ['June 99, 2027','11/12/2027','November 13-15','December 5-2, 2027']:
            self.assertIsNone(date_range(text))

    def test_multiple_editions_from_one_url_and_unknown_fields(self):
        rows=parse_page(CARDS,AUTO,ROOT)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['title'],'TwitchCon San Diego')
        self.assertEqual(rows[0]['start_date'],'2026-11-13')
        self.assertEqual(rows[0]['event_url'],ROOT+'san-diego-2026/')
        self.assertIsNone(rows[0]['country'])
        self.assertEqual(rows[1]['country'],'Germany')
        self.assertEqual(rows[1]['end_date'],'2027-05-23')
        self.assertEqual(rows[0]['format'],'unknown')
        self.assertNotIn('price',rows[0])
        self.assertIn('November 13-15, 2026',rows[0]['extraction']['evidence'])

    def test_news_dates_do_not_generate_events(self):
        body=b'<title>Company News</title><article><h1>Company update</h1><time>June 2, 2027</time><address>Berlin, Germany</address></article>'
        self.assertEqual(parse_page(body,AUTO,ROOT),[])

    def test_microdata(self):
        body=b'''<div itemscope itemtype="https://schema.org/Event"><span itemprop="name">Cloud Conference</span><meta itemprop="startDate" content="2027-06-02"><span itemprop="location">Berlin</span></div>'''
        rows=parse_page(body,AUTO,ROOT)
        self.assertEqual(rows[0]['title'],'Cloud Conference')
        self.assertEqual(rows[0]['extraction']['method'],'microdata')

    def test_bounded_crawl_finds_linked_calendar_and_keeps_partial_results(self):
        class Fetcher:
            def fetch(self,url):
                if url==ROOT:
                    return 200,b'<a href="/2027/calendar.ics">Calendar</a><a href="/about">About</a>',{}
                if url.endswith('.ics'):
                    return 200,b'BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:conference-2027\nSUMMARY:Cloud Conference\nDTSTART;VALUE=DATE:20270602\nDTEND;VALUE=DATE:20270604\nEND:VEVENT\nEND:VCALENDAR',{}
                raise RuntimeError('404')
        rows,report=collect_automatic(Fetcher(),{**AUTO,'selectors':{'max_pages':3,'render':False}})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['end_date'],'2027-06-03')
        self.assertEqual(report['pages_checked'],3)
        self.assertEqual(len(report['errors']),1)

    def test_external_news_session_and_exit_links_are_not_followed(self):
        body=b'<a href="https://evil.org/event">Outside</a><a href="/news/event">News</a><a href="/schedule/">Talks</a><a href="/exit/?url=https://evil.org">Exit</a><a href="/event-2027/">Event</a>'
        self.assertEqual(discover_links(body,ROOT,ROOT),[ROOT+'event-2027/'])
        for url in ['http://organizer.org/','https://organizer.org.evil.org/','https://organizer.org@evil.org/','https://organizer.org:444/']:
            self.assertFalse(same_site(url,ROOT))
        self.assertTrue(same_site('https://www.organizer.org/event',ROOT))

    def test_homepage_cache_cannot_hide_linked_changes(self):
        calls=[]
        class Fetcher:
            def fetch(self,url,*args):
                calls.append((url,args))
                return 200,CARDS,{}
        collect_automatic(Fetcher(),{**AUTO,'etag':'cached','selectors':{'max_pages':1,'render':False}})
        self.assertEqual(calls,[(ROOT,())])

    def test_auto_worker_imports_and_stores_report_without_publication(self):
        class Fetcher:
            def __init__(self,*args,**kwargs):pass
            def fetch(self,url):return 200,CARDS,{}
        db=FakeDB()
        with patch('collector.sync.Fetcher',Fetcher):
            result=sync_one(db,{**AUTO,'selectors':{'max_pages':1,'render':False}})
        self.assertEqual(result['created_count'],2)
        self.assertTrue(any('discovery_report' in change for change in db.sources))
        self.assertTrue(all(e['status']=='upcoming' for e in db.events))

    def test_js_fallback_is_used_when_plain_html_has_no_events(self):
        class Fetcher:
            def fetch(self,url):return 200,b'<title>DemoCon</title><script src="/app.js"></script>',{}
        with patch('collector.render.render_html',return_value=CARDS):
            rows,report=collect_automatic(Fetcher(),{**AUTO,'selectors':{'max_pages':1}})
        self.assertEqual(len(rows),2)
        self.assertIn('rendered_html',report['methods'])

if __name__=='__main__':unittest.main()
