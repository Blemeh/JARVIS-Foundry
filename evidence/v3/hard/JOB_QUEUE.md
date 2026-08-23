# V3 hardening serial job queue

Orientation recorded 2026-08-23 (Europe/Budapest).

- Worktree: `C:/temp/JARVIS-Overnight-V3-Hard-Gate`
- Branch: `candidate/overnight-v3-hard-gate`
- HEAD: `58f35b8` (local hardening checkpoint)
- Base: `origin/main` / `ceb90d7d201b4775757cae0f7e03ae1e2d6f22e2`
- Schema: v36
- Source evidence pin: `58f35b873f682a209eee2d12cf549cf315f469d9`
- Production mutation/push/deploy: none

## Queue

| Job | Status | Definition of done | Evidence / commit |
|---|---|---|---|
| A — close AC-01/07/08/09/14/17/20 | DONE | Reproduce each gap, repair source semantics, add regression/fault proof, rerun affected gate | GATE_REPORT.json: all ACs PASS |
| B — genuine 4-hour endurance soak | DONE | Real elapsed 14,400 s, >=8 cycles, zero severity-one, complete resource observations | SOAK.json + RESOURCE.json |
| C — STOP/race/Hermes ownership | DONE | No prohibited event after STOP across pre/post dispatch and result boundaries | V3-HARD-STOP-001 CLOSED |
| D — outcome lineage | DONE | Candidate -> decision -> mission -> step -> worker -> evidence -> verifier -> verdict -> outcome is reconstructible | V3-HARD-OUTCOME-001 and V3-HARD-LINEAGE-001 CLOSED |
| E — crash/fault recovery | DONE | All 12 named transition injections recover once with durable classification and no duplicate mutation | fault-matrix.json PASS |
| F — autonomous continuation | DONE | PASS/FAIL/repair/blocker/idle transitions select next useful action or honest WAIT | V3-HARD-CONT-001 CLOSED |
| G — resource envelope | DONE | CPU/RAM/process/worker/DB/event/retry/queue/provider counters are bounded and measured | RESOURCE.json PASS |
| H — scoring/policy correctness | DONE | Boundary and malformed values are deterministic and fail-closed | focused suite PASS |
| I — eligibility/admission/materialization | DONE | Duplicate/epoch/STOP/concurrency paths preserve one canonical mission truth | focused suite PASS |
| J — teams/independent verification | DONE | Fresh verifier identity, bounded repair, crash/restart and malformed verdicts are proven | independent verifier 36/36 PASS |
| K — Git effect truth | DONE | Disposable remote ambiguity/retry/no-op paths reconcile actual state | focused Git suite PASS |
| L — determinism/replay/concurrency | DONE | Seeded replay and concurrent duplicate paths produce identical durable decisions | hard E2E concurrency PASS |

Migration/restore v35->v36 remains preserved at 9/9 PASS and will only be rerun if schema or restore behavior changes.

## Checkpoint 1 — terminal outcome package

- Root cause: `record_outcome` trusted caller IDs and the unified cycle stopped at `COMPILED`; maintenance continuation was a reporting stub.
- Source changed: `v3_outcomes.py`, `v3_cycle.py`, and runtime `worker_loop.py` now prove durable linkage, carry lineage IDs, re-check STOP after observation/before scoring/materialization, write the V3 outcome on runtime PASS, and schedule idempotent maintenance/next-selection events.
- Tests added/updated: strict linkage, lineage, continuation wake, cycle lineage, and runtime candidate outcome assertions in `tests/foundry/test_v3_outcomes.py` and `tests/foundry/test_v3_discovery_cycle.py`.
- Tests executed: V3 outcomes/discovery/smoke; V3 hard core/scoring/loop; runtime worker loop/mission loop/verification loop — all PASS.
- Evidence: this queue and fresh local pytest output; full gate evidence has not been regenerated yet.
- Commit: `016546d` (`V3: persist terminal outcome lineage and continuation`).
- Remaining uncertainty: AC-01 still needs three complete E2E loops; AC-20 needs a fresh full chain including verifier/evidence IDs; full STOP race and fault matrix remain open.

## Current checkpoint (58f35b8)

- C STOP/race: DONE in source/tests; every consequential legacy/cycle/mission boundary now has a fresh STOP gate, with race tests proving no verifier, portfolio, decision, or candidate mission after a STOP race.
- D outcome lineage: DONE in source/tests; three disposable end-to-end runtime loops prove candidate, decision, mission, worker, evidence, verifier, outcome, and next-selection IDs.
- E fault recovery: DONE in source/tests; all 12 named points have one-shot hooks and replay/freeze tests in `test_v3_fault_matrix.py`.
- F continuation: DONE in source/tests; outcome recording schedules idempotent maintenance and next-selection follow-up, including honest wait state.
- G resource envelope: DONE; `v3_resources.py` and the real-soak runner measured CPU time, RSS when available, processes, workers, DB/events, retries, queue, and provider counters for 14,400 seconds with no violations.
- Commit: `58f35b8` (`V3: serialize concurrent cycles and preserve soak evidence`).
- Final gate: focused 154/154, supporting 63/63, migration 9/9, independent verifier 36/36, full foundry 1,611/1,611, and measured soak 14,400 seconds/470 cycles; all AC-01..AC-20 PASS and all six hard defects CLOSED. Candidate remains local-only pending owner review and semantic Git reconciliation; no promotion/push/deploy was performed.

## Final measured gate checkpoint (58f35b8)

- Gate verdict: PASS at source SHA `58f35b873f682a209eee2d12cf549cf315f469d9`.
- Fresh acceptance evidence: `GATE_REPORT.json`, `HANDOFF.json`, `SOAK.json`, `RESOURCE.json`, `fault-matrix.json`, focused/supporting/migration/independent JUnit artifacts, and `full-suite-final-58f35b8.junit.xml`.
- Resource envelope maxima: 1,883 events, 2,338,816 DB bytes, 363 observed processes; zero violations, failures, retries, workers, missions, or provider calls in the disposable provider-free soak.
- Remaining uncertainty: promotion still requires owner review and semantic reconciliation against the live Git graph; main, production state, live DB, providers, push, and deploy were untouched.

## Job record template

For every DONE job, append: root cause, source changed, tests added, tests executed, evidence path, commit SHA, and remaining uncertainty. BLOCKED jobs must name the exact unavailable dependency and handoff.
