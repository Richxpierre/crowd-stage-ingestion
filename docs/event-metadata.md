# Automatic event metadata before publication

The four passes classify format (stats), country/macro region (location), and event kind (details) before ingestion. `schema.org addressRegion` is an administrative state/province; it is never copied directly into CrowdStage's macro region.

Rules prioritize event-scoped structured addresses/attendance modes, explicit attendance wording and event-location headings/prose. A physical venue is an in-person inference unless an explicit online/mixed mode or hybrid label is present. Titles distinguish conference/congress, summit, expo/exhibition and trade show/fair. Unsupported kinds retain the configured source kind; no new event categories are invented.

Country names/codes map to the product's North America, LATAM, Europe, APAC and MEA groups. Turkey is grouped in MEA; Mexico in LATAM. Selected unambiguous venue cities can supply country. Footer offices, catalogs, registrations, recordings and negated attendance labels do not establish event attendance. A recognized country-code domain supplies a low-confidence region suggestion only; generic .com/.org/.net and repurposed .co/.me/.io-style domains do not establish a venue. Missing facts stay unknown rather than receiving a fabricated global default.

If region or format remains unresolved, the refresh worker uses Crawl4AI and SearXNG with three navigations, two searches and a shared 90-second deadline per source. Search only finds same-organizer pages; snippets never become facts. Alternate event pages require title/date identity. Network/tenant guards remain active. Optional failure does not discard valid dates or publish an event.

Each event stores `metadata_report`: assigned fields, method, confidence, evidence URL/text, suggestions and warnings. Source reports contain crawl/search errors. Editorial format/region/type changes are preserved when they differ from the previous collected values. A full refresh uses the existing parse queue, keeping event publication review separate.

Admins can use **Auto-detect event details** in an editor or **Auto-detect details for selected** on the event list. These queue all four passes for existing active approved sources. Queued/running source requests are deduplicated. A source refresh may update other editions belonging to that source. View progress in Sources → Parse settings, then reload the event editor; unsaved edits are never silently refreshed.

For a read-only Python preview with the worker's SearXNG/Crawl4AI environment:

```sh
python -m scripts.probe_metadata --url https://www.aasle.org/beijing2026-conference-info --title 'AASLE 2026 CONFERENCE' --start-date 2026-12-10
```

To refresh an approved source through the existing ingestion path, use `python -m collector.sync --slug SOURCE_SLUG` with `SOURCE_PARSE_PASSES=1` and `METADATA_ENRICHMENT=1`. Scheduled refreshes set these automatically. The pure classifier and evidence contract are the extension point for future AI; AI remains paused and no OpenAI key is required.
