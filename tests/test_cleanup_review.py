import unittest
from unittest.mock import patch
from collector.cleanup_review import validate_proposal,deterministic,collect_evidence,reviewed_plan
from collector.fetch import UnsafeDestination

class CleanupTests(unittest.TestCase):
 def test_unobserved_url_is_rejected(self):
  item={'kind':'validation','url':'https://conf.org'}
  page={'url':item['url'],'text':'Next conference in Berlin','links':[]}
  proposal={'action':'recheck','reason':'Repair the organizer URL from observed navigation','quote':'Next conference','url':'https://invented.org','title':'Conference'}
  with self.assertRaisesRegex(ValueError,'Unobserved'):validate_proposal(proposal,item,page)
 def test_unobserved_quote_is_rejected(self):
  item={'kind':'validation','url':'https://conf.org'};page={'url':item['url'],'text':'Annual conference','links':[]}
  proposal={'action':'reject','reason':'The event edition is explicitly obsolete','quote':'Past Event','url':item['url'],'title':'Conference'}
  with self.assertRaisesRegex(ValueError,'quote'):validate_proposal(proposal,item,page)
 def test_retain_can_report_access_uncertainty_without_fake_quote(self):
  item={'kind':'validation','url':'https://conf.org'};page={'url':item['url'],'text':'','links':[]}
  p={'action':'retain','reason':'Access is blocked; inspect the original organizer','quote':'','url':item['url'],'title':'Conference'}
  self.assertEqual(validate_proposal(p,item,page)['action'],'retain')
 def test_old_speaker_label_does_not_reject_evergreen_website(self):
  self.assertIsNone(deterministic({'kind':'validation','title':'Conference 2015','before_state':{}}))
 def test_expired_pending_event_can_be_archived_without_ai(self):
  self.assertEqual(deterministic({'kind':'event','url':'https://conf.org','before_state':{'end_date':'2020-01-01'}})['action'],'archive')
 def test_unsafe_destination_never_triggers_root_or_browser(self):
  with patch('collector.cleanup_review.page_evidence',side_effect=UnsafeDestination('Private network')) as fetch:
   with self.assertRaises(UnsafeDestination):collect_evidence('https://private.invalid/event')
   self.assertEqual(fetch.call_count,1)
 def test_root_is_only_second_attempt(self):
  with patch('collector.cleanup_review.page_evidence',side_effect=[RuntimeError('HTTP 404'),{'url':'https://conf.org/'}]) as fetch:
   self.assertEqual(collect_evidence('https://conf.org/broken')['url'],'https://conf.org/')
   self.assertEqual(fetch.call_args.args[0],'https://conf.org/')
 def test_quoted_dates_generate_guarded_four_pass_plan(self):
  text='Cloud Conference 2027 happens June 12–13, 2027 in Paris, France.'
  page={'url':'https://conf.org/','text':text,'headings':['Cloud Conference 2027']}
  proposal={'action':'recheck','url':page['url'],'title':'Cloud Conference 2027','facts':[{'field':f,'value':'June 12–13, 2027','quote':text} for f in ('start_date','end_date')]}
  plan=reviewed_plan(proposal,page)
  self.assertEqual(len(plan['parse_passes']),4)
  self.assertEqual(plan['parse_passes']['event_date']['verified_events'][0]['fields']['start_date'],'2027-06-12')
  self.assertTrue(plan['parse_passes']['event_date']['require_verified'])
 def test_multiple_event_headings_cannot_receive_one_shared_recipe(self):
  self.assertIsNone(reviewed_plan({'action':'recheck','url':'https://conf.org/','title':'Cloud Conference 2027','facts':[]},{'url':'https://conf.org/','headings':['One','Two'],'text':'Cloud Conference 2027'}))
 def test_generic_calendar_and_meetup_titles_are_not_conferences(self):
  from collector.source_validation import specific_event_title
  for title in ('Conferences','Our Upcoming Events','Prossimo evento','Weekly cloud meetup','The next edition of the conference will take place in May 2027'):
   self.assertFalse(specific_event_title(title),title)
  self.assertTrue(specific_event_title('Cloud Conference 2027 with workshops'))
  from collector.source_validation import title_edition_conflict
  self.assertTrue(title_edition_conflict('Security Conference 2026','2027-09-20'))
  self.assertFalse(title_edition_conflict('Conference 2026–2027 edition','2027-03-11'))
  from collector.discovery import excluded
  self.assertTrue(excluded('https://callforpaper.org/cfp/a-conference'))
  self.assertFalse(excluded('https://expfin.org/events/conference'))
 def test_calendar_and_track_records_do_not_reenter_ingestion(self):
  from collector.sync import in_scope_record
  for title in ('MLK Jr. Holiday (University Closed)','Open Registration','Microbusiness Coffee Hour'):
   self.assertFalse(in_scope_record({'title':title,'start_date':'2027-01-18','event_url':'https://conf.org/events/holiday'},'https://conf.org'))
  self.assertFalse(in_scope_record({'title':'Staff Engineering Skills','start_date':'2027-01-18','event_url':'https://conf.org/track/staff-skills'},'https://conf.org'))
  self.assertTrue(in_scope_record({'title':'Cloud Conference 2027','start_date':'2027-01-18','event_url':'https://conf.org'},'https://conf.org'))
