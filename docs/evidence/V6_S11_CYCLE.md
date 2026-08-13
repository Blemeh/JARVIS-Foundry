# V6-S11 — R0/R1/R2 CYCLES + R3 FAULT PROOF (THE END-TO-END LADDER)

```text
STATUS: PASS
PHASE: V6_CONTINUOUS_SELF_EVOLUTION
SLICE: S11 — R0/R1/R2 cycles + R3 fault proof
OBJECTIVE: run_cycle drives ONE complete autonomous candidate cycle
  through the REAL machinery — isolated build (S3) -> evaluation
  (S4) -> verification (S5) -> canary for R2/R3 (S7) -> atomic
  promotion (S8) — with AUTO-REJECTION of broken candidates (build
  failure, evaluation guardrail, absent effect, untrusted verdict,
  failed canary: the candidate never reaches the live slot) and the
  rollback path (S8) exercised against the live pointer. The demos
  use REAL subprocess workloads with REAL measurements; the broken
  candidates are real failures, not mocks. This is the operational
  heart of V6: the ladder the supervisor runs.
BASE COMMIT: 4999bd9 (S10 close)
CANDIDATE RELEASE: the S11 demonstration candidates (representative
  latency workloads; surfaces tests/foundry -> R1, evaluation -> R2)
RISK CLASS: the cycle DRIVER is R3-class machinery (it orchestrates
  promotion); the demo improvements are R1/R2
HYPOTHESIS: n/a (machinery + operational demos)
BASELINE METRIC: full suite 893 EXIT=0 at S10 close
SUCCESS THRESHOLD: R1 cycle promotes (real build + evaluation +
  verification + promotion); R2 cycle promotes with a real canary;
  a BROKEN candidate (failing evaluation cases) is auto-rejected with
  the reason recorded and live untouched; a FAILED build is
  auto-rejected at the build gate; a promoted candidate that regresses
  is rolled back (exact previous restored, quarantine, ROLLED_BACK);
  candidate controllers/STOP/unknown improvements refused at the
  boundary; every cycle recorded (cycle.started + cycle.completed
  with outcome and all artifact ids); all five projection touch
  points wired; suite green
GUARDRAILS: isolated runtime DB only (var/v6-runtime/v6.db, now schema
  v31); live DB stays v20; no live writes; controller identity checks;
  STOP sentinel; candidate roots outside the runtime home (S3 rule);
  build status gate (a FAILED build never reaches evaluation);
  all-or-nothing cycle recording
ACTIONS TAKEN: TDD red (9 tests) -> implemented evolution/cycle.py
  (run_cycle, cycle_outcome, CycleError) composing the REAL S1-S10
  machinery (no mocks — the ladder's stages are the actual modules
  verified in S1-S10) -> migration v31 (cycles) + PROJECTION_TABLES +
  reconcile key map + database row/all/count allowlists -> focused
  green (183 total across S1-S11) -> runtime DB migrated to v31
  (reconcile consistent) -> full suite
FILES CHANGED: src/jarvis_foundry/evolution/cycle.py (new);
  tests/foundry/test_evolution_cycle.py (new, 9 tests);
  src/jarvis_foundry/migrations.py (SCHEMA_VERSION 31 + migration 31);
  src/jarvis_foundry/projections.py (cycle.started/completed
  handlers, PROJECTION_TABLES); src/jarvis_foundry/reconcile.py (key
  map); src/jarvis_foundry/database.py (row/all/count allowlists)
LIVE SYSTEMS CHANGED: NONE (verified read-only at close time)
TESTS: 9 new S11 tests + S1-S10 regression (174) + FULL SUITE 902 passed
  EXIT=0 (391.96s; var/evidence/v6-s11/suite-v6-s11-full.txt,
  sha256 9fb1f439aef121e12d5942c50d14ca4b7ef9a6da14c679a5f0ed3817831f34db)
VERIFIER: <fill after dispatch>
EVIDENCE: var/evidence/v6-s11/ (suite artifacts, verdict JSON);
  var/v6-runtime/v6.db (isolated runtime, schema v31, consistent)
CANARY: the R2 demo cycle ran a REAL canary (2 ticks, healthy,
  COMPLETED) before promoting
PROMOTION OR ROLLBACK: R1 + R2 demonstration promotions on the
  isolated runtime; one ROLLED_BACK demonstration (outcome_regression
  trigger) — all inside the isolated V6 construction, never live
COST AND RESOURCE USE: local only (pytest + subprocess workloads)
UNRESOLVED RISKS: same-provider verifier limitation (D-031); the
  demonstration improvements are representative workloads, not yet the
  V6 machinery's own real improvements — the S12 operational gate
  decides whether real opportunities exist (absent -> NOT_TESTED/
  WAITING per the plan); the cycle's verifier identity defaults to the
  supervisor's session (the real deployment supplies the independent
  verifier session)
NEXT AUTONOMOUS ACTION: S12 — engineering + operational acceptance
OWNER INPUT REQUIRED: NONE
```

## Design

`src/jarvis_foundry/evolution/cycle.py`:

- `run_cycle(db, *, improvement_id, source_commit, workload_dir,
  baseline_command, candidate_command, build_command, controller,
  canary_ticks=None, verifier_identity=None, canary_params=None,
  timeout_s=120)` — boundary checks first (controller, STOP,
  improvement exists, workload dir exists); records cycle.started;
  then the REAL ladder: genesis release + live slot -> isolated build
  (S3 builder; a FAILED build is auto-rejected at the gate) ->
  candidate manifest + candidate slot -> evaluation (S4 lab:
  baseline + candidate on the same registered workload) ->
  verification (S5: request + structured PASS verdict with the
  improvement's guardrails as criteria) -> canary for R2/R3 (S7:
  start + the controller's ticks) -> atomic promotion (S8: ALL gates
  re-checked). Any stage failure is RECORDED as outcome=rejected with
  the reason — a broken candidate never promotes and never raises
  past the cycle boundary. cycle.completed carries every artifact id
  (promotion, canary, verification, runs, build, releases, workload)
  so the operational record is replayable.

Projection `cycles` (migration v31: cycle_id, improvement_id, outcome
CHECK promoted/rejected, reason, all artifact ids, controller) wired
into PROJECTION_TABLES, the reconcile key map, and database
row/all/count allowlists. Handlers validate the mandatory keys, the
outcome vocabulary, and the started->completed row transition.

## Tests (9)

R1 cycle promotes (real build/evaluate/verify/promote; outcome
promoted; pointers correct; row complete); R2 cycle with a REAL canary
promotes; broken candidate auto-rejected (guardrail violation; reason
recorded; live stays genesis); build failure auto-rejected at the
build gate; rollback after promotion (outcome_regression -> quarantine
+ previous restored + ROLLED_BACK + reconcile consistent); candidate
controller refused; STOP blocks; unknown improvement refused;
projection/schema integrity (pinned to the imported SCHEMA_VERSION
constant >= 31).

## 9. Verifier findings — repair record (deleg_dbaf806e, PASS)

Verdict JSON: var/evidence/v6-s11/verdict-deleg_dbaf806e.json. 13
scenarios. C1-C7 all PASS; 1 MEDIUM + 1 LOW + 3 INFO.

| Severity | Finding | Resolution |
|---|---|---|
| MEDIUM | Identical-content re-run: a SECOND cycle with the SAME improvement and bit-identical candidate content auto-rejected ("release X is not in the candidate slot"). Root cause: content-derived release ids + un-namespaced set_slot transitions — cycle 2's slot moves dedupe against cycle 1's (state-diverged no-op). Fail-closed, no corruption. | REPAIRED: run_cycle namespaces BOTH arena transitions per cycle (transition_id = "<cycle_id>:genesis" / "<cycle_id>:candidate") — the second cycle's slot moves re-apply; both cycles promote with distinct cycle/promotion ids. Regression: test_second_identical_cycle_promotes. |
| LOW | Arena-reset side effect: every cycle re-points live=genesis at start; after a REJECTED second cycle the final pointer state is live=genesis with the previously promoted release in previous. | RESOLVED BY THE SAME REPAIR (namespaced genesis transition makes the arena reset real — live re-points to the NEW genesis); the arena-local semantics are documented: live reflects the LATEST cycle's arena, and the previous pointer preserves the prior promoted release (auditable). |
| INFO | Extra (e): two cycles with distinct improvements create two distinct genesis releases, both promoted, 4 manifests, reconcile consistent — ACCEPTABLE isolated-arena design. | RECORDED. |
| INFO | C3 rejection reason names "quality" alongside "correctness" — consistent with compare_evaluations semantics. | RECORDED. |
| INFO | Extra (d) arithmetic: "both PROMOTED" holds only for differing candidate content pre-repair. | RECORDED. |

Round-2: fresh verifier with A/B discrimination dispatched after this
commit.
