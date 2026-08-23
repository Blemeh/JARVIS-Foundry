# V5-8 Browser / Procedural Connector Boundary

Verdict: PASS for the harmless local synthetic browser surface; desktop
automation remains separately PROBING/BLOCKED as documented by the existing
OpenAdapt registry.

## Implementation

`BrowserConnector` sits above the V5 HTTP connector. It resolves a target from
an exact target id plus structural role/name, hashes the observed version and
control identity, validates parameterized actions, and delegates idempotency /
timeout / reconciliation to the HTTP adapter. It never treats coordinates as
authority.

## Disposable proof

- A local synthetic app exposes `/ui`, `/action`, and `/state`. The same
  structural target resolves to the same hash, and a parameterized increment
  executes once; replay of the same key is marked `replayed` with one provider
  mutation.
- A changed UI version returns `UNKNOWN`/`target_drift` before POST. A
  geometry-only target is rejected, and a STOP callback returns
  `CANCELLED` before any effect.
- A delayed response after the action lands returns `UNKNOWN`; the state
  oracle reconciles the key, after which no second browser action is sent.
- Existing OpenAdapt desktop probing is not promoted by this synthetic proof.

## Verification

```text
python -m pytest -vv tests/foundry/test_browser_connector.py
3 passed in 3.64s

python -m pytest -q tests/foundry/test_browser_connector.py \
  tests/foundry/test_http_connector.py tests/foundry/test_procedural_learning.py \
  tests/foundry/test_prompt3_capability_restoration.py \
  tests/foundry/test_connector_contract.py tests/foundry/test_effects.py
exit 0

python -m compileall -q src/jarvis_foundry/connectors \
  src/jarvis_foundry/procedural_learning tests/foundry/test_browser_connector.py
exit 0
```

No real browser account, desktop automation, credential, production service,
or external provider was used.
