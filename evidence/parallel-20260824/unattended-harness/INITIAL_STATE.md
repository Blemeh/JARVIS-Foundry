# Unattended Acceptance Harness Initial State

Date: 2026-08-24
Frozen base: `107827d81eaee514edc66eab98390438b63c1650`
Frozen source: `origin/main`
Candidate branch: `candidate/postv7-unattended-harness-20260824`
Worktree: `C:\temp\JARVIS-PostV7-Unattended-Harness`

## Boundary

This lane owns a disposable acceptance harness. It does not add a Supervisor,
portfolio selector, worker loop, provider connector, or production chaos path.
Every fault target is an owned fixture process, fake provider, temporary release
root, or disposable SQLite database. The harness is an observer and verifier.

The canonical main worktree was clean before this lane started. `origin/main`
was fetched with pruning, then resolved to the frozen base above. No live
database, Scheduler task, provider account, credential, deployment, or runtime
process was changed by this lane.

## Source truth inspected

| Surface | Current classification | Reusable evidence or contract |
| --- | --- | --- |
| Architecture, event ledger, STOP, state | CODE_PRESENT | `docs/architecture/02_ARCHITECTURE_CONTRACT.md`, `src/jarvis_foundry/database.py`, `src/jarvis_foundry/events.py` |
| Supervisor and worker loop | CODE_PRESENT | `src/jarvis_foundry/supervisor/service.py`, `src/jarvis_foundry/runtime/worker_loop.py`, `tests/foundry/supervisor_fixture.py` |
| V7A portfolio | CANONICAL_SOURCE_PRESENT | `src/jarvis_foundry/evolution/executive_portfolio.py`, `src/jarvis_foundry/executive/portfolio_bridge.py` |
| V7B procedures and operations | CANONICAL_SOURCE_PRESENT | `src/jarvis_foundry/operations/procedure.py`, `evidence/parallel-20260823/v7-operations/V7B-2_PROCEDURE_MODEL.md` |
| V7C resilience and incidents | CANONICAL_SOURCE_PRESENT | `src/jarvis_foundry/resilience/`, `scripts/v7_resilience_fault_matrix.py` |
| V4 briefing events | CONTRACT_PRESENT | `src/jarvis_foundry/briefing_items.py`, `evidence/parallel-20260823/v4/V4-4_BRIEFING_EVENT_MODEL.md` |
| V5 effects and lineage | CANONICAL_SOURCE_PRESENT | `src/jarvis_foundry/effects.py`, `src/jarvis_foundry/reconcile.py`, `evidence/parallel-20260823/v5/` |
| V6 evolution and rollback | CANONICAL_SOURCE_PRESENT | `src/jarvis_foundry/evolution/`, `src/jarvis_foundry/deployment/release.py`, `src/jarvis_foundry/integrated_rollback.py` |
| Existing fault proof | REUSABLE_DISPOSABLE | `scripts/v7_resilience_fault_matrix.py`, `scripts/runtime_fault_repro.py`, V3 fault helpers |
| Existing endurance proof | HISTORICAL_REUSABLE_ONLY | `evidence/v3/hard/SOAK.json` records a prior isolated 14,400-second run. It is not evidence that this harness has run. |
| Existing acceptance directory | CONTRACT_ONLY | `tests/acceptance/README.md` explicitly says no acceptance test has run. |

## Baseline checks

- `git fetch --all --prune`: PASS.
- `git rev-parse origin/main`: `107827d81eaee514edc66eab98390438b63c1650`.
- Required candidate worktree: CREATED and isolated.
- V7A/B/C source interfaces: PRESENT on frozen base; their old stage report is
  treated as historical evidence and is independently rechecked by this lane.
- Focused V7 resilience, cross-lane wiring, and resource tests: PASS before
  harness changes.

## Evidence posture

Green source tests do not prove unattended operation. A run is PASS only when
the harness has complete durable evidence, every required invariant is sampled,
all required faults have fired, and the verdict engine accepts the bundle.
Missing, stale, partial, forged, or interrupted evidence is never promoted to
PASS.
