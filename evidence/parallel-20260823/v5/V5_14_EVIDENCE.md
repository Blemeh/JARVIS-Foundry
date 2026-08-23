# V5-14 Durable Connector Attempt Evidence

Verdict: PASS for the highest-value remaining V5 gap selected after the
composed mission.

## Implementation

`connectors.evidence.record_attempt` bridges the versioned `ConnectorAttempt`
shape into the existing append-only event ledger as
`connector.attempt_recorded`. It persists mission/resource/effect-ticket
lineage, hashes and safe before/after state, provider reference, outcome,
reconciliation, replay, cost, and evidence refs—never a raw request payload.
Identical full payloads deduplicate; changed observations remain new history.

The composed mission records its HTTP observation and browser attempt through
this bridge, in addition to its three cross-system lineage records.

## Verification

```text
python -m pytest -vv tests/foundry/test_connector_evidence.py
1 passed in 2.0s

python -m pytest -q tests/foundry/test_connector_evidence.py \
  tests/foundry/test_v5_composed_mission.py
exit 0
```

The attempt record is read from a freshly reopened database, proving the
append-only evidence survives process restart. Secret-like or oversized
attempt evidence is refused before append.
