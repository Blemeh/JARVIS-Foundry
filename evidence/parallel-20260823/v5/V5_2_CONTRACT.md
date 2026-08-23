# V5-2 Connector Contract Normalization

STATUS: PASS
PHASE: V5_MULTI_SYSTEM_INTELLIGENCE
SLICE: V5-2
OBJECTIVE: Normalize the existing connector boundary without creating a
second mission engine or plugin framework.

## Requirements

The slice maps to the attached V5 objective Job V5-2, the existing V5-S4
connector contract, the V5-S6 idempotency and reconciliation boundary, and
the architecture ActionEnvelope/effect invariants. It adds one request shape
and one attempt shape. The registry remains the source of connector health and
quota. The effect journal remains the source of durable terminal accounting.

## Baseline and diff

Baseline evidence commit: `150b06c`
Verified source commit: `ee1e4fd`
Branch: `candidate/v5-multisystem-20260823`
Worktree: `C:\temp\JARVIS-V5-MultiSystem`

## Files changed

- `src/jarvis_foundry/connectors/contract.py` adds connector metadata,
  `ConnectorRequest`, `ConnectorAttempt`, request/attempt validation, and
  payload-free evidence serialization.
- `src/jarvis_foundry/connectors/registry.py` persists and reloads the new
  metadata through the existing append-only registration event.
- `src/jarvis_foundry/connectors/fakes.py` adds a disposable contract fake with
  idempotent replay, conflicting-payload refusal, timeout ambiguity,
  reconciliation, and cancellation.
- `tests/foundry/test_connector_contract.py` covers the normalized shape and
  fake behavior.

## Implemented behavior

- Target identity, action kind, capability, request idempotency, and SHA-256
  payload identity are validated before adapter work.
- Attempt identity must match the request exactly.
- Attempt outcomes are limited to `COMPLETED`, `FAILED`, `UNKNOWN`, and
  `CANCELLED`.
- Failed and unknown attempts carry an explicit error code.
- Cancellation is allowed only when the connector declares support.
- Cost values are finite and non-negative.
- Evidence serialization contains the payload hash, never the raw payload.
- A retry with the same idempotency key returns the recorded attempt without a
  second invocation. A changed payload under that key is refused.
- A fake timeout stays `UNKNOWN` until a reconciliation call confirms the
  system-side state. No second invocation is performed.
- The STOP policy metadata is fail-closed at `broker-and-driver`.

## Tests and verification

Fresh-process contract verifier:

`set PYTHONPATH=src && set PYTHONHASHSEED=0 && python -m pytest -vv tests/foundry/test_connector_contract.py`

Result: 6 passed in 14.97s, exit 0.

Affected connector/effect/pipeline/perception/procedural/Git regressions were
run in one changed-tree process and exited 0. Compile-only validation for
`src/jarvis_foundry/connectors` exited 0. Ruff was not installed on this host;
no Ruff result is claimed.

## Disposable and live boundaries

The fake is in-memory and makes no network, provider, credential, scheduler,
database, production-account, or communication call. No live state was
touched. Provider-side reconciliation remains `NOT_TESTED`; the fake proves
the contract's ambiguity and retry behavior only.

## Rollback and next slice

Rollback is a normal revert of `ee1e4fd`. The next safe slice is V5-3, which
will attack the existing effect ticket and journal against duplicate dispatch,
crash windows, timeout ambiguity, stale claims, concurrency, cancellation,
and restart reconciliation.
