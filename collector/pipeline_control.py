"""Scheduled workflows obey the same durable controls as the admin dashboard."""
import os
from .client import DatabaseError

def configuration(db,pipeline):
    value=db.request('POST','rpc/conference_worker_configuration',json={'p_pipeline':pipeline})
    if not isinstance(value,dict):raise DatabaseError('Pipeline control settings unavailable; no work started')
    os.environ['AI_ENRICHMENT_ENABLED']='0'
    os.environ['OPENAI_PAUSED']='1'
    os.environ.pop('OPENAI_API_KEY',None)
    if not value.get('claude_enabled'):os.environ['CLAUDE_ENRICHMENT_ENABLED']='0'
    if not value.get('manus_enabled'):os.environ['MANUS_BACKUP_ENABLED']='0'
    value['can_run']=value.get('enabled') is True and not value.get('manual_paused')
    return value
