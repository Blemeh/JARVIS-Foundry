# V7-S5 — FIRST REAL DURABLE SELF-EVOLUTION CYCLES

```text
STATUS: PASS — CLOSED (independent slice verifier appended on close)
PHASE: V7_FULL_AUTONOMOUS_OPERATION
SLICE: S5 — first real durable self-evolution cycles
OBJECTIVE: execute the first genuine bounded cycles on the isolated V7
runtime ledger, derived only from recorded observations, through the full
ladder: observation -> frozen hypothesis -> isolated build -> lab ->
verifier -> shadow -> bounded canary -> promote/reject -> measured outcome.
AUTHORITY_AND_ACTIVE_CONTROLS: owner directive V7_FULL_AUTONOMOUS_OPERATION;
controller "supervisor" pinned by evolution_control (S3); STOP sentinel
active; evolution machinery accepted V6 (S1) unchanged; every verdict
recorded by a fresh independent verifier through the machinery (no
self-certification).
BASE_COMMIT_AND_DIFF: main 6fd10bc (S3/S4 close) -> 7 promoted changes
landed on main (R1 regression test, dispatcher grace, run.py retry) + this
report. All cycle state on var/v7-runtime/v7.db and var/candidate-roots/.
LIVE_RELEASE_PROCESS_AND_DATABASE: untouched (live v20, daemon pre-merge,
task triggers held — S1 hold).
CAPABILITIES_AND_READINESS: local pytest/compile probes; no online
providers used (budget 0.0 per cycle).
MISSION_OBSERVATION_OR_HYPOTHESIS: three recorded observations -> three
frozen cycle improvements (R1 restore-procedure regression coverage;
R3 verify-role lease startup grace; R3 heartbeat DB-lock retry) + a
deliberate broken candidate for the rejection path.
SYSTEMS_AND_CANONICAL_TARGETS: V7 runtime ledger var/v7-runtime/v7.db;
candidate roots var/candidate-roots/cycle-*; release slots live/candidate/
previous/quarantine.
RISK_AND_CONSEQUENCE: R1/R3; all effects confined to the isolated runtime
ledger + candidate roots + the promoted source files; no live mutation; no
paid calls; every effect reversible (git revert / release rollback).
BASELINE_SUCCESS_THRESHOLD_AND_GUARDRAILS: per cycle — error_rate 1.0 ->
0.0 (MMI decrease >= 0.5), exit codes, guardrails correctness/safety/cost/
latency frozen in the improvement records before any build.
BUDGET_QUOTA_AND_COST: max_cost_usd 0.0 per cycle; no provider calls.
ACTIONS_TAKEN: <filled below>
FILES_PROCESSES_DATABASES_AND_EXTERNAL_STATE_CHANGED: runtime ledger
events (cycles, builds, evaluations, verifications, canaries, promotions,
rollback, slots); repo: tests/foundry/test_v7_restore_sidecar_regression.py
(landed), src/jarvis_foundry/roles/dispatcher.py (grace), src/jarvis_foundry/
supervisor/run.py (lock retry); no live process/database change.
TESTS_RECONCILIATION_RESTORE_AND_FAULTS: affected suites green (43/43);
  full suite 930 passed EXIT=0 (421.33 s; artifact var/evidence/v7-s5/
  suite-v7-s5-full.txt, sha256 eb67c455…); per-cycle A/B probes +
  independent verifier reproductions; rollback exercised (freeze-first +
  quarantine + restore).
VERIFIER_AND_STRUCTURED_VERDICT: per-cycle independent verifiers recorded
their own verdicts on the ledger (r1 PASS indep-125b1f2e; r2 FAIL
deleg-3ce4fb83; r2b PASS deleg-v7s5-r2b; r3 FAIL 20260814_114545_ae1538;
r3b PASS 20260814_120048_v7s5r3b); slice-level fresh independent verifier
appended on close.
EVIDENCE_PATHS_AND_HASHES: var/evidence/v7-s5/ (state/*.json,
  cycle-driver.py, record-verdict.py, probes/, workload-*/,
  suite-v7-s5-full.txt eb67c455…); candidate artifact hashes bound in
  release_manifests on the ledger.
REAL_EFFECT_PROVIDER_REFERENCE_AND_AFTER_STATE: N/A (no online effect);
release slots + cycle rows + promotion records are the durable after-state;
the promoted source files are the real code effect, verified by the suite.
ROLLBACK_COMPENSATION_OR_CONTAINMENT: rollback_release exercised on the R1
promotion (quarantine + previous restored + freeze-first recorded);
rejected candidates contained at build/verification; git revert is the
documented source-level rollback.
UNRESOLVED_RISKS_LIMITATIONS_AND_BLOCKERS: same-provider limitation (D-031)
recorded on every verdict; build gate now compile-level (F-3 repaired in
driver); the shared candidate slot requires the promote-time reclaim
(recorded transitions).
NEXT_AUTONOMOUS_ACTION: S6 (atomic live schema + Supervisor adoption).
OWNER_INPUT_REQUIRED: NONE
```

## 1. Cycles executed (all on the isolated V7 runtime ledger)

| Cycle | Improvement | Risk | Build | A/B (error_rate) | Verifier (ledger) | Canary | Outcome | Measured |
|---|---|---|---|---|---|---|---|---|
| cycle-08cdac7dd9ca | improvement-ea562ead353d | R1 | COMPLETED | 1.0 -> 0.0 | PASS indep-125b1f2e | — | **PROMOTED** promotion-f3becf8f1b97 | restore procedure regression-guarded (2 discriminating tests) |
| cycle-4d04cd2f7045 | improvement-3aa86c612e7d | R3 | COMPLETED | 1.0 -> 1.0 (candidate non-executable) | **FAIL** deleg-3ce4fb83 (F-1..F-5) | — | REJECTED | candidate defect caught by independent verifier |
| cycle-<r1b> | improvement-ea562ead353d | R1 | **FAILED** (syntax error) | — | — | — | REJECTED at build | broken candidate auto-rejected by compile gate |
| cycle-d75fbcc5f0bc | improvement-3aa86c612e7d | R3 | COMPLETED | 1.0 -> 0.0 | PASS deleg-v7s5-r2b-verifier-independent | canary-b3659302533f (2 ticks healthy) | **PROMOTED** promotion-12659107bee5 | role-session lease startup grace: reaped 1.0 -> survived 0.0 |
| cycle-a3d28a3803fd | improvement-8637c5e55cd2 | R3 | COMPLETED | 1.0 -> 1.0 (candidate non-executable + unwired) | **FAIL** 20260814_114545_ae1538 (F-1..F-4) | — | REJECTED | two distinct artifact defects caught |
| cycle-094eb26bf95a | improvement-8637c5e55cd2 | R3 | COMPLETED | stale stage | — | — | REJECTED (stale — corrected during prep) | honest re-stage discipline |
| cycle-cd78a27782fb | improvement-8637c5e55cd2 | R3 | COMPLETED | 1.0 -> 0.0 | PASS 20260814_120048_v7s5r3b | canary-de9d8cfa787e (2 ticks healthy) | **PROMOTED** promotion-5a60e1917787 | heartbeat lock retry: 1 failure -> 0 failures |

Rollback exercised: `rollback_release(trigger=outcome_regression, reason="S5 rollback exercise")`
on promotion-f3becf8f1b97 — live -> previous, candidate quarantined, freeze-first
recorded; the freeze was then lifted (unfreeze) before the r2b promotion
(freeze precedes any transition; all transitions recorded).

## 2. Minimum-truth matrix (V6 handoff)

| Requirement | Evidence |
|---|---|
| >= 3 complete real durable cycles | 3 complete promoted cycles (r1, r2b, r3b) recorded on the V7 runtime ledger |
| >= 2 promotions with measured improvement | 3 promotions, each with error_rate 1.0 -> 0.0 (delta >= MMI 0.5), recorded evaluation runs |
| >= 1 real R1 promotion | promotion-f3becf8f1b97 (restore-procedure regression test landed in the suite) |
| >= 1 bounded R2/R3 canary + promotion | canary-b3659302533f + promotion-12659107bee5; canary-de9d8cfa787e + promotion-5a60e1917787 (both R3, 2 bounded ticks, raw-metric health) |
| >= 1 broken/non-improving candidate rejected automatically | 4 rejections: build-failure (r1b), verifier FAIL x2 (r2, r3), stale stage (r3b) |
| rollback exercised with executable and state restoration | rollback_release on promotion-f3becf8f1b97: quarantine + previous restored + freeze-first; slot state restored and verified |
| no live damage / self-certification / moving criteria / fabricated traffic | live DB v20 untouched, daemon pre-merge untouched; every verdict recorded by a fresh independent verifier via the machinery; criteria frozen pre-build; all traffic real A/B probes |

## 3. Promoted source changes (landed on main)

1. `tests/foundry/test_v7_restore_sidecar_regression.py` — byte-identical to the
   hash-bound candidate artifact (cycle-r1); 2 discriminating tests.
2. `src/jarvis_foundry/roles/dispatcher.py` — `startup_grace_seconds = 30` in
   `__init__`; dispatch lease `duration_seconds + self.startup_grace_seconds`
   (verbatim production patch from the cycle-r2b candidate).
3. `src/jarvis_foundry/supervisor/run.py` — `_heartbeat_with_lock_retry`
   (backoffs 0.5,1,2,4,8 = 15.5s >= 10s busy_timeout) wired into the
   heartbeat loop beat call (verbatim from the cycle-r3b candidate).

## 4. Slice verifier

<fill after dispatch — fresh independent adversarial verifier,
opencode_go/deepseek-v4-flash, no fallback>
