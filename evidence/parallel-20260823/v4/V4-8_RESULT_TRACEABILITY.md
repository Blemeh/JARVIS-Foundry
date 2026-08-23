# V4-8 — Result traceability

Status: PASS — commit `6280ae6`

## Behavior delivered

`src/jarvis_foundry/result_traceability.py` builds the additive
`v4.result-traceability.v1` read model from durable outcome rows and events.
For each bounded representative outcome it links decision, candidate, mission,
evidence bundles, worker runs, verifier verdicts, failure/repair events,
external-effect events, actual outcome, owner directives, next action, and
deduplicated evidence refs.

Missing candidate/mission/measurement/effect/decision data is represented as
`UNKNOWN` with a reason. A derived next action is labelled `DERIVED`; it is not
reported as an evidenced fact. The snapshot exposes the projection
additively as `result_traceability`, and the owner route renders its unknowns
and refs.

## Proof

`tests/foundry/test_v4_traceability.py` builds a representative decision →
worker → verifier → evidence → failure/repair → external effect → measured
outcome chain plus an owner directive, then verifies every link. Its second
case proves missing actual/effect state remains unknown. The affected
Command Center/convergence suite passed.

