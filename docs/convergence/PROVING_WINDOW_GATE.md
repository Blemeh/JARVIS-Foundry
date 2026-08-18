# Proving Window Gate

Program: JARVIS Post-V7 Remaster -> Canonical Convergence
Slice: post-promotion honest proving window
Executable gate:
[`tests/convergence/test_proving_window_gate.py`](../../tests/convergence/test_proving_window_gate.py)

## Contract

The proving window is Supervisor-owned and append-only. Every transition is
a ledger event on the existing canonical `FoundryDatabase` / `Event` APIs:

| Event | Meaning |
|---|---|
| `proving.window_registered` | Supervisor opens a window with real duration/mission criteria |
| `proving.window_checkpoint` | Durable state snapshot with replayable status evidence |
| `proving.window_closed` | Terminal `PASSED` / `FAILED` / `SUSPENDED` decision by Supervisor |

The implementation lives in `src/jarvis_foundry/proving.py`. No schema
migration is needed: all projections are replay-derived from the event log,
so a fresh database reopen yields the same status (restart-safe).

## No Fabricated Time

Elapsed days are UTC calendar days that have actually completed
(`observed_day - start_day > 0`). A day is healthy only when the ledger
contains real evidence for that day:

- at least one `supervisor.heartbeat`;
- no `supervisor.degraded` event;
- no `supervisor.stopped` event;
- no `emergency_stop.frozen` event;
- no active `STOP_EVERYTHING` / `PAUSE` directive overlapping the day.

Missing, degraded, stopped, and STOP/PAUSE days are counted as excluded with
their reason. No wall-clock fabrication and no synthetic heartbeat are ever
accepted.

## Verified Missions

A mission counts toward the proving requirement only when durable ledger
state contains:

- the mission row (`mission.created`);
- at least one evidence bundle (`evidence.created`);
- a `PASS` verdict (`verification.passed`) whose verifier run is distinct
  from the builder run, so a worker can never verify itself.

## Honest Status

`window_status(...)` returns `READY` only when the real healthy-day and
verified-mission counts meet the registered criteria; otherwise it returns
`WAITING` with the exact deficit. `close_window(... outcome="PASSED")`
refuses to close unless the criteria are genuinely met, and `FAILED` /
`SUSPENDED` closes require an explicit Supervisor reason.

Provider readiness, voice, UI, and external connector effects are always
reported as `NOT_TESTED` boundaries in every snapshot. This gate proves the
durable contract, not live provider effects or elapsed real time.

## Local Evidence

Run from the isolated proving worktree with the convergence venv:

```powershell
C:\temp\jarvis-convergence-venv\Scripts\python.exe -m pytest `
  tests\convergence\test_proving_window_gate.py -q -ra
```

Expected result on this candidate: 8 tests pass (registration/idempotency,
non-Supervisor refusal, restart-safe replay, healthy-day counting, degraded
and STOP exclusion, independent mission counting, WAITING/PASSED close, and
NOT_TESTED boundaries).
