# Mini enrichment on aggregator access blocks

Setup, ordinary discovery, and bulk adapters detect HTTP 403 / anti-bot failures
and recognizable HTTP-200 challenge pages. A blocked run searches SearXNG once
for public alternate pages on the same aggregator domain. Mini enrichment chooses
only observed result IDs; it cannot invent URLs, selectors, facts, or code.
Crawl4AI tests up to three selected pages using its existing destination, request,
robots, redirect and browser controls. Only the usual discovery/parser pipeline
can turn an accessible page into organizer candidates. Search snippets are never
event evidence, and publication/validation gates are unchanged.

OpenAI enablement is unchanged. When primary enrichment is paused, Manus uses
the existing shared credit buffer, private lite tasks and bounded evidence prompt.
Results are cached by aggregator URL and observed search evidence across runs.
A pending task releases the runner. Completion queues at most one follow-up crawl;
late callbacks cannot undo Stop/Delete, replace a newer crawl, or change the seed.
First-save setup resumes with its normal URL/job checks. Unresolved access blocks
remain visible in the crawler's Error reports → Mini access recovery section.

There is no CAPTCHA solving, stealth, identity/proxy rotation, authentication
bypass, or guarantee that a protected aggregator becomes accessible. If every
alternate route is blocked, the report asks for review or an approved API/feed.
429 responses enter cooldown without immediate requests or paid enrichment;
404, robots denials, certificate and unsafe-destination errors do not trigger this
AI path. Advanced setting `access_recovery_enabled: false` opts an aggregator out.

The worker starts its private SearXNG service for access recovery even when broad
search discovery is unchecked. This does not enable cross-site discovery. Deploy
the worker first, then the service-only resume migration, then the report UI.
