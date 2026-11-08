import unittest
from collector.parse_passes import extract_pass,assemble,PASS_TYPES

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

if __name__=='__main__':unittest.main()
