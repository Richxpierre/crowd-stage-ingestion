# Search-assisted discovery

Enable **Help this crawler with web search** in an aggregator's settings. The existing
queue and schedule run SearXNG searches, then inspect results with Crawl4AI. Optional
queries (up to three) override searches based on the aggregator hostname, name and current
year. Leave the query field empty for automatic searches.

The GitHub worker starts a private SearXNG container only for search-enabled jobs. Its
JSON API is bound to runner loopback, uses a generated secret, and is removed after the
job. The official image is pinned to a manifest digest in `scripts/start_search_service.py`.
No public instance, API key, extra hosted server, or browser search automation is required.
See [SearXNG's Search API](https://docs.searxng.org/dev/search_api.html) and
[container installation](https://docs.searxng.org/admin/installation-docker.html).

Search hits on the selected aggregator become navigation targets. Explicit website
links found there still use the existing Sources registration path. External search hits
are visited with the guarded reader; pages with event signals become **source review**
items, preserving query, engines, result URL and visited URL. A heading or Event JSON-LD
is a relevance signal, not proof of official ownership. Admin approval creates the source;
the normal website parser then extracts current editions, dates, descriptions and images.
Event publication remains a separate review step. Search snippets never supply event facts.

Search is opt-in and limited to three requests, 20 unique result URLs, 45 seconds and
the existing overall crawl page/time limits. Search-engine failures appear in crawl
reports without stopping ordinary website discovery. HTTP 429 is reported for retry on
the next run. No public-instance rotation or CAPTCHA bypass is attempted.

For a separately operated worker, set `SEARXNG_URL` to `http://127.0.0.1:8888` or a
trusted public HTTPS origin with JSON output enabled. An optional `SEARXNG_TOKEN`
is sent only to that fixed origin; redirects are rejected. Neither endpoint nor token is
read from admin-entered JSON. Keep credentials on the worker, never in frontend code.

Validation: `python -m unittest discover -s tests -p test_search.py -v` covers transport,
filters, cancellation, fallback, navigation and review behavior. The `search-service` CI
job also boots the pinned container and checks the actual JSON API. Upstream engines may
return no results or warnings from hosted-runner IPs; those outcomes remain visible and
are never replaced with fabricated discoveries.

