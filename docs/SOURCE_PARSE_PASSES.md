# Independent source parsing

Organizer websites have four passes: `event_date`, `event_stats`,
`event_location`, `event_details`. Scheduled website runs enable them with
`SOURCE_PARSE_PASSES=1`; explicit calendar/feed adapters retain their existing path.

One bounded page snapshot is shared per run. Pass-specific `page_urls` and
CSS `selectors` are stored under `conference_sources.selectors.parse_passes`.
All targets still pass HTTPS, public DNS, organizer-domain, redirect, response-size,
rate-limit and rendering guards. Missing facts stay unknown. Date extraction uses
the existing dated-event parsers; optional structured-data and HTML extraction do
not depend on date success. Settings can disable individual passes.

Each pass persists running/completed status, results, page URL, page SHA-256,
method, errors and elapsed time in `conference_source_parse_results`.
Invalid CSS fails only its own pass. The assembler joins facts by event name,
URL and edition. Administrator-declared single-event websites can join their
configured supplementary pages. Conflicting values stay unknown. Missing facts
can retain prior observations only for exactly the same identity and dates.

The admin page is `/admin/source-parser?source=<source UUID>`, linked from Sources.
Run one pass to save an extraction preview independently. Run all four to assemble
and ingest eligible upcoming events into the existing publication-review pipeline.
Single-pass reruns deliberately do not combine new facts with old snapshots.

Requests are stored in `conference_source_parse_requests`, with one queued/running
request per source. The existing refresh workflow drains up to three requests
before due sources. Claims are service-only and atomic. Stale running requests
become failed after 20 minutes when a worker next claims; admins can queue again.
Paused/unapproved sources cannot be processed. Ordinary members cannot read or
queue parser work. Admins can read extraction evidence but cannot forge results.

Use workflow `parse_only=true` with a specific source slug to verify and persist
all four passes without changing event records. AI is not involved.

Limitations: automatic optional extraction handles explicit Event JSON-LD,
OpenGraph/meta descriptions/images on named HTML event pages, microdata address
fields and unambiguous labelled attendance/exhibitor counts. Arbitrary unlabelled
addresses/prices need source-specific selectors; unknown does not mean zero.
