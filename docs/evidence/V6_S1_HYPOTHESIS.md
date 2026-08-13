# V6-S1 — HYPOTHESIS, METRIC, AND RISK-CLASS CONTRACT

```text
STATUS: PASS
PHASE: V6_CONTINUOUS_SELF_EVOLUTION
SLICE: S1 — hypothesis, metric, and risk-class contract
OBJECTIVE: durable, fail-closed improvement records: measured problem +
  evidence/provenance + frozen hypothesis + baseline metric + minimum
  meaningful improvement + correctness/safety/cost/latency guardrails +
  evaluation workload + observation requirement + deterministic risk
  class (declared vs surface-derived, unknown fails closed to R3) +
  budget/capability scope + reversibility/rollback threshold. Records
  freeze BEFORE implementation; missing or mutable metrics make a
  candidate promotion-ineligible; a builder may propose but can never
  lower a risk class.
BASE COMMIT: de0f0ee (S0 close) → 6aa5d52 (S0 entry) → fa4e65f (base)
CANDIDATE RELEASE: none (S1 is machinery, not a candidate)
RISK CLASS: R3-class machinery (release/evolution control plane) built
  and verified entirely inside the isolated V6 construction; no live
  surface touched
HYPOTHESIS: n/a (machinery slice; the contract machinery itself is
  exercised by the S11 ladder)
BASELINE METRIC: full suite 719 EXIT=0 at S0 baseline
SUCCESS THRESHOLD: complete records freeze + eligibility; incomplete or
  secret-bearing records refused; deterministic risk classification
  (unknown → R3); monotonic risk raise only; all five projection touch
  points wired; suite green
GUARDRAILS: isolated runtime DB only (var/v6-runtime/v6.db, schema v21);
  live DB stays v20 (verified read-only: no improvement_records table,
  zero improvement/release events); no live writes; append-only records
ACTIONS TAKEN: TDD red (18 tests) → implemented evolution/records.py
  (record_improvement, classify_risk, improvement_eligibility,
  raise_improvement_risk, ImprovementError) → migration v21 +
  PROJECTION_TABLES + reconcile key map + database row/all/count
  allowlists → focused green → created the isolated V6 runtime DB
  (initialize → v21, reconcile consistent) → full suite
FILES CHANGED: src/jarvis_foundry/evolution/__init__.py (new);
  src/jarvis_foundry/evolution/records.py (new); tests/foundry/
  test_evolution_records.py (new, 18 tests); src/jarvis_foundry/
  migrations.py (SCHEMA_VERSION 21 + migration 21); src/jarvis_foundry/
  projections.py (improvement.recorded + improvement.risk_raised
  handlers, PROJECTION_TABLES); src/jarvis_foundry/reconcile.py (key
  map); src/jarvis_foundry/database.py (row/all/count allowlists)
LIVE SYSTEMS CHANGED: NONE (verified read-only at close time)
TESTS: 18 new S1 tests + focused neighbor suites (migrations, event
  store, knowledge claims) + FULL SUITE 737 passed EXIT=0 (327.91s;
  var/evidence/v6-s1/suite-v6-s1-full.txt,
  sha256 139f26833c9690b521460664f6ab9de0f0cbdbb61b9d547850004bf4c44dafef).
  One pre-existing literal-pin defect found by the v21 migration and
  repaired: test_effects.py::test_v20_schema_accepts_frozen pinned
  `version == 20` (assert now `version >= 20` — v20's FROZEN-rebuild
  effect must persist at any later schema; the literal-pin class from
  the V2 gate).
VERIFIER: <fill after dispatch>
EVIDENCE: var/evidence/v6-s1/ (suite artifacts, verdict JSON);
  var/v6-runtime/v6.db (isolated runtime, schema v21, consistent)
CANARY: none (machinery slice)
PROMOTION OR ROLLBACK: none
COST AND RESOURCE USE: local only (pytest runs; one full suite ~5 min)
UNRESOLVED RISKS: same-provider verifier limitation (D-031); the S1
  classifier token table will need extension as V6 slices add surfaces
  (documented extension point, covered by unknown→R3 fail-closed)
NEXT AUTONOMOUS ACTION: S2 — immutable release registry and slots
OWNER INPUT REQUIRED: NONE
```

## Design

`src/jarvis_foundry/evolution/records.py`:

- `classify_risk(surfaces)` — deterministic surface→risk classifier.
  R3 tokens: supervisor/, control/, owner_controls, estop, events.py,
  database.py, migrations.py, schema, credential, redaction,
  evolution/, release, rollback, ledger. R2: mission/, projections.py,
  reconcile.py, knowledge, retrieval, scheduler, workflow, tool,
  discovery, evaluation, shadow, canary, execution/, roles/,
  connectors/, obsidian, correlation, worker_context, systems.py,
  v3_loop, command_center, live_ingestion, vault_indexer,
  vault_retrieval, research_queue. R1: skill, prompt, briefing, voice,
  tests/, config/, pyproject, installer. R0: docs/, .md, readme,
  assets/, comment. A surface matching NO token fails closed to R3;
  empty surface list → R0 (nothing changes).
- `record_improvement(...)` — fail-closed validation of every field
  (problem, evidence_refs, provenance, hypothesis, expected_mechanism,
  baseline_metric{name,value,unit,source finite}, mmi{minimum_delta
  finite, direction, unit}, guardrails{correctness,safety,cost,latency},
  workload{ref, 64-hex hash}, observation{requirement, cycles|duration},
  declared_risk, surfaces, budget{max_cost_usd≥0, capability_scope},
  reversibility{plan, rollback_threshold}); secret-like content refused
  at the boundary (V5-S7 guard family); final risk = max(declared,
  classifier); content-derived improvement id + idempotency key
  (identical content dedupes, changed content = new record); optional
  `supersedes` lineage pointer validated in-transaction (handler-level
  check too).
- `improvement_eligibility(...)` — promotion gate: unknown record,
  non-FROZEN state, or any missing/empty metric field → ineligible with
  the exact reason; complete frozen record → eligible.
- `raise_improvement_risk(...)` — strictly ascending reclassification
  only (lower or equal → ImprovementError); the projection handler
  re-checks monotonicity inside the append transaction (defense in
  depth).

Projection `improvement_records` (migration v21, state CHECK 'FROZEN' =
immutability enforced in DDL), wired into PROJECTION_TABLES, the
reconcile key map, and database row/all/count allowlists (the five
touch points).

## Tests (18)

frozen-and-eligible; missing metric / missing guardrail / NaN metric /
bad workload hash refused; identical-content dedup; changed-content new
record + supersedes lineage; unknown supersedes parent refused;
immutability after record (risk raise touches only risk_class);
declared R1 + control surface → R3; unknown surface → R3; R0/R1/R2/R3
known-surface mapping + empty list → R0; risk-lower and same-class
refusal; reason/record requirements for raises; secret-like rejection +
zero ledger hits; eligibility unknown/incomplete fail-closed; all
projection touch points + rebuild determinism; schema version pinned to
the imported constant (≥21).
