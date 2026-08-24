# Post-V7 integration orientation

Status: PASS for read-only orientation. This file freezes the source and live
baseline before candidate integration. It does not certify the post-V7 result.

## Authority and scope

- Objective: Full Post-V7 Stage Closure from the owner-provided objective file.
- Repository: `C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry`.
- Integration branch: `integration/full-post-v7-20260824`.
- Integration worktree: `C:\temp\JARVIS-Full-PostV7-Integration`.
- Canonical source base is the verified `origin/main`, not a SHA copied from the
  objective. No canonical history is reset, rebased, or force-pushed.

## Source baseline

| Check | Observed value | Evidence |
|---|---|---|
| `main` | `13b1a5a3239b771a12054b10272f1797ebf12dc4` | `git rev-parse main` |
| `origin/main` | `13b1a5a3239b771a12054b10272f1797ebf12dc4` | `git rev-parse origin/main` |
| Local/origin match | PASS | exact SHA equality |
| Canonical worktree | clean | `git status --short --branch` |
| Ref refresh | completed | `git fetch --all --prune` |
| Candidate merge-base | `107827d81eaee514edc66eab98390438b63c1650` | all three candidates |
| Source after live release | docs/evidence-only | `f12a2215..main` changes only docs/evidence |

The integration branch was created from the verified canonical base. Existing
worktrees and branches are preserved. The three candidate refs resolve locally
and remotely to the exact accepted SHAs recorded in `CANDIDATE_PROVENANCE.md`.

## Live baseline

Live install authority is `C:\Users\b\AppData\Local\JARVIS`. Live durable
state is `C:\Users\b\AppData\Local\hermes\foundry`.

| Check | Observed value | Classification |
|---|---|---|
| `CURRENT` | `f12a2215f0bc98ca0d44337dcf0dc1ab0096e147` | PASS |
| `PREVIOUS` | `441113bcfd278505b81cede1f346fac77898c7dc` | PASS |
| CURRENT release ID | `rel-f12a2215f0bc-c3bb04f6` | PASS |
| PREVIOUS release ID | `rel-441113bcfd27-de548929` | PASS |
| CURRENT tree | `e50543693b1c68564404bc96bc742cd68e103aca` | PASS |
| PREVIOUS tree | `5a8111f882ca272e04d8c8c6fa3d766acd4d6619` | PASS |
| Package/live schema | 36 / 36 | PASS |
| Database quick check | `ok` | PASS |
| Database event count | 271,429 at first read-only query | informational, live-changing |
| Supervisor | PID 2652, `RUNNING`, singleton lease owned by its instance | PASS at check |
| Heartbeat | sequence 271,433 at `2026-08-24T14:41:00.556796Z` | PASS at check |
| STOP sentinel | absent at `foundry/EMERGENCY_STOP` | PASS at check |
| Scheduled authority | `JARVIS-Foundry-Supervisor`, enabled, repeating one-minute trigger | PASS at check |

The process command line resolves to
`C:\Users\b\AppData\Local\JARVIS\releases\f12a2215f0bc98ca0d44337dcf0dc1ab0096e147\.venv\Scripts\pythonw.exe`
with the live Foundry home. No production process, scheduler task, database,
release pointer, credential, or provider was changed by orientation.

## Active owner controls and boundaries

The live directives include the V7 full-autonomy goal and the constraint
`no external financial or destructive effects`. The integration therefore uses
only local/disposable proof targets unless a later explicit provider/account
authorization is already present. Missing credentials are reported as
`UNCONFIGURED`, `BLOCKED`, or `OWNER_ACTIVATION_REQUIRED`, never inferred from
environment variables. Accelerated harness evidence remains accelerated.

## Orientation commands

The baseline above was independently checked with `git fetch --all --prune`,
`git rev-parse`, `git status`, `git worktree list --porcelain`, candidate ref
resolution and merge-base checks, read-only release pointer/manifest reads,
read-only SQLite `schema_migrations`, `quick_check`, event, lease, directive,
and supervisor-state queries, process inspection, and `schtasks /query`.

## Gate to proceed

Proceed only in this isolated worktree. Candidate provenance and semantic
interfaces are frozen next. Canonical main and the live release remain
untouched until the combined source gates, adversarial review, release build,
rollback preparation, and live proof all pass.
