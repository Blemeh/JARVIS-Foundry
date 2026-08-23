# V5-4 Resource Discovery and Registration

Verdict: PASS for the isolated discovery slice.

## Scope

The candidate now has one deterministic discovery path built on the existing
`project.registered` event and `resources` projection. There is no second
resource registry and discovery never grants authority or performs a provider
call.

## Proven proofs

- Filesystem roots are canonicalized with `Path.resolve`, receive a stable
  `resource-<sha256>` identity, and can be restricted to an owned bounded root
  using `os.path.commonpath` rather than a string-prefix test.
- Every record carries endpoint identity, readiness, health, allowed action
  semantics, provenance/observation mode, observed capability states, rollback
  strategy, and reconciliation strategy. The full payload remains in the
  append-only event ledger; the existing `resources` projection stores the
  baseline and capability JSON.
- HTTP and browser endpoints are parsed only. They report `NOT_TESTED`/
  `UNKNOWN`, mark network probing as `NOT_TESTED`, and reject userinfo or
  password-bearing URLs. No credential lookup, browser launch, or network
  request is made.
- Re-registering an identical record deduplicates through the full payload
  hash. A changed health observation creates a new event and updates the
  projection without creating a second resource row.

## Verification

```text
python -m pytest -vv tests/foundry/test_connector_resources.py
4 passed in 2.01s

python -m pytest -q tests/foundry/test_registration.py \
  tests/foundry/test_connector_resources.py tests/foundry/test_connectors.py \
  tests/foundry/test_connector_contract.py tests/foundry/test_connector_activation.py \
  tests/foundry/test_effects.py tests/foundry/test_correlation.py \
  tests/foundry/test_crash_recovery.py tests/foundry/test_exactly_once_resume.py
exit 0

python -m compileall -q src/jarvis_foundry/connectors \
  tests/foundry/test_connector_resources.py
exit 0
```

`ruff` is not installed in this environment, so lint is recorded as not
tested rather than inferred from passing tests.

## Boundaries

This is source and disposable-DB evidence only. No live endpoint, account,
credential, scheduler task, production database, or external provider was
used.
