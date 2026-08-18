# Phase 4 Bounded Performance Evidence

These are point-in-time local measurements, not a production capacity claim.

| Operation | Input | Result |
|---|---|---|
| OpenAdapt-Flow compiler | Phase-1 `recording-scripted18` | exit 0; 5-step bundle; 14.3 seconds wall |
| Governed browser replay | local synthetic app; 2 parameters | exit 0; 5/5 steps; approximately 20.4 seconds wall; 0 model calls |
| Same request replay | same request ID and parameters | approximately 1.8 seconds; no OpenAdapt launch; no duplicate backend row |
| STOP before dispatch | new request with sentinel | approximately 1.8 seconds; `NOT_READY`; no external process |
| Process cleanup | one parent plus one child test tree; fresh OpenAdapt replay | no owned descendants after termination; hardened replay `hardened-3` completed with 0 model calls |

The adapter keeps model grounding disabled, writes parameter bindings to a
temporary run file rather than process arguments, and removes that file after
the run. No continuous loop or background scheduler is started by the probe.
