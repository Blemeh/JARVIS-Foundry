# V5-11 Connector Health / Quota / Cost / Rerouting Truth

Verdict: PASS for event-derived operational status and bounded routing.

## Implementation

`connectors.operations` wraps the existing append-only connector registry. It
adds truthful `READY/HEALTHY`, `DEGRADED`, `BLOCKED`, `UNKNOWN`, `DISABLED`,
and `VETOED` handling, quota/cost samples, and a deterministic selector that
never hides the reason a preferred connector was rejected.

## Proven behavior

- A preferred connector sampled `BLOCKED` is skipped and an equivalent
  `READY` connector is selected with a `rerouted from ...` reason naming the
  outage.
- Quota usage at its limit reports `EXHAUSTED`; no selection can use it.
- With no cost sample and no verified cost metadata the state is `UNKNOWN`;
  an explicit known cost sample changes it to `KNOWN`.
- A `VETOED` health sample remains visible and prevents selection. Connector
  health and routing state are append-only/event-derived, not inferred from an
  import or class existence.

## Verification

```text
python -m pytest -vv tests/foundry/test_connector_operations.py
2 passed in 2.07s

python -m pytest -q tests/foundry/test_connector_operations.py \
  tests/foundry/test_connectors.py tests/foundry/test_connector_contract.py \
  tests/foundry/test_connector_resources.py tests/foundry/test_effects.py
exit 0

python -m compileall -q src/jarvis_foundry/connectors \
  tests/foundry/test_connector_operations.py
exit 0
```
