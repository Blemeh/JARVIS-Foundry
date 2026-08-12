# WP8 F5 — Network Outage During a Provider-Required Step

Status: **PASS** (2026-08-12; repair + regressions + live staged experiment;
independent verifier `deleg_<pending>`)
Gate: `docs/architecture/04_V1_ACCEPTANCE_GATE.md` §7 F5
Execution: JARVIS executive continuation run (owner redirect 2026-08-12)
Finding: **FIND-2026-08-12-04** (adapter exit-0 failure-report classification)

## 1. Gate Criterion and Pass Condition

> F5 — Network outage. Disconnect network during a provider-required step.
> Pass condition: local state persists, network work waits with bounded
> retry, local eligible work may continue, and reconnection resumes once.

## 2. Finding (FIND-2026-08-12-04): exit-0 failure report = false success

The staged probe (real `.h` checkout, real oneshot, isolated home copy)
discovered the failure signature the adapter did not handle:

- The real Hermes CLI resolves the opencode-go endpoint from the
  **credential-pool entry in `auth.json`** (verified: `base_url:
  https://opencode.ai/zen/go/v1`; `config.yaml model.base_url` and the
  `OPENCODE_GO_BASE_URL` env var are NOT the runtime endpoint for this
  provider — probe iterations 1–3 proved each channel ignored).
- With the endpoint pointed at a dead `127.0.0.1` port (ECONNREFUSED —
  the network-layer failure signature), the CLI retries internally
  **3 times** (bounded), then prints to **STDOUT**:
  `API call failed after 3 retries: Connection error.` and **exits 0**
  (verified against `.h agent/conversation_loop.py:5854`;
  `_final_response = f"API call failed after {max_retries} retries: ..."`).
- The adapter's `returncode != 0` check therefore recorded the outage as
  **COMPLETED with the error text as "evidence"** — a fabricated success
  that would advance a mission on no evidence.

Recovery analysis: the provider was never reachable; this is provider
unavailability, not a session result. The adapter must classify the CLI's
known failure-report prefixes as `ProviderUnavailable` so the router
fallback chain engages (F6 semantics) and exhaustion becomes an honest
BLOCKED state (F5 semantics).

## 3. Repair

`src/jarvis_foundry/roles/adapter.py`:

- New `FAILURE_REPORT_PREFIXES = ("API call failed after ",
  "Billing or credits exhausted:")` — the two exit-0 failure-report
  forms emitted by `conversation_loop.py:5854`.
- After the non-zero-exit check, the adapter inspects the trimmed first
  stdout line; a prefix match raises `ProviderUnavailable("oneshot
  reported provider failure (exit 0): ...")` instead of returning
  COMPLETED. Non-zero exits are unchanged (F2 semantics preserved:
  `hermes_oneshot_failed`).

Regression tests: `tests/foundry/test_f5_network_outage.py` (6 tests):
exit-0 failure report → honest BLOCKED/provider_unavailable, run row
FAILED, lease released, exactly ONE attempt (bounded), never COMPLETED;
reconnect → fresh dispatch COMPLETES with the failed row preserved;
configured fallback engaged and recorded on outage (also F6 unit
evidence); healthy exit-0 transcript still COMPLETES (no false positive);
non-zero exit still FAILED hermes_oneshot_failed; billing-block report
also classified provider_unavailable.

## 4. Experiment (live staged, live DB untouched)

`var/evidence/wp8-gate/f5/run_f5_experiment.py` — snapshot copy of the
live Foundry DB; REAL `.h` checkout; REAL oneshot dispatches; isolated
home copies of `var/wp8-hermes-home` (never the live home):

| Phase | Injection | Expected | Actual | Verdict |
|---|---|---|---|---|
| A | baseline, endpoint healthy | COMPLETED | COMPLETED (7.6 s, evidence "OK", real provider) | PASS |
| B | endpoint → dead 127.0.0.1:59999 (ECONNREFUSED) | honest wait with bounded retry; local work continues; no false success | **BLOCKED** `provider_unavailable` (54.8 s = CLI's own 3 bounded retries); run row FAILED provider_unavailable; exactly 1 dispatch attempt; lease released; **live daemon wrote 2 events during the outage** (local eligible work continued); zero secret leaks | PASS |
| C | endpoint restored, fresh dispatch | reconnection resumes once | COMPLETED (9.5 s); failed row preserved (truth kept) | PASS |

State: run rows `run-f5-a` COMPLETED / `run-f5-b` FAILED
provider_unavailable / `run-f5-c` COMPLETED; role leases 0; quick_check ok;
`f5-experiment-summary.json` overall **PASS**.

## 5. Bounded-Retry Semantics (as designed)

- The CLI itself retries 3× per oneshot (observed: 54.8 s outage
  latency), then reports.
- The dispatcher makes exactly ONE adapter attempt per provider per
  dispatch (probe: `run_f5_b_attempts == 1`); on `ProviderUnavailable`
  it tries configured fallbacks, then lands in BLOCKED
  provider_unavailable — an event-driven wait, never a busy loop.
- Recovery is the next mission-cycle dispatch (fresh worker), proven by
  Phase C.

## 6. Live-System Protection

- Live Foundry DB: read-only during the experiment (snapshot via sqlite
  backup API; live event counts sampled read-only around Phase B).
- Live Hermes gateway: never launched, never touched (the oneshot runs
  against the staged homes only).
- Staged homes carry a copy of the scoped credential material; fragment
  leak checks on raw captured bytes printed only booleans (false, false).
- `var/wp8-hermes-home` (the isolated dispatch home) unchanged — hashes
  recorded in the freeze manifest.

## 7. Residual Risk / Observations

- The failure-prefix list is a whitelist of the CLI's KNOWN exit-0 error
  forms. If the CLI later emits a new error format, it would again look
  like a completion until the prefix list is extended. **This risk
  materialized the same day**: FIND-2026-08-12-05 (F6) found the
  `HTTP 401: Model … is not supported` exit-0 shape and the whitelist was
  extended with the full source-verified set (see `WP8_F6_FAULT_INJECTION.md`).
  Mitigation noted (out of Foundry's control): the CLI should exit
  non-zero or write a structured error for oneshot failures; recorded for
  the V2+ adapter boundary.
- Same-provider verifier limitation applies (opencode-go family) unless a
  distinct provider verifier is available.
- Real oneshot cost in Phases A/C: usage reports read 0.0 USD (recorded
  as observed).

## 8. Evidence Index

- `var/evidence/wp8-gate/f5/`: run_f5_experiment.py, f5-experiment-summary.json,
  foundry-suite.txt (full suite result), freeze manifest (sha256).
- `tests/foundry/test_f5_network_outage.py` (6 regression tests).
- Ledger: `fault_injection.completed:F5:2026-08-12` (+ verifier verdict
  events when the independent review records them).
