# Defects fixed during MASTER GOAL 02 integrated acceptance

Isolated candidate only. Live JARVIS, `main`, and origin were not mutated.

## High: campaign child stranded after verified MissionLoop PASS

**Symptom.** After a campaign child's MissionLoop outcome was `PASSED` and
the mission projection was `COMPLETE`, `reconcile_campaign_children` moved
the child to `UNKNOWN` instead of `SUCCEEDED`. Goal 01 recorded this as a
known limitation and raised `max_parallel_children` rather than repairing
it. Acceptance proved it is a real stall: a later eligible child could not
rely on canonical acceptance even after verifier evidence existed.

**Cause.** Two cooperating gaps on the existing campaign/MissionLoop spine:

1. `ChildMissionRecord.effect_keys` includes a declared `effect_action_key`
   used for isolation identity. `_effect_blockers` treated a never-started
   key as journal `UNKNOWN`, which forbids `SUCCEEDED`.
2. MissionLoop COMPLETEd the mission but did not invoke the existing
   `reconcile_outcome_learning` + `reconcile_campaign_children` path, so
   acceptance waited for a later worker `startup_resume_pass`.

**Fix.**

- `src/jarvis_foundry/executive/campaigns.py`: never-started declared effect
  identities are not join blockers. Started `STARTED` / `FROZEN` /
  journaled `UNKNOWN` / `FAILED` effects still block.
- `src/jarvis_foundry/mission/loop.py`: after contract-driven `COMPLETE`,
  call the existing outcome reconciler and campaign-child reconciler. No
  direct `SUCCEEDED` database write.

**Regression.**
`tests/foundry/test_v14_v16_convergence.py::test_campaign_child_terminalizes_after_verified_missionloop_pass`

**Proof.** Scenario C after the fix: child status `SUCCEEDED` immediately
after MissionLoop PASS, `acceptance_outcome_id` bound, repeat workflow also
terminalizes. Duplicate mission/effect/continuation/false-success remain 0.

**Follow-on proof update.** The V12 disposable endurance script observed
parallel occupancy by counting leftover `RUNNING` children after the first
verified PASS. That observation method is now stale. The script admits both
dispatchable children through canonical `record_child_state(..., "RUNNING")`
before sequential MissionLoop work. Focused rerun PASS.

## Readiness observation (not a defect)

`cap.workflow.run` Catalog `observed_readiness` is `UNKNOWN` until the first
verified Fabric outcome. After Scenario C's first verified `workflow.run`,
readiness became `READY` / `MATCH`. A second eligible workflow executed
without deadlock. No catalog repair was required.
