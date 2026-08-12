# WP8 F11 — Concurrent Obsidian Edit (Temp Vault)

Status: **PASS** (2026-08-12; temp vault, never the real V1 vault)
Gate: `docs/architecture/04_V1_ACCEPTANCE_GATE.md` §7 F11

## 1. Criterion

> F11 — Concurrent Obsidian edit. Edit a V1 note externally between
> JARVIS read and write. Pass condition: expected-hash mismatch creates
> a conflict, no content is silently overwritten, and reconciliation
> preserves both versions.

## 2. Injection and Results

`var/evidence/wp8-gate/f11/run_f11_experiment.py` — temp vault with the
V1 governed note set + REAL WP6 machinery (ObsidianWriter +
ObsidianService):

| Phase | Injection | Actual | Verdict |
|---|---|---|---|
| A | baseline governed write | COMPLETED; write_started/write_completed protocol events | PASS |
| B | owner edit lands between JARVIS read (stale expected_hash) and write | **ObsidianConflictError**; note on disk **keeps the concurrent edit**; incoming JARVIS version **preserved** under `Audit/conflicts/<note>.<hex>.conflict.md`; ledger row **CONFLICT**; `obsidian.conflict` event appended; no silent overwrite; quick_check ok | PASS |
| C | reconciliation (fresh hash read) | write COMPLETED; conflict file still holds the incoming version — **both versions preserved** | PASS |

## 3. Live-System Protection

Temp vault only — the real governed vault
(`C:\Users\b\Blendi\JARVIS\V1`) was never opened by this experiment.
Evidence: `var/evidence/wp8-gate/f11/` (script + summary + staging).
