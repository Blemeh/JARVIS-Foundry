# WP5 Evidence — Mission And Verification Loop

Status: **PASS** (gate closed 2026-08-11, continuation run)
Updated: 2026-08-11

## Gate Close Record

- Two verification rounds: deleg_20def896 (round 1) returned FAIL with three
  confirmed defects (F1 verdict-ordering tie-break, F2 fresh-instance
  REPAIRING resume collision, F3 pre-dispatch validation gap) + F4/F6/F7
  minors; all repaired with regression tests (8 new). deleg_1b6d0485 (round
  2) re-verified the repaired tree adversarially: **PASS on D1–D7**, all
  findings reproduced fixed, tree stable.
- Executive re-verification: full suite **167 passed** fresh; Hermes estop
  baseline 24 passed; canonical DBs byte-unchanged.
- Frozen snapshot: var/evidence/wp5-gate/ (snapshot sha256
  `2f608f6a344d78b0d181eb0b6b5585d2b8e7f1f2f8f447f3d53240822c730150`).
- Full detail: `docs/evidence/WP5_GATE_REPORT.md`.

## Objective (CURRENT_WORK_PACKAGE.yaml)

The Supervisor runs a mission end-to-end without owner prompting: propose the
next action from durable mission state, dispatch it through the WP4 role
adapter, intake the structured result, verify it independently
(provider-diverse when available), repair a deliberately failed step, and
commit locally only after a verified PASS.

## Implementation

| Module | Responsibility |
|--------|----------------|
| `src/jarvis_foundry/mission/proposal.py` | `propose_next_action`: deterministic proposal from the durable mission record; `ProposalError` for unknown missions and non-dispatchable states (no invented work); `Waiting` — honest waiting when no requirement is scheduled, zero events |
| `src/jarvis_foundry/mission/verification.py` | `start_verification` (verification.started with independence metadata: builder/verifier provider identity, provider_diverse, same_provider_limitation), `record_verification_outcome` (verification.passed/failed/blocked), `latest_verdict_for_step`, `failed_verdict_count` (durable per-step repair-budget accounting) |
| `src/jarvis_foundry/mission/commit.py` | `commit_after_pass`: refuses without a PASS verdict for the step (from the ledger), refuses when the action policy does not ALLOW the commit, then creates exactly one deterministic local commit (`jarvis: <mission> <step> verified PASS (run <run_id>)`) in the registered worktree; returns before/after state |
| `src/jarvis_foundry/mission/loop.py` | `MissionLoop.run_step`: propose -> advance READY/EXECUTING -> builder or repair dispatch -> VERIFYING -> verifier dispatch (provider-diverse via `diversity_resolution`, pinned by override) -> PASS: commit (while VERIFYING, after the verdict is durable) + evidence.created + ACCEPTING + ACTION_SELECTION(next_action cleared) / FAIL: bounded REPAIRING or BLOCKED with precise reason; `run_until_settled`: FAIL -> repair cycles until PASSED/BLOCKED/WAITING, bounded by the durable budget |

## Loop Semantics (deterministic)

- Proposal: ACTION_SELECTION requires a scheduled `next_action`; REPAIRING
  continues the step (only with a durable FAIL verdict for it); READY
  continues; everything else raises ProposalError.
- Pre-dispatch envelope validation (D2): step identity, capability registered
  in the resource map, expected effects, rollback contract, and target inside
  the registered root are enforced BEFORE any dispatch — rejected steps never
  reach an adapter.
- Repair budget: FAIL verdicts for the step are counted from the durable
  ledger; `fails <= max_repairs` -> REPAIRING (next pass dispatches role
  "repair"), else VERIFYING -> BLOCKED with the exact count.
- Commit: PASS verdict recorded first (selected by LEDGER SEQUENCE — never by
  timestamps or ids), bound to the verdict's builder run, then policy ALLOWED
  (capability `git_local_commit` READY/ACTIVE, target inside the registered
  root, no veto), then one local commit. Any refusal blocks the mission
  precisely — nothing is ever committed after FAIL/BLOCKED/NOT_TESTED.
- Honest waiting: an exhausted mission returns WAITING with zero new events.
- Idempotency: mission.state_changed events are keyed per loop pass via the
  pass's run_id (durable identity — a fresh loop instance resumes cleanly);
  run events replay through the WP4 dispatcher's ledger replay; verdict and
  evidence events are keyed per verdict/bundle.

## Safety Invariants

- The loop commits only in the mission's registered worktree
  (`resources.resolved_root`) — never canonical DBs, never live Hermes,
  never `.h`.
- Real Hermes dispatch stays disabled by default (WP4 boundary); the loop
  drives the fake adapter and the bounded adapter identically.
- No unbounded loops: dispatch cycles are capped by the durable repair
  budget; `run_until_settled` has a defensive safety cap.

## Test Inventory (167-test suite; WP5 = 22 new)

- `test_mission_loop.py` (14): GATE — deliberate builder failure (artifact
  omitted), verifier FAIL, repair delivers, verifier PASS, commit after PASS,
  mission continues, then honest WAITING; proposal validation (unknown /
  non-dispatchable); honest waiting (zero events); repair-budget exhaustion
  -> precise BLOCKED (no infinite loop); builder outage -> BLOCKED; verifier
  outage -> BLOCKED with blocking_dependency; continuation across a second
  scheduled action; policy-denied commit -> BLOCKED; pre-dispatch rejection
  matrix (unknown capability, empty rollback, out-of-root target, missing
  step_id — zero dispatcher calls); repair requires durable FAIL verdict;
  fresh-instance REPAIRING resume.
- `test_verification_loop.py` (8): independence metadata (diverse provider
  recorded, distinct run ids), FAIL findings + repair requirements durable,
  commit refusal matrix (no verdict / FAIL / policy denial), exactly one
  deterministic commit after PASS, no commit on BLOCKED outcomes, ledger-
  sequence verdict ordering (identical timestamps), commit run_id binding.

## Verification

- Round 1 (deleg_20def896): FAIL — three confirmed defects, all repaired
  with regression tests (F1 ledger-sequence ordering, F2 run_id-scoped
  advance keys, F3 pre-dispatch validation; plus F4/F6/F7).
- Round 2 (deleg_1b6d0485): **PASS on D1–D7** — all findings reproduced
  fixed, full battery green, tree stable. Summaries:
  `subagent-summary-0-20260811_214241_498601.txt` (round 1),
  `subagent-summary-0-20260811_220341_360205.txt` (round 2).
- Full suite: **167 passed** (fresh reproduction, frozen state).

## Remaining Work

- WP5 gate closed (PASS). Next: WP6 (governed V1 Obsidian write-back) —
  V1 folder creation, note metadata, transactional writes, snapshots,
  conflicts, audit links, rollback. Gate: concurrent edit and rollback tests
  preserve both meaning and bytes.
