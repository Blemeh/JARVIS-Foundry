# V7 Resilience Continuity Cross-Lane Handoff

## To the separate integration/live-promotion lane

This lane owns source and disposable fault proof only. It must not promote,
restart, migrate, or alter the live runtime. The candidate branch may be
reviewed and promoted only after this lane records a clean worktree, fresh
independent verification, and the final gate verdict.

## Shared architectural constraints

- Keep the existing deterministic Supervisor, release authority, event ledger,
  STOP, capability broker, effect reconciler, and worker contracts.
- Treat current-state docs and historical runtime snapshots as claims requiring
  fresh verification.
- Do not infer provider or production proof from configuration, fixtures,
  screenshots, or a listening port.
- Any remaining live/provider/physical-machine boundary must be reported as
  `BLOCKED` or `NOT_TESTED`, not upgraded to `PASS`.

## Handoff payload expected at close

- candidate HEAD and branch;
- per-job evidence paths and test commands;
- fault matrix with actual containment and recovery outcomes;
- open critical/high defects and residual host-only rows;
- explicit `MAIN MODIFIED: NO` and `PRODUCTION MODIFIED: NO` proof.

## Current source checkpoint

- Candidate branch: `candidate/v7-resilience-continuity-20260823`.
- Source/disposable V7 matrix: `V7C-16_FAULT_MATRIX.json`, 11/11 PASS.
- Focused V7/adjacent suite: PASS.
- Repository-wide suite: FAIL only at the live-staging Task Scheduler restart
  probe (`test_task_restarts_after_hard_kill_via_repeating_trigger`).
- Candidate push/promotion: BLOCKED pending owner-authorized Scheduler
  investigation and a fresh full-suite result.
