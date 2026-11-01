# CrowdStage — Direct Conference Website Ingestion Backend

Additive Python / Supabase / GitHub Actions backend for the **existing CrowdStage website**. No frontend, no standalone `events` schema, and no recurring ConferenceGrid or Sessionize collection.

**Start here:** [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md)  
**Field-by-field mapping:** [`docs/INTEGRATION_MAPPING.md`](docs/INTEGRATION_MAPPING.md)

> Prerequisite: Supabase must already have the CrowdStage Phase 2+ schema, including `events`, `user_roles`, their enums, authorization helpers and existing RLS. The file you supplied describes those objects but does not certify their current deployed state. This project adds ingestion objects; it is not the site's complete database migration.

## What it does

1. Optionally imports candidates from saved SQLite / CSV / JSON / JSONL snapshots, or performs one-time permitted sitemap/RSS/Atom/HTML discovery (review queue only).
2. Registers **approved official organizer URL** targets. Archive/directory URLs are provenance only.
3. Scheduled GitHub Actions executes Python to parse official ICS, JSON-LD Event, or CSS-selected HTML.
4. A service-only RPC inserts new `events` in `pending_review` and updates eligible existing ingested events in place.
5. `conference_event_observations` and `conference_ingestion_changes` preserve provenance and history.
6. Authenticated platform-admin Edge Functions expose status and review/publish operations.
7. Existing CrowdStage calendar continues reading `events` filtered to `published` using its current RLS.

## Relationship to the website

This repository is the sole recurring collector. The website in
[`Richxpierre/crowd-stage`](https://github.com/Richxpierre/crowd-stage)
reviews sources and publishes events in the same Supabase database.
Its queue-refresh action marks an approved source due using `next_check_at`;
this worker selects it on the next scheduled run. Queueing does not start a
workflow or mean collection has completed. For immediate processing, manually
run **Refresh approved official conference websites** in Actions.

Before enabling this worker against a project that used website collection,
follow the website's [handoff guide](https://github.com/Richxpierre/crowd-stage/blob/main/docs/INGESTION_HANDOFF.md):
apply its single-owner migration and retire the deployed legacy Edge Functions.
Keep only this repository's 15-minute queue schedule enabled. Store server credentials
only in trusted backend settings, and use the website's Supabase project URL.
Existing ingestion tables and the batch RPC remain the shared contract.

The website's `20261101054731_ingested_review_details.sql` migration is also
required for its current schema: it accepts unknown format, region, and price
on ingested review records and requires a moderator to confirm them before
publication. Without it, the original website's NOT NULL constraints reject
partially described events. Apply the website's corrective migration after this
repo's initial additive schema migration; do not replace the website schema.

## Local validation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

**Only sources explicitly registered or enabled by an administrator are collected.** Production deployment, external-site permissions, and integration with your live application must be verified separately. No Python worker or secret belongs in client-side Netlify code.


## Conference-only source validation

Apply the website migration `20261101061416_dynamic_event_discovery.sql` before running this collector. Active sources must have `record_scope=conference`; existing unverified registrations are paused for review. Review the actual event page and its organizer permission before enabling a source. RSS publication dates and session schedule pages are not accepted as conference dates. Past events are skipped; cancellations remain eligible for archival.

For a dedicated page representing one conference edition, set `selectors.single_event=true` to preserve identity if the organizer changes its title. Do not set this on a multi-event catalog. Existing observations retain their original identity across collector upgrades.

## Add a website with only its URL

Apply the website migration `20261101161645_url_only_sources.sql`. In Admin → Sources, paste the official organizer URL and click Add website. Names and identifiers are generated, the source is enabled in automatic mode and queued, and future discoveries go to Pending Review. No event is auto-published. Existing sources and advanced settings are preserved when the same URL is added again.

Automatic mode tries JSON-LD Event data, schema.org microdata, linked ICS calendars, and dated HTML event sections. It follows relevant HTTPS links on the same website (including the www alias), up to 8 pages by default and 20 maximum, with a two-minute per-site HTTP budget. It can consult a sitemap when the landing page provides no event facts or useful links. The job installs Chromium and sets RENDER_HTML=1 for at most two JavaScript-rendering fallbacks per source. Rendering sends requests through the guarded HTTP fetcher; third-party domains, forms, WebSockets and service workers are blocked.

Every discovery records its source page, method and supporting evidence. The admin sees pages checked, candidate count and errors. Missing dates produce no event; unknown prices, formats and location fields are not invented. English month-name dates and ISO dates are supported; unfamiliar languages/layouts, external-only widget data, login walls and robots restrictions may need a specific page URL or manual selectors in Settings. No generic parser can guarantee extraction from every website.

The scheduled workflow checks due sources every 15 minutes after this branch is merged to main; GitHub may delay scheduled jobs. Each source retains its own refresh interval (24 hours by default, configurable from 6 to 720). This replaces the previous six-hour queue poll and does not add another collector or Supabase cron. Manual workflow dispatch can process a queued source sooner. Never re-enable the retired website collectors.
