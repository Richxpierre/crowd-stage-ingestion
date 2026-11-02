# Restartable aggregator discovery

Set `settings.adapter` to `sessionize` or `conference_monkey` on an admin-selected
crawler target. Sessionize starts at `/speakers-directory`; Conference Monkey starts
at `/top/conferences`. These adapters follow observed directory, profile, event and
Next links. Sessionize uses only profile Events links, not speaker bios or talk links.
Conference Monkey uses explicit Visit conference/Website buttons.

The service-only `conference_crawl_frontier` queue stores pages and website links
separately. Unique target/generation/URL/type keys prevent repeats across jobs.
Workers lease tasks atomically, retry failures with a delay, and mark exhausted tasks
as errors. Pages are acknowledged only after children have been saved. Source URLs
are registered and acknowledged in the same database transaction, with per-item
failure isolation. Existing paused sources stay paused. New sources use website-only
parsing and events retain the normal publication review.

A batch retains the existing 100-page and 50-link maximums. Reaching a batch cap does
not discard the remaining queue. The GitHub worker drains batches for ten minutes,
then can request up to two further worker invocations by default. Optional setting
`bulk_max_handoffs` accepts 0–5. The built-in GitHub token receives Actions write
permission only in this worker job and is used only to dispatch this repository's
existing discovery workflow with a specific queued job ID. No new credential is
needed. At the handoff cap, unfinished work remains queued for cron or a manual run.
This bounds automatic runtime; a large catalogue may require another invocation.

Stop immediately prevents further source writes and pauses scheduling. Reset uses a
new queue generation; old history remains. Delete archives the target. The worker
checks stop timestamps before creating a continuation. Failed network requests may
finish before the worker exits. Blocked sites are reported; these adapters do not
bypass CAPTCHAs or access blocks.

Static HTML is fetched with the existing public-host, HTTPS, response-size, rate and
robots guards. Crawl4AI renders only when useful links are missing or adapter markup
requires rendering. An explicit aggregator robots override stays confined to that
aggregator. Completed page tasks can refresh after seven days; source refresh remains
a separate collector.

Verification:
- Unit tests simulate 1,003 website links across small batches with no lost tasks or
  re-reading of the initial page. These are fixtures, not live events.
- `python -m scripts.probe_bulk_discovery` checks real public pages without database writes.
- `scripts/verify_bulk_database.sql` checks transactions, evidence validation,
  continuation, cancellation and service-only permissions, then rolls back.
- Event extraction now recognizes unambiguous event-heading/date blocks without
  requiring address markup, Austrian February dates, and complete dated H4 headings.
  Missing hrefs no longer create fake `/More` or `/Download` navigation URLs.
- Historical edition pages also try the organizer homepage for a current edition.
  A genuinely past-only site remains unchanged, and cancellation observations are retained.

GitHub cron remains best-effort. Handoffs reduce dependence on cron between active
batches; this does not claim a guaranteed schedule or unlimited ingestion capacity.
