# V5-9 Communication / Calendar Adapter Contracts

Verdict: PASS for local fake/test providers; live activation is intentionally
not attempted.

## Implementation

`CommunicationConnector` and `CalendarConnector` use in-memory fake providers
with explicit target, provenance, identity, idempotency, provider-id, status,
reconciliation, and compensating-action fields. Bodies/titles are never put in
attempt evidence; only hashes and provider references are retained.

## Disposable proof

- Mail requires one exact recipient and a non-empty transparent sender
  identity (`JARVIS (automated)`) plus content provenance. The fake provider
  returns a message id; same-key replay is recorded without a second send.
- A timeout-after-effect leaves `UNKNOWN`; provider reconciliation confirms
  the message, retry replays it, and `withdraw` transitions the provider
  status to `WITHDRAWN` with one send total.
- Calendar creation requires an allow-listed exact calendar id, explicit
  times, identity, and provenance. It returns a provider event id and supports
  cancel/withdraw reconciliation. An unregistered calendar target is refused.
- Conflicting idempotency payloads are failed closed. No SMTP, calendar API,
  account, invite, credential, or live communication exists in this proof.

## Verification

```text
python -m pytest -vv tests/foundry/test_communication_contracts.py
3 passed in 1.82s

python -m pytest -q tests/foundry/test_communication_contracts.py \
  tests/foundry/test_connector_contract.py tests/foundry/test_connectors.py \
  tests/foundry/test_effects.py tests/foundry/test_correlation.py \
  tests/foundry/test_crash_recovery.py
exit 0

python -m compileall -q src/jarvis_foundry/connectors \
  tests/foundry/test_communication_contracts.py
exit 0
```
