# Website admission gate

Aggregator links and manual URLs enter `conference_source_validations` first.
They cannot insert an active Source directly. A database trigger requires a
validated record whose canonical URL matches the Source; legacy direct promotion
is blocked. Existing aggregator registration now enqueues validation instead.

The validation worker uses the existing guarded Crawl4AI reader and private
SearXNG service. It loads the discovered URL and searches within that website,
then inspects up to five observed/same-owner search links. A 403 or 404 at the
original URL triggers one retry at `https://hostname` without path/query/fragment.
Subdomains/hosted tenants are retained; it never guesses a different domain or
switches to a hosting provider. Redirects and every request retain network guards.
Continued blocks are held for review; access restrictions are not bypassed.

Admission requires a readable conference website, explicit unambiguous upcoming
event evidence extracted from crawled pages, successful same-site search evidence
and a verified four-pass plan. Search snippets cannot supply event dates. Missing
optional fields remain unknown. Parked, empty, expired, ambiguous, blocked and
search-unavailable websites stay in review. External search discoveries retain
their organizer-review requirement.

The generated settings map actual inspected page URLs to date/stats/location/
details passes. Unique CSS selectors are proposed only from observed DOM nodes,
and are tested through the pass extractors before admission. Multi-event catalogs
retain structured-node identity matching; unscoped selectors apply only when one
edition is unambiguous. This does not require OpenAI; AI remains paused.

`/admin/source-validation` shows pending/reviewed sites, original and recovered
URLs, crawl/search evidence, errors and parser plans. Reviewers may edit a URL and
requeue; only the service worker can validate and atomically admit a Source.
The scheduled worker checks every ten minutes (GitHub pickup may be delayed),
processes up to five sites and skips expensive browser/search setup when empty.
`dry_run=true` saves a review outcome without adding Sources.

The explicitly approved reset cleared live Sources, discoveries, frontier/jobs,
parse/ingestion history and ingested events. Accounts, aggregator configuration
and organizer-owned events remain. Aggregators are paused until reviewed rollout.
A private, non-exposed recovery copy is in `crowdstage_reset_20261102`; it is not
used by the app. Fresh installations replay the reset migration against empty
tables; the reset is a one-time migration, not a recurring worker operation.
