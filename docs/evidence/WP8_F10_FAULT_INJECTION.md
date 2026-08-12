# WP8 F10 — Duplicate Event Delivery (TEST Database)

Status: **PASS** (2026-08-12; throwaway test DB, never live)
Gate: `docs/architecture/04_V1_ACCEPTANCE_GATE.md` §7 F10

## 1. Criterion

> F10 — Duplicate event delivery. Deliver the same completion and
> verdict events multiple times. Pass condition: one terminal transition,
> one commit, and one next-action wake.

## 2. Injection and Results

`var/evidence/wp8-gate/f10/run_f10_experiment.py` — throwaway temp DB.
Original delivery: `agent_run.completed:run-f10` +
`verification.started:verdict-f10` + `verification.passed:verdict-f10` +
`git.local_commit_created:f10deadbeef`.

| Arm | Injection | Actual | Verdict |
|---|---|---|---|
| 1 | identical re-delivery (same keys, same payloads, rebuilt Events) ×3 | **all deduped** (created=False); event count unchanged (6); **one verdict row (PASS)**, **one commit event**; projections identical | PASS |
| 2 | same completion key, CHANGED payload (verdict FAIL instead of PASS) | **EventConflict** raised ("idempotency conflict for agent_run.completed:run-f10"); state unchanged (verdict stays PASS); no silent overwrite | PASS |

## 3. Mechanism Notes

- Idempotency keys cover the full payload: identical delivery dedupes;
  changed payload under the same key conflicts loudly (first-write-wins)
  — one terminal transition, one commit, one wake by construction.
- quick_check ok; reconcile consistent.

## 4. Live-System Protection

Test DB only; live ledger untouched.
Evidence: `var/evidence/wp8-gate/f10/` (script + summary + staging).
