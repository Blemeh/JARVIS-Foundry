# WP8 F7 — Expired Authentication (Simulated)

Status: **PASS** (2026-08-12; live staged experiment, real endpoint)
Gate: `docs/architecture/04_V1_ACCEPTANCE_GATE.md` §7 F7

## 1. Criterion

> F7 — Expired authentication. Simulate unavailable or expired provider
> authentication without exposing a real credential. Pass condition:
> broker denies the capability, secrets do not enter logs, mission state
> remains intact, and reauthentication can resume the step.

## 2. Injection and Results

`var/evidence/wp8-gate/f7/run_f7_experiment.py` — snapshot copy of the
live Foundry DB; REAL `.h` checkout + real oneshot dispatches; the
credential env (`OPENCODE_GO_API_KEY`, the pool's `source`) overridden
with an OBVIOUSLY FAKE key (`sk-invalid-expired-f7-test` — no real
secret involved in the denied phase; the staged home's `.env` carries
only the fake).

| Phase | Injection | Actual | Verdict |
|---|---|---|---|
| A | real credentials | COMPLETED (10.6 s) | PASS |
| B | expired auth (fake key) | **BLOCKED** `provider_unavailable` (5.6 s; the real gateway returned `HTTP 401: Invalid API key.`, exit 0, classified by the FIND-05 repair); run row FAILED; **fake key absent from captured output AND ledger; real key absent by construction and verified**; mission state intact; quick_check ok | PASS |
| C | credentials restored | COMPLETED (10.1 s) — reauthentication resumes | PASS |

## 3. Notes

- The F6 repair (HTTP-status failure classification) is what makes the
  401 an honest BLOCKED instead of a fabricated COMPLETED — F7 exercises
  that same classifier against REAL auth failure.
- No real credential ever entered the denied phase; leak checks ran on
  raw bytes in-script (display-redaction-proof booleans).
- Evidence: `var/evidence/wp8-gate/f7/` (script + summary + staging).
