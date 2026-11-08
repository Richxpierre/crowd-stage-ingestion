import json
import unittest
from unittest.mock import patch
from collector.automatic import parse_page
from collector.parse_passes import extract_pass,PASS_TYPES

URL='https://example.org/conference'
SOURCE={'url':URL,'name':'Cloud Conference','publisher':'Organizer','selectors':{'single_event':True}}
BODY=b'<main><h1>Cloud Conference 2027</h1><p>June 10-11, 2027</p></main>'

class SnapshotCacheTests(unittest.TestCase):
 def test_four_passes_reuse_dates_without_leaking_manual_overrides(self):
  cache={}
  with patch('collector.parse_passes.parse_page',wraps=parse_page) as parse:
   original=extract_pass('event_date',{URL:BODY},SOURCE,{},snapshot_cache=cache)
   for kind in PASS_TYPES[1:]:extract_pass(kind,{URL:BODY},SOURCE,{},snapshot_cache=cache)
   self.assertEqual(parse.call_count,1)
   override=extract_pass('event_date',{URL:BODY},SOURCE,{'selectors':{'start_date':'p'}},snapshot_cache=cache)
   self.assertEqual(parse.call_count,1)
   self.assertEqual(original[0]['fields']['start_date'],'2027-06-10')
   self.assertEqual(override[0]['fields']['start_date'],'2027-06-10')
 def test_a_changed_snapshot_or_source_gets_new_evidence(self):
  cache={}
  first=extract_pass('event_date',{URL:BODY},SOURCE,{},snapshot_cache=cache)
  second=extract_pass('event_date',{URL:BODY.replace(b'June 10-11',b'July 12-13')},SOURCE,{},snapshot_cache=cache)
  self.assertEqual(first[0]['fields']['start_date'],'2027-06-10')
  self.assertEqual(second[0]['fields']['start_date'],'2027-07-12')
  self.assertNotEqual(first[0]['evidence']['snapshot_sha256'],second[0]['evidence']['snapshot_sha256'])

if __name__=='__main__':unittest.main()
