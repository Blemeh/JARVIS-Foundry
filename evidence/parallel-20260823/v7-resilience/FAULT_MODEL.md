# V7 Resilience Fault Model

The fault model is bounded and disposable. Faults are injected into fixture
databases, fake providers, owned worker processes, and temporary release roots.
No live production path is a test target.

## Failure classes

| Class | Representative faults | Required safety property |
|---|---|---|
| Control | STOP during worker, Supervisor crash, duplicate Supervisor | STOP remains independent; one authority survives. |
| Worker | crash, false success, stale lease, restart race | Work is recovered or honestly blocked without duplicate authority. |
| Provider | unavailable, timeout, partial outage, all providers unavailable | Deterministic control lives; reasoning work waits or blocks honestly. |
| Connector/effect | timeout-after-effect, lost local result, unknown remote state | Effect freezes and reconciles before any retry or compensation. |
| Durable state | lock, mid-transaction death, corrupt copy, migration/WAL failure, stale projection | Canonical event truth survives or fails closed; restore is verified. |
| Release | degraded health, missing PREVIOUS, incompatible schema, rollback failure | Freeze precedes rollback; recovery requires observed health. |
| Concurrency | duplicate wake, duplicate result/verdict, PID reuse, lease steal | Idempotency and process identity bound authority. |
| Resource/cost | task/process/memory growth, quota exhaustion, expensive retry loop | Only affected capability is suspended where possible. |

## Fault injection rules

1. Every fault has a deterministic name and a bounded attempt budget.
2. Every injected effect records whether it may have reached the fake remote.
3. A missing observation is `UNKNOWN`, never inferred success.
4. Evidence is append-only and redacted before reporting.
5. A fault test must verify containment and recovery, not only the exception.

