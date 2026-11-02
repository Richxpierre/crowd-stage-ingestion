import unittest
from unittest.mock import patch
from collector.recovery import collect_source

class HistoricalTests(unittest.TestCase):
    def test_old_edition_tries_homepage_for_current_conferences(self):
        old=[{'end_date':'2001-01-02','status':'upcoming'}];current=[{'end_date':'2099-01-02','status':'upcoming'}]
        source={'url':'https://conference.org/2001','kind':'auto','selectors':{'website_only':True}}
        fetcher=type('Fetcher',(),{})()
        with patch('collector.recovery.collect_automatic',side_effect=[(old,{}),(current,{})]) as collect:
            records,_,_,report=collect_source(fetcher,source)
        self.assertEqual(records,current);self.assertEqual(report['mode'],'website_fallback')
        self.assertEqual(collect.call_args_list[1].args[1]['url'],'https://conference.org/')
    def test_past_only_homepage_is_not_a_parser_failure(self):
        old=[{'end_date':'2001-01-02','status':'upcoming'}]
        source={'url':'https://conference.org/','kind':'auto','selectors':{}}
        with patch('collector.recovery.collect_automatic',return_value=(old,{})):
            records,_,_,report=collect_source(type('Fetcher',(),{})(),source)
        self.assertEqual(records,old);self.assertEqual(report['mode'],'past_only')
    def test_cancellation_observations_are_preserved(self):
        old=[{'end_date':'2001-01-02','status':'cancelled'}]
        source={'url':'https://conference.org/2001','kind':'auto','selectors':{}}
        with patch('collector.recovery.collect_automatic',return_value=(old,{})) as collect:
            records,_,_,_=collect_source(type('Fetcher',(),{})(),source)
        self.assertEqual(records,old);self.assertEqual(collect.call_count,1)

if __name__=='__main__':unittest.main()
