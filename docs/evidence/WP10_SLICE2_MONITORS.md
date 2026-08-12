# WP10 — V3 DISCOVERY AND BUILDING, Slice-2: Monitored Sources + Mission-Execution Wiring

Status: **IMPLEMENTED** (2026-08-12; 8/8 tests green; independent
verifier <PENDING>). Slice-1 CLOSED — PASS (deleg_d70f796a; ledger
5100–5102).

## Delivered (bounded slice-2 scope)

1. **Schema v11** (migrations.py, migration 11): `monitors`
   (monitor_id PK, mission_id, source_kind, source_ref, poll_interval_s,
   enabled CHECK, last_poll_at) + `missions.created_from` (ALTER TABLE
   ADD COLUMN — provenance for the outcome wiring).
2. **Events** (projections.py handlers): discovery.monitor_registered,
   discovery.monitor_polled (last_poll_at + counts),
   discovery.monitor_disabled; a new `mission.completed` handler wires
   candidate outcomes: a mission whose created_from is candidate:<id>
   auto-records the outcome (prediction = objective; reality = the
   mission outcome) and marks the candidate EXECUTED.
3. **`discovery_monitors.py`**: `register_monitor` (returns the id),
   `poll_monitor` (feeds items into the candidate pipeline — repeat
   polls dedup via the canonical-objective detection; duplicates are
   recorded as pointer rows per the ledger discipline; polls on
   disabled/missing monitors raise), `disable_monitor`.
4. Registries updated: PROJECTION_TABLES, reconcile key-column map,
   row/all/count allowlists (monitors).

## Evidence

- Tests: `tests/foundry/test_discovery_monitors.py` — 8 tests (red
  before implementation, green after): register row; poll feeds
  candidates + repeat-poll dedup (found=0, pointer rows recorded);
  polled event recorded; disabled monitors refuse polls; mission
  completion wires the candidate outcome (EXECUTED + prediction/reality
  in evidence); completion WITHOUT candidate provenance touches nothing;
  rebuild determinism; schema version == code.
- Slice-1 regression: 9/9 candidate tests still green (17/17 combined).
- Full suite: <PENDING> (expect EXIT=0, 310 tests) →
  var/evidence/wp10-slice2/suite-slice2.txt.

## Live-DB impact

Schema v11 (monitors + missions.created_from) applies on the next
initialize(); zero discovery/monitor events on the live ledger (all
tests use throwaway temp DBs).

## Next

- Independent verifier for slice-2 (fresh adversarial session).
- Slice-3 per the V3 design: mission execution via the established
  teams machinery + verified push/deploy chain wiring; then the V3 gate
  evidence and V4 (command center) activation.
