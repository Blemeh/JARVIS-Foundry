# Overnight release/runtime implementation queue

Lane: `candidate/overnight-release-candidate`

Baseline: `4ce42f809fcfe054488d787c0240ed0990c784b4` (`origin/main` at orientation was `ceb90d7d201b4775757cae0f7e03ae1e2d6f22e2`)

Protected: canonical `main`, `origin/main`, production/live DB and services, Task Scheduler production tasks, provider accounts, credentials, and other registered worktrees.

Data shapes for the runtime-control work are `RuntimeControlRequest` and the derived `RuntimeControlState`. Requests are durable, idempotent records keyed by owner request ID. State is one of `STOPPED`, `RUNNING`, `BLOCKED`, `DEGRADED`, or `UNKNOWN`, with release, process, lease, and reconciliation identity attached when known.

| Job | Status | Exact files | Tests / harness | Commit |
|---|---|---|---|---|
| 1. Reconcile checkpoint and implement next runtime capability | PASS | `src/jarvis_foundry/supervisor/control.py` (new), `src/jarvis_foundry/supervisor/service.py`, `src/jarvis_foundry/supervisor/run.py`, `src/jarvis_foundry/supervisor/worker.py`, `src/jarvis_foundry/cli/main.py`, `tests/foundry/test_runtime_control.py` (new), `scripts/runtime_control_repro.py` (new) | Focused lifecycle/control tests, broader supervisor/reconciliation/headless/release regression, disposable runtime-control harness with clean Windows cleanup | `90963d6` |
| 2. Restart and owner-control state machine | PASS | `src/jarvis_foundry/supervisor/control.py`, `src/jarvis_foundry/supervisor/service.py`, `src/jarvis_foundry/supervisor/run.py`, `src/jarvis_foundry/supervisor/worker.py`, `tests/foundry/test_runtime_control.py`, `scripts/runtime_control_repro.py` | Durable generation reuse, duplicate live-trigger STOP, lease-loss heartbeat, stale PID identity, registration-failure cleanup, disposable adversarial harness | `cedd1e1` |
| 3. Release identity, promotion, and rollback hardening | IN_PROGRESS | `src/jarvis_foundry/deployment/release.py`, installer/launcher/status, focused release tests and disposable build/install/rollback harness | build → staging install → validate → controlled start → rollback; fail-closed identity faults | pending |
| 4. Scheduler and headless process reliability | PENDING | `src/jarvis_foundry/installer.py`, `src/jarvis_foundry/platform/windows_subprocess.py`, scheduler tests/harness | XML, ownership/prefix guards, host probe if available; no production task mutation | pending |
| 5. Provider/voice/delivery boundary | PENDING | owned provider/voice/delivery boundaries, safe fake adapters, focused replay/failure tests | timeout, unknown cost, cancellation, retry, idempotent replay; no billable provider | pending |
| 6. Health, telemetry, and operational observability | PENDING | owned runtime health/doctor/release views, diagnostic harness, focused tests | typed `UNKNOWN`/`DEGRADED`/`BLOCKED`, correlated redacted identity output | pending |
| 7. Fault and concurrency hardening | PENDING | owned runtime boundaries, deterministic fault/concurrency tests and harness | launch/PID/lease/restart/rollback/scheduler/delivery races | pending |
| 8. Select next missing runtime capability | PENDING | selected owned runtime files after fresh source/docs review | focused tests, disposable proof, local commit | pending |

## Checkpoint rows

| Iteration | Evidence-backed change | Predicate movement | Status | Commit |
|---|---|---|---|---|
| 0 | Confirmed `4ce42f8` and inspected its diff. Found owner-control request gap behind the implemented-but-unproven restart boundary. | Queue created; no source claim yet. | PASS | `pending local checkpoint` |
| 1 | Added durable idempotent owner requests, conservative runtime state projection, pre-lease claims, stale lease/PID identity reconciliation, request-only CLI control, process-start registration cleanup, and a disposable state-machine harness. | Owner request and restart-control source path moved from implemented-unproven to locally demonstrated. Live promotion and production behavior remain unperformed. | PASS | `90963d6` |
| 2 | Made lifecycle generations durable across reused instance IDs, prevented heartbeats from reacquiring stolen leases, recorded lease-loss degradation, and extended the harness with duplicate-trigger and generation-reuse adversarial paths. | Restart/owner-control behavior now has bounded local proof for duplicate requests, lease ownership loss, stale identity, process-start registration failure, and restart reuse. Live control remains unperformed. | PASS | `cedd1e1` |
