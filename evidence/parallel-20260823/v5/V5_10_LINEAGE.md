# V5-10 Cross-System Entity and Effect Lineage

Verdict: PASS for exact disposable lineage records and ambiguity handling.

## Implementation

`connectors.lineage` builds on the existing append-only entity correlation
stream. A deterministic lineage id binds resource, mission, connector request,
effect ticket, optional external object id, evidence refs, source refs,
outcome, confidence, and ambiguity reason. Queries derive latest state from
`correlation.lineage_recorded` events; no second mutable registry is added.

## Proven behavior

- Identical full payloads deduplicate; the mission query returns one durable
  record containing provider object and evidence refs.
- A `COMPLETED` record without an external/provider id is rewritten at the
  boundary as `UNKNOWN` with `missing_provider_id` and zero confidence.
- Exact external-object lookup returns one record, no record for stale ids,
  and raises `ambiguous` when two lineage identities claim the same provider
  object. There is no fuzzy or same-name authority path.
- Existing entity matching keeps duplicate Unicode names as separate
  candidates, and an already-bound `(system, source_ref)` is refused rather
  than destructively merged.

## Verification

```text
python -m pytest -vv tests/foundry/test_connector_lineage.py
3 passed in 2.07s

python -m pytest -q tests/foundry/test_connector_lineage.py \
  tests/foundry/test_correlation.py tests/foundry/test_connector_contract.py \
  tests/foundry/test_effects.py tests/foundry/test_crash_recovery.py \
  tests/foundry/test_exactly_once_resume.py
exit 0

python -m compileall -q src/jarvis_foundry/connectors \
  tests/foundry/test_connector_lineage.py
exit 0
```
