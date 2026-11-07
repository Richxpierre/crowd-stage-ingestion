"""Manus API adapter, with the user's strict daily token ceiling preserved.

Manus v2 exposes credit usage, not a maximum task-token parameter. Therefore
automatic task creation is blocked until an enforceable budget contract exists.
Read-only authentication/status/structured-result inspection are supported.
"""
from __future__ import annotations
import json
import os
import re
import requests

API='https://api.manus.ai/v2/'
DAILY_TOKEN_LIMIT=2000
MAX_PROMPT_BYTES=2000
SAFE_CODES={'unauthenticated','permission_denied','resource_exhausted','not_found','invalid_argument'}


class ManusError(RuntimeError):
    pass


def configured():
    return os.environ.get('MANUS_BACKUP_ENABLED')=='1'


def readiness():
    return {'provider':'manus','configured':configured(),'credential_available':bool(os.environ.get('MANUS_API_KEY')),
            'daily_limit':DAILY_TOKEN_LIMIT,'daily_limit_unit':'model_tokens',
            'task_creation_allowed':False,'reason':'manus_hard_token_cap_unavailable'}


class ManusClient:
    def __init__(self,session=None):
        self.session=session or requests.Session();self.owns_session=session is None
        self.session.trust_env=False

    def close(self):
        if self.owns_session:self.session.close()

    def _request(self,method,endpoint,**kwargs):
        key=os.environ.get('MANUS_API_KEY')
        if not key:raise ManusError('manus_missing_key')
        try:
            response=self.session.request(method,API+endpoint,headers={'x-manus-api-key':key},
                timeout=(3,12),allow_redirects=False,**kwargs)
            if len(response.content)>256000:raise ManusError('manus_response_too_large')
            data=response.json()
        except ManusError:raise
        except Exception:raise ManusError('manus_connection_error') from None
        if response.status_code!=200 or not isinstance(data,dict) or data.get('ok') is not True:
            code=data.get('error',{}).get('code') if isinstance(data,dict) else None
            raise ManusError('manus_'+code if code in SAFE_CODES else 'manus_request_failed')
        return data

    def authenticate(self):
        self._request('GET','user.me')
        # Never return account email, name, user ID, credentials or raw responses.
        return {'provider':'manus','authenticated':True,**readiness()}

    @staticmethod
    def _task_id(task_id):
        if not isinstance(task_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',task_id):
            raise ManusError('manus_invalid_task_id')
        return task_id

    def task_status(self,task_id):
        data=self._request('GET','task.detail',params={'task_id':self._task_id(task_id)})
        task=data.get('task') or {}
        status=task.get('status')
        credits=task.get('credit_usage')
        return {'task_id':task_id,'status':status if status in ('running','pending','waiting','stopped','completed','failed') else 'unknown',
                'credits_used':credits if type(credits) in (int,float) and credits>=0 else None,
                'tokens_used':None}

    def result(self,task_id,payload):
        data=self._request('GET','task.listMessages',params={
            'task_id':self._task_id(task_id),'order':'desc','limit':50})
        # One bounded page per check, no polling loops or follow-up agent messages.
        for event in data.get('messages',[])[:50]:
            if event.get('type')!='structured_output_result':continue
            output=event.get('structured_output_result') or {}
            if output.get('success') is not True:raise ManusError('manus_structured_output_failed')
            return validate_selection(output.get('value'),payload)
        return None

    def stop(self,task_id):
        self._request('POST','task.stop',json={'task_id':self._task_id(task_id)})
        return {'task_id':task_id,'stop_requested':True}

    def create(self,payload):
        # This is deliberately before any network call. Neither an estimated
        # prompt length nor a post-task credit check bounds hidden agent tokens.
        raise ManusError('manus_hard_token_cap_unavailable')


def prepare_task(payload):
    """Reviewable private/lite request; this function never submits it."""
    from .mini_enrichment import INSTRUCTIONS,SCHEMA
    if not isinstance(payload,dict) or not set(payload)<= {'stage','url','event','fields','nodes'}:
        raise ManusError('manus_invalid_payload')
    compact={k:payload[k] for k in ('stage','event','fields') if k in payload}
    compact['nodes']=[]
    # Original page URL is not needed by Manus: evidence selections use IDs.
    # Strip URL values too, so a future task receives only already crawled facts.
    for node in payload.get('nodes',[])[:20]:
        compact['nodes'].append({k:v for k,v in node.items() if k in ('id','value','context','hint','label')})
        prompt=INSTRUCTIONS+'\nDo not browse, search, run tools or use connectors. Return only the requested node IDs.\n'+json.dumps(compact,ensure_ascii=True)
        if len(prompt.encode())>MAX_PROMPT_BYTES:
            compact['nodes'].pop();break
    prompt=INSTRUCTIONS+'\nDo not browse, search, run tools or use connectors. Return only the requested node IDs.\n'+json.dumps(compact,ensure_ascii=True)
    if not compact['nodes'] or len(prompt.encode())>MAX_PROMPT_BYTES:raise ManusError('manus_insufficient_evidence')
    # Manus structured-output extraction is post-processing, not an agent budget.
    schema=json.loads(json.dumps(SCHEMA));schema['properties']['selections'].pop('maxItems',None)
    return {'message':{'content':prompt},'agent_profile':'lite','clear_connectors':True,
            'interactive_mode':False,'share_visibility':'private','title':'CrowdStage evidence enrichment',
            'structured_output_schema':schema}


def validate_selection(value,payload):
    if not isinstance(value,dict) or set(value)!={'selections'} or not isinstance(value['selections'],list) or len(value['selections'])>8:
        raise ManusError('manus_invalid_result')
    ids={n['id'] for n in payload.get('nodes',[])};fields=set(payload.get('fields',[]));seen=set();rows=[]
    for row in value['selections']:
        if not isinstance(row,dict) or set(row)!={'field','node_id'}:raise ManusError('manus_invalid_result')
        field=row['field'];node=row['node_id']
        if not isinstance(field,str) or field not in fields or type(node) is not int or node not in ids:
            raise ManusError('manus_unobserved_selection')
        if (field,node) not in seen:seen.add((field,node));rows.append(row)
    return {'selections':rows}


def fallback_report(primary_reason):
    """Persist why backup did not run; never silently bypass the daily ceiling."""
    report=readiness()
    report['primary_reason']=primary_reason
    if not configured():report['reason']='manus_disabled'
    elif not report['credential_available']:report['reason']='manus_missing_key'
    return report

