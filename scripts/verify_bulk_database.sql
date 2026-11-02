begin;
select set_config('request.jwt.claim.role','service_role',true);
do $$
declare t uuid; j uuid; good uuid; bad uuid; result jsonb; next_job uuid;
begin
 insert into public.conference_crawler_targets(name,url,settings) values('Rollback bulk test','https://sessionize.com/speakers-directory?bulk-test=rollback','{"adapter":"sessionize"}') returning id into t;
 insert into public.conference_crawl_jobs(target_id,status,config) values(t,'running',jsonb_build_object('url','https://sessionize.com/speakers-directory?bulk-test=rollback','bulk_generation','test','settings',jsonb_build_object('adapter','sessionize'))) returning id into j;
 insert into public.conference_crawl_frontier(target_id,generation,url,task_kind,payload) values
 (t,'test','https://bulk-test.example.org/','source','{"listing_url":"https://sessionize.com/test-profile","title":"Bulk test conference","evidence":{"method":"sessionize_profile_event_link","link_label":"Conference","section":"events","selector":"#events .c-s-event a.c-s-event__name[href]"}}') returning id into good;
 insert into public.conference_crawl_frontier(target_id,generation,url,task_kind,payload) values
 (t,'test','https://bulk-test-invalid.example.org/','source','{"listing_url":"https://sessionize.com/test-profile","title":"Bad evidence","evidence":{"method":"sessionize_profile_event_link","link_label":"Conference","section":"sessions"}}') returning id into bad;
 perform public.claim_conference_frontier(j,'source',20);
 result:=public.complete_conference_frontier_sources(j,array[good,bad]);
 if not exists(select 1 from public.conference_crawl_frontier where id=good and status='done') then raise exception 'Good source did not commit'; end if;
 if not exists(select 1 from public.conference_crawl_frontier where id=bad and status='queued' and last_error is not null) then raise exception 'Bad evidence was not isolated'; end if;
 if not exists(select 1 from public.conference_sources where url='https://bulk-test.example.org/' and selectors->>'website_only'='true') then raise exception 'Source registration configuration failed'; end if;
 if exists(select 1 from public.conference_sources where url='https://bulk-test-invalid.example.org/') then raise exception 'Invalid evidence became a source'; end if;
 insert into public.conference_crawl_frontier(target_id,generation,url,task_kind) values(t,'test','https://sessionize.com/test-profile','page');
 update public.conference_crawl_jobs set status='partial' where id=j;
 next_job:=public.continue_conference_bulk_crawl(j);
 if next_job is null then raise exception 'Continuation did not queue'; end if;
 if public.continue_conference_bulk_crawl(j) is not null then raise exception 'Continuation duplicated active job'; end if;
 update public.conference_crawl_jobs set status='cancelled' where id=next_job;
 update public.conference_crawler_targets set settings=settings||jsonb_build_object('stop_requested_at',now()+interval '1 second') where id=t;
 if public.continue_conference_bulk_crawl(j) is not null then raise exception 'Stop did not block continuation'; end if;
 update public.conference_crawl_jobs set status='cancelled' where id=j;
 begin perform public.claim_conference_frontier(j,'page',1); raise exception 'Cancelled job claimed work';
 exception when raise_exception then if sqlerrm<>'Crawl is not running' then raise; end if; end;
 if has_function_privilege('anon','public.claim_conference_frontier(uuid,text,integer)','execute')
  or has_function_privilege('authenticated','public.complete_conference_frontier_sources(uuid,uuid[])','execute') then raise exception 'Service-only permissions leaked'; end if;
end $$;
rollback;
