import json
import unittest
from unittest.mock import patch
from collector.images import choose_image, image_url
from collector.parsers import parse_jsonld, parse_ics
from collector.official import parse_official_html
from collector.automatic import parse_page, collect_automatic
from collector.sync import sync_one
from test_ingestion import FakeDB, SOURCE

URL='https://organizer.org/events/2027/'
SRC={**SOURCE,'name':'Cloud Conference','url':URL,'publisher':'organizer.org'}

class ImageTests(unittest.TestCase):
    def test_schema_imageobject_wins_over_share_image_and_changes_hash(self):
        def parse(image):
            event={'@type':'Event','name':'Cloud Conference','startDate':'2027-06-02','image':[{'@type':'ImageObject','contentUrl':image}]}
            return parse_jsonld(('<meta property="og:image" content="/generic.jpg"><script type="application/ld+json">'+json.dumps(event)+'</script>').encode(),SRC)[0]
        one,two=parse('/one.jpg'),parse('/two.jpg')
        self.assertEqual(one['cover_url'],'https://organizer.org/one.jpg')
        self.assertEqual(one['image_method'],'jsonld_event')
        self.assertEqual(one['dedupe_key'],two['dedupe_key'])
        self.assertNotEqual(one['content_hash'],two['content_hash'])

    def test_relative_og_twitter_and_explicit_selector(self):
        self.assertEqual(choose_image(b'<meta property="og:image" content="../cover.jpg">',URL)['cover_url'],'https://organizer.org/events/cover.jpg')
        self.assertEqual(choose_image(b'<meta name="twitter:image" content="//cdn.organizer.org/banner.jpg">',URL)['image_method'],'twitter_image')
        image=choose_image(b'<meta property="og:image" content="/generic.jpg"><img id="chosen" src="/chosen.jpg">',URL,selectors={'cover_image':'#chosen'})
        self.assertEqual(image['cover_url'],'https://organizer.org/chosen.jpg')

    def test_public_https_links_only(self):
        for value in ['http://organizer.org/image.jpg','data:image/png;base64,AAA','javascript:alert(1)','https://u:p@organizer.org/image.jpg','https://127.0.0.1/a.jpg','https://[::1]/a.jpg','https://host.local/a.jpg','https://organizer.org:444/a.jpg']:
            with self.subTest(value=value):self.assertIsNone(image_url(value,URL))
        self.assertEqual(image_url('https://cdn.organizer.org/image.jpg?signature=abc&width=1200',URL),'https://cdn.organizer.org/image.jpg?signature=abc&width=1200')

    def test_banner_fallback_skips_logos_and_preserves_no_image(self):
        body=b'<img src="/logo.png" width="1200" height="400"><img src="/hero.jpg" width="1600" height="600">'
        self.assertEqual(choose_image(body,URL)['cover_url'],'https://organizer.org/hero.jpg')
        self.assertEqual(choose_image(b'<img src="/icon.png" width="20" height="20">',URL),{})

    def test_distinct_card_images_do_not_use_catalog_share_image(self):
        body=b'''<title>Cloud Conference</title><meta property="og:image" content="/generic.jpg">
        <figure><img src="/berlin.jpg"><h2>Berlin</h2><time>June 2-3, 2027</time><address>Berlin, Germany</address></figure>
        <figure><img src="/paris.jpg"><h2>Paris</h2><time>July 2-3, 2027</time><address>Paris, France</address></figure>'''
        rows=parse_page(body,SRC,URL)
        self.assertEqual([r['cover_url'] for r in rows],['https://organizer.org/berlin.jpg','https://organizer.org/paris.jpg'])

    def test_custom_and_calendar_parsers_include_images(self):
        source={**SRC,'selectors':{'title':'h1','start_date':'time'}}
        rows=parse_official_html(b'<h1>Cloud Conference</h1><time datetime="2027-06-02"></time><meta property="og:image" content="/cover.jpg">',source)
        self.assertEqual(rows[0]['cover_url'],'https://organizer.org/cover.jpg')
        rows=parse_ics(b'BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:one\nSUMMARY:Cloud Conference\nDTSTART:20270602\nIMAGE;VALUE=URI:https://cdn.organizer.org/cover.jpg\nEND:VEVENT\nEND:VCALENDAR',SRC)
        self.assertEqual(rows[0]['image_method'],'calendar_image')

    def test_image_enrichment_failure_does_not_drop_calendar_event(self):
        class Fetcher:
            def __init__(self,*args,**kwargs):self.calls=0
            def fetch(self,*args):
                self.calls+=1
                if self.calls>1:raise RuntimeError('Image page unavailable')
                return 200,b'BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:one\nSUMMARY:Cloud Conference\nDTSTART:20270602\nURL:https://organizer.org/event\nEND:VEVENT\nEND:VCALENDAR',{}
        db=FakeDB()
        with patch('collector.sync.Fetcher',Fetcher):result=sync_one(db,{**SRC,'kind':'ics'})
        self.assertEqual(result['created_count'],1)

    def test_old_sources_bypass_cached_imageless_payload_once(self):
        calls=[]
        class Fetcher:
            def __init__(self,*args,**kwargs):pass
            def fetch(self,url,etag=None,modified=None):
                calls.append((etag,modified))
                return 200,b'BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:one\nSUMMARY:Cloud Conference\nDTSTART:20270602\nEND:VEVENT\nEND:VCALENDAR',{}
        db=FakeDB()
        with patch('collector.sync.Fetcher',Fetcher):sync_one(db,{**SRC,'kind':'ics','etag':'old','last_modified':'yesterday'})
        self.assertEqual(calls[0],(None,None))
        self.assertEqual(db.sources[-1]['discovery_report']['image_extraction_version'],1)

if __name__=='__main__':unittest.main()
