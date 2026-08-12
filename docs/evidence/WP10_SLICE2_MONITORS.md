# WP10 — V3 DISCOVERY AND BUILDING, Slice-2: Monitored Sources + Mission-Execution Wiring

Status: **CLOSED — PASS** (2026-08-12; round-2 verifier
deleg_verifier_slice2_adv_r2; ledger verification.passed:verdict-wp10-slice2
seq 5830 + evidence.created:wp10-slice2 seq 5831). 10/10 slice tests
green; full suite 312 tests EXIT=0 (var/evidence/wp10-slice2/suite-slice2.txt,
sha256 5cf4bee5…). Slice-1 CLOSED — PASS (deleg_d70f796a; ledger
5100–5102). Per DIR-2026-08-12-master-plan-alignment: **ENGINEERING PASS
only**; V3 operational gate NOT_TESTED (canonical:
docs/evidence/ALIGNMENT_2026-08-12_RECLASSIFICATION.md). Work package
archived: docs/planning/WP10_SLICE2_WORK_PACKAGE.closed.yaml.

## Round-1 FAIL lineage (verifier deleg_verifier_slice2_adv, 2026-08-12)

Verdict: **FAIL** (ledger `verification.failed:verdict-wp10-slice2:round1`,
seq 5639; verdict JSON archived at
var/evidence/wp10-slice2/verdict-round1-deleg_verifier_slice2_adv.json,
sha256 52297273…). C1/C2/C3/C5/C6/C7(a,b,c,e) PASS; **C4 FAIL** and
**C7d FAIL**:

1. **C4** — `mission.completed` handler was unguarded against pre-v11
   schemas: `SELECT created_from FROM missions` is unconditional
   (projections.py:76), so appending mission.completed to a v10 DB
   raised `sqlite3.OperationalError: no such column: created_from`
   (atomic rollback). Commit ac65025 fixed ONLY `mission.created`;
   the slice-2 `mission.completed` wiring (b9d279a) lacked the same
   PRAGMA guard. No production emitter exists today, but the live DB
   is v10 — V3 loops would hit it.
   **Repair**: PRAGMA table_info guard in the mission.completed handler —
   pre-v11 schemas make the outcome wiring a no-op (same adaptation as
   mission.created) — plus a mandatory-key validation for mission_id.
2. **C7d** — `mission.created` with a missing mandatory payload key
   (`acceptance_criteria`) raised a raw `KeyError` from deep inside the
   handler (projections.py:53), not a clean validation error.
   Pre-existing v1-era gap (no per-type payload validation in
   Event.validate), not introduced by slice-2; the criterion demanded a
   clean error.
   **Repair**: explicit ValueError validation at the top of the
   mission.created handler (atomic rollback preserved).

Regression tests added (tests/foundry/test_discovery_monitors.py, +2):
`test_mission_completed_pre_v11_schema_is_noop` (true v10 DB via
migrate(target=10) + commit — bare sqlite3 connections roll back on
close without an explicit commit) and
`test_mission_created_missing_acceptance_criteria_clean_error`
(ValueError + zero rows/events after rollback). Full suite 312 EXIT=0
after the repair. Broader payload-schema validation for ALL event types
remains a recorded residual (out of slice scope).

## Round-2 verification (deleg_verifier_slice2_adv_r2) — PASS

All criteria PASS. R1/R2 repairs A/B-discriminated: the OLD code
(HEAD~1 projections.py loaded through a shim into the real append path)
reproduces both defects (OperationalError on v10 mission.completed; raw
KeyError for missing acceptance_criteria) while the NEW code no-ops
safely / raises clean ValueErrors with atomic rollback, and the v11
outcome wiring still works. Full contract re-probed (C1–C7): schema
v11 fresh + v10→v11 upgrade, monitor lifecycle incl. canonical dedup +
pointer rows + deliberate ValueErrors, rebuild/idempotency/EventConflict
atomicity, registries + reconcile, artifact integrity (sha256 exact,
EXIT=0, 312 dots; the 310→312 collect-only discriminator proves the
suite ran the repaired tree), adversarial probes a–e. Verdict JSON:
var/evidence/wp10-slice2/verdict-round2-deleg_verifier_slice2_adv.json
(sha256 8db8209e…). Note: artifact mtime ~40 s before the repair commit
(test-then-commit sequencing; validity established via the count
discriminator + clean tree).

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
- Slice-1 regression: 9/9 candidate tests still green (17/17 combined;
  10/10 with the round-1 regressions).
- Full suite: **312 tests EXIT=0** →
  var/evidence/wp10-slice2/suite-slice2.txt (sha256 5cf4bee5…).

## Live-DB impact

Schema v11 (monitors + missions.created_from) applies on the next
initialize(); zero discovery/monitor events on the live ledger (all
tests use throwaway temp DBs).

## Next

- Independent verifier for slice-2 (fresh adversarial session).
- Slice-3 per the V3 design: mission execution via the established
  teams machinery + verified push/deploy chain wiring; then the V3 gate
  evidence and V4 (command center) activation.
