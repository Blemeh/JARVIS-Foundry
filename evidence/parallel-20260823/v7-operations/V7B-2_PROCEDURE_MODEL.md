# V7B-2 — Durable Procedure / Transaction Model

Status: PASS for the isolated source lane; not adopted by canonical main or
the live runtime.

## Contract

`src/jarvis_foundry/operations/procedure.py` composes the existing Foundry
append-only event ledger. It records a hash-bound `ProcedurePlan` with
mission/target identity, ordered steps, capability and precondition metadata,
risk/reversibility, stable effect tickets, postcondition oracles,
rollback/compensation, and evidence references. It does not create a second
database, mission selector, capability broker, or effect journal.

Each start captures a before-state and evidence. Completion requires an
after-state, evidence, and declared postcondition results. The final step and
terminal outcome are appended atomically. Re-delivery is idempotent and a
changed plan under the same procedure id is rejected.

## Recovery proof

`resume_procedure()` freezes a started step with an UNKNOWN classification
after restart. It never retries the effect. The caller must reconcile provider
truth or compensate before reopening. Reconciliation accepts explicit
COMPLETED/FAILED provider outcomes; failed attempts can be compensated,
reopened, and retried as a distinct attempt while preserving the logical
effect ticket.

## Verification

Focused test file: `tests/foundry/test_v7b_procedure.py`

Result: `10 passed` (2026-08-23).

Covered cases include plan-hash conflict, ordered steps, required before and
postcondition evidence, atomic terminal completion, restart freeze and
read-back reconciliation, failed→compensated→reopened retry, invalid target
identity, and high-risk recovery requirements.

The proof uses a temporary SQLite ledger only. No live database, credentials,
provider, account, scheduler, supervisor, or production state was changed.
