# AI mini enrichment

This replaces the proposed page-by-page AI crawl planner with bounded evidence selection inside the existing pipeline. It does not depend on or merge the old AI planner PRs.

## Pipeline

1. **Aggregator discovery:** existing Sessionize/Conference Monkey adapters and generic link rules run first. If both static and browser extraction fail, one small request can classify observed links as follow, next page, or organizer website. Only real link IDs are accepted. AI-selected external links remain in ownership review. Existing paths use zero AI calls.
2. **Website validation:** guarded Crawl4AI and SearXNG recovery remain limited to the existing three strategies. Search results guide navigation, never supply event facts. One shared AI budget spans all recovery strategies for a candidate.
3. **Event date:** deterministic parsing establishes an explicit event identity and edition. This pass spends zero AI tokens. Missing/ambiguous dates remain reviewable; AI cannot invent dates or establish a new event identity.
4. **Event stats:** only unresolved attendance format triggers AI by default. The model points at observed text; existing rules normalize it. No guessed online/in-person default.
5. **Event location:** unresolved region triggers a small location evidence pass. Exact country names/codes map to regions through the existing country table. A domain suffix alone remains a suggestion. AI never supplies a country or region string.
6. **Event details:** only missing event kind triggers AI by default. Summary, tags, prices and counts remain deterministic/unknown unless an advanced source explicitly sets `selectors.ai_optional_fields=true`. They do not cause extra default calls or invalidate a complete event.
7. **Assembly:** only one known edition can receive enrichment. Known fields are never overwritten. Existing conflict handling, ownership checks and publication review remain in force. Model output never directly sets validated/published status.

## Cost controls

- Default model remains `gpt-4.1-mini`; optional worker variable `OPENAI_CRAWLER_MODEL`.
- At most three requests per validation candidate/source/job and 12 per worker process, further constrained by a conservative 30,000 token-unit reservation budget. Bulk follow-up jobs are separate worker processes, so these are per-worker limits, not an account-wide daily spend cap.
- At most 6,500 serialized evidence input bytes, 60 candidate nodes and 450 output tokens per call. Only relevant visible nodes or link labels are sent, never full HTML, scripts, forms, cookies or API keys. Input also includes fixed instructions and a small schema.
- Fixed 20-second HTTP timeout, no automatic model retries. Authentication, quota, rate limit and malformed-response failures stop subsequent model calls in the worker. Deterministic ingestion continues.
- Store false; Structured Outputs return only field names and existing node IDs. Unknown fields, invented IDs and ambiguous multi-node selections are rejected.
- Cache by model, version, event identity, requested fields and evidence. Cached selections are rechecked against current HTML. Persist the bounded 32-entry cache in existing crawler config, validation reports and source discovery reports; no new database table or schema migration.
- Reports include request counts, actual input/output tokens, cache hits, selected fields and safe error codes. Incomplete responses also retain usage when supplied. Reserved units are conservative and separate from actual provider token counts.

## Activation and review

Worker workflows receive the repository secret `OPENAI_API_KEY`; it never goes to the frontend. Set the repository Actions variable `AI_ENRICHMENT_ENABLED=1` to activate. With the variable unset, deterministic behavior continues without paid API requests. No existing aggregator is unpaused by this change.

Review `config.ai_enrichment` for discovery, `report.ai_enrichment` for Candidates validation, and `discovery_report.ai_enrichment` plus per-pass reports for source parsing. Each selected value retains its page URL, snapshot digest and selector.

Run the tests before enabling. Measure validation yield, tokens per rescued event, false approvals and review reasons on a small sample before increasing daily usage. This implementation adds no daily account-wide budget or admin budget editor; those need a shared ledger rather than per-worker counters.

## Live smoke probe

The Backend checks workflow has an optional manual `run_ai_probe` input. It performs at most one paid request against public Conference Monkey HTML and has no database credentials. Ordinary pushes, PRs and scheduled runs never invoke this probe.

The initial probe on 2026-11-07 reached OpenAI but returned HTTP 429. No usable AI links were produced, and no retry followed. The initial code did not record a provider subcode, so billing quota versus temporary rate limiting is not yet established. The implementation now reports recognized safe quota/rate codes. API availability must be resolved and a manual probe must pass before enabling normal paid enrichment.

