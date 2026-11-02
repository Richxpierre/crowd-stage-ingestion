import unittest
from collector.automatic import parse_page,discover_links,date_range,collect_automatic

URL='https://organizer.org/event'
SOURCE={'url':URL,'name':'Organizer','publisher':'organizer.org','selectors':{'website_only':True}}

class HeroTests(unittest.TestCase):
    def parse(self,html,url=URL):return parse_page(html.encode(),SOURCE,url)
    def test_event_dates_near_heading_do_not_require_address_markup(self):
        r=self.parse('<section><h1>Cloud Conference</h1><p>26–27 April 2027</p><p>University campus</p></section>')
        self.assertEqual((r[0]['start_date'],r[0]['end_date']),('2027-04-26','2027-04-27'))
        self.assertIsNone(r[0]['city']);self.assertIsNone(r[0]['venue'])
    def test_austrian_month_and_two_explicit_dates(self):
        self.assertEqual(date_range('Do., 11. Feber 2027 - Fr., 12. Feber 2027'),('2027-02-11','2027-02-12'))
    def test_wix_secondary_heading_with_complete_event_and_dates(self):
        r=self.parse('<h4>MIRDEC International Academic Conference 24-25 November 2026, Barcelona</h4><h2>Topics</h2>')
        self.assertEqual(r[0]['start_date'],'2026-11-24')
    def test_deadlines_news_and_conflicting_dates_do_not_become_events(self):
        for html in ['<section><h1>Cloud Conference</h1><p>Submission deadline: 26 April 2027</p></section>',
                     '<section><h1>Cloud Conference</h1><p>26 April 2027</p><p>29 May 2027</p></section>',
                     '<section><h1>Cloud Conference</h1><p>Updated 26 April 2027</p></section>']:
            self.assertEqual(self.parse(html),[])
        self.assertEqual(self.parse('<section><h1>Cloud Conference</h1><p>26 April 2027</p></section>','https://organizer.org/news/announcement'),[])
    def test_missing_href_does_not_create_more_or_download_urls(self):
        links=discover_links(b'<a>More</a><a>Download</a><a href="/event/2027">Conference</a>',URL,URL,website_only=True)
        self.assertEqual(links,['https://organizer.org/event/2027'])
    def test_complete_edition_header_does_not_crawl_registration_pages(self):
        class Fetcher:
            calls=[]
            def fetch(self,url):
                self.calls.append(url)
                if url!=URL:raise AssertionError('Unneeded page fetched')
                return 200,b'<section><h1>Cloud Conference</h1><p>26-27 April 2027</p></section><a href="/registration">Registration</a>',{}
        fetcher=Fetcher();records,report=collect_automatic(fetcher,SOURCE)
        self.assertEqual(len(records),1);self.assertEqual(fetcher.calls,[URL])

if __name__=='__main__':unittest.main()
