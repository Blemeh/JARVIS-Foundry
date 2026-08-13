# V6-S4 — REPRODUCIBLE EVALUATION LABORATORY

```text
STATUS: PASS
PHASE: V6_CONTINUOUS_SELF_EVOLUTION
SLICE: S4 — reproducible evaluation laboratory
OBJECTIVE: baseline and candidate subjects run against the SAME frozen
  representative workload: registered inputs with content hashes,
  hash-verified copies per run, deterministic seeds where possible,
  explicit nondeterminism handling, complete case coverage (no
  cherry-picking), comparable output artifacts, and fail-closed
  comparison against the improvement's frozen MMI and guardrails.
BASE COMMIT: 5502f18 (S3 close)
CANDIDATE RELEASE: none (machinery slice)
RISK CLASS: R3-class machinery (evaluation control) built and verified
  inside the isolated V6 construction; live surfaces untouched
HYPOTHESIS: n/a (machinery; exercised by the S11 ladder)
BASELINE METRIC: full suite 778 EXIT=0 at S3 close
SUCCESS THRESHOLD: workload registration deterministic + idempotent +
  secret-scanned; runs copy hash-verified inputs, execute every case,
  record correctness/latency/cost/error rate/nondeterminism handling;
  comparisons fail closed on incomparability (different workload/input
  hash/kind/missing runs/unknown improvement); MMI + guardrails
  evaluated; all five projection touch points wired; suite green
GUARDRAILS: isolated runtime DB only (var/v6-runtime/v6.db, now schema
  v24); live DB stays v20; no live writes; subjects run with sanitized
  env; workload dirs are copied per run — inputs are never shared
  mutable state between baseline and candidate
ACTIONS TAKEN: TDD red (14 tests) -> implemented evolution/lab.py
  (register_workload, workload_by_id, run_evaluation,
  compare_evaluations, LabError) -> migration v24 + PROJECTION_TABLES +
  reconcile key map + database row/all/count allowlists -> focused
  green (73 total across S1-S4) -> runtime DB migrated to v24
  (reconcile consistent) -> full suite
FILES CHANGED: src/jarvis_foundry/evolution/lab.py (new); tests/foundry/
  test_evolution_lab.py (new, 14 tests); src/jarvis_foundry/migrations.py
  (SCHEMA_VERSION 24 + migration 24); src/jarvis_foundry/projections.py
  (evaluation.run_started/run_completed handlers, PROJECTION_TABLES);
  src/jarvis_foundry/reconcile.py (key map); src/jarvis_foundry/
  database.py (row/all/count allowlists)
LIVE SYSTEMS CHANGED: NONE (verified read-only at close time)
TESTS: 14 new S4 tests + S1-S3 regression (59) + FULL SUITE 792 passed
  EXIT=0 (329.79s; var/evidence/v6-s4/suite-v6-s4-full.txt,
  sha256 3bfd6d8810bddaa88886f90cdad9fdf7bde417f543b2cdc6db65638d8d015eb0)
VERIFIER: <fill after dispatch>
EVIDENCE: var/evidence/v6-s4/ (suite artifacts, verdict JSON);
  var/v6-runtime/v6.db (isolated runtime, schema v24, consistent)
CANARY: none (machinery slice)
PROMOTION OR ROLLBACK: none
COST AND RESOURCE USE: local only (pytest runs; per-case subprocesses)
UNRESOLVED RISKS: same-provider verifier limitation (D-031);
  evaluation cost_usd is declared by the caller (no real provider
  billing exists on this host — provider reconciliation stays
  NOT_TESTED, R-EXT-01); duration deltas are wall-clock measurements
  (seeded determinism verified; cross-machine reproducibility is the
  S9 restoration scope)
NEXT AUTONOMOUS ACTION: S5 — independent verifier boundary
OWNER INPUT REQUIRED: NONE
```

## Design

`src/jarvis_foundry/evolution/lab.py`:

- `register_workload(db, *, name, input_dir, config, seeds,
  controller)` — content-hashes every input file (reparse-point
  pruned, deterministic order); the workload id binds name + input_dir
  + input_hashes + config + seeds (identical re-registration dedupes);
  config secret-scanned (V5-S7 family).
- `run_evaluation(...)` — copies the registered inputs into a fresh
  run dir (lab-scratch), re-hashes the copy and REFUSES on mismatch
  (fail closed on incomparable measurements); runs one invocation per
  input file (every case recorded — no cherry-picking); records per-
  case exit codes, output hashes, error rate, duration, cost,
  nondeterminism handling (seeded | explicit treatment). Sanitized
  env (no HERMES_HOME/JARVIS_HOME/credential vars).
- `compare_evaluations(db, *, baseline_run_id, candidate_run_id,
  improvement_id)` — refuses when: either run missing, baseline is not
  kind=baseline, candidate is not kind=candidate, workloads differ,
  input hashes differ, improvement unknown or ineligible. Metric key
  from the frozen baseline_metric name (duration_s | error_rate);
  delta vs the frozen MMI (direction-aware); guardrails: correctness
  (candidate exit != 0), quality (candidate error rate > baseline),
  cost (candidate cost > budget.max_cost_usd). Appends
  evaluation.comparison_recorded.

Projection `evaluation_runs` (migration v24, subject_kind CHECK,
error_rate/cost/duration columns, case + output JSON) wired into
PROJECTION_TABLES, the reconcile key map, and database row/all/count
allowlists. Handlers validate mandatory keys and refuse unknown runs
inside the append transaction.

## Tests (14)

workload registration deterministic + idempotent + hashes + config/
seeds bound; secret config refused; unknown workload refused; baseline
run all 5 cases (no cherry-picking); candidate run same input hash;
failing case -> error_rate 0.25 + overall exit 1; seeded determinism
(identical output hashes); nondeterminism handling recorded;
improvement met (small MMI, real delta); improvement not met; guardrail
violation (correctness); comparison refusals (missing run, kind
mismatch, different workload, unknown improvement); bad subject kind /
empty command refused; projection/schema integrity (pinned to the
imported SCHEMA_VERSION constant >= 24).
