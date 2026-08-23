# V4-6 — Owner-decision queue

Status: PASS — commit `c8b10ce`

## Behavior delivered

`src/jarvis_foundry/decision_queue.py` derives a read-only owner-attention
queue from the briefing item model. It distinguishes `INFORM`,
`DECISION_REQUIRED`, `BLOCKED_EXTERNAL_DEPENDENCY`, `OWNER_VETO_ACTIVE`, and
`UNKNOWN`; it does not turn every notification into an approval request.

Each record includes consequence, evidenced options (bounded to five), source
event/item/evidence refs, entity and mission links, dedupe identity, and
`OPEN`/`RESOLVED`/`SUPERSEDED` lifecycle. Revoked directives and resolved or
materialized decisions are reconstructed from later ledger events. Unknown
directive authority stays `UNKNOWN`.

The snapshot exposes this queue additively at `decision_queue` and surfaces its
open count in `controls`.

## Proof

`tests/foundry/test_v4_decision_queue.py` covers veto, blocked, and unknown
classification, evidence/options, and revocation resolution. The Command
Center and convergence regressions passed in the isolated candidate lane.

