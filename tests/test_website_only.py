import json,unittest
from collector.automatic import collect_automatic,parse_page
ROOT='https://organizer.org/'
SOURCE={'url':ROOT,'name':'Cloud Conference','publisher':'organizer.org','kind':'auto','selectors':{'website_only':True,'render':False,'max_pages':3}}
EVENT={'@type':'Event','name':'Cloud Conference','startDate':'2027-06-02','endDate':'2027-06-03','url':ROOT+'conference/'}
class WebsiteOnlyTests(unittest.TestCase):
    def test_main_site_can_link_annual_subdomains_without_crossing_tenants(self):
        from collector.automatic import same_site
        self.assertTrue(same_site('https://2027.organizer.org/event','https://organizer.org/'))
        self.assertFalse(same_site('https://other.blogspot.com/','https://one.blogspot.com/'))
        self.assertFalse(same_site('https://organizer.org.evil.org/','https://organizer.org/'))
    def test_crawls_event_pages_without_requesting_linked_feeds(self):
        class Fetcher:
            calls=[]
            def fetch(self,url):
                self.calls.append(url)
                if url==ROOT:return 200,b'<a href="/feed/">RSS</a><a href="/events.xml">XML</a><a href="/calendar.ics">ICS</a><link type="application/rss+xml" href="/subscribe"><a href="/conference/">Conference</a>',{}
                if url==ROOT+'conference/':return 200,('<script type="application/ld+json">'+json.dumps(EVENT)+'</script>').encode(),{}
                raise AssertionError('Feed or unexpected URL requested: '+url)
        f=Fetcher();rows,report=collect_automatic(f,SOURCE)
        self.assertEqual(len(rows),1);self.assertEqual(f.calls,[ROOT,ROOT+'conference/'])
        self.assertTrue(report['website_only'])
    def test_does_not_parse_feed_served_at_a_website_url(self):
        body=b'<rss><channel><item><title>Cloud Conference</title><startDate>2027-06-02</startDate><endDate>2027-06-03</endDate></item></channel></rss>'
        self.assertEqual(parse_page(body,SOURCE,ROOT),[])
    def test_sitemap_still_discovers_html_pages_and_skips_feed_urls(self):
        class Fetcher:
            calls=[]
            def fetch(self,url):
                self.calls.append(url)
                if url==ROOT:return 200,b'<h1>Conference</h1>',{}
                if url==ROOT+'sitemap.xml':return 200,('<urlset><url><loc>'+ROOT+'conference/</loc></url><url><loc>'+ROOT+'event.xml</loc></url></urlset>').encode(),{}
                return 200,('<script type="application/ld+json">'+json.dumps(EVENT)+'</script>').encode(),{}
        f=Fetcher();rows,_=collect_automatic(f,SOURCE)
        self.assertEqual(len(rows),1);self.assertNotIn(ROOT+'event.xml',f.calls)
    def test_explicit_feed_mode_remains_available(self):
        source={**SOURCE,'selectors':{'website_only':False}}
        body=b'BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:conference\nSUMMARY:Cloud Conference\nDTSTART;VALUE=DATE:20270602\nDTEND;VALUE=DATE:20270604\nEND:VEVENT\nEND:VCALENDAR'
        self.assertEqual(len(parse_page(body,source,ROOT+'calendar.ics')),1)
