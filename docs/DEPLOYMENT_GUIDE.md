# CrowdStage Conference Calendar Backend — Deployment Guide

**Version:** 2.0 · **Date:** 2026-10-30  
**Scope:** Add conference discovery staging and *direct official-website refresh* to the **existing CrowdStage website and database**. **No new website is deployed.**

> **Preflight requirement:** The provided `crowdstage-data-model-and-security.md` is a **Phase 2+ blueprint**, not proof the described database objects have already been created. Confirm the real deployed Supabase schema, backups, triggers, RLS, and any customized column types before executing SQL. This migration intentionally **requires** `public.events` and `public.user_roles` and aborts if they are missing.

## 1. Resulting architecture

```text
RESEARCH / ONE TIME                        RECURRING / PRODUCTION
Sessionize snapshot / association index       Approved organizer pages
RSS, Atom, sitemap, supplied CSV             Official ICS / JSON-LD / HTML
                |                                      |
       offline CSV/SQLite importer               GitHub Actions cron
                |                                  Python 3.12
       conference_url_candidates                       |
       [needs_review: not events]             HTTP/robots/rate limits
                |                                      |
     human resolves official URL              parse / normalize / hash
                |                                      |
      conference_sources  ----------------------> ingest_crowdstage_conference_batch()
                                                       |
                       existing CrowdStage public.events [pending_review]
                                                       |
                          existing CrowdStage moderator publishes
                                                       |
                         existing CrowdStage website queries events
```

**Hard separation:** `conference_sources.url` is an organizer-hosted URL. `conference_sources.discovery_url` is only a provenance field, never fetched by the recurring worker. The refresh worker explicitly rejects ConferenceGrid and Sessionize domains. A URL imported from them stays an untrusted `conference_url_candidates` record until its **official organizer URL** is reviewed and separately registered.

## 2. Included files and destinations

| File or directory | Purpose | Where to put it |
|---|---|---|
| `supabase/migrations/202610300100_crowdstage_conference_ingestion.sql` | Additive tables, private RLS, guarded service-role ingestion RPC | Existing Supabase database migrations |
| `collector/` | Python direct URL fetch + ICS / JSON-LD / CSS parsers | Backend repository or dedicated ingestion repository |
| `scripts/import_url_candidates.py` | One-time offline CSV/JSON/SQLite import; optional Supabase review-queue upload | Backend repository |
| `scripts/one_time_discovery.py` | Opt-in, **one-run** URL discovery from authorized sitemap/RSS/Atom/HTML indexes | Run manually only; never scheduled |
| `.github/workflows/refresh-official-conferences.yml` | Checks only approved official URLs, every 6 hours | GitHub repo default branch |
| `.github/workflows/test.yml` | Python CI smoke tests | GitHub repo |
| `supabase/functions/ingestion-status/` | Authenticated admin-only refresh health view | Existing Supabase Edge Functions |
| `supabase/functions/ingestion-review/` | Authenticated admin-only publish/reject (with audit logging) | Existing Supabase Edge Functions |
| `integration/calendar-query.example.ts` | Optional query adapter for existing UI; not a new site | Reference only; adapt to current frontend |
| `docs/INTEGRATION_MAPPING.md` | Exact schema and source mapping | Engineering docs |

**Do not run the previous package's `202610300001_initial.sql`, `202610300002_direct_website_refresh.sql`, or deploy its demo `site/` directory, `netlify.toml`, `netlify/functions/conferences.mjs`, or `public-conferences` function.** Those belong to the superseded standalone database/UI design and would collide with CrowdStage.

## 3. Verify the existing CrowdStage database

In **Supabase > SQL Editor**, inspect the current schema before changing anything:

```sql
select to_regclass('public.events') as events,
       to_regclass('public.user_roles') as user_roles,
       to_regclass('public.audit_log') as audit_log;

select column_name, data_type, udt_name, is_nullable
from information_schema.columns
where table_schema = 'public' and table_name = 'events'
order by ordinal_position;

select typname from pg_type
where typname in ('event_status','event_format','event_kind','event_source');
```

Required baseline `events` columns: `id`, `slug`, `org_id`, `status`, `source`, `name`, `summary`, `kind`, `format`, `start_date`, `end_date`, `city`, `country`, `venue`, `attendees`, `price_from`, `external_url`, `dedupe_key`, `updated_at`. The provided model specifies these; your **live instance** must match. Admin functions additionally assume `user_roles` and `audit_log` match the supplied spec. If the live schema differs, adapt and stage the migration rather than forcing it.

Create a recent Supabase database backup / staging copy first; review existing `events` triggers for restricted ingestion status updates and approved admin publication. Ensure platform admins can already read their `user_roles` and update ingested `events` through existing RLS. The migration **does not weaken** existing RLS.

## 4. Apply the additive migration

**Preferred repository/CLI path** (once you have linked your existing Supabase project):

```bash
supabase login
supabase link --project-ref YOUR_EXISTING_PROJECT_REF
# Copy the NEW migration into your existing repo's supabase/migrations/
supabase db push
```

Alternatively, paste **only** `supabase/migrations/202610300100_crowdstage_conference_ingestion.sql` into the SQL Editor once. Do not apply through both routes. The migration creates only:

- `conference_sources`
- `conference_ingestion_runs`
- `conference_event_observations`
- `conference_ingestion_changes`
- `conference_url_candidates`
- `ingest_crowdstage_conference_batch(uuid,jsonb)`

It never creates a second `events` table, rewrites subscriber/organizer membership, or changes billing, user roles, existing `events` RLS, saved events, feeds, or calendar tokens.

**Check the result:**

```sql
select to_regclass('public.conference_sources'),
       to_regclass('public.conference_ingestion_runs'),
       to_regclass('public.conference_event_observations');
select count(*) from public.conference_sources;
```

Both queries should work; the source count normally starts at zero.

## 5. Register ONE approved official website

Start with a single organizer-controlled **event page** or organizer-controlled **ICS feed** that allows your collection and listing use. Document the real permission terms, not a made-up value. The default template at `supabase/example_source.sql` remains disabled by design.

After verifying rights and choosing the right URL:

```sql
insert into public.conference_sources (
 slug,name,publisher,kind,url,discovery_url,
 permission_basis,permission_reviewed_at,
 ingestion_allowed,is_official_source,is_active,
 refresh_hours, min_delay_seconds
) values (
 'verified-organizer-2027','Verified organizer conference page','organizer.example',
 'official_html','https://organizer.example/conference/2027',
 null,
 'REPLACE WITH THE ACTUAL REVIEWED PERMISSION BASIS', now(),
 true,true,true,24,2
);
```

**Replace the example host with an actual organizer website**; the SQL above is illustrative. In the real source record, `permission_basis` must describe what was reviewed. An active source requires a reviewed permission basis and official ownership. For pages without schema.org Event JSON-LD, populate `selectors`:

```sql
update public.conference_sources
set selectors = '{"title":"h1","start_date":"time.event-start","end_date":"time.event-end","venue":".venue"}'::jsonb
where slug = 'verified-organizer-2027';
```

Choose `kind='jsonld'` for official pages with schema.org Event; `kind='official_html'` checks JSON-LD first then configured CSS; `kind='ics'` for organizer-controlled ICS calendars (multiple conferences per feed). `event_kind` controls CrowdStage's `conference`, `expo`, `trade-show`, or `summit` classification. Do not register general Sessionize or ConferenceGrid directory URLs as refresh targets.

## 6. Configure GitHub Actions (Python cron)

This can live in your existing website repository or a **separate private backend repository**. If using the site repository, add only `collector/`, `scripts/`, `requirements.txt`, tests and the `.github/workflows/` directory; **do not replace the website source files**.

Add GitHub **Actions > Secrets and variables > Actions > Repository secrets**:

| Secret | Value |
|---|---|
| `SUPABASE_URL` | Your *existing* `https://<project-ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | The service-role key for server-side ingestion only; never in browser/Netlify frontend |

Add GitHub **Actions repository variable**:

| Variable | Value |
|---|---|
| `COLLECTOR_USER_AGENT` | `CrowdStageConferenceBot/1.0 (+mailto:real-contact@yourdomain.com)` |

Set a real email and review applicable source rules. No keys go into Git, `.env`, public site JS, or Netlify client variables.

**Local test:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
# Export the secrets locally (or use your secrets manager), then:
python -m collector.sync --slug verified-organizer-2027
```

**GitHub test:** Actions > **Refresh approved official conference websites** > **Run workflow** > optionally enter `source_slug` to force one approved official source. Without an input, only sources with `next_check_at <= now()` are selected. Every 6 hours the cron checks for due sources; each source defaults to 24-hour refresh, with a minimum of 6 hours. Scheduled GitHub workflows can run later than their cron time.

To inspect runs:

```sql
select s.slug,r.started_at,r.status,r.fetched_count,r.created_count,
       r.updated_count,r.unchanged_count,r.skipped_count,r.error_message
from public.conference_ingestion_runs r
join public.conference_sources s on s.id=r.source_id
order by r.started_at desc limit 50;

select id,name,start_date,end_date,status,source,org_id,dedupe_key
from public.events where source='ingested' order by created_at desc limit 30;
```

## 7. Deploy the admin Edge Functions

These integrate into your *existing* Supabase instance; they are **not** needed for public calendar rendering:

```bash
supabase functions deploy ingestion-status
supabase functions deploy ingestion-review
```

They expect runtime variables `SUPABASE_URL`, `SUPABASE_ANON_KEY` (or `SUPABASE_PUBLISHABLE_KEY`) and `SUPABASE_SERVICE_ROLE_KEY`. Supabase-hosted projects normally provide the standard variables; verify in **Edge Functions > Secrets**. Both endpoints require a **signed-in CrowdStage admin user's JWT**, verify that user's `user_roles.role='admin'` through RLS, and only then use elevated access. They do not grant admin access based on a user-controlled profile field.

Status endpoint: `GET /functions/v1/ingestion-status` returns approved source health and recent runs. Review endpoint: `POST /functions/v1/ingestion-review` with JSON `{"event_id":"<uuid>","action":"publish"}` or `"reject"`. Do **not** call these with an anonymous browser session. The review update and audit insert are two requests, so the function reports an explicit partial-success warning if audit storage fails; for stronger transactional guarantees, migrate review to a single database RPC in a follow-on iteration.

## 8. Hook into the EXISTING website (Netlify or otherwise)

**No new Netlify site, build output, or deploy configuration is supplied.** The existing CrowdStage frontend should continue querying its existing `public.events` (through its own Supabase client and existing RLS). The calendar should filter `status='published'`, dates intersecting the requested view, and use its current `CSEvent` UI adapter. The optional `integration/calendar-query.example.ts` demonstrates the query shape **only**; adapt it to your actual application (`src/types/crowdstage.ts` was referenced but not included).

```ts
const { data, error } = await supabase.from('events')
  .select('id,slug,name,start_date,end_date,city,country,format,kind,external_url')
  .eq('status', 'published')
  .lte('start_date', rangeEnd)
  .gte('end_date', rangeStart)
  .order('start_date');
```

**Publishing workflow:** first fetch from official URL → `public.events` inserted as `source='ingested'`, `org_id=null`, `status='pending_review'` → admin reviews and publishes → existing public RLS displays event. Later official-site changes update the *same* ingested event with change history. Cancellation sets `status='archived'` so it stops appearing on public calendars. If an organizer claims the listing (`org_id` becomes non-null) or it changes to an organizer/admin-managed source, automatic updates skip it.

## 9. One-time discovery from your archives (optional)

The imported Sessionize snapshot contains about **10,955 candidate URLs**. They are Sessionize pages—not proven official conference websites. The importer *never contacts* Sessionize or ConferenceGrid:

```bash
unzip -p "Catalog conference links on Sessionize speaker profiles.zip" \
  sessionize_event_urls.sqlite > sessionize_event_urls.sqlite

python -m scripts.import_url_candidates \
  --input sessionize_event_urls.sqlite \
  --provenance sessionize-2026-10-15 \
  --output sessionize_leads_review.csv
```

Add `--upload` **only** if you want to load the review queue into Supabase using server-side credentials. This does not publish events or crawl the URLs. You can use the same importer for CSV/JSON/JSONL exports from the *independent source collector* in your research (sitemaps, RSS, Atom or HTML indexes). Each candidate needs to be classified and resolved to an organizer-controlled official URL before it can become a `conference_sources` record.

For *additional* authorized discovery sources described by the independent source collector research, configure `docs/ONE_TIME_DISCOVERY.example.json` (copy to `discovery-sources.json`, add explicitly approved sitemap/RSS/Atom/HTML sources and a real user agent), then run manually:

```bash
python -m scripts.one_time_discovery \
  --config discovery-sources.json \
  --db local_discovery.sqlite \
  --export-csv fresh_leads.csv
python -m scripts.import_url_candidates \
  --input fresh_leads.csv --provenance approved-association-index \
  --output leads_for_official_url_review.csv
```

This **one-shot** helper has no `--watch` option or GitHub cron job and refuses Sessionize/ConferenceGrid as source hosts. The exported URLs still require human official-URL resolution and permission review.

The ConferenceGrid archive and terms notes remain *research/provenance*, not scheduled sources. Written authorization for any third-party ingestion or redistribution must be verified separately. The default package contains zero enabled sources.

## 10. Operations and troubleshooting

- **No source selected:** verify `is_active`, `is_official_source`, `ingestion_allowed`, permission review, and `next_check_at` (or force one slug).
- **Source failed:** inspect `conference_ingestion_runs.error_message` and `conference_sources.last_error`; parser intentionally rejects pages lacking real date/title fields.
- **Robots or HTTP issue:** direct fetcher checks robots.txt, limits responses to 8 MB, refuses private addresses and unreviewed redirects, and respects delays. Fix the source URL or ask the organizer for an approved feed; do not bypass restrictions.
- **No event in public UI:** ingested items start `pending_review`, and your existing site displays `published` only. Verify the existing site's RLS/grants and moderator action.
- **Repeated event after date/name change:** dedicated `official_html` URL identity is stable, but JSON-LD without `@id` uses event URL + edition year; annual evergreen pages and year changes can need a manual dedupe review.
- **Existing Phase 2 schema missing:** the file supplied is a design specification, not executable database creation SQL. Install your existing CrowdStage base migrations before this additive migration.
- **Concurrency/scale:** the default implementation supports modest approved URL lists. For thousands of active hosts, add durable leases and job sharding before turning up concurrency. Current scheduled job uses GitHub workflow concurrency at repository level, not a cross-worker distributed lock.
- **No live deployment validation:** local unit tests validate parsers and control-flow; this package was not run against your real Supabase, GitHub, or Netlify services.

## 11. Go-live checklist

- [ ] Existing CrowdStage schema, RLS, triggers and backups verified in staging
- [ ] Additive SQL migration applied once and smoke-tested
- [ ] Test organizer URL is official, approved and documented
- [ ] Python test suite passes; first approved source returns expected fields
- [ ] Ingested event appears in admin review, but not public until publication
- [ ] Admin JWT role check succeeds; non-admin access is denied
- [ ] Existing calendar displays a published event and excludes archived/cancelled ones
- [ ] No ConferenceGrid/Sessionize domain is registered for recurring refresh
- [ ] Secrets remain server-side and not in Netlify/public app bundles
- [ ] Refresh failures, source health and moderation queue have designated owners
