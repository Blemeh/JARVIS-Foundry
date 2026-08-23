# V5-12 Malicious Input / Secret-Leak Resilience

Verdict: PASS for the exercised connector boundaries.

## Hardened seams

- External page/message content is normalized through the existing safe-page
  boundary, bounded, HTML-escaped, and represented as `DATA_ONLY`; evidence
  carries only source and content hash, never text or owner authority.
- URL parsing refuses credentials, control characters, unsupported schemes,
  redirect cycles, and excessive hops. The HTTP connector uses a no-redirect
  opener and reports redirect responses as `UNKNOWN`/`redirect_refused`.
- HTTP payloads have a 256 KiB bound and reject secret-like assignments.
  Communication bodies/provenance reject the same class. Fake provider IDs
  must equal the connector-derived idempotency evidence; forged ids are
  refused.
- Malformed JSON remains `UNKNOWN`; timeout/connection ambiguity is never
  silently converted into no-effect authority.

## Verification

```text
python -m pytest -vv tests/foundry/test_connector_safety.py
4 passed in 2.35s

python -m pytest -q tests/foundry/test_connector_safety.py \
  tests/foundry/test_http_connector.py tests/foundry/test_browser_connector.py \
  tests/foundry/test_communication_contracts.py tests/foundry/test_connector_operations.py \
  tests/foundry/test_connector_lineage.py tests/foundry/test_connector_resources.py \
  tests/foundry/test_filesystem_connector.py tests/foundry/test_git_connector.py \
  tests/foundry/test_connector_contract.py tests/foundry/test_connectors.py \
  tests/foundry/test_effects.py
exit 0
```

The lane evidence tree was scanned for credential-like assignments and
private-key material; no findings were present. No live URL, account,
credential, provider, or production system was used.
