# V5-7 HTTP / Service Connector

Verdict: PASS for the in-process disposable HTTP service fixture.

## Implementation

`HttpServiceConnector` uses the standard-library HTTP client, canonical URL
parsing, explicit `Idempotency-Key` headers, bounded retry/backoff, provider
state reconciliation, and a per-instance circuit breaker. It stores hashes and
provider references in attempts rather than request bodies.

## Disposable proof

- `GET /state` and idempotent `POST /write` run against a local
  `ThreadingHTTPServer` only.
- The fixture applies a write and delays its response beyond the connector
  timeout. The connector returns `UNKNOWN`/`timeout`; reconciliation queries
  `/state`, confirms the same key, and a retry returns `COMPLETED`/`replayed`
  without a second provider mutation (`write_count == 1`).
- 429 responses retry only the bounded configured number of times and finish
  as `FAILED`/`rate_limited`; 5xx and malformed responses remain
  `UNKNOWN` because provider-side effect cannot be assumed absent.
- Connection loss moves health through `UNKNOWN` to `BLOCKED` after the
  configured threshold. An open circuit refuses further calls on that
  connector only; a separate healthy fixture remains `READY` and completes a
  write.
- No redirect-following, credential lookup, or external network call is part
  of the proof.

## Verification

```text
python -m pytest -vv tests/foundry/test_http_connector.py
3 passed in 4.11s

python -m pytest -q tests/foundry/test_http_connector.py \
  tests/foundry/test_connector_contract.py tests/foundry/test_connector_resources.py \
  tests/foundry/test_filesystem_connector.py tests/foundry/test_git_connector.py \
  tests/foundry/test_v3_git_reconcile.py tests/foundry/test_v3_fault_matrix.py \
  tests/foundry/test_effects.py
exit 0

python -m compileall -q src/jarvis_foundry/connectors \
  tests/foundry/test_http_connector.py
exit 0
```

No production service, credential, account, deployment, scheduler, or live
provider was touched.
