import unittest
from unittest.mock import Mock
from collector.sync import preserve_existing_identity
from collector.parsers import _finish

class ReviewedIdentityTests(unittest.TestCase):
    def test_duplicate_schema_nodes_honor_the_reviewed_identity(self):
        db,source,record=self.setup_case()
        duplicate={**record,'source_record_id':'second-schema-node'}
        result=preserve_existing_identity(db,source,[record,duplicate])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['source_record_id'],'canonical-2027')

    def test_distinct_event_dates_are_never_collapsed(self):
        db,source,record=self.setup_case()
        other={**record,'start_date':'2027-05-01','end_date':'2027-05-02'}
        self.assertEqual(len(preserve_existing_identity(db,source,[record,other])),2)

    def setup_case(self):
        url='https://official.org/event'
        source={'id':'s','publisher':'Organizer','selectors':{'single_event':True,'identity_choice':{'edition':'2027','event_url':url,'source_record_id':'canonical-2027'}}}
        record=_finish({'title':'Updated Event Name','event_url':url,'start_date':'2027-04-01','end_date':'2027-04-02'},'Organizer','new-parser-id')
        db=Mock();db.observations.return_value=[{'source_record_id':key,'payload':{'event_url':url,'start_date':'2027-04-01','end_date':'2027-04-02'}} for key in ('canonical-2027','old-duplicate')]
        return db,source,record

    def test_reviewed_choice_preserves_canonical_identity_without_deleting_history(self):
        db,source,record=self.setup_case()
        self.assertEqual(preserve_existing_identity(db,source,[record])[0]['source_record_id'],'canonical-2027')
        self.assertEqual(len(db.observations.return_value),2)

    def test_unknown_choice_cannot_invent_an_observation_identity(self):
        db,source,record=self.setup_case();source['selectors']['identity_choice']['source_record_id']='invented'
        with self.assertRaisesRegex(ValueError,'Reviewed identity'):preserve_existing_identity(db,source,[record])

    def test_choice_expires_for_a_different_edition(self):
        db,source,record=self.setup_case();record['start_date']='2028-04-01';record['end_date']='2028-04-02'
        self.assertEqual(preserve_existing_identity(db,source,[record])[0]['source_record_id'],'new-parser-id')

