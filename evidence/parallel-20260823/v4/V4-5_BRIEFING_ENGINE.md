# V4-5 — Deduplicated twice-daily and exception briefing engine

Status: PASS — commit `c8b10ce`

## Behavior delivered

`BriefingPlanner` now selects from event-derived items using the durable local
`briefing_outbox` history. It records per-kind cursor start/end, ledger head,
selected and deduped counts, late/future counts, and zero-change state.

The cursor is conservative: when priority ordering selects a newer item first,
the checkpoint stops at the lowest selected source sequence so an older item
cannot be skipped. Prior dedupe keys are replay-safe across process restarts.
TWICE_DAILY windows suppress only when the content digest is unchanged; the
EXCEPTION path suppresses only when there is no new exception item. Existing
briefing-key idempotency remains intact.

No scheduler, Task Scheduler job, provider, connector, or live database was
changed. Tests use a local database and explicit fake-clock timestamps.

## Proof

`tests/foundry/test_v4_briefing_items.py` covers restart-safe same-key retry,
cross-window zero-change suppression, new-event reopening, exception
informational suppression, and late/future selection metadata. `test_v7_briefing.py`
and the Command Center suite preserve the prior outbox contract.

