# V5-3 Effect Ticket and Journal Hardening

STATUS: PASS
PHASE: V5_MULTI_SYSTEM_INTELLIGENCE
SLICE: V5-3
OBJECTIVE: Prevent duplicate external invocation across concurrent workers and
keep crash and timeout outcomes honest using the existing effect journal.

## Requirements

The slice maps to the attached V5 objective Job V5-3, the existing V5-S6
effect driver, R-IDM-01 retry safety, R-EXT-01 reconciliation boundaries, and
the STOP/veto and ActionEnvelope executor checks. It reuses the existing
`system.action_started` and `effect.frozen` terminology. No second journal or
mission engine was introduced.

## Baseline and diff

Baseline evidence commit: `33af953`
Verified source commit: `aa01401`
Branch: `candidate/v5-multisystem-20260823`
Worktree: `C:\temp\JARVIS-V5-MultiSystem`

## Implemented behavior

- `execute_effect` claims a fresh effect inside the existing `BEGIN IMMEDIATE`
  ledger transaction before invoking the external callable.
- Two workers racing on the same effect produce one invocation. The competing
  worker returns `UNKNOWN` while the durable claim is active.
- A claim has a durable token, start time, and configurable expiry. A missing
  or expired claim is stale and freezes for review. It is never re-executed.
- A crash after the external callable and before the terminal event is proven
  with a short-lived claim. Retry freezes after expiry and the callable count
  stays one.
- A timeout writes `effect.reconcile_pending` and `effect.frozen` with a
  redacted reason and explicit unknown provider state. It is not recorded as a
  clean `FAILED` result.
- Existing terminal monotonicity, ticket scope, fresh STOP/veto checks,
  compensation, rebuild, and provider-reconciliation APIs remain in place.

## Tests and verification

Fresh-process effects verifier:

`set PYTHONPATH=src && set PYTHONHASHSEED=0 && python -m pytest -vv tests/foundry/test_effects.py`

Result: 30 passed in 7.63s, exit 0.

The affected connector, effect, correlation, crash, exactly-once, pipeline,
perception, procedural, Git, and R9 regression command exited 0. Compile-only
validation for `effects.py` and its tests exited 0. Ruff was not installed on
this host; no Ruff result is claimed.

## Disposable and live boundaries

The race and crash probes use an isolated SQLite database and an in-memory
callable. No live database, scheduler, provider, credential, remote, account,
or communication was touched. Provider-side reconciliation remains
`NOT_TESTED`; timeout handling now preserves that uncertainty instead of
manufacturing a failure claim.

## Known limitation

The claim lease is time-based and has no heartbeat extension yet. Long-running
connectors must supply a safe `claim_ttl_s`; a future connector runtime slice
can add durable lease renewal if a real adapter needs it. This is recorded as
an open V5-3 follow-up, not hidden as completion.

## Rollback and next slice

Rollback is a normal revert of `aa01401`. The next safe slice is V5-4,
resource discovery and registration, using deterministic lane-owned records
without treating discovery as authority.
