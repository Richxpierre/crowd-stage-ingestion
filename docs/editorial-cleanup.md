# Editorial cleanup

The admin Review & cleanup page snapshots held validations, pending events and failing Sources. Each item preserves its initial record, current observed version, page evidence, proposal and final admin decision. Records changed during review are held as conflicts rather than overwritten.

The GitHub cleanup worker is dispatched by the existing minute queue watchdog when an unpaused batch has work. It does not run an idle polling schedule. Each run claims at most eight items, each claim has a three-minute lease, and a batch reserves at most its configured review-slot limit. Failed calls consume a reservation; reported token usage is retained. OpenAI stays off. Claude uses the configured Haiku model unless an administrator changes the existing model setting.

Public evidence collection tries the submitted HTTPS page, its same-host root when appropriate, then guarded Crawl4AI rendering. It never logs in or bypasses access challenges. Unsafe network destinations stop immediately. At most three collection strategies run per item. Repeated worker claims stop after three attempts.

Claude proposes an editorial action and short verbatim evidence. Supported date/location/format/industry facts can become declarative four-pass edition recipes. Quotes must occur in the supplied page; URLs must be observed; dates must have an explicit year. The existing recipe engine rechecks the exact supporting text during Source validation and later parsing. A changed page returns to review. Unknown facts stay unknown.

Admins inspect and apply proposals individually or select up to 25 visible proposals. Corrections return to normal Source validation, not direct admission. Cleanup can archive only pending events; it never changes published events through the archival action. Sources can be retried or paused explicitly. Event editing and publication remain available in the existing event editor.

Unresolved import attempts default to retention. An admin can explicitly choose to propose rejection for that batch. This declines the current admission attempt, not the website's validity. Evidence and reasons remain available and the URL can be reopened through Candidates. The manual November 16 cleanup used this policy at the user's request.

The dashboard distinguishes slot limits, manual pauses, conflicts and worker failures. Intake pauses used for a manual backlog review should be restored after the review; timers must not override indefinite admin pauses.
