# CrowdStage Conference Ingestion — Integration Mapping

**Date:** 2026-10-30 · **Basis:** `crowdstage-data-model-and-security.md` (Phase 2+), the two Sessionize research archives, `ConferenceGrid public structure notes.md`, the ConferenceGrid historical archive, and the original conference calendar starter project.

## A. Design decision: keep the existing site and database

The supplied CrowdStage Phase 2+ design has an `events` table whose `status` is **editorial/publication state**, not a time-state. Valid values: `draft`, `pending_review`, `published`, `rejected`, `archived`. The source field is `event_source`: `organizer`, `admin`, `ingested`. It also has `org_id` for organizer ownership and a nullable `dedupe_key` reserved for ingestion.

The former starter used **its own incompatible `public.events` table** (`title`, `publication_state`, `status='upcoming'`, `official_url` and `categories[]`). **That schema, view and demo site must not be deployed to CrowdStage.** The new code writes directly to the **existing CrowdStage** `events` schema, with a separate ingestion metadata namespace.

## B. Architecture mapping

| Concern | CrowdStage object | Updated module | Behavior |
|---|---|---|---|
| Candidate discovery | `conference_url_candidates` (new) | `scripts/import_url_candidates.py`, `scripts/one_time_discovery.py` | One-time/offline import from research snapshots or permitted index sources; `needs_review`; never public |
| Official target registry | `conference_sources` (new) | Admin SQL/registry | Only verified official URL may be active; provenance `discovery_url` is never polled |
| Scheduler | none required in DB | `.github/workflows/refresh-official-conferences.yml` | 6-hour cron checks for due official websites |
| Python worker | none in DB | `collector/sync.py`, `collector/fetch.py` | Conditional GET, robots awareness, rate limiting, private network safeguards |
| Extraction | observation payload (new) | `collector/parsers.py`, `collector/official.py` | Organizer ICS / JSON-LD Event / configured CSS |
| Deduplication | `events.dedupe_key` | Parser hash + guarded ingestion RPC | Stable publisher + source record identity |
| Event publication | Existing `events` | `ingest_crowdstage_conference_batch` | New rows `status='pending_review'`, `source='ingested'`, `org_id=null` |
| Organizer ownership | Existing `organizations`, `event_claims` | RPC ownership guard | Claimed or organizer/admin-owned rows are not overwritten |
| Change history | `conference_ingestion_changes` (new) | Ingestion RPC | Records before/after payloads on substantive updates |
| Last-seen provenance | `conference_event_observations` (new) | Ingestion RPC | Links original source URL / UID / hash / read time to event |
| Job observability | `conference_ingestion_runs` (new) | Worker + `ingestion-status` Edge | Successful, unchanged, failed runs, counts, source health |
| Publication moderation | Existing `user_roles` / `events` / `audit_log` | `ingestion-review` Edge | Authenticated platform admins only; publish/reject and audit |
| Public calendar | Existing `events` RLS / `src/types/crowdstage.ts` | `integration/calendar-query.example.ts` | No replacement site; query published events through current app client |
| User saves & feeds | Existing `saved_events`, `feeds`, `feed_subscriptions` | none | Unchanged; refer to original event UUIDs |
| Calendar feeds/tokens | Existing `calendar_tokens` | none | Unchanged; downstream feeds can pick up published ingested events |
| Billing & memberships | Existing `subscriptions`, `profiles`, `organization_members` | none | No mutations |

## C. Field-by-field parse -> CrowdStage database

| Parsed property | Existing `events` field | Rule / limitation |
|---|---|---|
| Stable publisher-scoped SHA-256 identity | `dedupe_key` | Used to match same event across refreshes; no heuristic fuzzy merge |
| Conference title | `name` | Required; up to 240 chars |
| Short text description | `summary` | Optional; max 1,200 chars |
| Source classification | `kind` | Uses configured `conference_sources.event_kind` enum (`conference`, `expo`, `trade-show`, `summit`) |
| In-person | `format = 'in-person'` | Maps parser `in_person` |
| Virtual | `format = 'virtual'` | Maps parser `online` |
| Hybrid | `format = 'hybrid'` | Maps parser `hybrid` |
| Unknown attendance format | `format = NULL` on first insert | Does **not** fabricate a format; retains prior value on update |
| Start/end date | `start_date`, `end_date` | Required start; end defaults to start; exclusive all-day ICS DTEND corrected |
| City/country/venue | `city`, `country`, `venue` | Nullable values; absent values stay null |
| Official page / event URL | `external_url` | HTTPS; from parsed organizer payload or official target URL |
| Ingestion provenance | `source='ingested'` | New record from service role only |
| Event ownership | `org_id=NULL` | Until organizer claims event; then worker never overwrites it |
| Initial moderation | `status='pending_review'` | Never auto-publication; existing UI RLS displays published only |
| Cancellation signal | `status='archived'` | Published listing removed from public view; change logged |
| New event URL-stable slug | `slug` | Lowercase title with suffix from dedupe hash, to avoid collisions |
| Published record refresh | `name`, dates, location, format, summary, `external_url` | Updated in-place for still-unclaimed ingested events; stays published unless cancelled |

**Fields deliberately *not* auto-populated** because the supplied schema or sources do not safely support them: `region` (CrowdStage has a different enum from generic address regions), `size`, `vertical_slug`, `event_tags`, `attendees`, `price_from`, `cover_url`, `featured`, `sponsorship_packages`, `event_deadlines`, `created_by`, and `org_id`. Source `timezone`, `categories`, `cfp_url`, `cfp_deadline`, `registration_url` remain in the ingestion observation's JSON payload until explicit mapping and validation rules are added. The worker does not pretend a null price is a free event.

## D. Research sources and permitted use in this system

| Archive or note | Research finding | How this backend uses it |
|---|---|---|
| `Catalog conference links on Sessionize speaker profiles.zip` | SQLite snapshot with 10,955 Sessionize event URL candidates | Offline CSV/SQLite import into private candidate review queue; does not scrape or auto-publish them |
| `Catalog conference links on Sessionize speaker profiles (1).zip` | Additional independent URL collector for sitemaps, RSS/Atom and curated HTML links | Offline import of its generated CSV/JSON; configure newly resolved organizer URLs as direct targets |
| `ConferenceGrid public structure notes.md` | Conference details, ICS normalized event fields, source provenance, rights/terms concerns | Parser fields, provenance, change logs and source-permission gate; no recurring directory calls |
| `Scrape ConferenceGrid for an active conference list.zip` | Historical October 2026 archive with 95 ICS feeds, 7,331 VEVENT occurrences, and 3,614 distinct UIDs | Parser validation reference only; not shipped as live import/data feed and not republished by default |
| Prior `conference-calendar-starter.zip` | Python parsers, security-focused HTTP fetcher, scheduled workflow, sample UI and independent schema | Reuse/refactor Python collection patterns; **remove** standalone events table, view, demo site, Netlify proxy and public-conferences API |
| CrowdStage Phase 2+ schema | Multi-tenant organizer, subscriber, saved feeds, audit, editorial event states | Additive integration preserves existing model, RLS and permissions |

**Core principle:** Discovery directories are only leads. After an official URL is verified, repeated data collection is directly against that source's website or organizer-hosted feed. This backend does not contain a recurring ConferenceGrid or Sessionize crawler.

## E. Relationships and lifecycle

```text
conference_url_candidates (review-only leads; NEVER published)
             |
             | human verifies organizer's event URL + rights
             v
conference_sources -----------> conference_ingestion_runs
             |
             | official URL refresh (Python; GitHub Actions)
             v
ingest_crowdstage_conference_batch() service-role-only
             |
             +------> events (existing, source='ingested', org_id=NULL)
             |              |
             |              +--> event_claims (existing: organizer takeover)
             |              +--> saved_events / feeds (existing)
             |
             +------> conference_event_observations
             +------> conference_ingestion_changes

Admin user JWT -> ingestion-review -> events.status=published -> existing calendar
```

**State examples:**

1. Brand-new confirmed organizer event: upsert `events` as `pending_review`; not public.
2. CrowdStage admin publishes: same UUID and dedupe key; existing saved-events references remain valid.
3. Organizer changes dates: same official event identity; dates update in place, changed payload logged.
4. Organizer cancels: `events.status` becomes `archived`, removing it from the published calendar. If a previously cancelled source reopens, it returns to `pending_review`, not automatic publication.
5. Organizer claims event: `org_id` now set; collector stops overwriting that event even if its source continues to refresh.
6. Feed disappears/returns empty: worker fails safely and preserves all events; automatic missing-event archival is not implemented.

## F. Existing site integration contract

The provided schema maps Phase 1 mocks (`src/data/*`, `src/types/crowdstage.ts`) to the actual database. The only live UI change potentially needed is switching its event-list query from mock data to Supabase-backed `events` if that has not already happened. No new UI, CSS, Netlify `publish` directory, or route replacements are in this project.

| Existing Phase 1 source | Existing database target | Ingestion effect |
|---|---|---|
| `EVENTS`, `CSEvent` | `events` + optionally `event_tags` | New ingested rows become visible **after** admin publication |
| `ORGANIZERS` | `organizations` | No automatic organization creation; `org_id=NULL` initially |
| `VERTICALS`, `TAGS` | `verticals`, `tags` | No automatic taxonomy changes |
| `User.savedEventSlugs` | `saved_events` | UUID-based association stays valid through updates |
| `MOCK_FEEDS`, `PUBLIC_FEEDS` | `feeds`, `feed_subscriptions` | Existing feed filters keep working against published events |
| `PLANS` | `subscriptions.plan` | Billing unaffected |
| `src/data/activity.ts` | `event_views` / `saved_events` | Ingestion does not fabricate page views or analytics |
| `session.tsx` account switch | `profiles.default_org_id` / `organization_members` | Unchanged |

**Important assumption:** No actual CrowdStage frontend source tree or deployed database dump was attached; only its data-model/security blueprint was supplied. Therefore TypeScript field names and UI adapters must be checked against the existing repository before copy/paste; the example query is intentionally not a replacement implementation.

## G. Security rules and remaining work

- Service-role credentials run only on GitHub Actions or trusted Supabase server functions. Do not place them in Netlify public build variables.
- `conference_*` tables have RLS enabled with no `anon`/`authenticated` access. Existing `events` RLS remains untouched.
- API publishing is a separate user-authorized admin action, not a collector feature. Edge Functions check the **authenticated user's** `user_roles` before using any elevated key.
- `ingestion-review` writes the existing `audit_log`, but its update and audit are not atomic. If your production baseline has additional RLS/trigger requirements, validate this action in staging or replace with a single transaction RPC.
- Old CrowdStage spec says `event_deadlines`, `event_tags`, and iCal token feeds exist conceptually. Their production migrations and handlers are not in the materials supplied; this backend does not implement them.
- HTML extraction is either schema.org JSON-LD or explicitly configured CSS selectors. JavaScript-only/blocked websites require owner-approved alternate data access; not stealth automation.
- Deferred: official URL resolution automation, cross-organizer fuzzy dedupe, geographic normalization, vertical/tag mapping, image capture, registration/CFP deadlines, reconciliation of missing records, high-volume queue leasing, transactional approval RPC, per-field provenance arbitration, and integration tests against actual hosted CrowdStage schema.

## H. Superseded files — DO NOT DEPLOY

From `conference-calendar-starter.zip`:

- `supabase/migrations/202610300001_initial.sql`
- `supabase/migrations/202610300002_direct_website_refresh.sql`
- `supabase/functions/public-conferences/index.ts`
- `supabase/functions/admin-source-status/index.ts`
- `netlify/functions/conferences.mjs`, `netlify.toml`, `site/*`
- the earlier README's standalone deployment steps

These were built for a different table shape (`title`, `publication_state`, `published_conferences`) and can overwrite/conflict with the actual CrowdStage design. Use **only the files in the new ZIP** for this integration.
