# Emergency Stop And Recovery Contract

Status: DESIGN ONLY - NOT INSTALLED

## 1. Objective

The owner must be able to freeze JARVIS even when Hermes, every model provider,
the command center, or the network is unavailable.

## 2. Independent Stop Inputs

V1 acceptance requires multiple deterministic inputs:

- CLI command handled by the Supervisor;
- sentinel file at `%HERMES_HOME%\foundry\EMERGENCY_STOP`;
- local UI or tray control when that surface exists;
- owner hotkey when implemented and tested.

Natural-language `STOP EVERYTHING` may create the same authenticated stop
event, but it is not the only mechanism.

## 3. Freeze Sequence

On the first valid stop signal, the Supervisor must atomically or idempotently:

1. persist `FREEZE_REQUESTED` with source and time;
2. reject new ActionEnvelopes;
3. suspend executive wakeups that create work;
4. cancel queued missions and new worker dispatch;
5. revoke capability leases and prevent renewal;
6. disable candidate promotion and external actions;
7. request cancellation from controllable workers;
8. terminate workers that exceed the bounded cancellation timeout;
9. preserve state, logs, and redacted evidence;
10. enter `FROZEN` and report what may still be in flight.

Repeated stop signals are harmless and produce no duplicate destructive work.

## 4. Freeze Is Not Rollback

The Supervisor does not automatically reverse every completed mutation. It
first classifies effects:

- never started;
- in flight and cancelable;
- completed and reversible;
- completed with compensating action;
- irreversible or externally uncertain.

Rollback begins only under deterministic pre-approved policy or an owner
decision. The system never claims an irreversible effect was undone.

## 5. Recovery Preconditions

Resume requires:

- stop source cleared through an authenticated owner path;
- event and database integrity PASS;
- capability grants revalidated;
- registered resources reconciled;
- uncertain external effects resolved;
- failed component health verified;
- incident severity and required owner decision recorded.

Removing the sentinel file alone does not resume execution.

## 6. Required Tests

- Stop while idle.
- Stop during worker execution.
- Stop during an Obsidian write.
- Stop after Hermes termination.
- Stop during provider outage.
- Stop with the UI closed.
- Stop repeatedly and concurrently.
- Restart Supervisor while sentinel remains present.
- Attempt capability renewal while frozen.
- Attempt candidate promotion while frozen.

Each test reconciles events, processes, leases, filesystem state, and claimed
outcome.
