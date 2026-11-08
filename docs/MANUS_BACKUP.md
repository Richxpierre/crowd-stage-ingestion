# Manus mini enrichment

Manus is the website-research alternative while OpenAI is paused. Deterministic extraction runs first. Only missing fields are queued. The agent browses up to three organizer pages for missing facts. Exact citations are independently fetched with Crawl4AI and rechecked against the current snapshot, and re-enter normal validation/publication review. No generated facts are directly published.

## Credit admission trigger

The account currently reports 300 daily refresh credits, rather than the earlier assumed 2,000 model tokens. The worker reads usage.availableCredits and bounded usage.list pages before claiming any paid task. It uses only daily refresh credits, never add-on/subscription balances. At most 80% of the smaller of the live daily allowance and 2,000 credits is admitted; at least 50 credits are reserved. A new task reserves at least 50 credits, rising to 1.5 times the largest recent observed task cost. This is a soft admission policy, not a promise that an already-running Manus agent cannot overshoot.

A Supabase advisory transaction lock and one global active task prevent simultaneous GitHub workers from overspending the same snapshot. Account-wide consumption, available daily balance, completed task costs and unsettled reservations all constrain admission. Low/unknown balance pauses new submissions. The exact provider reset time defines the period; the next fresh usage snapshot allows queued work to continue automatically. Unknown submission outcomes are held without resubmission to avoid duplicate charges.

## Async lifecycle

Collectors combine missing date, location, format and type fields into one durable research task per organizer/edition/month. They never wait for an agent or call task.create. Process Manus mini enrichment runs every five minutes, submits at most one admitted task per invocation, and reconciles its status/results. It does not resume agent questions or confirmations. Waiting/overlong tasks receive one stop request. The lite/private task may browse/search public pages on the organizer's own domain; it must return exact event headings, quotes, values and URLs. Crawl4AI verifies each citation independently with the guarded fetcher. Footer/catalog/cross-tenant evidence is rejected. Current deterministic fields win, and existing known edition dates cannot be replaced. Completed research is reverified when consumed by validation or each of the four parsers; it never publishes directly. An empty result does not repeatedly requeue consumers. Old node-selection jobs remain readable for compatibility.

When a result is ready, matching Sources/validation rows become due for their normal guarded checks. Changed page evidence has a different fingerprint and does not reuse stale selections. Unknown usage is never described as zero model tokens. A failed task with unreported usage keeps its reservation for that period.

## Dashboard and controls

/admin/enrichment shows the live allowance, available credits, period consumption, 80% trigger, reset time, queue/task states and actual reported credits. Admins can pause/resume new submissions; this does not resume or create agent follow-ups. OpenAI's existing opt-in remains off. MANUS_BACKUP_ENABLED=0 explicitly disables Manus; otherwise the saved key and database gate enable the alternative.

The signed webhook receiver records notifications and matches known task IDs. Polling remains available when a webhook is not registered. Neither callbacks nor the Manus worker apply generated event facts directly.

## Credentials and verification

MANUS_API_KEY remains a repository secret. API keys, raw account profiles and reasoning transcripts are never persisted in task reports or dashboard records. A branch-specific live probe uses a real public organizer snapshot and at most one paid task; repeat pushes reuse its queue fingerprint. Ordinary unit tests do not use paid APIs.

Official API references: https://open.manus.ai/docs/v2/task.create, https://open.manus.ai/docs/v2/task-lifecycle, https://open.manus.ai/docs/v2/usage.list.
