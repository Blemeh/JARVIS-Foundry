# V3 hardening serial job queue

Orientation recorded 2026-08-23 (Europe/Budapest).

- Worktree: `C:/temp/JARVIS-Overnight-V3-Hard-Gate`
- Branch: `candidate/overnight-v3-hard-gate`
- HEAD: `41b9bb856d31b6da18247507f6358317082d6c6b`
- Base: `origin/main` / `ceb90d7d201b4775757cae0f7e03ae1e2d6f22e2`
- Schema: v36
- Source evidence pin: `7849fc3a9998a16da45c922e4ccc506c0c5eab04`
- Production mutation/push/deploy: none

## Queue

| Job | Status | Definition of done | Evidence / commit |
|---|---|---|---|
| A — close AC-01/07/08/09/14/17/20 | IN_PROGRESS | Reproduce each gap, repair source semantics, add regression/fault proof, rerun affected gate | open at orientation |
| B — genuine 4-hour endurance soak | PENDING | Real elapsed 14,400 s, >=8 cycles, zero severity-one, complete resource observations | existing 8-cycle probe is NOT_TESTED for soak |
| C — STOP/race/Hermes ownership | PENDING | No prohibited event after STOP across pre/post dispatch and result boundaries | V3-HARD-STOP-001 |
| D — outcome lineage | IN_PROGRESS | Candidate -> decision -> mission -> step -> worker -> evidence -> verifier -> verdict -> outcome is reconstructible | V3-HARD-OUTCOME-001, V3-HARD-LINEAGE-001; terminal outcome package is implemented but full E2E closure remains |
| E — crash/fault recovery | PENDING | All 12 named transition injections recover once with durable classification and no duplicate mutation | V3-HARD-FAULT-001 |
| F — autonomous continuation | PENDING | PASS/FAIL/repair/blocker/idle transitions select next useful action or honest WAIT | V3-HARD-CONT-001 |
| G — resource envelope | PENDING | CPU/RAM/process/worker/DB/event/retry/queue/provider counters are bounded and measured | V3-HARD-RESOURCE-001 |
| H — scoring/policy correctness | PENDING | Boundary and malformed values are deterministic and fail-closed | not yet audited |
| I — eligibility/admission/materialization | PENDING | Duplicate/epoch/STOP/concurrency paths preserve one canonical mission truth | not yet audited |
| J — teams/independent verification | PENDING | Fresh verifier identity, bounded repair, crash/restart and malformed verdicts are proven | existing supporting suite is partial |
| K — Git effect truth | PENDING | Disposable remote ambiguity/retry/no-op paths reconcile actual state | focused Git suite currently PASS |
| L — determinism/replay/concurrency | PENDING | Seeded replay and concurrent duplicate paths produce identical durable decisions | not yet audited |

Migration/restore v35->v36 remains preserved at 9/9 PASS and will only be rerun if schema or restore behavior changes.

## Checkpoint 1 — terminal outcome package

- Root cause: `record_outcome` trusted caller IDs and the unified cycle stopped at `COMPILED`; maintenance continuation was a reporting stub.
- Source changed: `v3_outcomes.py`, `v3_cycle.py`, and runtime `worker_loop.py` now prove durable linkage, carry lineage IDs, re-check STOP after observation/before scoring/materialization, write the V3 outcome on runtime PASS, and schedule idempotent maintenance/next-selection events.
- Tests added/updated: strict linkage, lineage, continuation wake, cycle lineage, and runtime candidate outcome assertions in `tests/foundry/test_v3_outcomes.py` and `tests/foundry/test_v3_discovery_cycle.py`.
- Tests executed: V3 outcomes/discovery/smoke; V3 hard core/scoring/loop; runtime worker loop/mission loop/verification loop — all PASS.
- Evidence: this queue and fresh local pytest output; full gate evidence has not been regenerated yet.
- Commit: `016546d` (`V3: persist terminal outcome lineage and continuation`).
- Remaining uncertainty: AC-01 still needs three complete E2E loops; AC-20 needs a fresh full chain including verifier/evidence IDs; full STOP race and fault matrix remain open.

## Job record template

For every DONE job, append: root cause, source changed, tests added, tests executed, evidence path, commit SHA, and remaining uncertainty. BLOCKED jobs must name the exact unavailable dependency and handoff.
