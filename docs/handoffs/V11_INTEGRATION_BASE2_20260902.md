# V11 Integration Base 2 — 2026-09-02

## Verdict

`V11_INTEGRATION_BASE2_READY`

This isolated candidate combines A2 judgment, A4 durability,
accepted-final-outcome learning, and productionized continuous observation.
It deliberately contains no A3 implementation and grants no promotion, live
runtime, Scheduler, release, or production-trust authority.

```text
BRANCH:                         candidate/v11-integration-base2-20260902
WORKTREE:                       C:\temp\JARVIS-V11-Integration-Base2-20260902
EXACT BASE:                     d5cca15df800831f0409253b8a65d90ea16249ba
CONTINUOUS OBSERVATION TIP:     8004a8c16aceb888db1d1401c3bc67842cb4746f
CONTINUOUS IMPLEMENTATION:      5282a31dd6e7f52aaa280e665cf03deefdf4057f
INTEGRATION IMPLEMENTATION:     899e2d3affee41d02145514acb9c308db57afaf6
CONTINUOUS PATCH ID:            ae7ea2f13c0093d48556756795bbd6744ece792d
SCHEMA:                         v40 candidate / v38 live
LIVE/CANONICAL/ORIGIN:          unchanged
A3:                             not implemented / not tested
FINAL BROAD V11 SUITE:          not run by instruction
```

## Semantic integration

The two inputs share A2 base `00ee2e148c12151699a796649a1cb87ed892a69b`.
Pre-convergence has four unique commits carrying A4, outcome truth, combined
restart repair, and documentation. Continuous observation has one implementation
commit plus one lane-local documentation commit. A terminal merge would have
deleted A4/outcome code and tests, so only implementation commit `5282a31...`
was applied onto exact pre-convergence tip `d5cca15...`.

The source and integrated implementation patch IDs are identical. The
continuous lane's `CURRENT_STATE.md` and execution-state YAML were not copied;
they were reconciled here so A4 and outcome-learning truth remain canonical.
No second scheduler, Executive, MissionLoop, effect journal, ResearchQueue,
monitor registry, or capability authority was introduced.

## Preserved contracts

### A2 and continuous observation

```text
observe -> candidate -> fingerprint dedupe -> bounded research
-> reasoned reject / ELIGIBLE READY_FOR_ADMISSION -> WAIT
```

- The original A2 flag stays zero-execution and returns before admission.
- Continuous observation uses the existing monitor projection and Continuous
  Executive `WAIT_UNTIL_TIME` wake path.
- Calls before durable due time append nothing.
- Due but unchanged input adds no observation, candidate, refresh, research,
  or judgment work; only cadence advances.
- A meaningful evidence revision refreshes the same candidate identity and may
  receive exactly one new bounded research pass for that revision.
- TTL expiration records durable `STALE` rejection history.
- Readiness, failure count, exponential backoff, last content hash, and next
  due time survive database reopen.
- STOP is checked before configuration, wake trigger, source read, and research.
- Before A3 there are zero objectives, missions, effects, worktrees, provider
  writes, and external writes.

### A4 durability

- Dead-process lease recovery remains bound to PID plus process-creation
  identity.
- Accepted-step replay restores missing evidence/continuation without builder
  redispatch.
- Local commit effects keep one stable action identity and reconcile from Git.
- Mission terminal state and `mission.completed` remain atomic.
- Wait/wake, STOP/restart/resume, accepted-contract recovery, and parent
  continuation remain replay-safe.
- The eight fault points retain zero duplicate missions, continuations, builder
  steps, and effects.

### Final outcome and learning

```text
verification.accepted
-> evidence.created
-> mission.completed
-> mission.outcome_finalized
-> executive.calibration_recorded
-> WorldModel observation/learning
-> Tier-C memory observation / briefing projection
```

Runtime outcome text, preliminary `verification.passed`, builder/provider
claims, or a forged final-outcome event cannot create accepted-success learning.
The Executive terminal reconciler finalizes/replays learning before parent-plan
continuation after a post-completion restart.

## Schema v38 -> v39 -> v40

`src/jarvis_foundry/migrations.py` remains the only migration authority.
Migration 39 retains A2 candidate lineage, judgments, research, cycles, indexes,
and legacy rejection repair. Additive migration 40 extends `monitors` with the
durable watch lifecycle and extends the existing `research_queue` with
`input_hash` plus one candidate/revision/pass uniqueness index.

The focused migration proof creates a v38 database, records legacy candidate,
rejection, and research rows, migrates to v39, closes/reopens, migrates to v40,
verifies versions 1..40 exactly once, closes/reopens v40 as a no-op, checks the
new columns/indexes and preserved rows, runs SQLite quick check, and reconciles
the ledger/projections.

## Exact A3 integration map

### Running A3 snapshot

At final read-only inspection, worktree
`C:\temp\JARVIS-V11-A3-Initiative-Bridge-20260902` remained at base
`00ee2e148c12151699a796649a1cb87ed892a69b` with uncommitted work:

- modified `src/jarvis_foundry/projections.py`;
- modified `src/jarvis_foundry/runtime/config.py`;
- modified `src/jarvis_foundry/runtime/worker_loop.py`;
- new `scripts/v11_a3_flagship.py`;
- new `src/jarvis_foundry/initiative_a3.py`;
- new `tests/foundry/test_v11_a3_initiative_bridge.py`.

Re-resolve the running lane's final committed SHA and diff before integration.
Do not copy its terminal branch, generated proof, or documentation blindly.

### File-by-file selection

1. `src/jarvis_foundry/initiative_a3.py` — candidate for selective addition.
   Keep it as a subordinate admission/worktree/objective seam. Its input must be
   one durable A2 SELECT plus matching `ELIGIBLE/READY_FOR_ADMISSION` judgment.
   It must consume existing Executive, Fabric, mission, worktree, and effect
   contracts rather than create parallel authorities.
2. `src/jarvis_foundry/runtime/config.py` — hard overlap. Preserve Base 2's
   `initiative_a2` and `continuous_observation` fields, YAML loading, and
   mutual-exclusion rules. Add A3 as a separate opt-in execution mode. The
   standalone continuous-observation flag must remain zero-objective. A3 mode
   may call the continuous driver as its observation stage, but the two runtime
   entry flags must not independently run in one cycle.
3. `src/jarvis_foundry/runtime/worker_loop.py` — mandatory manual semantic
   integration. Preserve ordering: STOP -> singleton lease -> existing accepted
   contract recovery -> due continuous observation -> explicit A3 admission ->
   existing Executive/MissionLoop. In A3 mode call
   `run_continuous_observation_cycle()`, not the old one-shot A2 driver, so due,
   unchanged, revision, backoff, reopen, and wake semantics remain in force.
   If no due cycle or no selected survivor exists, return WAIT. Reuse the
   report's exact decision; never rerank or substitute another candidate.
4. `src/jarvis_foundry/projections.py` — hard overlap. Add only A3 admission,
   worktree, and candidate-terminal projections around Base 2's v39/v40
   candidate/research/monitor handlers. Do not reinterpret
   `verification.passed` as terminal truth or replace final-outcome projection.
5. `scripts/v11_a3_flagship.py` and
   `tests/foundry/test_v11_a3_initiative_bridge.py` — selectively add after
   rebasing their imports/config onto schema v40 and the continuous driver.
   Keep every owner-repo mutation inside disposable isolated worktrees.
6. `src/jarvis_foundry/executive/autonomy.py` — Base 2 is canonical unless A3
   needs a narrow binding. Preserve `reconcile_terminal_mission_outcomes()` and
   its learning-before-parent-continuation order.
7. `src/jarvis_foundry/mission/loop.py`, `mission/verification.py`,
   `world_model/connector.py`, `research_queue.py`, `migrations.py`, and
   `discovery_monitors.py` — Base 2 is canonical. A3 should consume these APIs;
   it does not need a schema migration, scheduler, verifier, final-outcome,
   learning, research, or observation authority.

### Admission checks

- Recheck STOP/veto, TTL/freshness, prior rejection/equivalent work, WIP cap 1,
  bounded budget, reversibility, explicit owner-project mapping, and canonical
  Fabric readiness at the moment of admission.
- Admit the exact candidate fingerprint plus A2 decision/judgment identity.
- Record reasoned WAIT/DEFER when admission fails; do not silently choose the
  next candidate except through a new canonical Executive judgment cycle.
- Prepare one isolated worktree from one exact owner-repository base SHA.
- Stable replay identities must yield one admission, objective, plan, bound
  mission, and worktree receipt per candidate attempt.

### Terminal truth checks

The running A3 worker-loop diff currently records Executive/portfolio outcome
and candidate terminal state from runtime `StepOutcome`. Reject that ordering.
After MissionLoop terminal state, first call/replay
`reconcile_outcome_learning()`. Only when its returned final contract is
`finalized` may A3 derive candidate-terminal status, acceptance identity,
verifier receipt, evidence references, parent continuation, and briefing.

A preliminary PASS followed by AcceptanceGate refusal must produce zero success
calibration, WorldModel learning, Tier-C memory, success briefing, and accepted
candidate terminal. Replaying one accepted identity must add no second final
outcome, calibration, World observation, memory observation, terminal candidate
record, continuation, mission, effect, or briefing item.

### Required combined A3 proof

- Re-run continuous unchanged/due/revision/TTL/STOP/reopen tests with A3 enabled.
- Prove zero admission when no source is due or input is unchanged.
- Prove exactly one admission from one eligible revised candidate.
- Re-run A4's eight kill points through the A3 path.
- Prove preliminary PASS refusal gives zero success learning.
- Prove accepted final outcome creates exactly one calibration, WorldModel
  observation, Tier-C memory observation, candidate terminal, and briefing.
- Run the A3 flagship only in a disposable owner-repository clone/worktree.
- Keep A3/A4 gate status `NOT_TESTED` until that combined proof passes.

## Focused evidence

### A2 flagship

- `C:\temp\JARVIS-V11-Integration-Base2-20260902-evidence\A2-flagship\V11_A2_FLAGSHIP_PROOF.json`
- SHA-256 `1a8c201de74a3726f692cff0a5682ef17b37f2a50a7cfee6bd948afb0dda87b5`
- schema v40; outcome WAIT; passed true; 192 events reconciled;
- zero objectives, missions, effects, external writes, duplicate candidates,
  and replay event delta;
- GitHub/project/World READY; web honestly unavailable; Gmail deferred.

### Continuous observation

- `C:\temp\JARVIS-V11-Integration-Base2-20260902-evidence\continuous-observation\V11_CONTINUOUS_OBSERVATION_PROOF.json`
- SHA-256 `8202ec40bbdd99ff1dcbc24d3481ee7a8e12c272b47b5b2fcd4bc3135c5a4200`
- schema v40; passed true; 148 events reconciled; SQLite quick check `ok`;
- pre-due event delta zero; unchanged cycle zero new work;
- one changed revision -> one refresh and one research pass;
- restart due recovery and STOP event delta zero;
- zero objectives, missions, effects, and external writes.

### Focused tests

- 57 combined tests: A2 sources/initiative/judgment/persistence, continuous
  observation, A4 eight-point durability, 11 outcome-learning cases, and
  migrations.
- Imports, compileall, migration/reopen, and `git diff --check` are required on
  the final clean tree.

## Remaining boundaries

- A3 implementation and A3 flagship: not integrated / not run.
- A3 and A4 roadmap gates: `NOT_TESTED` (the A4 foundation test passing alone
  does not close the combined V11 gate).
- Final broad V11 suite and elapsed unattended gate: not run.
- Live runtime/database, install, Scheduler, release pointers, trust, main,
  origin, and remotes: unchanged.
- Promotion and push: not performed.
