# R8 Convergence Gate: Production Organization

Program: JARVIS Post-V7 remaster forward-port
Candidate checkout: C:\temp\JARVIS-Foundry-Convergence
Worker architecture: canonical workers package only

## Acceptance Contract

R8 is accepted here only when a worker result is checked against observable
state and a fresh verifier, and when the same control loop can repair an
existing asset. Worker prose is evidence input, never certification.

| Requirement | Canonical surface | Evidence in this gate |
| --- | --- | --- |
| Bounded mission context | workers.contracts.WorkerStepContract | round-trip payload, deterministic context hash, scope, budget and acceptance |
| Deterministic worker selection | workers.router.WorkerRouter | bounded contract routes to the explicitly READY OpenHands canary |
| Real digital asset | SubprocessWorkerAdapter through HybridStepController | three committed service notes become docs/health.json and docs/health.md |
| Actual-result inspection | workers.evidence.EvidenceReconciler | changed paths, diff hash, expected artifacts and acceptance test exit are reconciled |
| Independent verification | HybridStepController plus fresh Hermes adapter session | verifier parses the generated JSON/report and reruns tests against the actual worktree |
| Maintenance/regression repair | fresh repair contract through HybridStepController | a committed manifest loader defect fails first, then Hermes repairs src/catalog/manifest.py |
| Durable repair truth | events and mission projection | evidence mismatch, verification.failed, repair run, verification.passed and ACTION_SELECTION are recorded |
| Dirty-owner preservation | EvidenceReconciler delta_baseline | owner-notes.md remains byte-for-byte unchanged while the worker adds only its declared outputs |
| Isolation | workers.isolation.HeldOutBoundary and DisposableWorkerState | controller-private boundary and protected-root refusal |
| Honest continuation | mission.proposal and MissionLoop | deterministic next action and zero-dispatch WAITING when no useful action exists |

## Real Asset Proof

The asset repository has three committed Markdown inputs and a committed
acceptance test. A subprocess worker reads every input in sorted order and
writes a useful health index with:

- source identity and relative path;
- SHA-256 of the inspected bytes;
- byte and line counts;
- headings and aggregate health totals;
- a Markdown report with an acceptance section and one row per source.

HybridStepController records the route, before state, worker envelope, observed
diff, expected artifact presence, test exit, and a fresh verifier verdict. The
test then inspects both generated files and the repository status directly.
This is a multi-input report/index deliverable, not a placeholder result file.

## Maintenance Proof

The maintenance repository starts with a committed catalog manifest and a
regression test that requires order and disabled-record preservation. The
existing loader intentionally drops disabled records, and the baseline test is
run first to reproduce that defect.

The OpenHands-bound builder claims no mutation. Evidence reconciliation records
the failing test, the fresh Hermes verifier returns FAIL, and the controller
creates a new repair contract. A subprocess repair changes only the existing
src/catalog/manifest.py root-cause line. Reconciliation and a second fresh
Hermes verifier then require the repaired source, passing test suite, changed
path and changed source hash before the mission returns to ACTION_SELECTION.

## Run

From the candidate checkout, use the configured venv:

    C:\temp\jarvis-convergence-venv\Scripts\python.exe -m pytest tests\convergence\test_r8_production_gate.py -q

Relevant canonical worker regression suites:

    C:\temp\jarvis-convergence-venv\Scripts\python.exe -m pytest tests\foundry\test_worker_orchestrator.py tests\foundry\test_worker_evidence.py tests\foundry\test_worker_contracts.py -q

The gate uses local disposable repositories and a subprocess boundary. It does
not claim live provider readiness, external effects, deployment, or credential
proof. The candidate worktree, canonical main checkout, remaster checkout and
Eyes lab are not worker roots and are not modified by the canaries. No commit
is created by this subtask.

## Scope

Assigned files only:

- tests/convergence/test_r8_production_gate.py
- docs/convergence/R8_GATE.md

R6 history remains untouched. Concurrent candidate edits and sibling stage
evidence remain untouched. A fresh independent verifier should rerun the
candidate gate after this edit before any stage transition.
