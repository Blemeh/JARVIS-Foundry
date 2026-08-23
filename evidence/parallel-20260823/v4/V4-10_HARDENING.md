# V4-10 — Failure, restart, and honesty hardening

Status: PASS — commit `6280ae6`

## Behavior delivered

The briefing item, queue, planner, Command Center, packet, and traceability
read models now tolerate old or malformed payload shapes without promoting
them to authority. Invalid clocks produce explicit `UNKNOWN` freshness;
future events remain `FUTURE`; summaries strip NULs and are bounded; malformed
stored briefing payloads return an `UNKNOWN` payload instead of crashing a
retry. Snapshot/executive/timeline joins treat non-object payloads as empty
unknown evidence and remain read-only.

The durable cursor remains conservative under priority selection, and packet
and traceability projections rebuild from events/rows rather than mutable UI
state.

## Proof

`tests/foundry/test_v4_hardening.py` directly injects a malformed legacy event
payload, an invalid fake clock, a future hostile-text event, and corrupt
outbox JSON; replay, snapshot, queue, packet, and planner paths all complete
without false success. `test_v4_briefing_items.py`,
`test_v4_traceability.py`, the owner/control/mission/convergence regressions,
and the 108-test UI suite also passed.

