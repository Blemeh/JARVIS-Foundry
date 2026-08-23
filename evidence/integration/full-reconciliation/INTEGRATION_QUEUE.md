# Full Reconciliation Integration Queue

Baseline for this queue: `origin/main` at `ceb90d7d201b4775757cae0f7e03ae1e2d6f22e2` (local `main` at archaeology start: `701aa748a2db412e3b6e83ab91f7982408ea6573`, an ancestor and nine commits behind).

The queue was created only after `CANDIDATE_INVENTORY.md`/`.json`. It contains only source or test work classified `UNIQUE_ACCEPTED_WORK`; evidence-only, rejected, abandoned, patch-equivalent, and already-canonical lanes are not merged. Every accepted source package is integrated in an isolated worktree, serially, with focused tests before the next package.

## Required pre-integration actions

1. Push normal, non-force backup refs for the five accepted local candidates and verify each remote SHA. Do not overwrite an existing remote ref.
2. Create `C:\temp\JARVIS-Full-Reconciliation` from the verified `origin/main` with branch `integration/full-reconciliation-2026-08-23`.
3. Keep all existing worktrees and processes untouched. The dirty command-center endurance log and dirty operational egg-info files remain owned by their existing lanes and are not copied into the integration worktree.

## Serial queue

| Order | Candidate | HEAD | Patch-equivalent unique commits | Scope | Required treatment |
|---:|---|---|---:|---|---|
| 1 | `candidate/overnight-release-candidate` | `7159ea5e964619d040f9d85c09e83466449d650b` | 38 | release identity/provenance, owner control, delivery boundary, diagnostics, supervisor/worker safety, disposable factory; includes the accepted audit-R2 lineage | Merge with `--no-ff` only after reviewing conflicts. Do not retain generated egg-info churn or stale candidate `CURRENT_STATE` claims without reconciliation. Run release/runtime focused tests and source-bound factory checks with provider, scheduler, and live DB disabled. |
| 2 | `candidate/overnight-v3-hard-gate` | `e1aa681c1a3d173197b2ab15eb053835c555e479` | 13 | V3 concurrency, STOP/fault matrix, terminal outcome lineage, continuation, resources, Git reconciliation and executable hard gate | Merge serially after Q1. Resolve only semantic conflicts, preserving Q1 release authority and V3 schema lineage. Run V3 hard-core, fault, outcome, migration and bounded disposable tests. |
| 3 | `candidate/overnight-v3-operational-slice` | `af355dc510ee719b0948a5ad91c7426f080c8c56` | 3 | ledger-backed Discovery projection, real adapter mapping, automatic outcome closure, command-center Discovery mapper | Merge/cherry-pick after Q2; reconcile `runtime/worker_loop.py`, `v3_outcomes.py`, and Discovery UI contracts explicitly. Retain the two low adversarial findings as regression obligations, not as PASS evidence. |
| 4 | `candidate/audit-remediation-2026-08-18` | `8af6838437a5d461b944fc132f34953f12365f1c` | 8 | STOP sentinel ownership, adapter child PID registration, read-only doctor/events/reconcile, truthful NOT_TESTED handling, evidence mismatch closure | **Semantically absorbed by Q1-Q3.** The merged release/V3 source already contains the PID table, owned-tree termination, read-only paths, STOP normalization, supervisor gating, and evidence-mismatch closure. The residual environment-boundary improvement was integrated as `26e73c7`; no duplicate cherry-pick was retained. Fresh worker-control, adapter, mission-bridge, verifier-truth, CLI, supervisor and runtime tests pass. |
| 5 | `candidate/overnight-command-center-truth` | `fb42c6d386c23afc3a74cf548f25e533c64dc264` | 37 | operator UI routes, drilldowns, resilience, accessibility, product verification and endurance harness | Merge after backend contracts settle. Reconcile Discovery files against Q3 and do not import the worktree-only `endurance-live.log`. Run lint, typecheck, unit, bounded e2e, build, security and product-verification checks. |

## Explicitly excluded from source queue

- `candidate/audit-r2-integration`, `candidate/audit-truth-remediation-r2`, and `candidate/audit-truth-remediation`: the accepted R2 lineage is already contained or semantically superseded by Q1.
- `candidate/24h-independent-review`, `candidate/v3-command-center`, `candidate/v3-lab`, `candidate/remaster-forward-port`, and `integration/procedural-learning-phase4-remediation`: evidence-only.
- Patch-equivalent convergence/repair/worker-loop branches: `FULLY_SUPERSEDED` or `ALREADY_CANONICAL` by the inventory proof.
- `candidate/command-center-ui` and `remaster/post-v7-purpose`: older UI/remaster surfaces superseded by current canonical truth.
- `integration/hybrid-worker`: older worker package superseded by canonical worker activation.
- `experiment/graphiti-openhands-a2a`: `FAILED_OR_REJECTED`; outside the canonical architecture.

No source package is considered accepted until it has a fresh focused test result on the integration HEAD. Historical branch PASS artifacts remain provenance only. Production/live DB, Task Scheduler, provider credentials/calls, deployment, migration, restart, and live runtime activation are prohibited throughout this queue.

## Post-queue semantic resolution

The audit lane is classified `PARTIALLY_ABSORBED` at the integrated HEAD: seven of its eight behavioral themes are already present through Q1-Q3 with stronger later implementations, and the remaining environment-scoping behavior is represented by `26e73c7`. Its original commit IDs remain preserved in the inventory and remote backup; no accepted behavior is stranded.
