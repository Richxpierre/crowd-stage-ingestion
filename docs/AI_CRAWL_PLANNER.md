# AI crawl mapping

In an existing aggregator's settings, choose **Map with AI**. The admin-only crawl
queue receives a planning job; the regular five-minute GitHub worker processes it.
The job inspects up to four pages with Crawl4AI and asks OpenAI for a strict JSON
step containing follow, website and pagination selectors. It tests those selectors
against the inspected HTML, follows a real matching link and repeats until it finds
external website links or exhausts the sample budget. If the initial URL fails,
SearXNG can locate another indexed page on the same aggregator.

The report is stored in the job's `config.ai_plan_report`, not automatically applied
to the target. The admin sees explanations, inspected pages, match counts and sample
conference URLs. **Use tested map** selects a validated report; **Save aggregator**
persists it as `settings.crawl_plan`. Existing settings are preserved. No database
migration is required; queue insertion uses the existing admin RLS policy.

Subsequent discovery uses that sequence, with separate page/stage queue entries and
same-stage pagination. All network traffic retains the guarded reader's public DNS,
redirect, robots, size, timing and cancellation controls. Mapping jobs add no Sources
and publish no events. Links collected by a saved AI map require source ownership
review; normal event parsing and publication remain separate.

Set repository Actions secret `OPENAI_API_KEY` in **crowd-stage-ingestion**. The key
is provided only to the worker step, never to browser code, target JSON or logs.
`OPENAI_CRAWLER_MODEL` is an optional repository variable; default `gpt-4.1-mini`.
Each inspected page makes one bounded Responses API call using Structured Outputs,
`store: false` and at most 1,800 output tokens. A missing key, refusal, incomplete
response or unsupported selector fails visibly without replacing saved settings.
Input contains public link structure and headings; scripts, forms, input values and
non-link biography text are removed. Website content is explicitly untrusted.

The live probe workflow runs only on the `feat/ai-crawl-planner` branch. It verifies
the saved API secret and maps public Conference Monkey pages without accessing the
database. Its report artifact contains public sampled pages and links, never the key.

Limits: sample validation does not prove that every directory page shares the same
layout or that a linked website is an official organizer. Layout changes require a
new map. No generated code, browser scripts, shell instructions or invented URLs
are executed.
