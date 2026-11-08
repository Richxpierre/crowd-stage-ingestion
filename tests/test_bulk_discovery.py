import asyncio
import copy
import os
import unittest
from unittest.mock import patch,AsyncMock,Mock
from collector.aggregators import extract,historical_date_hint
from collector.bulk_discovery import run_bulk_job,StaticFirstReader,recover_organizer

SEED='https://sessionize.com/speakers-directory'
def page(url,html):return {'url':url,'html':html,'links':[],'markdown':''}

class Reader:
    pages={};calls=[]
    def __init__(self,*a,**kw):self.stats={'http_pages':0,'browser_pages':0}
    async def __aenter__(self):return self
    async def __aexit__(self,*a):pass
    async def read(self,url):self.calls.append(url);return self.pages[url]

class DB:
    def __init__(self):self.tasks={};self.sources=set();self.records=[];self.updates=[];self.cancel=False
    def update_crawl_job(self,id,value):self.updates.append(copy.deepcopy(value))
    def crawl_job(self,id):return {'status':'running','cancel_requested':self.cancel}
    def enqueue_frontier(self,job,generation,tasks):
        for t in tasks:
            key=(generation,t['url'],t['task_kind'])
            if key not in self.tasks:self.tasks[key]={**copy.deepcopy(t),'id':str(len(self.tasks)),'status':'queued','attempts':0,'generation':generation}
    def claim_frontier(self,id,kind,limit):
        rows=[t for t in self.tasks.values() if t['task_kind']==kind and t['status']=='queued'][:limit]
        for t in rows:t.update(status='leased',attempts=t['attempts']+1)
        return rows
    def finish_frontier(self,j,id):next(t for t in self.tasks.values() if t['id']==id)['status']='done'
    def fail_frontier(self,j,item,error):item.update(status='error' if item['attempts']>=3 else 'queued',last_error=error)
    def frontier_errors(self,target,generation):return [t for t in self.tasks.values() if t['status']=='error']
    def complete_frontier_sources(self,j,ids):
        out=[]
        for t in self.tasks.values():
            if t['id'] not in ids:continue
            existing=t['url'] in self.sources;self.sources.add(t['url']);t['status']='done';self.records.append(t)
            out.append({'url':t['url'],'status':'existing' if existing else 'added'})
        return out
    def frontier_progress(self,j):
        return {'pending_pages':sum(t['task_kind']=='page' and t['status'] in ('queued','leased') for t in self.tasks.values()),
                'pending_sources':sum(t['task_kind']=='source' and t['status'] in ('queued','leased') for t in self.tasks.values()),
                'failed_tasks':sum(t['status']=='error' for t in self.tasks.values())}

class BulkTests(unittest.TestCase):
    def test_sessionize_skips_explicitly_past_dates_without_guessing_unknowns(self):
        from datetime import date
        self.assertTrue(historical_date_hint('February 2026',date(2026,11,8)))
        self.assertFalse(historical_date_hint('November 2026',date(2026,11,8)))
        self.assertFalse(historical_date_hint('November 15, 2026',date(2026,11,8)))
        self.assertFalse(historical_date_hint(None,date(2026,11,8)))
        old='<div class="c-s-event"><a class="c-s-event__name" href="https://old.org/2020/">Old Conference</a><span class="c-s-event__meta--date">February 2020</span></div>'
        future='<div class="c-s-event"><a class="c-s-event__name" href="https://new.org/">New Conference</a><span class="c-s-event__meta--date">February 2099</span></div>'
        p=page('https://sessionize.com/speaker','<section id="speaker"><h1 class="c-s-speaker-info__name">Speaker</h1></section><section id="events">'+old+future+'</section>')
        rows=extract(p,'sessionize',SEED)
        self.assertEqual([r['url'] for r in rows],['https://new.org/'])
        self.assertEqual(rows[0]['payload']['evidence']['date_hint'],'February 2099')
        self.assertEqual(p['discovery_filter_report']['historical_skipped'],1)
    def setUp(self):Reader.calls=[]
    def job(self):return {'id':'job','target_id':'target','config':{'url':SEED,'max_pages':12,'max_sources':17,'settings':{'adapter':'sessionize'}}}
    def test_thousand_links_survive_small_batches_without_restart(self):
        n=1003
        Reader.pages={SEED:page(SEED,'<div class="c-entry--speaker">'+''.join(f'<h3 class="c-entry__title"><a href="/speaker{i}">Speaker</a></h3>' for i in range(n))+'</div>')}
        for i in range(n):
            u=f'https://sessionize.com/speaker{i}'
            Reader.pages[u]=page(u,f'<section id="speaker"><h1 class="c-s-speaker-info__name">Speaker</h1></section><section id="events"><div class="c-s-event"><a class="c-s-event__name" href="https://conf{i}.org/">Conference</a></div></section>')
        db=DB()
        for _ in range(100):
            result=asyncio.run(run_bulk_job(db,self.job(),Reader))
            if result['status']=='completed':break
        self.assertEqual(len(db.sources),n)
        self.assertEqual(Reader.calls.count(SEED),1)
        self.assertEqual(len(Reader.calls),n+1)
        self.assertEqual(len(db.records),n)
    def test_session_events_only_dedup_social_missing_historical(self):
        html='<section id="speaker"><h1 class="c-s-speaker-info__name">Speaker</h1></section><a href="https://employer.org/">Company</a><section id="sessions"><a href="https://talk.org/">Talk</a></section><section id="events"><div class="c-s-event"><a class="c-s-event__name" href="https://conf.org/">Conf 2024</a><a class="c-s-event__name" href="https://youtube.com/test">Video</a><a class="c-s-event__name" href="https://conf.org/">Conf 2023</a><a class="c-s-event__name" href="https://conf.org/brochure.pdf">PDF</a></div></section>'
        result=extract(page('https://sessionize.com/speaker',html),'sessionize',SEED)
        self.assertEqual([x['url'] for x in result],['https://conf.org/'])
        self.assertEqual(result[0]['payload']['evidence']['method'],'sessionize_profile_event_link')
    def test_real_pagination_hrefs_not_guessed_depth_paths(self):
        p=page(SEED+'/ict','<div class="c-entry--speaker"><h3 class="c-entry__title"><a href="/speaker">X</a></h3></div><nav class="c-nav-page"><a href="?page=1">Next</a></nav>')
        r=extract(p,'sessionize',SEED)
        self.assertIn(SEED+'/ict?page=1',[x['url'] for x in r])
        p=page('https://conferencemonkey.org/top/conferences','<a class="post-link" href="/conference/cloud">Cloud</a><a rel="next" href="?page=1">Next page next ›</a>')
        r=extract(p,'conference_monkey',p['url'])
        self.assertEqual(len(r),2);self.assertIn(p['url']+'?page=1',[x['url'] for x in r])
    def test_cm_explicit_link_and_missing_button_failure(self):
        u='https://conferencemonkey.org/conference/test'
        r=extract(page(u,'<p><a href="https://conf.org/register">Registration</a></p><a href="https://conf.org/">Visit Website</a>'),'conference_monkey',u)
        self.assertEqual([x['url'] for x in r],['https://conf.org/'])
        with self.assertRaises(ValueError):extract(page(u,'<h1>Blocked</h1>'),'conference_monkey',u)
    def test_complete_listing_without_button_uses_search_once_then_finishes(self):
        seed='https://conferencemonkey.org/conference/missing'
        Reader.pages={seed:page(seed,'<h1>Cloud Conference 2027</h1><p>'+('Event information. '*20)+'</p>')}
        job={'id':'job','target_id':'target','config':{'url':seed,'max_pages':10,'max_sources':10,
             'settings':{'adapter':'conference_monkey','search_enabled':True}}}
        db=DB()
        with patch('collector.bulk_discovery.recover_organizer',new_callable=AsyncMock,return_value=[{'status':'needs_review'}]) as recovery:
            first=asyncio.run(run_bulk_job(db,job,Reader))
            second=asyncio.run(run_bulk_job(db,job,Reader))
        recovery.assert_awaited_once();self.assertEqual(first['needs_review'],1)
        self.assertEqual(first['status'],'completed');self.assertEqual(second['pages_scanned'],0)
        self.assertTrue(all(t['status']=='done' for t in db.tasks.values()))
    def test_missing_button_search_is_bounded_and_never_adds_direct_sources(self):
        import time
        seed='https://conferencemonkey.org/top/conferences';official='https://cloudconf.org/'
        search=Mock();search.search.return_value={'results':[{'url':official}]}
        Reader.pages={official:page(official,'<h1>Cloud Conference 2027</h1>')}
        db=DB();db.record_discovery=Mock(return_value={'status':'needs_review'})
        report={'requests':0,'results':0,'pages_checked':0,'warnings':[]}
        job={'id':'job','config':{'url':seed}}
        result=asyncio.run(recover_organizer(db,job,page(seed+'/detail',''),'Cloud Conference 2027',time.monotonic()+90,3,report,lambda **kw:search,Reader))
        self.assertEqual(result,[{'status':'needs_review'}]);self.assertEqual(report['requests'],1)
        self.assertEqual(db.record_discovery.call_args.args[3],'needs_review')
        self.assertTrue(db.record_discovery.call_args.args[7]['requires_review'])
        report['requests']=3
        self.assertEqual(asyncio.run(recover_organizer(db,job,page(seed,''),'Cloud',time.monotonic()+90,3,report,lambda **kw:search,Reader)),[])
        search.search.assert_called_once()
    def test_cancelled_does_not_fetch_or_claim(self):
        db=DB();db.cancel=True
        result=asyncio.run(run_bulk_job(db,self.job(),Reader))
        self.assertEqual(result['status'],'cancelled');self.assertEqual(Reader.calls,[])
    def test_failed_pages_retry_then_become_visible_errors(self):
        Reader.pages={SEED:page(SEED,'<h1>Unknown markup</h1>')};db=DB()
        for _ in range(4):result=asyncio.run(run_bulk_job(db,self.job(),Reader))
        self.assertEqual(len(Reader.calls),3)
        self.assertEqual(next(iter(db.tasks.values()))['status'],'error')
        self.assertEqual(result['status'],'partial')
        self.assertTrue(db.updates[-1]['errors'])
    def test_static_html_does_not_start_browser(self):
        class Fetcher:
            def fetch(self,url):return 200,b'<a href="/conference">Conference</a>',{}
        async def run():
            with patch.dict(os.environ,{'COLLECTOR_USER_AGENT':'CrowdStage test'}):
                async with StaticFirstReader('CrowdStage test') as reader:
                    reader.fetcher=Fetcher();p=await reader.read(SEED)
                    self.assertIsNone(reader.browser);return p
        self.assertIn('Conference',asyncio.run(run())['html'])

if __name__=='__main__':unittest.main()
