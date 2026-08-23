# V7 Executive and Portfolio Lane Initial State

Status: FROZEN BASE RECORDED
Date: 2026-08-23

## Boundary

The candidate lane starts from `6db467a2b0e6bdc37adbc4cbfac309418e33afec`.
That SHA was both local `main` and `origin/main` after fetch. The canonical main worktree is unchanged.

The candidate worktree is `C:\temp\JARVIS-V7-Executive-Portfolio`.
The branch is `candidate/v7-executive-portfolio-20260823`.

The V4, V5, V6, and cinematic Command Center integration work remains in sibling worktrees. This lane does not merge, deploy, restart Supervisor, change Scheduler tasks, mutate a live database, change CURRENT or PREVIOUS, use live credentials, or enable production flags.

## Machine and repository observations

- The canonical checkout was clean at the frozen base.
- `var/autonomy-run.marker` reports an active historical `hermes-executive-continuation` session with `ACTIVE_SOLE_ENGINE`. This lane does not interrupt or claim that process.
- The repository has many historical and active sibling worktrees. Their ownership remains outside this lane.
- `docs/CURRENT_STATE.md` and `docs/CAPABILITY_MATRIX.md` contain claims for a later integration SHA than the frozen base. They are treated as documentation inputs, not current Git authority.

## Existing authorities

- `ExecutiveDecisionService` is the canonical candidate-level selector.
- `materialize_selection` is the canonical candidate-to-mission boundary.
- `mission.loop` and the runtime worker loop own mission execution and verification.
- `evolution/portfolio.py` owns the existing project registration and project-only ranking surface.
- `evolution/scheduler.py` owns the existing event-first schedule tick and basic budget gate.
- `research_queue.py`, `v3_resources.py`, `v3_outcomes.py`, `v3_teams.py`, and the worker/verifier contracts remain reusable machinery.

## V7 gap found

No single durable selector currently compares active mission continuation, repair, new candidate work, maintenance, research, unknown investigation, incidents, improvements, and WAIT. Capacity and budget state is split between project slots, resource counters, scheduler arguments, and mission rows. There is no portfolio-level lease, revisit, fairness, outcome calibration, or management-by-exception packet that survives a fresh process without reconstructing it from one contract.

## Initial verdict

The V7 lane is technically actionable in an isolated worktree. The first implementation unit will add one event-sourced executive portfolio contract above the existing candidate scorer and mission materializer. It will not create a second ledger, mission engine, scoring policy, STOP plane, or resource registry.

## Post-build note

The candidate now contains the additive event-sourced selector and its disposable lab. The source remains candidate-only; the historical docs above still describe later integrated SHAs and are not treated as live proof. Final verification and the candidate push are recorded in the handoff after the repository-wide gate.
