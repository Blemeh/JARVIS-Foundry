# WP8 F6 — Provider/Model Outage During a Provider-Required Step

Status: **PASS** (2026-08-12; FIND-2026-08-12-05 repaired; regressions +
live staged experiment; independent verifier verdict recorded with the F5
review / final gate review)
Gate: `docs/architecture/04_V1_ACCEPTANCE_GATE.md` §7 F6
Execution: JARVIS executive continuation run (owner redirect 2026-08-12)
Finding: **FIND-2026-08-12-05** (exit-0 `HTTP 401: Model … is not
supported` recorded as COMPLETED)

## 1. Gate Criterion and Pass Condition

> F6 — Provider or model outage. Make the preferred role model
> unavailable. Pass condition: configured eligible fallback is used and
> recorded, or the step enters an honest waiting state. No nonexistent
> model is invented.

## 2. Finding (FIND-2026-08-12-05): model-outage false success

A REAL oneshot (`.h` checkout, real provider endpoint, isolated home)
dispatched with a clearly nonexistent model id
(`model-does-not-exist-xyz-2026`) returned on STDOUT:

```
HTTP 401: Model model-does-not-exist-xyz-2026 is not supported
```

with **exit code 0** — the gateway rejects unknown model ids with 401 and
the CLI surfaces the body as the session's final response. The adapter's
exit-code check recorded **COMPLETED/PASS** with the error text as
"evidence" (same defect class as FIND-2026-08-12-04). Source audit of
`.h agent/conversation_loop.py` enumerated the full exit-0
failure-report surface: `Invalid API response after {N} retries: …`,
`Request payload too large …`, `Context length exceeded …`,
`Model generated invalid tool call: …`,
`Response truncated due to output length limit`, plus the previously
known `API call failed after {N} retries:` / `Billing or credits
exhausted:` forms.

## 3. Repair

`src/jarvis_foundry/roles/adapter.py` — `FAILURE_REPORT_PREFIXES`
extended to all source-verified exit-0 failure prefixes, plus a regex
`^HTTP \d{3}[: ]` covering the observed HTTP-status error lines. Any
match raises `ProviderUnavailable` (fallback chain engages; exhaustion =
honest BLOCKED). Non-zero exits unchanged (F2 semantics).

## 4. Experiment (live staged, live DB untouched)

`var/evidence/wp8-gate/f6/run_f6_experiment.py` — snapshot copy of the
live Foundry DB; REAL `.h` checkout + real oneshot for Phase B; the
deterministic `FakeHermesAdapter` for the fallback-chain phases:

| Phase | Injection | Expected | Actual | Verdict |
|---|---|---|---|---|
| A | baseline, real model | COMPLETED | COMPLETED (8.7 s) | PASS |
| B | preferred MODEL unavailable (nonexistent id, real endpoint) | honest non-success; configured model everywhere | **BLOCKED** `provider_unavailable` (5.5 s; the 401 model-not-supported line classified); `agent_run.started` model == configured id (no invention); no completed event | PASS |
| C | preferred provider fails, eligible fallback configured | fallback used AND recorded | COMPLETED via `vertex_ai`; `agent_run.failed:…:provider_unavailable:opencode_go` + `agent_run.started:…:vertex_ai` + completed recorded | PASS |
| D | preferred provider fails, NO fallback | explicit honest waiting | BLOCKED `provider_unavailable` (0.05 s) | PASS |

State: role leases 0; quick_check ok; `f6-experiment-summary.json`
overall **PASS**.

## 5. "No Nonexistent Model Is Invented" — Evidence

Every `agent_run.started` event for the outage run carries exactly the
CONFIGURED model id (`model-does-not-exist-xyz-2026`); the failure
detail names the same configured id. The system records the configured
model and never fabricates an alternative. The provider's silent serving
behavior for unknown ids was observed (Phase A/B) and is recorded; model
availability validation remains a provider-side concern (residual risk).

## 6. Live-System Protection

Live Foundry DB read-only (snapshot backup); live Hermes gateway never
launched/touched; isolated homes are copies of `var/wp8-hermes-home`
(unchanged); no secret material in evidence (fragment checks on raw
bytes, booleans only).

## 7. Residual Risk / Observations

- The failure-prefix whitelist is a curated list of the CLI's KNOWN
  exit-0 error forms (source-verified + empirically observed). A new CLI
  error shape would need a whitelist extension; the F5/F6 lineage
  (FIND-04 → FIND-05) shows the class is real and the list is the
  mitigation. Long-term fix (out of Foundry's control): the CLI should
  exit non-zero / emit structured errors for oneshot failures.
- Same-provider verifier limitation applies unless a distinct provider
  verifier is available.
- Phase B cost: real oneshot with a nonexistent model consumed no
  tokens (rejected before generation); usage reports read 0.0 USD.

## 8. Evidence Index

- `var/evidence/wp8-gate/f6/`: run_f6_experiment.py, f6-experiment-summary.json,
  staging/ (snapshot DB + staged home).
- `tests/foundry/test_f6_provider_outage.py` (6 regression tests:
  HTTP-401 shape, context-length shape, invalid-API-response shape, no
  false positive, fallback with real adapter, non-zero exit preserved).
- Ledger: `fault_injection.completed:F6:2026-08-12` (+ verifier verdict
  events at the independent review).
