"""Bounded first-save setup. Crawl evidence first, tiny node selection only if needed."""
import asyncio
from datetime import datetime,timezone
import os
import time
from urllib.parse import urlsplit
from .crawler_reader import Crawl4AIReader,same_owner
from .discovery_mapping import observed_nodes,path_pattern,mapped_links
from .mini_enrichment import MiniEnricher
from .manus_backup import configured


async def run_setup(db,job,reader_factory=Crawl4AIReader):
    config=job['config'];seed=config['url'];settings=config.get('settings') or {}
    state=config.get('setup_state') or {'queue':[seed],'visited':[],'steps':[],'examples':[],'errors':[]}
    deadline=time.monotonic()+150;ai=MiniEnricher(deadline=deadline)
    generation=config.get('setup_generation') or job['id'];pending=None

    def finish(status,reason,generated=None):
        report={'status':status,'reason':reason,'pages_checked':len(state['visited']),'page_limit':3,
                'steps':state['steps'],'example_urls':state['examples'][:5],'errors':state['errors'][-3:],
                'ai':ai.audit,'manus_job_id':pending,'checked_at':datetime.now(timezone.utc).isoformat()}
        return db.request('POST','rpc/finish_conference_crawler_setup',json={
            'p_job_id':job['id'],'p_report':report,'p_generated':generated or {},'p_state':state})

    def heartbeat(stage):
        current=db.crawl_job(job['id'])
        if not current or current.get('status')!='running' or current.get('cancel_requested'):return False
        db.update_crawl_job(job['id'],{'pages_scanned':len(state['visited']),'heartbeat_at':datetime.now(timezone.utc).isoformat(),
            'config':{**config,'setup_state':state,'progress_stage':stage}})
        return True

    try:
        if not heartbeat('Inspecting aggregator structure (up to 3 pages)'):return finish('cancelled','Setup cancelled')
        async with reader_factory(os.environ.get('COLLECTOR_USER_AGENT',''),deadline=deadline,
                skip_robots_for=seed if settings.get('skip_robots') is True else None) as reader:
            while state['queue'] and len(state['visited'])<3 and time.monotonic()<deadline:
                url=state['queue'].pop(0)
                if not same_owner(url,seed) or url in state['visited']:continue
                if not heartbeat('Mapping listing → event/profile → organizer website'):return finish('cancelled','Setup cancelled')
                try:page=await reader.read(url)
                except Exception as exc:
                    state['visited'].append(url);state['errors'].append({'url':url,'message':str(exc)[:300]});continue
                if not same_owner(page['url'],seed):
                    state['visited'].append(url);state['errors'].append({'url':url,'message':'Cross-domain redirect needs review'});continue
                nodes=observed_nodes(page,seed);selections=[]
                prior=state.pop('pending',None)
                if prior and prior['url']==url:
                    cached=db.request('GET','conference_manus_jobs',params={'id':'eq.'+prior['id'],'select':'status,result','limit':'1'})
                    if cached and cached[0]['status']=='completed':
                        # Rebind saved node IDs to current anchors only if the URL still exists.
                        current={n['url']:n for n in nodes}
                        old={n['id']:n for n in prior['nodes']}
                        for choice in (cached[0].get('result') or {}).get('selections',[]):
                            n=old.get(choice.get('node_id'),{})
                            if choice.get('field') in ('follow','website','pagination') and n.get('url') in current:
                                selections.append((choice['field'],current[n['url']]))
                else:
                    # Explicit website buttons / recognized listing anchors need no paid inference.
                    selections=[(n['hint'],n) for n in nodes if n['hint']]
                    detail_page='/conference/' in urlsplit(url).path or '/speakers/' in urlsplit(url).path
                    weak_follows=all(n.get('_priority',6)>=4 for f,n in selections if f=='follow')
                    if not any(f=='website' for f,n in selections) and (detail_page or weak_follows or len({n['selector'] for f,n in selections if f=='follow'})>3):
                        selections=[]
                        fields=['website'] if detail_page else ['follow','website','pagination']
                        payload_nodes=[{k:n[k] for k in ('id','value','context','hint','label')} for n in nodes]
                        chosen=await asyncio.to_thread(ai.choose,'discovery_setup',url,'Aggregator setup '+job['target_id'],fields,payload_nodes)
                        byid={n['id']:n for n in nodes}
                        selections=[(c['field'],byid[c['node_id']]) for c in chosen if c.get('node_id') in byid]
                        if not selections and configured() and payload_nodes:
                            from .manus_queue import enqueue
                            payload={'stage':'discovery_setup','url':url,'event':'Aggregator setup '+job['target_id']+':'+generation,
                                     'fields':fields,'nodes':payload_nodes}
                            queued=enqueue(payload,db)
                            if queued['status']=='completed':
                                selections=[(c['field'],byid[c['node_id']]) for c in (queued.get('result') or {}).get('selections',[]) if c.get('node_id') in byid and c.get('field') in payload['fields']]
                            elif queued['status'] in ('queued','running'):
                                pending=queued['id'];state['pending']={'id':pending,'url':url,'nodes':nodes};state['queue'].insert(0,url)
                                return finish('awaiting_enrichment','Waiting for Manus mini enrichment; shared credit buffer applies')
                            else:state['errors'].append({'url':url,'message':'Mini enrichment unavailable: '+str(queued.get('reason') or queued['status'])})
                state['visited'].append(url)
                step={'match_path':path_pattern(page['url'])}
                website_nodes=[n for f,n in selections if f=='website']
                if website_nodes:step['website_require_label']=all(n['hint']=='website' for n in website_nodes)
                if any(f=='website' for f,n in selections) and page['url'].rstrip('/').count('/')==2:
                    step['match_path']='/*'
                for field,node in selections:
                    if field not in ('follow','website','pagination'):continue
                    key=field+'_selector'
                    if key not in step:step[key]=node['selector']
                    elif node['selector'] not in step[key]:step[key]+=', '+node['selector']
                    if len(step[key])>500:step.pop(key,None)
                if len(step)>1:
                    inside,outside=mapped_links(page,seed,[step]);state['steps'].append(step)
                    state['examples'].extend(x['url'] for x in outside if x['url'] not in state['examples'])
                    state['queue'].extend(u for u in inside if u not in state['visited'] and u not in state['queue'])
                if state['examples']:
                    return finish('ready','Verified organizer links on sampled aggregator pages',
                                  {'discovery_mode':'website_links','discovery_steps':state['steps']})
        return finish('needs_review','No verified organizer-link sequence in the three-page sample. Check the entry URL or advanced settings.')
    except Exception as exc:
        state['errors'].append({'url':seed,'message':str(exc)[:300]})
        return finish('failed','Automatic setup could not complete. Review the error and retry.')
