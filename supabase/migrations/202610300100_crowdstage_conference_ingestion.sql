-- CrowdStage ADDITIVE integration only. Requires the existing CrowdStage Phase 2 schema.
-- Does not create/replace public.events, its enums, RLS, roles, subscribers, or feeds.
-- Apply through reviewed Supabase migrations once; back up before running.
create extension if not exists pgcrypto;

do $$ begin
  if to_regclass('public.events') is null or to_regclass('public.user_roles') is null then
    raise exception 'Install the CrowdStage baseline schema BEFORE this additive ingestion migration';
  end if;
end $$;

create table if not exists public.conference_sources (
 id uuid primary key default gen_random_uuid(),
 slug text not null unique,
 name text not null,
 publisher text not null,
 kind text not null check (kind in ('ics','jsonld','official_html')),
 url text not null unique check (url ~ '^https://'),
 discovery_url text,  -- provenance ONLY: NEVER fetched by the refresh worker
 permission_basis text not null default '',
 permission_reviewed_at timestamptz,
 ingestion_allowed boolean not null default false,
 is_official_source boolean not null default false,
 is_active boolean not null default false,
 selectors jsonb not null default '{}'::jsonb check (jsonb_typeof(selectors) = 'object'),
 event_kind public.event_kind not null default 'conference',
 refresh_hours integer not null default 24 check (refresh_hours between 6 and 720),
 next_check_at timestamptz not null default now(),
 min_delay_seconds numeric not null default 2 check (min_delay_seconds >= 1),
 etag text,
 last_modified text,
 last_attempt_at timestamptz,
 last_success_at timestamptz,
 last_error text,
 created_at timestamptz not null default now(),
 constraint conference_source_permission check (
   not is_active or (is_official_source and ingestion_allowed and permission_reviewed_at is not null and length(trim(permission_basis)) >= 8)
 )
);
create index if not exists ix_cs_due on public.conference_sources(next_check_at)
  where is_active and is_official_source and ingestion_allowed;

create table if not exists public.conference_ingestion_runs (
 id uuid primary key default gen_random_uuid(),
 source_id uuid not null references public.conference_sources(id) on delete cascade,
 started_at timestamptz not null default now(),
 completed_at timestamptz,
 status text not null default 'running' check (status in ('running','success','unchanged','failed')),
 http_status int,
 fetched_count int not null default 0,
 created_count int not null default 0,
 updated_count int not null default 0,
 unchanged_count int not null default 0,
 skipped_count int not null default 0,
 error_message text
);
create index if not exists ix_cs_runs on public.conference_ingestion_runs(source_id,started_at desc);

create table if not exists public.conference_event_observations (
 id uuid primary key default gen_random_uuid(),
 event_id uuid not null references public.events(id) on delete cascade,
 source_id uuid not null references public.conference_sources(id) on delete cascade,
 source_record_id text not null,
 source_url text not null,
 content_hash text not null,
 payload jsonb not null default '{}'::jsonb,
 first_seen_at timestamptz not null default now(),
 last_seen_at timestamptz not null default now(),
 unique (source_id,source_record_id)
);
create index if not exists ix_cs_observations_event on public.conference_event_observations(event_id);

create table if not exists public.conference_ingestion_changes (
 id bigint generated always as identity primary key,
 event_id uuid not null references public.events(id) on delete cascade,
 source_id uuid not null references public.conference_sources(id) on delete cascade,
 change_type text not null check (change_type in ('created','updated','cancelled')),
 old_record jsonb,
 new_record jsonb,
 changed_at timestamptz not null default now()
);
create index if not exists ix_cs_changes on public.conference_ingestion_changes(event_id,changed_at desc);

create table if not exists public.conference_url_candidates (
 url text primary key check (url ~ '^https://'),
 discovered_from text not null,
 found_at timestamptz not null default now(),
 review_state text not null default 'needs_review' check (review_state in ('needs_review','approved','rejected')),
 official_url text check (official_url is null or official_url ~ '^https://'),
 reviewed_at timestamptz,
 notes text
);

-- All new ingestion tables private: service role may operate, users cannot directly read/write.
-- The existing CrowdStage events RLS continues unchanged.
do $$ declare t text; begin
  foreach t in array array[
    'conference_sources','conference_ingestion_runs','conference_event_observations',
    'conference_ingestion_changes','conference_url_candidates'
  ] loop
    execute format('alter table public.%I enable row level security',t);
    execute format('revoke all on public.%I from anon,authenticated',t);
    execute format('grant all on public.%I to service_role',t);
  end loop;
end $$;

-- Grant the identity sequence only to service role (identity column for changes table).
grant usage,select on sequence public.conference_ingestion_changes_id_seq to service_role;

create or replace function public.ingest_crowdstage_conference_batch(p_source_id uuid, p_records jsonb)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
 s public.conference_sources%rowtype;
 item jsonb;
 existing public.events%rowtype;
 eid uuid;
 new_slug text;
 new_format public.event_format;
 new_kind public.event_kind;
 new_status public.event_status;
 new_start date;
 new_end date;
 new_url text;
 old_payload jsonb;
 n_created int := 0;
 n_updated int := 0;
 n_unchanged int := 0;
 n_skipped int := 0;
begin
 if auth.role() is distinct from 'service_role' then raise exception 'service role required'; end if;
 select * into s from public.conference_sources where id=p_source_id;
 if not found or not s.is_active or not s.is_official_source or not s.ingestion_allowed
   or s.permission_reviewed_at is null or length(trim(s.permission_basis)) < 8 then
   raise exception 'Inactive/unapproved official conference source';
 end if;
 if jsonb_typeof(p_records) is distinct from 'array' or jsonb_array_length(p_records) > 100 then
   raise exception 'Expected array of no more than 100 records';
 end if;
 for item in select value from jsonb_array_elements(p_records) loop
   if length(coalesce(item->>'dedupe_key','')) <> 64
     or coalesce(item->>'source_record_id','') = '' or coalesce(item->>'title','') = ''
     or coalesce(item->>'start_date','') = '' or length(coalesce(item->>'content_hash','')) <> 64 then
     raise exception 'Missing required conference fields';
   end if;
   new_start := (item->>'start_date')::date;
   new_end := coalesce(nullif(item->>'end_date','')::date,new_start);
   if new_end < new_start or new_end > new_start + 366 then raise exception 'Invalid event date range'; end if;
   new_url := coalesce(nullif(item->>'official_url',''),nullif(item->>'event_url',''),s.url);
   if new_url !~ '^https://' then raise exception 'Official event URL must be HTTPS'; end if;
   new_format := case item->>'format'
     when 'hybrid' then 'hybrid'::public.event_format
     when 'online' then 'virtual'::public.event_format
     when 'in_person' then 'in-person'::public.event_format
     else null end;
   new_kind := s.event_kind;
   new_status := case when item->>'status'='cancelled'
     then 'archived'::public.event_status else 'pending_review'::public.event_status end;
   new_slug := left(trim(both '-' from regexp_replace(lower(item->>'title'),'[^a-z0-9]+','-','g')),55)
               || '-' || substr(item->>'dedupe_key',1,12);
   select * into existing from public.events where dedupe_key=item->>'dedupe_key' for update;
   if not found then
     insert into public.events(
       slug,org_id,status,source,name,summary,kind,format,start_date,end_date,
       city,country,venue,attendees,price_from,external_url,dedupe_key
     ) values (
       new_slug,null,new_status,'ingested',left(item->>'title',240),left(item->>'summary',1200),
       new_kind,new_format,new_start,new_end,nullif(item->>'city',''),
       nullif(item->>'country',''),nullif(item->>'venue',''),null,null,new_url,item->>'dedupe_key'
     ) returning id into eid;
     insert into public.conference_ingestion_changes(event_id,source_id,change_type,new_record)
       values(eid,p_source_id,case when item->>'status'='cancelled' then 'cancelled' else 'created' end,item);
     n_created := n_created + 1;
   else
     eid := existing.id;
     -- Once claimed or organizer/admin-managed, automated ingestion must not overwrite it.
     if existing.source <> 'ingested' or existing.org_id is not null then
       n_skipped := n_skipped + 1;
       continue;
     end if;
     select payload into old_payload from public.conference_event_observations
       where event_id=eid and source_id=p_source_id and source_record_id=item->>'source_record_id';
     if old_payload is not null and old_payload->>'content_hash'=item->>'content_hash' then
       n_unchanged := n_unchanged + 1;
     else
       update public.events set
         name=left(item->>'title',240), summary=left(item->>'summary',1200),
         kind=new_kind, format=coalesce(new_format,existing.format),
         start_date=new_start,end_date=new_end,city=nullif(item->>'city',''),
         country=nullif(item->>'country',''),venue=nullif(item->>'venue',''),
         external_url=new_url,
         status=case when item->>'status'='cancelled' then 'archived'::public.event_status
           when existing.status='archived' and old_payload->>'status'='cancelled'
             then 'pending_review'::public.event_status else existing.status end,
         updated_at=now()
       where id=eid;
       insert into public.conference_ingestion_changes(event_id,source_id,change_type,old_record,new_record)
         values(eid,p_source_id,case when item->>'status'='cancelled' then 'cancelled' else 'updated' end,old_payload,item);
       n_updated := n_updated + 1;
     end if;
   end if;
   insert into public.conference_event_observations(
     event_id,source_id,source_record_id,source_url,content_hash,payload
   ) values (eid,p_source_id,item->>'source_record_id',coalesce(item->>'source_url',s.url),item->>'content_hash',item)
   on conflict(source_id,source_record_id) do update set
     event_id=excluded.event_id,source_url=excluded.source_url,content_hash=excluded.content_hash,
     payload=excluded.payload,last_seen_at=now();
 end loop;
 return jsonb_build_object('created',n_created,'updated',n_updated,'unchanged',n_unchanged,'skipped',n_skipped);
end $$;
revoke all on function public.ingest_crowdstage_conference_batch(uuid,jsonb) from public,anon,authenticated;
grant execute on function public.ingest_crowdstage_conference_batch(uuid,jsonb) to service_role;

comment on table public.conference_sources is 'Only organizer-controlled URLs. discovery_url is audit provenance, never polled by recurring jobs.';
comment on table public.conference_url_candidates is 'Untrusted leads, not publication-ready events. Resolve official URL manually first.';
