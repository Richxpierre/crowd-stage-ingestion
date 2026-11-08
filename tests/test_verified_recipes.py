import unittest
import json,hashlib
from collector.parse_passes import extract_pass,assemble,collect_passes,PASS_TYPES

URL='https://example.org/conference/'
SOURCE={'url':URL,'name':'Cloud 2027','publisher':'Organizer','selectors':{'single_event':True}}
BODY=b'<h1>Cloud 2027</h1><p>May 17-20, 2027 in Paris</p><p>Early bird ends December 31, 2026</p>'

def recipe(kind):
    values={'event_date':{'start_date':'2027-05-17','end_date':'2027-05-20'},
            'event_stats':{'format':'in_person'},'event_location':{'city':'Paris','country':'France','region':'Europe'},
            'event_details':{'event_kind':'conference','summary':'Cloud engineering conference.'}}
    return {'page_url':URL,'event_url':URL,'title':'Cloud 2027','start_date':'2027-05-17','edition':'2027',
            'source_record_id':'legacy-event-identity','require_text':['Cloud 2027','May 17-20, 2027 in Paris'],'fields':values[kind]}

class VerifiedRecipeTests(unittest.TestCase):
    def test_reviewed_date_replaces_ticket_deadline_without_duplicate(self):
        passes={kind:{'results':extract_pass(kind,{URL:BODY},SOURCE,{'verified_events':[recipe(kind)],'require_verified':True})} for kind in PASS_TYPES}
        rows=assemble(passes,SOURCE)
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['title'],rows[0]['start_date'],rows[0]['end_date']),('Cloud 2027','2027-05-17','2027-05-20'))
        self.assertEqual(rows[0]['source_record_id'],'legacy-event-identity')
        self.assertEqual((rows[0]['format'],rows[0]['city']),('in_person','Paris'))

    def test_future_edition_cannot_reuse_old_review(self):
        with self.assertRaisesRegex(ValueError,'evidence changed'):
            extract_pass('event_date',{URL:BODY.replace(b'2027',b'2028')},SOURCE,{'verified_events':[recipe('event_date')],'require_verified':True})

    def test_other_url_cannot_borrow_evidence(self):
        with self.assertRaisesRegex(ValueError,'evidence changed'):
            extract_pass('event_date',{'https://example.org/other/':BODY},SOURCE,{'verified_events':[recipe('event_date')],'require_verified':True})

    def test_script_evidence_is_not_visible_fact(self):
        with self.assertRaisesRegex(ValueError,'evidence changed'):
            extract_pass('event_date',{URL:b'<script>Cloud 2027; May 17-20, 2027 in Paris</script>'},SOURCE,{'verified_events':[recipe('event_date')],'require_verified':True})

    def test_review_does_not_remove_another_catalog_event(self):
        source={**SOURCE,'selectors':{'single_event':False}}
        body=BODY+b'<script type="application/ld+json">{"@type":"Event","name":"Other Conference","url":"https://example.org/other","startDate":"2027-06-01"}</script>'
        rows=extract_pass('event_date',{URL:body},source,{'verified_events':[recipe('event_date')]})
        self.assertTrue(any(r['title']=='Other Conference' for r in rows))
        self.assertTrue(any(r['title']=='Cloud 2027' and r['fields']['start_date']=='2027-05-17' for r in rows))

    def test_unreviewed_sources_keep_existing_behavior(self):
        body=BODY+b'<script type="application/ld+json">{"@type":"Event","name":"Cloud 2027","url":"https://example.org/conference/","startDate":"2027-05-17"}</script>'
        rows=extract_pass('event_date',{URL:body},SOURCE,{})
        self.assertEqual(rows[0]['fields']['start_date'],'2027-05-17')
        self.assertEqual(rows[0]['evidence']['method'],'date_parser')

    def test_bare_domain_settings_use_the_already_fetched_root(self):
        root='https://example.org'
        source={**SOURCE,'url':root,'selectors':{'single_event':True,'render':False,'max_pages':1,'parse_passes':{}}}
        for kind in PASS_TYPES:
            rule={**recipe(kind),'page_url':root,'event_url':root}
            source['selectors']['parse_passes'][kind]={'page_urls':[root],'verified_events':[rule],'require_verified':True}
        class Fetcher:
            def fetch(self,url):return 200,BODY,{}
        report,_=collect_passes(Fetcher(),source)
        self.assertEqual(report['event_date']['status'],'success')
        self.assertEqual(assemble(report,source)[0]['start_date'],'2027-05-17')

    def test_structured_event_guard_is_exact_and_not_arbitrary_script_text(self):
        node={'@type':'Event','name':'Cloud 2027','url':URL,'startDate':'2027-05-17','endDate':'2027-05-20'}
        rule={**recipe('event_date'),'require_text':[], 'require_event':{
            'name':node['name'],'start_date':'2027-05-17','end_date':'2027-05-20',
            'sha256':hashlib.sha256(json.dumps(node,sort_keys=True,separators=(',',':')).encode()).hexdigest()}}
        body=('<script type="application/ld+json">'+json.dumps(node)+'</script>').encode()
        rows=extract_pass('event_date',{URL:body},SOURCE,{'verified_events':[rule],'require_verified':True})
        self.assertEqual(rows[0]['fields']['end_date'],'2027-05-20')
        changed={**node,'endDate':'2027-05-21'}
        body=('<script type="application/ld+json">'+json.dumps(changed)+'</script>').encode()
        with self.assertRaisesRegex(ValueError,'evidence changed'):
            extract_pass('event_date',{URL:body},SOURCE,{'verified_events':[rule],'require_verified':True})

    def test_reviewed_dedicated_child_does_not_import_embedded_parent_event(self):
        body=BODY+b'<script type="application/ld+json">{"@type":"Event","name":"Parent Conference","url":"https://example.org/parent/","startDate":"2027-05-17","endDate":"2027-05-21"}</script>'
        rows=extract_pass('event_date',{URL:body},SOURCE,{'verified_events':[recipe('event_date')],'require_verified':True})
        self.assertEqual([r['title'] for r in rows],['Cloud 2027'])

    def test_reviewed_only_catalog_excludes_unreviewed_training(self):
        source={**SOURCE,'selectors':{'single_event':False}}
        body=BODY+b'<script type="application/ld+json">{"@type":"Event","name":"Other Training","url":"https://example.org/training/","startDate":"2027-06-01"}</script>'
        rows=extract_pass('event_date',{URL:body},source,{'verified_events':[recipe('event_date')],'require_verified':True})
        self.assertEqual([r['title'] for r in rows],['Cloud 2027'])

if __name__=='__main__':unittest.main()
