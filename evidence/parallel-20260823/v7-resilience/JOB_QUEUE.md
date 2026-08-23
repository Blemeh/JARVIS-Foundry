# V7 Resilience Continuity Job Queue

Queue owner: `candidate/v7-resilience-continuity-20260823`
Queue policy: one job at a time; each job ends with focused proof, a checkpoint,
and an explicit PASS, FAIL, BLOCKED, or NOT TESTED verdict.

| Job | Scope | Status | Evidence / next action |
|---|---|---|---|
| V7C-1 | Failure and health truth map | IN PROGRESS | Reuse existing Supervisor, diagnostics, leases, providers, releases, resources, effects, and scheduler. Add correlation gaps to tests. |
| V7C-2 | Durable incident record | QUEUED | Event-derived lifecycle with fresh-process reconstruction. |
| V7C-3 | Deterministic anomaly detector | QUEUED | Rules over durable events and synthetic metrics. |
| V7C-4 | Capability-level suspension | QUEUED | Scoped state changes with recovery criteria and STOP precedence. |
| V7C-5 | Blast-radius dependency graph | QUEUED | Small resource -> connector -> capability -> mission graph. |
| V7C-6 | Diagnostic playbook | QUEUED | Read-only checks before any bounded repair. |
| V7C-7 | Bounded auto-repair | QUEUED | Owned worker/lease/cache/effect repairs with attempt budgets. |
| V7C-8 | Release degradation and rollback | QUEUED | Disposable freeze, PREVIOUS validation, rollback, health proof. |
| V7C-9 | Provider/network continuity | QUEUED | Deterministic Supervisor and safe work survive provider failure. |
| V7C-10 | Database/durable-state faults | QUEUED | Lock, mid-transaction, corrupt copy, migration, WAL, restore fixtures. |
| V7C-11 | Duplicate/concurrency faults | QUEUED | Authority bounded across duplicate wake, PID, lease, and verdict races. |
| V7C-12 | Effect ambiguity | QUEUED | Freeze, reconcile, classify; never blind retry or compensation. |
| V7C-13 | Resource/cost anomaly | QUEUED | Synthetic runaway, memory, quota, and retry-spike containment. |
| V7C-14 | V4/V6 output contract | QUEUED | Structured incident projection only. |
| V7C-15 | Restart continuity lab | QUEUED | Two-process/two-phase disposable restart harness. |
| V7C-16 | Bounded fault matrix | QUEUED | Expected/actual/evidence/containment/recovery/verdict for all rows. |
| V7C-17+ | Next evidenced resilience gap | NOT STARTED | Continue only if the final gate still has an evidenced high-value gap. |

