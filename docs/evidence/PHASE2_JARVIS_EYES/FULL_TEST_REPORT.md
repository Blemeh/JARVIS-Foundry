# FULL TEST REPORT — JARVIS EYES PHASE 2

## Focused perception tests

`tests/foundry/test_perception.py`: 23 tests PASS (contracts, registration,
read-only semantics, privacy/redaction, normalization, aggregation,
significance, cursor/restart, STOP, disable/resume, degraded mode,
no-claim/no-candidate/no-mission/no-directive boundaries).

## Affected existing suites

Connectors, connector activation, capability broker/states, stop controls,
event store, live ingestion, knowledge ingestion, V7 capabilities: all
PASS on the candidate (focused run, 145 tests green).

## Full canonical suite

`pytest tests/foundry -q` on candidate: EXIT 0, all collected tests PASS
(raw: `var/phase2-eyes/full-suite-run.txt`).

## Test-hardening repair (pre-existing flake)

`test_v7_scheduler.py::test_tick_is_idempotent_no_duplicate_fires` failed
1/5 in isolation under wall-clock load (1s cooldown vs whole-second epoch
granularity). Reproduced, root-caused (time-sensitivity, not perception),
repaired the test with a 3600s cooldown preserving the exact invariant,
5/5 green after repair. No production behavior change.
