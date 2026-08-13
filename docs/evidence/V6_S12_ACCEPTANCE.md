# V6-S12 — ENGINEERING + OPERATIONAL ACCEPTANCE

```text
STATUS: PASS
PHASE: V6_CONTINUOUS_SELF_EVOLUTION
SLICE: S12 — engineering + operational acceptance
OBJECTIVE: engineering_acceptance checks the machinery itself
  (migration replay idempotency, schema pin == SCHEMA_VERSION,
  reconcile consistency, event integrity — fail closed);
  operational_acceptance counts the plan's operational minimums from
  the cycles/promotions ledger (>= 3 complete autonomous candidate
  cycles, >= 2 promotions WITH measured improvement, >= 1 R1 real
  promotion, >= 1 R2 canary + promotion, >= 1 broken candidate
  auto-rejected, >= 1 rollback exercised) and FAILS CLOSED with the
  unmet reasons; record_acceptance archives every gate (event +
  acceptance_runs row) so the acceptance history is replayable.
BASE COMMIT: c1a3463 (S11 close)
CANDIDATE RELEASE: none (gate machinery)
RISK CLASS: R3-class gate machinery (it sums the promotion ledger)
HYPOTHESIS: n/a (gate; exercised against the S11 ladder)
BASELINE METRIC: full suite 903 EXIT=0 at S11 close
SUCCESS THRESHOLD: engineering green on a fresh DB AND after a
  re-initialize (no-op) AND after an intermediate-target migration
  then full; operational FAILS CLOSED on an empty ledger AND with one
  promotion alone; operational PASSES with the full ledger (R1 + R2
  canary promotions, broken rejection, rollback); acceptance records
  are archived + replayable; candidate controllers refused; all five
  projection touch points wired; suite green
GUARDRAILS: isolated runtime DB only (var/v6-runtime/v6.db, now schema
  v32); live DB stays v20; no live writes; controller identity checks;
  fail-closed counts (a rolled-back promotion still counts as a
  promotion PERFORMED — the ledger keeps both; live_promotions is
  reported separately for transparency)
ACTIONS TAKEN: TDD red (9 tests) -> implemented evolution/acceptance.py
  (engineering_acceptance, operational_acceptance, record_acceptance,
  AcceptanceError) -> initialize(target=...) additive parameter (the
  migration runner's existing target support exposed through the
  public API for the from-any-base replay check) -> migration v32
  (acceptance_runs) + PROJECTION_TABLES + reconcile key map +
  database row/all/count allowlists -> focused green (192 total across
  S1-S12) -> runtime DB migrated to v32 (reconcile consistent) ->
  full suite
FILES CHANGED: src/jarvis_foundry/evolution/acceptance.py (new);
  tests/foundry/test_evolution_acceptance.py (new, 9 tests);
  src/jarvis_foundry/migrations.py (SCHEMA_VERSION 32 + migration 32);
  src/jarvis_foundry/projections.py (acceptance.completed handler,
  PROJECTION_TABLES); src/jarvis_foundry/reconcile.py (key map);
  src/jarvis_foundry/database.py (initialize(target=...), row/all/
  count allowlists)
LIVE SYSTEMS CHANGED: NONE (verified read-only at close time)
TESTS: 9 new S12 tests + S1-S11 regression (183) + FULL SUITE 912 passed
  EXIT=0 (410.19s; var/evidence/v6-s12/suite-v6-s12-full.txt,
  sha256 0d45114d9c7899511585c527a70b5a7ce43e91b39a69d3a37b68e7365b6a51f2)
VERIFIER: <fill after dispatch>
EVIDENCE: var/evidence/v6-s12/ (suite artifacts, verdict JSON);
  var/v6-runtime/v6.db (isolated runtime, schema v32, consistent)
CANARY: none (gate machinery)
PROMOTION OR ROLLBACK: the S12 ledger demo re-ran the REAL S11 ladder
  (R1 promoted, R2 canary-promoted, broken rejected, one rollback) on
  the isolated runtime
COST AND RESOURCE USE: local only (pytest + subprocess workloads)
UNRESOLVED RISKS: same-provider verifier limitation (D-031); the
  operational minimums are counted from the cycles/promotions ledger —
  the final S13 acceptance also re-verifies the evidence trail (suite
  artifacts + verdict JSONs) which the machinery itself cannot
  inspect (files outside its ledger)
NEXT AUTONOMOUS ACTION: S13 — closure + V7 handoff
OWNER INPUT REQUIRED: NONE
```

## Design

`src/jarvis_foundry/evolution/acceptance.py`:

- `engineering_acceptance(db)` — initialize() replay (must be a
  no-op), schema_migrations MAX(version) == SCHEMA_VERSION,
  reconcile(db).consistent, verify_event_integrity() without
  exceptions. Any check failing -> passed False (fail closed).
- `operational_acceptance(db, min_cycles=3, min_promotions=2)` —
  counts from the ledger: cycles_total, promotions (PROMOTED +
  ROLLED_BACK — a rollback presupposes a promotion), live_promotions
  (currently PROMOTED, reported separately), rollbacks, rejections,
  r1_promotions (cycle whose improvement froze R0/R1),
  r2_canary_promotions (promoted cycles with a canary),
  measured_improvements (promoted cycles with baseline + candidate
  runs). Unmet minimums -> passed False + reasons.
- `record_acceptance(db, *, report, controller)` — archives the
  report (acceptance.completed + acceptance_runs row).

Projection `acceptance_runs` (migration v32: run_id, passed,
report_json, controller) wired into PROJECTION_TABLES, the reconcile
key map, and database row/all/count allowlists. The handler validates
the mandatory keys, the boolean type of passed, and that report_json
parses.

`FoundryDatabase.initialize(target=...)` — additive optional
parameter exposing the migration runner's existing target support,
used by the from-any-base replay check (v10 -> full).

## Tests (9)

Engineering green on a fresh DB; re-initialize is a no-op; intermediate
target then full is consistent; empty-ledger operational FAILS CLOSED;
full-ledger operational PASSES (R1 + R2 canary promotions, broken
rejection, rollback — all REAL S11 cycles); one-promotion-only FAILS
CLOSED; acceptance archives (row + event); candidate controller
refused; projection/schema integrity (pinned to the imported
SCHEMA_VERSION constant >= 32).
