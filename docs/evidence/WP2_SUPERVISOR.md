# WP2 Evidence — Independent Supervisor And STOP

Status: IN PROGRESS — independent verification COMPLETED (PASS), gate close in progress
Updated: 2026-08-11T20:05+02:00

## Implemented

- `WorkerController` (`src/jarvis_foundry/supervisor/worker.py`): bounded
  worker subprocess spawn, durable lease tracking, lease renewal, graceful
  termination escalating to kill, expiry reaping, natural-exit cleanup,
  terminate-all. Double-spawn of a live run is rejected (any owner); a failed
  spawn releases its lease. Hermes-independent.
- `StopControls` (`src/jarvis_foundry/supervisor/controls.py`): hidden
  message-only window hosting a `RegisterHotKey` emergency hotkey (Alt+F12)
  and a Windows tray icon with Stop/Resume/Exit menu; both write/clear the
  deterministic sentinel via `EmergencyStop`. Pure ctypes, no dependencies;
  hotkey collision fails closed with the real registration error.
- `EmergencyStop` fail-closed hardening: unparseable OR incomplete sentinels
  produce complete deterministic records, so STOP always reaches the freeze
  state machine (no KeyError on a missing `reason`).
- Supervisor service (`src/jarvis_foundry/supervisor/service.py`):
  - startup reconciliation gate — inconsistent state yields `DEGRADED`
    (with a durable `supervisor.degraded` event) instead of RUNNING;
  - worker reaping on every heartbeat; all workers terminated on freeze;
  - doctor reports reconciliation and active workers.
- CLI: `--home` accepted before or after the subcommand (argparse parent
  parser with SUPPRESS defaults).

## Automated Evidence

`python -m pytest tests/foundry -q`:

```text
78 passed
```

New coverage since WP1 close (23 tests): supervisor recovery (forced-death
reconstruction, lease reclaim once, degraded start/repair, doctor), worker
control (spawn/lease/heartbeat/terminate/reap/terminate-all, double-spawn
rejection, failed-spawn lease release), STOP controls (hotkey, tray
stop/resume/exit, close idempotency, hotkey collision fail-closed, sentinel
determinism), incomplete-sentinel freeze, CLI (both --home positions, every
subcommand end-to-end).

## Independent Verification (deleg_759bb4b0, adversarial, fresh session)

Verdict: **PASS on all 6 gate criteria** (A1-A6). 70/70 repo tests reproduced;
40-check scratch adversarial suite (38 pass + 2 documenting real non-gate
defects); real Win32 message-loop verification of tray/hotkey including
cleanup; zero orphan processes; no scope violations; no canonical DB touched.

Findings found by the verifier and REPAIRED (each with a regression test):

1. CLI `--home` after subcommand exited rc=2 -> parent-parser fix + 5 CLI
   tests (test_cli.py).
2. Silent double-spawn of a live run_id (same-owner lease defeated the
   guard) -> spawn() now rejects any live lease + test.
3. Failed spawn leaked its lease -> spawn() releases on Popen failure + test.
4. Parseable sentinel missing `reason` crashed heartbeat with KeyError ->
   EmergencyStop.read() fills deterministic defaults + freeze-reached test.

Independence limitation (recorded per architecture contract 14.3): same
provider family as builder (deepseek-v4-flash/opencode-go), fresh isolated
session, no builder reasoning.

## Real Staging Evidence

Live exercise on `var/wp2-staging/foundry.db` (project-local staging):

```yaml
supervisor_start: RUNNING
worker_tracked: demo-worker
doctor_quick_check: ok
doctor_consistent: true
tray_and_hotkey_registered: true (real desktop, closed cleanly)
sentinel_stop: FROZEN
workers_terminated_on_stop: true
cli_stop: sentinel written (source local_cli)
cli_resume: sentinel cleared
final_events: [supervisor.started, emergency_stop.frozen, supervisor.stopped]
```

All four STOP mechanisms proven against the same sentinel: sentinel file,
local CLI, tray menu, hotkey. STOP detection requires no Hermes, LLM,
provider, network, or UI (controls run on ctypes + filesystem only).

## Combined Baseline

- Foundry suite: 78 passed (fresh).
- Hermes baseline (`.h`, isolated HERMES_HOME): estop 24 passed; state/WAL/
  health suite 317 passed, 8 skipped (fresh re-run).
- Live Hermes and canonical Foundry databases untouched.

## WP2 Gate Criteria (CURRENT_WORK_PACKAGE.yaml)

| ID | Criterion | Verdict |
|---|---|---|
| A1 | Supervisor works with Hermes absent | PASS |
| A2 | Sentinel/CLI/tray/hotkey STOP produce freeze, reject new work | PASS |
| A3 | Freeze-before-rollback, no automatic mass rollback | PASS |
| A4 | Lease expiry reclaims dead worker lease once; expired-alive worker terminated | PASS |
| A5 | Forced death reconstruction, no duplicates | PASS |
| A6 | doctor reports health, quick_check, reconciliation | PASS |

## Remaining WP2 Gate Work

- Frozen-state snapshot + fresh verification battery at gate close.
- Record WP2 PASS verdict, update `PROJECT_STATE.md`, activate WP3.
- WP7 remains the installation gate: no startup task, tray, or hotkey is
  installed at logon until WP7 recovery/activation tests pass.

Verdict: NOT_TESTED until the frozen-state re-verification completes.
