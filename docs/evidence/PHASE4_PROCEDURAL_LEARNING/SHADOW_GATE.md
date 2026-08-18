# Phase 4 Shadow Gate

The Phase-4 `shadow_procedure` bridge was exercised through the existing V6
`shadow_run` machinery before any local browser replay. The focused test
`test_shadow_procedure_stays_zero_effect_before_local_replay` creates a frozen
procedure bundle reference, a valid existing improvement/release pair, and a
copied harmless input case.

Observed result:

- shadow run status `COMPLETED`;
- `zero_live_effects=1`, `live_attempts=0`;
- external live marker byte-identical before/after;
- no `system.action_started` event;
- proposal recorded by the existing `ShadowSink`, with no capability opening.

This proves the Phase-4 bridge cannot turn shadow mode into a live capability.
The bounded local browser replay remains a separate, later ActionEnvelope path.
