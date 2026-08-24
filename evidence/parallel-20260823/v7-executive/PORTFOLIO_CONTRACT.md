# V7 Portfolio Contract

## Purpose

The portfolio executive chooses the next valuable action across projects without replacing existing mission, scoring, resource, STOP, or verification machinery.

## Durable record shape

Every portfolio work item carries an immutable identity and provenance. It may link an owner goal, project, resource, mission, candidate, maintenance obligation, unknown, or improvement record. Its comparable inputs are expected value, urgency, information gain, confidence, effort, cost, risk, reversibility, dependencies, opportunity cost, and observed outcome history.

Every selection records one class and entity, the reason it is timely, the goal alignment, the alternatives rejected, the capacity and budget decision, the owner veto or STOP check, expected value, uncertainty, and the wake or continuation condition.

## Selection classes

`ACTIVE_MISSION`, `REPAIR`, `CANDIDATE`, `NEW_MISSION`, `MAINTENANCE`, `RESEARCH`, `UNKNOWN`, `INCIDENT`, and `IMPROVEMENT` are comparable portfolio classes; `WAIT` is the explicit no-selection outcome. Candidate scoring reuses `executive.scoring.rank_candidates`. Portfolio allocation adds only class, capacity, fairness, lease, and outcome semantics.

## Invariants

1. Owner veto and STOP outrank every score.
2. Active missions remain visible; failed or blocked missions re-enter as bounded `REPAIR` work, and maintenance obligations are scheduled explicitly.
3. Workers do not choose portfolio priority.
4. A builder never certifies consequential work.
5. Unknown evidence remains unknown.
6. Capacity and cost limits are durable and explain rejected alternatives.
7. WAIT includes a reason, wake conditions, and a deterministic revisit time.
8. Failed work has a bounded repair budget and does not retry forever.
9. A selection is idempotent by loop identity. A claim is idempotent for the same entity and decision, while a new decision may replace an expired lease.
10. Raw outcome observations remain immutable. Calibration is bounded and cannot be rewritten by one noisy result.
11. A fresh process reconstructs the same portfolio state from the event stream.

## Integration boundary

The adapter may call `materialize_selection` for a selected candidate. It must re-check eligibility, STOP, and veto at materialization. Team planning uses the existing bounded role manifest and fresh verifier binding. Command Center and V4 briefing code consume a projection packet rather than a second briefing engine.
