# V11 Pre-Convergence — 2026-09-02

## Verdict

`V11_PRECONVERGENCE_READY`

This isolated source base contains A2 observation/judgment, A4
restart/replay/STOP durability, and accepted-outcome learning truth together.
It does not contain A3, does not close A3 or A4 as a V11 gate, and is not a
promotion or live-runtime candidate.

```text
BRANCH:                 candidate/v11-preconvergence-20260902
WORKTREE:               C:\temp\JARVIS-V11-PreConvergence-20260902
CANONICAL COMMON BASE:  2905c206fed0461bf3085bc0cb43773be913300a
A2 HEAD / BASE:         00ee2e148c12151699a796649a1cb87ed892a69b
A2 SOURCE:              91c3deaeefb3fc71ffd091ad0f4abc2e2fd8023c
A4 INPUT:               daae32cb14d6d8882166d30d515daa9d796c6857
OUTCOME-TRUTH INPUT:    02e62a85a50bd642a1abdc6e415a2215ac5d2689
A4 INTEGRATION COMMIT:  318f73fae83a58ce4c8d67e8a8dee6997ac82b3e
TRUTH INTEGRATION:      6743d1eaa441fa32b38bf25a326c03ef4182ad5c
COMBINED REPAIR SOURCE: 51eb998642fee713458998bd3f44219a76ab670d
SCHEMA:                 v39 candidate / v38 live
LIVE/CANONICAL/ORIGIN:  unchanged
```

## Ancestry and semantic selection

All inputs share canonical base `2905c206...`. A2 is a five-commit descendant
of that base. A4 and outcome truth are independent single-commit descendants,
not descendants of A2. Pre-convergence therefore starts at exact A2 head and
replays each accepted single commit separately.

A4 source and integration patch IDs both equal
`cd7057b95e172d37991d9b75d1ca0c26425c637a`. Outcome-truth source and
integration patch IDs both equal
`b072133ba78a0389b0ec00bd36327441a554e0b6`. No terminal branch was merged.

The lane overlap was:

- A2/A4: `runtime/worker_loop.py`.
- A2/truth: `runtime/worker_loop.py`.
- A4/truth: `executive/autonomy.py` and `runtime/worker_loop.py`.

Git resolved those text overlaps without conflict, but semantic review found
one real combined gap. A hard kill after the atomic `mission.completed` append
could let A4 recover the parent objective while bypassing outcome truth and
learning. Commit `51eb998...` makes the existing terminal reconciler finalize
or replay accepted outcome, calibration, WorldModel, and Tier-C learning before
it advances the parent plan. If final outcome is not accepted/finalizable, the
parent step remains unreconciled for a later safe retry.

## Preserved authority and ordering

A2 remains:

```text
observation -> candidate -> fingerprint dedupe -> bounded research
-> rejection-first score/reject -> ELIGIBLE / READY_FOR_ADMISSION -> WAIT
```

The A2 flag still returns at the worker choke point before admission,
objective/mission creation, MissionLoop, worktree creation, effects, or
provider writes. No candidate-to-objective admission was added here.

A4 remains subordinate to the existing MissionLoop and effect journal:

- dead-process lease reclamation requires PID plus process creation identity;
- accepted step replay restores missing evidence/continuation without builder
  redispatch;
- local commit effects retain one stable action key and reconcile from Git;
- terminal mission state and `mission.completed` append atomically;
- wait/wake, STOP/restart/resume, and parent-objective recovery are replay-safe.

Outcome truth remains:

```text
verification.accepted
-> evidence.created
-> mission.completed
-> mission.outcome_finalized
-> executive.calibration_recorded
-> world.observation_recorded / WorldModel learning
-> memory.observed / briefing projection
```

Raw `verification.passed`, runtime `StepOutcome`, builder/provider assertions,
or forged final-outcome events cannot create accepted success learning.

## Exact A3 integration map

### Observed A3 surfaces

The separately running A3 worktree was inspected read-only on 2026-09-02. It
was based on A2 head and had these uncommitted surfaces at inspection time:

- modified `src/jarvis_foundry/runtime/worker_loop.py` — hard semantic overlap;
- modified `src/jarvis_foundry/projections.py` — candidate admission projection;
- modified `src/jarvis_foundry/runtime/config.py` — A3 feature gate and mutual
  exclusion;
- new `src/jarvis_foundry/initiative_a3.py` — admission/worktree/objective seam;
- new `tests/foundry/test_v11_a3_initiative_bridge.py`.

Because A3 is active elsewhere, re-resolve its final committed SHA and diff at
integration time. Do not copy its terminal branch or documentation blindly.

### Files likely to conflict

1. `src/jarvis_foundry/runtime/worker_loop.py` — mandatory manual semantic
   integration. Preserve A2's early inert return, A4 lease/accepted-contract
   recovery, truth's `reconcile_outcome_learning()` boundary, and only then
   add the A3 admission/continuation branch.
2. `src/jarvis_foundry/executive/autonomy.py` — preserve
   `reconcile_terminal_mission_outcomes()` and its outcome-learning-before-
   parent-continuation order if A3 changes objective/mission continuation.
3. `src/jarvis_foundry/projections.py` — A3 admission projection must be
   additive to v39 candidate/judgment/research/cycle projections and may not
   reinterpret `verification.passed` as terminal truth.
4. `src/jarvis_foundry/runtime/config.py` — keep `initiative_a2` strictly
   zero-execution and mutually exclusive from every execution selector; A3
   must remain separately opt-in and mutually exclusive from competing entry
   paths.
5. `src/jarvis_foundry/mission/loop.py`,
   `src/jarvis_foundry/mission/verification.py`, and
   `src/jarvis_foundry/world_model/connector.py` — retain pre-convergence as
   canonical if A3 touches these. A3 should consume their contracts, not add a
   second verifier, final-outcome, or learning authority.
6. `docs/CURRENT_STATE.md`, this handoff, the A2 handoff, and execution-state
   YAML — reconcile only in the final A3 convergence docs-only commit; retain
   honest gate status until the real A3/A4 proof passes.

### Candidate-to-objective seam A3 must satisfy

- Input is exactly one durable A2 `discovery.candidate_judged` record with
  `outcome=ELIGIBLE`, `reason_code=READY_FOR_ADMISSION`, its candidate
  fingerprint, and the matching recorded Executive SELECT decision.
- Recheck current STOP, veto, TTL/freshness, prior rejection/equivalent work,
  autonomous WIP cap 1, bounded budget, reversibility, explicit owner-project
  mapping, and canonical Fabric readiness at admission time.
- Do not rerank or redesign A2 scoring. A3 either admits the selected survivor
  or records a durable reasoned WAIT/DEFER; it never substitutes a different
  candidate silently.
- Enter the existing Executive objective/plan/mission path. No second chooser,
  scheduler, mission state machine, verifier, effect journal, or worktree
  authority is allowed.
- Prepare only an isolated candidate worktree at an exact base SHA. No main,
  origin, live install, live database, Scheduler, or release pointer mutation.
- Stable identities must make re-entry idempotent: one admission record, one
  objective, one plan, one bound mission per attempt, and one prepared
  worktree for the candidate fingerprint.

### Acceptance/outcome seam A3 must satisfy

- Never treat runtime outcome text or preliminary verifier PASS as accepted
  truth. The success boundary is the exact accepted verifier receipt plus
  evidence and terminal mission event.
- Preserve the full success order:
  `effect -> reconcile -> verifier -> verification.accepted -> evidence ->
  mission completion -> final outcome -> learning/briefing`.
- At WorkerLoop terminal handling, call or replay
  `reconcile_outcome_learning()` first. Record A3 candidate-terminal outcome
  and parent continuation only when the returned final contract is finalized;
  derive status, verifier identity, evidence references, and acceptance event
  from that contract rather than from `StepOutcome` or provider claims.
- A preliminary PASS followed by AcceptanceGate refusal must yield zero
  success calibration, WorldModel learning, Tier-C memory, and success
  briefing. Failure/block/rejection remain distinct outcomes.
- Replaying the same acceptance identity must not add a second final outcome,
  calibration record, World observation, memory observation, candidate
  terminal record, or briefing item.

### Durability identity requirements

- observation: source identity plus content hash;
- candidate: versioned fingerprint and stable candidate ID;
- judgment: decision ID plus candidate ID, disposition, and reason code;
- admission: candidate fingerprint plus exact A2 decision/judgment identity;
- worktree: candidate fingerprint, source root, exact base SHA, and stable
  branch/path receipt;
- objective/plan: stable candidate-derived IDs; replay updates neither count;
- mission/step contract: stable objective/plan/step binding and immutable
  contract version;
- builder attempt: durable run/attempt identity, never process-local counter;
- effect: canonical effect action key and provider/idempotency identity;
- verifier: verdict ID bound to mission, step, builder run, and independent
  verifier run;
- acceptance: `verification.accepted` event identity;
- evidence: deterministic bundle identity from mission/step/run/verdict and
  artifact/commit truth;
- completion: one `mission.completed` key per mission;
- final outcome: acceptance/finality-derived outcome identity;
- continuation: objective/plan/step identity with one terminal progress event;
- scheduler cycle IDs may aid replay but must not be the sole mission/effect
  identity when a restart changes the parent loop ID.

## Proof run on pre-convergence source

Real A2 flagship:

- report:
  `C:\temp\JARVIS-V11-PreConvergence-20260902-evidence\A2-flagship-r1\V11_A2_FLAGSHIP_PROOF.json`;
- SHA-256:
  `6107670f6fd3c564b6d11d5e8ed928c5571412826691433a060bad6876a4c461`;
- schema 39; outcome `WAIT`; passed true;
- ready source classes: GitHub, project, World; local web preflight truthfully
  unavailable; Gmail remained blocked/deferred;
- 9 observations, 5 raw candidates, 3 unique candidates, 2 suppressed
  duplicates, 1 researched, 2 rejected, 1 eligible;
- objective creations 0, mission creations 0, mutating effects 0, external
  writes 0, duplicate candidate records 0, replay event delta 0;
- STOP before/after, interruption resume, partial-source failure, worktree
  barrier, SQLite quick check, and 188-event reconciliation passed.

Focused tests:

- 47/47 lane tests passed: A2 sources/initiative/judgment/persistence, A4's
  eight parameterized durability points, 11 outcome-learning tests, and the
  immutable-release packaging test.
- 228/228 focused compatibility tests passed across migrations, MissionLoop,
  verification, WorkerLoop, Supervisor/reaper recovery, briefing, discovery,
  legacy outcomes, World Intelligence, and directly affected V9 surfaces.
- The combined post-completion kill test proves one mission, two distinct
  builder steps, zero duplicate builder steps/effects, two acceptances, two
  evidence bundles, one completion, one terminal parent-progress event, one
  final outcome, one calibration, one World observation, and one memory
  observation after three restart/replay cycles.
- `uv lock --check`, import health, `compileall`, v38-to-v39 disposable
  migration, schema 39 quick check, and `git diff --check` passed.

The first compatibility attempt intentionally encountered the immutable
packaging test's dirty-tree guard while the combined repair was uncommitted.
After commit `51eb998...`, that exact packaging test passed in 192.17 seconds.
This is classified as a harness precondition, not a product failure.

## Boundaries and remaining work

- A3 implementation and A3 flagship: not integrated / not run.
- Final broad V11 regression: not run by instruction.
- 48-hour V11 unattended gate: not run.
- Live runtime, live DB, install, Scheduler, release pointers, trust, main,
  origin, and remotes: unchanged.
- Promotion and push: not performed.
