import json
import unittest
from copy import deepcopy
from collector.parse_passes import collect_passes,extract_pass,assemble,retain_known_optional,PASS_TYPES
from unittest.mock import patch

URL='https://example.org/'
SOURCE=dict(id='source',url=URL,name='Cloud Conference',publisher='Cloud',kind='auto',is_official_source=True,
            selectors={'single_event':True,'max_pages':3,'render':False})
def page(node):return ('<script type="application/ld+json">'+json.dumps(node)+'</script>').encode()
def node(**extra):return {'@type':'Event','name':'Cloud Conference','url':URL,**extra}
class Fetcher:
    def __init__(self,body):self.body=body;self.calls=[]
    def fetch(self,url):self.calls.append(url);return 200,self.body,{}

class ParsePassTests(unittest.TestCase):
    def test_progress_is_saved_before_fetch_and_for_each_extracted_page(self):
        updates=[]
        fetcher=Fetcher(page(node(startDate='2027-04-26')))
        def emit(kind,result):updates.append((kind,deepcopy(result),len(fetcher.calls)))
        report,_=collect_passes(fetcher,SOURCE,on_result=emit)
        self.assertTrue(all(value['progress_stage']=='collecting' and calls==0 for _,value,calls in updates[:4]))
        date_updates=[v for k,v,_ in updates if k=='event_date']
        self.assertTrue(any(v['progress_stage']=='extracting' and v['pages_done']==1 and v['pages_total']==1 for v in date_updates))
        self.assertEqual(report['event_date']['progress_stage'],'complete')
        self.assertTrue(report['event_date']['heartbeat_at'])

    def test_failed_pass_finishes_progress_and_keeps_error(self):
        source={**SOURCE,'selectors':{**SOURCE['selectors'],'parse_passes':{'event_location':{'selectors':{'city':'['}}}}}
        report,_=collect_passes(Fetcher(page(node(startDate='2027-04-26'))),source)
        result=report['event_location']
        self.assertEqual((result['status'],result['progress_stage']),('failed','complete'))
        self.assertTrue(result['errors'][0]['message'])

    def test_optional_passes_survive_missing_dates(self):
        body=page(node(location={'name':'Hall','address':{'addressLocality':'Paris'}},
                       offers={'price':'0','priceCurrency':'EUR'},keywords='cloud, security'))
        report,_=collect_passes(Fetcher(body),SOURCE)
        self.assertEqual(report['event_date']['status'],'missing')
        for kind in PASS_TYPES[1:]:self.assertEqual(report[kind]['status'],'success')
        self.assertEqual(assemble(report,SOURCE),[])
        self.assertEqual(report['event_stats']['results'][0]['fields']['price_from'],0)

    def test_bad_location_selector_does_not_lose_date_or_details(self):
        source={**SOURCE,'selectors':{**SOURCE['selectors'],'parse_passes':{'event_location':{'selectors':{'city':'['}}}}}
        report,_=collect_passes(Fetcher(page(node(startDate='2027-04-26',keywords='cloud'))),source)
        self.assertEqual(report['event_location']['status'],'failed')
        record=assemble(report,source)[0]
        self.assertEqual(record['start_date'],'2027-04-26');self.assertEqual(record['categories'],['cloud'])

    def test_snapshot_is_shared_and_focused_run_only_executes_selected_pass(self):
        fetcher=Fetcher(page(node(startDate='2027-04-26',keywords='cloud')))
        report,_=collect_passes(fetcher,SOURCE,['event_details'])
        self.assertEqual(fetcher.calls,[URL]);self.assertEqual(list(report),['event_details'])
        self.assertEqual(assemble(report,SOURCE),[])

    def test_edition_location_never_leaks(self):
        body=page([node(startDate='2026-04-26',location='Old Hall'),node(startDate='2027-04-26',location='New Hall')])
        source={**SOURCE,'selectors':{'max_pages':1,'render':False}}
        report,_=collect_passes(Fetcher(body),source)
        records=assemble(report,source)
        self.assertEqual({r['start_date']:r.get('venue') for r in records},{'2026-04-26':'Old Hall','2027-04-26':'New Hall'})

    def test_mixed_currencies_and_conflicting_locations_stay_unknown(self):
        body=page(node(startDate='2027-04-26',offers=[{'price':10,'priceCurrency':'EUR'},{'price':5,'priceCurrency':'USD'}]))
        report,_=collect_passes(Fetcher(body),SOURCE)
        self.assertNotIn('price_from',assemble(report,SOURCE)[0])

    def test_unreachable_website_reports_all_four_failures(self):
        class Blocked:
            def fetch(self,url):raise PermissionError('403 access denied')
        report,_=collect_passes(Blocked(),SOURCE)
        self.assertTrue(all(r['status']=='failed' for r in report.values()))

    def test_html_details_without_date(self):
        results=extract_pass('event_details', {URL:b'<h1>Cloud Conference</h1><meta name="description" content="All about cloud"><meta name="keywords" content="cloud, infrastructure">'},SOURCE,{})
        self.assertEqual(results[0]['fields']['categories'],['cloud','infrastructure'])

    def test_date_conflict_same_identity_is_not_last_write_wins(self):
        body=page([node(**{'@id':'one','startDate':'2027-04-26'}),node(**{'@id':'one','startDate':'2027-05-26'})])
        report,_=collect_passes(Fetcher(body),SOURCE)
        self.assertEqual(assemble(report,SOURCE),[])

    def test_index_alias_does_not_create_a_second_event(self):
        body=page(node(startDate='2027-04-26',url='https://example.org/index.html'))
        report,_=collect_passes(Fetcher(body),SOURCE)
        record=assemble(report,SOURCE)[0]
        self.assertEqual(record['event_url'],URL)

    def test_retained_facts_require_same_identity_and_dates(self):
        report,_=collect_passes(Fetcher(page(node(startDate='2027-04-26'))),SOURCE)
        record=assemble(report,SOURCE)[0]
        old={**record,'city':'Paris','categories':['cloud']}
        retain_known_optional([record],[{'source_record_id':record['source_record_id'],'payload':old}],SOURCE['publisher'])
        self.assertEqual(record['city'],'Paris')
        self.assertEqual(record['categories'],['cloud'])

    def test_worker_preview_saves_four_passes_and_never_ingests_events(self):
        from tests.test_ingestion import FakeDB,SOURCE as APPROVED
        from collector.sync import sync_one
        class DB(FakeDB):
            def __init__(self):super().__init__();self.passes={}
            def save_parse_pass(self,run_id,source_id,kind,result):self.passes[kind]=result
            def observations(self,sid):return []
        body=page(node(startDate='2027-04-26',keywords='cloud'))
        db=DB()
        source={**APPROVED,**SOURCE,'id':APPROVED['id']}
        with patch('collector.sync.Fetcher',return_value=Fetcher(body)):
            sync_one(db,source,parse_only=True)
        self.assertEqual(set(db.passes),set(PASS_TYPES));self.assertFalse(db.events)

    def test_date_override_replaces_auto_value_without_duplicate(self):
        body=page(node(startDate='2027-04-26'))+b'<time datetime="2027-04-27"></time>'
        source={**SOURCE,'selectors':{**SOURCE['selectors'],'parse_passes':{'event_date':{'selectors':{'start_date':'time','end_date':'time'}}}}}
        report,_=collect_passes(Fetcher(body),source)
        records=assemble(report,source)
        self.assertEqual(len(records),1);self.assertEqual(records[0]['start_date'],'2027-04-27')

if __name__=='__main__':unittest.main()
