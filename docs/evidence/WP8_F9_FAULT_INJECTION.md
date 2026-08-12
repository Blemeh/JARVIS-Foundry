# WP8 F9 — Corrupted / Incomplete Event Write (TEST Database)

Status: **PASS** (2026-08-12; throwaway test DB, never live)
Gate: `docs/architecture/04_V1_ACCEPTANCE_GATE.md` §7 F9

## 1. Criterion

> F9 — Corrupted or incomplete event write. Inject an interrupted
> transaction, invalid payload hash, and projection lag in the test
> database. Pass condition: invalid event is rejected, valid events
> remain, projections rebuild deterministically, and no mission
> transition is fabricated.

## 2. Injection and Results

`var/evidence/wp8-gate/f9/run_f9_experiment.py` — THROWAWAY temp DB
(the gate mandates test-database injection for this scenario):

| Arm | Injection | Actual | Verdict |
|---|---|---|---|
| 1 | interrupted transaction (subprocess BEGIN + INSERT + `os._exit(1)` before COMMIT) | **no partial row survives** (events 2→2); WAL recovery automatic; quick_check ok | PASS |
| 2a | tampered payload_json on an existing event (hash column unchanged) | `verify_event_integrity()` **rejects**: "payload hash mismatch for <id>" | PASS |
| 2b | forged INSERTed row with wrong payload_hash (mission.state_changed COMPLETE) | `rebuild_projections()` **rejects and rolls back**: projections unchanged; **no fabricated transition** (mission never COMPLETE) | PASS |
| 2c | corruption removed (payload restored, forged row deleted) | rebuild succeeds; two consecutive rebuilds **byte-identical** (deterministic) | PASS |
| 3 | projection lag (projection rows wiped) | rebuild **restores identical state**; twice-identical | PASS |

## 3. Mechanism Notes

- `Event.validate()` recomputes the payload hash; both
  `verify_event_integrity()` and `rebuild_projections()` validate every
  row — the rebuild validates INSIDE the transaction, so a forged row
  fails the whole rebuild atomically (rollback = no fabricated
  projection).
- Appends require DB access (trust boundary); unknown event types are
  recorded-but-inert (F13). The hash validation closes the tamper path.

## 4. Live-System Protection

Test DB only; live ledger untouched (event count verified before/after).
Evidence: `var/evidence/wp8-gate/f9/` (script + summary + staging).
