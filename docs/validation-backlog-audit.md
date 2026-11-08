# Validation backlog audit — 2026-11-08

The review inventory contained 7,393 needs-review validations, distinct from the
370 existing Sources. All inventory URLs received a guarded public HTTP check:
4,170 loaded and 3,223 did not. Transport failure is not proof that a conference
is invalid. Saved snapshots and per-ID evidence remain outside the repository.

## Causes and corrections

* Sessionize speaker histories include ended editions. Read explicit date
  metadata before queueing links; retain unknown dates and report skipped counts.
* Empty H1 nodes crashed image/hero extraction. Guard missing titles.
* Some date heroes live in semantic headers or use localized month names. Keep
  those headers and recognize explicit localized dates without guessing ambiguous
  numeric dates.
* Four passes repeated identical DOM and metadata work. Cache only within the
  exact source/snapshot/configuration boundary and copy mutable results.
* Complete direct organizer evidence unnecessarily depended on search availability.
  Search remains useful for recovery; it is optional when direct evidence passes.
* Root recovery could replace a specific obsolete event with unrelated content.
  Preserve the submitted URL when recovery fails and limit root retries to access
  failures. The existing three-strategy/eight-read/three-search limits remain.
* Session/speaker pages and non-rendered client error shells can contain global
  Event JSON-LD. Hold them and verify an observed parent URL within the same bounds.
* Explicit conflicting visible event windows now cause review instead of accepting
  a stale structured date. Individual deadlines do not establish a contradiction.
* Avoid paid research for clearly ended editions. Public access restrictions,
  redirects, response-size limits, robots policies and destination checks remain.

The companion frontend/database change synchronizes terminal validation decisions
with related discovery rows, reuses existing outcomes during rediscovery and stops
counting rejected records as unvalidated. It does not mutate existing Sources or
publish events.

## Review rules

Acceptance requires organizer identity, an upcoming edition and verified four-pass
settings through the existing claim/finish proof gate. Reject only supported
historical editions or clearly non-organizer destinations. Inaccessible or
ambiguous sites remain reviewable with the evidence and error recorded. Never
empty the queue by declaring all access failures invalid.

Local tools in `scripts/` produce evidence and proposals without database writes.
They do not constitute automatic approval, browser-rendered proof or paid research.
