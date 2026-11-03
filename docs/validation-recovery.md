# Bounded website validation recovery

Each candidate gets at most **three strategy evaluations total**, including the original URL. All strategies share at most eight page navigations, three uncached SearXNG searches, a maximum two fetches of one URL, and a 150-second strategy budget inside the existing 180-second candidate deadline. A strategy evaluation has a maximum 45 seconds. Successful snapshots and search results are reused. The loop never calls itself or resets its budgets.

## Failure strategy map

| Failure | Next applicable strategy | Final applicable fallback | Review outcome |
| --- | --- | --- | --- |
| Deep URL returns 404/410 or soft 404 | Organizer host root | Observed or same-site search event link | missing_page / missing_event_evidence |
| DNS or transient server failure | Root or identity-matching indexed URL | Verified same-owner host alias or one transport retry | dns_failure / server_error / timeout |
| Access challenge / 403 | Public root or observed alternate event page | Same-owner search result, if available | access_blocked; no CAPTCHA bypass |
| Empty rendered page | Root / relevant navigation | One rendered retry with a small bounded wait | empty_page |
| Robots redirects to a public policy host | Follow up to five policy redirects with HTTPS/public-DNS and size checks | Apply returned rules to original authority | policy_unavailable if no valid policy |
| Robots endpoint redirects to an HTML event page | Keep the policy unresolved | Treat an observed same-owner target as a new URL with its own policy checks | policy_unavailable if blocked |
| Clear event prose but no JSON-LD / hero dates | Scoped event-date sentence extraction | Root or focused event page | missing_event_evidence if still ambiguous |
| Search outage / site not indexed | Directly crawled event proof can qualify | Crawl observed same-site links when proof is missing | Search snippets never supply event facts |
| Optional image/summary/price disagreement | Leave conflicting optional values unknown and report them | Prefer the event page over unrelated navigation | Does not invalidate an otherwise proven event |
| Genuine date/location/edition conflict | Focused same-owner evidence | A recovery cannot silently switch to different dates | conflicting_evidence / identity_conflict |
| Past edition | Search/crawl current official edition within scope | Root or observed event link | past_edition if no upcoming evidence |
| Unsafe/private destination, explicit policy denial, parked domain, 429 | Stop unsafe or unproductive work | No recursive retries | Specific review reason; policy denial is respected |

Root fallback preserves the organizer hostname and hosted tenant. Parent institutional URLs require observed redirect evidence or matching event identity. `sites.google.com/view/<tenant>` and hosted conference subdomains cannot collapse into their provider. The system does not guess alternate .com/.org/.net domains. A new unrelated domain is not admitted based on a search snippet.

## Research evidence

The previous queue had 37 held websites: extraction/optional-field conflicts, missing dates, unindexed websites, and access/DNS failures were mixed together. A bounded guarded HTTP probe now extracts explicit dates from these official pages without database writes:

- [LIS conference](https://www.lisdatacenter.org/news-and-events/events/conferences/2027-3rd-iii-lis-comparative-economic-inequality-conference/): 11–12 February 2027, in an article paragraph rather than the hero heading.
- [APEF](https://apef.ear.com.sg/): 10–11 December 2026, in conference prose below an H3 heading.
- [IOS](https://leibniz-ios.de/wissen-vermitteln/aktuelles/newsdetail/ios-jahrestagung-2027-recentering-the-margins-of-the-world-economy): 20–21 May 2027, separated from the submission deadline in the same paragraph.
- [MIRDEC](https://www.mirdec.com/barca2026home): 24–25 November 2026; unrelated page metadata should not invalidate the event page.
- [ForenSoc](https://www.forensoc.org/): 5–7 February 2027; directly crawled evidence survives absent search indexing.
- [AASLE](https://www.aasle.org/beijing2026-conference-info): 10–12 December 2026.

The fufifest.org hostname still failed DNS in research; search evidence of a different domain is not enough to manufacture a valid organizer URL. AFML's robots URL redirects to a Corvinus event page. International Pension Conference's robots redirects across hosts to an HTML response, which remains an unresolved policy rather than an assertion that the conference is invalid. [RFC 9309 section 2.3.1.2](https://www.rfc-editor.org/rfc/rfc9309.html#section-2.3.1.2) supports bounded cross-authority robots-policy redirects; HTML responses are never treated as allow-all policies.

Every report stores strategy names, URLs, outcomes, timing, final failure code, warnings, and shared budget usage. Existing JSON report storage and execution-attempt history preserve this evidence. External discoveries requiring organizer approval still require approval. New event publication still requires review.

Generated CSS mappings must reproduce independently extracted facts before they are saved. This prevents an otherwise unique submission-deadline node from overriding an explicitly dated conference heading. The live browser probe checks exact researched event dates, and accepts an access-unresolved result only for a specific network/policy category; missing event evidence on a readable known event remains a test failure.

