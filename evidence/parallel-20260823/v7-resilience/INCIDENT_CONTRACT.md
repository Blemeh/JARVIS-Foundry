# V7 Incident Contract

## Lifecycle

```text
DETECTED -> TRIAGED -> CONTAINING -> CONTAINED -> DIAGNOSING
          -> REPAIRING -> VERIFYING -> RECOVERED
```

Terminal alternatives are `BLOCKED`, `ESCALATED`, and `UNKNOWN`.

## Durable truth

Incident state is derived from append-only `incident.*` events. A fresh process
must reconstruct the same state from the ledger without trusting in-memory
state or a worker completion message. Original observations and failed repair
attempts remain immutable.

## Required record fields

- `incident_id`, first and last signal, severity, and correlation key;
- affected resources, capabilities, missions, and dependency scope;
- observed evidence and suspected versus proven cause;
- containment, diagnostic, repair, reconciliation, and verification actions;
- effect ambiguity classification;
- bounded attempt and cost budgets;
- recovery criteria and owner relevance;
- terminal state and unresolved blockers.

## Invariants

1. STOP is independent and stronger than incident automation.
2. Freeze precedes rollback.
3. Unknown external effects are frozen and reconciled, never blindly retried.
4. Incident automation cannot rewrite evidence.
5. Suspension is capability-scoped unless a shared dependency requires more.
6. Recovery needs observed health, not process-start evidence.
7. Repair attempts are bounded; repeated failure escalates.
