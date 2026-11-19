# Pipeline recovery and backlog controls

OpenAI is deliberately off in every workflow and removed from worker process credentials. Neither a Claude failure nor an admin resume enables OpenAI.

Install the frontend's `20261116165432_pipeline_recovery_controls.sql` migration before this worker version. It creates controls with recovery, Claude and automatic publication initially paused. Deploy the matching queue-dispatch function, then activate recovery and the desired workers from Ingestion runs → Pipeline controls.

## Recovery rules

- Every scheduled, dispatched and manually launched worker consults the same database controls before claiming work and between items. A pause stops new claims, not a website request already in progress.
- An indefinite admin pause is never automatically resumed. Timed pauses expire only when the recovery timer is enabled.
- The minute timer recovers expired leases, and retries transient worker failures at most three times per 24-hour window, with configurable cooldown. Authentication, permissions and quota errors remain held.
- Technical validation failures are requeued in bounded groups of at most 25, with three recovery attempts per candidate. Missing dates and ambiguous editions stay in review. An administrator can request another technical batch.
- Sources back off after failures and are held after three consecutive Source errors. Successful refreshes clear the counter. An explicit Source retry releases only that Source; the bulk queue does not release held Sources.
- Claude research and event jobs have UUID claims, leases, availability dates and three-attempt limits. Unknown paid Manus task outcomes are never automatically resubmitted. Manus reconciliation can run with creation of new tasks disabled; its existing provider credit buffer still applies.
- Current aggregator listing pages get a small priority window before the old detail backlog, avoiding starvation without discarding continuation history.

## Event safety

Only exact page quotes tied to the event and page URL can supply AI fields. Region and format values are normalized to database enums. Unknown prices stay unknown. A blocked page, timeout or inconclusive result never archives an event. Enrichment only fills missing fields, preserving concurrent admin edits.

Verified publication is separately controlled. It requires complete current dates, format, region, industry, an eligible event type, a recent matching observation from an active validated official Source, and no extraction conflicts. The existing event guard remains in place; only a transaction-local private permit from the service-only verified publisher can authorize the ingestion exception. Optional unknown price and attendance do not block publication.

## Rollout

Start with a small validation batch and Claude event batch, inspect saved results, and then enable bounded timer recovery. GitHub execution is still subject to runner availability; dispatch-check time is not a guaranteed start time. Controls and automatic recovery actions are recorded for admin inspection.
