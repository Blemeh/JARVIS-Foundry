# R1 purpose acceptance protocol

`jarvis-purpose-protocol-v1` is an implementation-independent subprocess
boundary. The evaluator writes one canonical UTF-8 JSON request to an adapter's
standard input. The adapter writes one JSON response to standard output. An
adapter needs only a JSON parser, a way to inspect its own system, and the
ability to place content-addressed evidence snapshots below the request's
`run_root`; it does not import this repository.

The request embeds all ten frozen journey contracts and their SHA-256 hashes.
The evaluator validates the frozen local copies before creating the request,
then verifies the embedded objects again. It also freezes
`requested_proof_scope`, `challenge_nonce`, `evaluator_authority_id`, the exact
`OBSERVER_REGISTRY.json` hash, and an optional evaluator-selected `observer_id`.
The response deliberately has no aggregate-verdict field: the evaluator derives
each journey result and the aggregate from contract targets, budgets,
content-addressed evidence, control checks, failure injections, outcomes, and
the evaluator-requested scope. An adapter's scope label is only an echo and
cannot promote its own evidence.

Unknown fields, duplicate or unknown journeys, invalid status words, stale or
unidentifiable evidence, target substitution, evidence hash mismatch, files
outside `run_root`, budget overflow, a shared actor/verifier run identity,
missing after-state, failed recovery/rollback/compensation, contradiction loss,
forged owner control, duplicate effects, and every forbidden financial effect
fail closed. A subprocess timeout, non-zero exit, absent executable, malformed
JSON, or missing protocol endpoint becomes a structured `FAIL`; none is allowed
to crash the runner or become `PASS` merely because a process exited zero.

The two proof scopes are intentionally different. `HARNESS_SELF_TEST` permits
real temporary-directory file effects only and always emits
`product_claim_status: NOT_TESTED`. `PRODUCT_LIVE` fails closed unless the
evaluator invokes a separately registered observer subprocess. The empty
registry shipped by R1 therefore exposes the interface without fabricating a
positive live run. Files beneath `run_root` are evidence copies only and never
count as live targets, receipts, providers, or observations.

For a registered live run, the registry binds an observer ID to its artifact
path, verified artifact SHA-256, exact command template, and the canonical
target/account-or-service identities it may inspect. The evaluator hashes the
actor command artifacts and canonical actor response, issues separate actor and
observer execution identities from its authority and challenge, then sends an
`jarvis-purpose-observer-request-v1` request directly to the registered command
without a shell. A valid `jarvis-purpose-observer-response-v1` attestation must
echo those evaluator-issued bindings and independently bind every target's
canonical identity, account or service, source-identity digest, external
locator, before/after hashes, effect or provider receipt, and idempotency key.
It also supplies observation-receipt IDs for failure injection, restart,
duplicate delivery, recovery, rollback, compensation where required,
usefulness, after-state, and STOP/veto semantics. Subject-selected or
subject-registered observers, different identity strings, rehashed evidence,
booleans, and listed injection IDs do not establish live proof.

Every journey carries one receipt per frozen canonical target. `PASS` requires
`status_detail: null`. Each of `BLOCKED`, `NOT_TESTED`, `WAITING`, and `FAIL`
requires an exact, unknown-field-rejecting `status_detail` with a controlled
reason code, explanation, affected criterion IDs, and content-addressed evidence
IDs linked to those criteria. In addition:

- `BLOCKED` names the unavailable external dependency, the evidence proving
  unavailability, retry policy, and unblock condition.
- `WAITING` names a durable event and source, resume condition, and either
  event-driven resumption or a bounded future check.
- `NOT_TESTED` names the exact unexecuted criteria and why they were not run.
- `FAIL` names the failed criteria, failure class, and observed failure evidence.

The referenced evidence after-state contains a matching machine-readable
`status_observation`; dangling references, blind status flips, unchanged
all-success outcomes, mismatched details, and unknown fields fail closed.

Canonical CLI form:

```text
C:\Python313\python.exe scripts\remaster\r1\run_acceptance.py --purpose-root docs\remaster\r1 --evaluation-time 2030-01-01T00:00:00Z --proof-scope HARNESS_SELF_TEST --run-root <isolated-directory> --output <result.json> -- <adapter executable and arguments>
```

The command after `--` is passed directly to `subprocess.run` without a shell.
Omit `--run-root` for an automatically cleaned temporary run. Output uses
sorted-key canonical JSON and contains no random temporary path or wall-clock
duration, so identical contracts, request ID, evaluation time, and adapter
behavior produce byte-identical verdicts.

`PRODUCT_LIVE` additionally requires `--observer-id` naming a pre-existing
entry in the evaluator-controlled registry. The adapter never receives a way to
add, replace, or select that registration.
