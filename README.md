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
Keep only this repository's six-hour schedule enabled. Store server credentials
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

**No source is enabled automatically.** Production deployment, external-site permissions, and integration with your live application must be verified separately. No Python worker or secret belongs in client-side Netlify code.


