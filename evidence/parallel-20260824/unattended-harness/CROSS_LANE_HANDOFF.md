# Cross-Lane Handoff

## V7A portfolio

Use `select_next_work`, `record_portfolio_outcome`, and the existing
`executive.portfolio_selection_enabled` gate for integration probes. The
harness must not select work independently. It measures selection, WAIT, wake,
and terminal-outcome closure through the canonical event seam.

## V7B procedures and effects

Use procedure plans, stable effect tickets, before/after evidence, and explicit
reconciliation. A started effect that loses its response is `UNKNOWN` and
`FROZEN`; the harness never retries it directly.

## V7C resilience and incidents

Reuse `scripts/v7_resilience_fault_matrix.py` as a disposable fault oracle.
Its 21 rows cover worker, STOP, provider, database, effect, release, resource,
authority, and restart behavior. The acceptance runner records the matrix as a
component proof and does not treat its historical JSON as fresh without rerun.

## V4 briefings

Briefing counts come from authoritative projected events. Heartbeat and outbox
noise are excluded. Owner action is measured by classification, not by a
generic approval queue.

## V5 and V6

Effects and reconciliation remain the only effect journal. V6 evolution remains
behind its existing verifier and release gates. This lane measures readiness and
rollback behavior; it does not promote candidates or enable live evolution.

## Integration dependency

After this branch is pushed, an owner-approved integration lane must reconcile
the harness adapters against the final V7 canonical HEAD. This candidate does
not claim live-provider proof, a 12/24-hour real soak, or production readiness.
