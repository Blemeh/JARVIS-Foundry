# V5-5 Filesystem / Project Connector

Verdict: PASS for the disposable filesystem vertical slice.

## Implementation

`FilesystemConnector` is a contract adapter over the existing bounded
`FsConnector`; it does not introduce a second effect driver. It adds a
hash-only, root-local idempotency ledger, atomic replacement, readback,
restart replay, reconciliation, and snapshot restore.

## Disposable proof

- A lane-owned root accepts a `file.write`, and a subsequent `file.read`/
  snapshot reports the exact SHA-256 resulting state.
- Retrying the same request returns `COMPLETED` with `replayed=True` and does
  not rewrite the file (mtime remains unchanged). A new connector instance
  replays from the root-local hash ledger; if a crash occurred after file
  replacement but before ledger persistence, matching readback is also treated
  as a replay rather than a second write.
- A same-key/different-payload retry is `FAILED` with
  `idempotency_conflict`; a post-write tamper is `UNKNOWN` with
  `state_drift`, never silently overwritten.
- A captured snapshot restores the exact prior state, including deleting a
  file that did not exist before the effect.
- `Path.resolve`-based bounded resolution rejects traversal, absolute outside
  targets, and symlink escapes when symlink creation is available. The
  connector's own hash ledger is reserved. Mode-bit checks refuse read-only
  files before mutation; no credential or file content is placed in attempt
  evidence.

## Verification

```text
python -m pytest -vv tests/foundry/test_filesystem_connector.py
3 passed, 1 skipped in 2.09s

python -m pytest -q tests/foundry/test_filesystem_connector.py \
  tests/foundry/test_connector_activation.py tests/foundry/test_registration.py \
  tests/foundry/test_connector_resources.py tests/foundry/test_connector_contract.py \
  tests/foundry/test_connectors.py tests/foundry/test_effects.py \
  tests/foundry/test_correlation.py tests/foundry/test_crash_recovery.py \
  tests/foundry/test_exactly_once_resume.py
exit 0

python -m compileall -q src/jarvis_foundry/connectors \
  tests/foundry/test_filesystem_connector.py
exit 0
```

The symlink escape check is `SKIPPED` on this Windows host because symlink
creation is unavailable; regular traversal and absolute escape checks remain
covered by the existing activation suite. `ruff` is unavailable and remains
NOT_TESTED.

No live filesystem, credential, account, provider, scheduler, or production
database was touched.
