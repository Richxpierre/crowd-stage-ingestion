# Automatic aggregator discovery setup

Creating an aggregator queues a setup-only job in the existing crawler queue. The
GitHub discovery worker runs `collector.aggregator_setup` before adapter routing.
This does not write Sources, candidates, or events. Existing aggregators are not
reconfigured; admins can explicitly rebuild their settings.

The worker samples up to three public pages within 150 seconds, following only
links on the aggregator's own domain. Crawl4AI's existing destination, robots,
redirect, request-size, and browser controls apply. Explicit link patterns run
first. Ambiguous navigation uses a small observed-node selection request through
the configured mini provider. OpenAI enablement is unchanged. Manus uses its
existing shared credit gate, private lite tasks, and bounded evidence prompts.
There are no generated URLs, executable code, or model-generated CSS selectors.

If Manus is pending, the crawl job releases the runner. Its completion/failure
wakes a continuation using the same generation and saved evidence. Selected URLs
must still occur on the re-read page. At most three setup jobs run per generation;
there is no polling loop creating paid tasks or recursive fallback chain.

Successful settings contain `discovery_mode: website_links` and `discovery_steps`:
each step has a path glob and CSS selectors for follow, pagination, or website
links. Website-label checks prevent a shared button class from collecting ticket
and sponsor links. At least one organizer URL must be observed before readiness.
Normal discovery uses the explicit mapping and does not silently switch to the
generic AI fallback when its selectors stop matching.

The service-only finish function compares the URL, job identity, cancellation,
and current settings before saving. Manual values win; conflicts require review.
Rebuild removes only previous generated values that admins have not edited.
New automatic schedules wait for readiness. Setup failures, blocked pages, or
inconclusive samples stay visible in settings with a rebuild action. Stop/Delete
prevent late callbacks from applying settings; ordinary Stop preserves an already
verified configuration. Existing publication and source validation gates remain.

Rollout order: worker code first, then database migration, then admin frontend.
The migration installs the first-save trigger without backfilling existing targets.
Test with `python -m unittest tests.test_aggregator_setup` and the full suite.
