# Manus backup adapter

The ingestion worker has a Manus v2 adapter and fallback-readiness reporting. This is **not active agent enrichment**: task creation remains blocked because the requested hard limit is 2,000 model tokens per day and Manus does not expose an enforceable task-token ceiling.

## What is connected

- Repository secret `MANUS_API_KEY`, available only to backend workflows.
- Optional `MANUS_BACKUP_ENABLED=1` enables readiness reporting in mini-enrichment reports; it cannot bypass the budget gate.
- Missing/disabled OpenAI and primary-provider failures attach a `backup` report containing the Manus configuration state and the reason it could not run.
- Read-only `user.me` authentication, task status/credit usage, and one-page structured-output retrieval for an existing task.
- Stop support for an explicitly supplied task ID. No task resumption, follow-up messages, automatic confirmation or polling loops.
- Private/lite task-payload preparation, at most 2,000 prompt bytes, connectors cleared, no source URLs in the prompt, and strict observed-node result validation. This payload is never submitted by the current implementation.
- A read-only Backend checks `run_manus_probe` input. It authenticates and publishes readiness metadata to the admin dashboard without creating a task or changing Sources/events.

## Budget behavior

`ManusClient.create()` rejects with `manus_hard_token_cap_unavailable` **before any network request**. Prompt bytes are not token usage, and neither one task per day nor post-task credit accounting can guarantee a ceiling on an agent's internal model tokens. `credits_used` and `tokens_used` remain separate; unknown token usage is reported as null.

There is no daily spending ledger in this change because there are no permitted generation calls to reserve or charge. A future credits-based policy would require explicit agreement on its unit and possible per-task overshoot, plus a transactional shared daily ledger across GitHub workers. A local file/process counter is not an adequate daily limit.

The API credential was previously verified by read-only authentication. It is not committed in code, test fixtures, artifacts, task prompts or reports. OpenAI stays paused; the Manus feature does not unpause any aggregators, create Sources, modify events or publish listings.

## Official references

- [Authentication](https://open.manus.ai/docs/v2/authentication)
- [Create task: asynchronous agent, lite profile and connector controls](https://open.manus.ai/docs/v2/task.create)
- [Structured output: post-processing after the agent runs](https://open.manus.ai/docs/v2/structured-output)
- [Credit consumption and possible negative balances](https://help.manus.im/en/articles/11711097-what-are-the-rules-for-credits-consumption-and-how-can-i-obtain-them)

The admin dashboard is `/admin/enrichment`. Regular worker heartbeats update OpenAI configuration without model calls. The Manus card retains the last explicit read-only authentication check; configuration-only heartbeats do not erase its result. Run Backend checks with `run_manus_probe` to refresh it.

