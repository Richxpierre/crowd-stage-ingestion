import os
import unittest
from unittest.mock import patch
from collector.handoff import dispatch_bulk_continuation

class DB:
    def __init__(self):self.calls=[]
    def prepare_bulk_handoff(self,*args):self.calls.append(args);return True
class Session:
    calls=[]
    def post(self,*args,**kwargs):self.calls.append((args,kwargs));return type('Response',(),{'status_code':204})()

class HandoffTests(unittest.TestCase):
    def test_fixed_repository_and_capped_continuation(self):
        job={'config':{'settings':{},'bulk_worker_handoffs':0}};db=DB();session=Session();session.calls=[]
        env={'BULK_WORKFLOW_TOKEN':'test-only','GITHUB_REPOSITORY':'Richxpierre/crowd-stage-ingestion','GITHUB_REF_NAME':'main'}
        with patch.dict(os.environ,env):
            self.assertTrue(dispatch_bulk_continuation(db,job,'00000000-0000-0000-0000-000000000001',session))
            job['config']['bulk_worker_handoffs']=2
            self.assertFalse(dispatch_bulk_continuation(db,job,'00000000-0000-0000-0000-000000000001',session))
        self.assertEqual(len(session.calls),1);self.assertEqual(db.calls[0][1],1)
        self.assertFalse(session.calls[0][1]['allow_redirects'])
    def test_no_token_or_other_repository_never_dispatches(self):
        with patch.dict(os.environ,{'BULK_WORKFLOW_TOKEN':'','GITHUB_REPOSITORY':'other/repo'}):
            self.assertFalse(dispatch_bulk_continuation(DB(),{'config':{}},'not-a-uuid',Session()))

if __name__=='__main__':unittest.main()
