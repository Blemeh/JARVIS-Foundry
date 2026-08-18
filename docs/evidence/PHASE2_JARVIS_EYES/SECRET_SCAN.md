# SECRET SCAN — JARVIS EYES PHASE 2

## Synthetic values (fixtures only, never real credentials)

```text
api_key=FAKE_JARVIS_TEST_SECRET_123
password=FAKE_PASSWORD_456
token=FAKE_TOKEN_789
authorization=Bearer FAKE_BEARER_ABC
```

## Method

Feed the synthetic values as screen text fragments through the integrated
path (`run_cycle` with real privacy policy + existing redactor), then scan
every durable target: Foundry DB file bytes, all event payloads, and the
cycle report JSON (probe log/report representative).

## Result

- VERDICT: PASS (zero retained synthetic values in any durable target)
- Raw evidence: `var/phase2-eyes/secret_scan.json`
  (sha256 `e7f462eb3b220a4567595f999f097430e1dc3887ccb91db3d1d8abd12c75562f`)
- The synthetic values appear ONLY in the authorized test fixture /
  probe script sources (they are fixture inputs, not retained outputs).
