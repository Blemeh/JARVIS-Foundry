# Lane 2 Runtime and Production Truth

Status: IN PROGRESS
Branch: `candidate/lane2-runtime-production-truth-20260828`
Base: `fe0592991e98de3f5de27c53d40339f4281156f1`
Worktree: `C:\\temp\\JARVIS-Lane2-Runtime-Truth-20260828`

## Exit predicate

For a supplied final Lane 1 source SHA, the candidate tooling must prove all
of the following without mutating production:

- canonical source SHA, installed immutable release SHA, active database
  release authority SHA, and running runtime SHA are identical;
- one valid Supervisor owns the singleton lease and no duplicate or stale
  runtime process is accepted;
- the Windows task launches deterministically, restarts after a hard kill, and
  does not create overlapping launchers or legacy worker tasks;
- release build, install, rollback, and recovery are immutable and idempotent;
- `/api/health` and `/api/status` expose the same verified runtime identity and
  truthful trust, STOP, projection, and parity state;
- trust projection, event projections, and reconciliation converge from a
  disposable database copy;
- legacy idle events are bounded and do not manufacture activity;
- migration, backup, restore, STOP, PAUSE, RESUME, REVOKE, reboot, and
  hard-kill recovery survive disposable destructive probes;
- the promotion procedure is deterministic and production promotion remains
  explicitly separate from candidate proof.

## Entry observations

The canonical checkout is dirty and remains out of scope. `origin/main` was
refreshed before this worktree was created. The live task snapshot showed an
enabled repeating trigger and two `headless_launcher` processes. The live API
returned `/api/health` but not `/api/status`. The installed release was
`260abd961ea5f3dca5f0a429a2db4642b828c078`, while the repaired source head was
`fe0592991e98de3f5de27c53d40339f4281156f1`. The live database was inspected
through a read-only URI and was not migrated or rewritten.

## Scope boundary

This lane changes only runtime, deployment, state authority, recovery,
reconciliation, and their tests and evidence. Command Center design and
unrelated V4-V7 feature work are excluded. Production promotion, live database
migration, Scheduler mutation, and process termination require a separate
explicit promotion operation after candidate proof.

## Checkpoint table

| Iteration | Change | Verification | Predicate movement |
|---|---|---|---|
| 0 | Isolated worktree and entry evidence | Read-only Git, Scheduler, process, API, release, and SQLite inspection | Baseline recorded; predicate not yet met |
| 1 | Synchronous identity-bound headless launcher; DB live-slot authority; transition marker and recovery; strict launcher parity | Lane 2 release/launcher tests; focused release suite; `py_compile` | Scheduler overlap and filesystem-only activation paths now fail closed |
| 2 | Process census with install-root filtering and PID creation/executable identity; `/api/status`; scheduler action contract; trust/idle/parity reporting | Lane 2 runtime tests; Command Center health/status tests; read-only disposable probe | Duplicate/stale process and cross-install false positives are observable; status/health share one truth projection |
| 3 | Read-only SQLite connection scopes close deterministically; disposable backup/rebuild/reconcile script; promotion/repro helpers carry DB authority | Full Foundry run with candidate `PYTHONPATH`: focused runtime paths green; two pre-existing/environmental tests remain separately classified | Disposable recovery cleanup is safe on Windows; final SHA promotion remains the only unproven production step |

## Verification notes

- Focused Lane 2/runtime suite: green (including hard-kill owner reclaim,
  STOP/PAUSE/RESUME/REVOKE durability, pointer-fault recovery, process
  identity classification, scheduler/status contracts, and projection rebuild).
- Full `tests/foundry` run: the candidate paths were exercised to 100%; the
  existing long Scheduler hard-kill test could not locate its process because
  this host's PowerShell `Select-Object` probe is unavailable, and the
  unrelated high-cardinality Command Center benchmark exceeded its historical
  2-second bound. Neither failure mutated production or invalidated the Lane 2
  runtime tests.
- Ruff is not installed in the bundled interpreter; `py_compile` and
  `git diff --check` are clean.
