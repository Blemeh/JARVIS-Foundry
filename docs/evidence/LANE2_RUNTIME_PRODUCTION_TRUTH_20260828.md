# Lane 2 Runtime and Production Truth

Status: COMPLETE — RUNTIME_READY_FOR_FINAL_RELEASE (candidate machinery; production promotion not run)
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
| 4 | Portfolio idle-event coalescing excludes polling-only deadline churn; launcher binds install root to its own immutable path; releases publish through hidden staging and atomic rename; Scheduler XML truth probe preserves `wscript` switches and path variants | `tests/foundry/test_lane2_runtime_truth.py`: 10 passed; failed-build cleanup and scheduler-contract regressions included; `py_compile` and `git diff --check` clean | New release/runtime mechanics are deterministic and fail closed on stale environment or partial publication |
| 5 | Final candidate identity/recovery rehearsal and final read-only live probe | Candidate code SHA `dc8bfbc136b70d85fecfe16d8a07ab3b94880209`, tree `b64a0c5b759fc2e1ad1a96e6e83c09cafcee64e0`; `scripts/release_identity_repro.py` PASS; live probe JSON recorded below; disposable recovery schema 37/reconciliation PASS | Candidate is ready for a supplied Lane 1 SHA; live installation remains intentionally old and degraded until explicit promotion |

## Final candidate evidence

The exact runtime/deployment code proof was run from this isolated worktree at
`dc8bfbc136b70d85fecfe16d8a07ab3b94880209` (tree
`b64a0c5b759fc2e1ad1a96e6e83c09cafcee64e0`). The disposable identity harness
`scripts/release_identity_repro.py` built two immutable releases, activated the
database live slot, started the first release, rolled back to the prior
release, and started it again. It returned `verdict PASS`; both starts reached
`RUNNING`, the rollback pointer and generation matched, and all temporary
artifacts were outside production.

The final read-only production inspection was written to
`C:\\temp\\lane2-live-status-dc8bfbc-20260828.json` by
`scripts/lane2_runtime_truth.py` with `production_mutation: false`. It proves
the disposable recovery rehearsal independently (`schema: 37`, SQLite
integrity `ok`, reconciliation `consistent: true`, `live_mutated: false`).
The live install is intentionally not a pass: source expected
`dc8bfbc136b70d85fecfe16d8a07ab3b94880209`, installed/runtime
`260abd961ea5f3dca5f0a429a2db4642b828c078`, the database has no active live
slot (`FILESYSTEM_ONLY_AUTHORITY`), three duplicate/unowned processes were
observed around one valid owner, the launcher contract is still the old live
VBS, and recent legacy portfolio/general idle events remain. Trust and event
reconciliation are consistent; no production rows, pointers, tasks, or
processes were changed.

The final candidate test file is
`tests/foundry/test_lane2_runtime_truth.py`. It covers synchronous/path-bound
VBS launch, atomic failed-build cleanup, scheduler switch contracts, database
activation/rollback and pointer-fault recovery, idle coalescing, duplicate and
PID-reuse classification, `/api/status`, durable owner controls, and hard-kill
Supervisor lease reclaim.

## Exact promotion procedure (not run)

Promotion is deliberately a separate, approved operation after Lane 1 supplies
the final source SHA. Use a fresh PowerShell session and substitute the full
Lane 1 SHA for `$sha`:

1. Set `$repo` to the clean Lane 1 source checkout, `$home` to
   `C:\\Users\\b\\AppData\\Local\\hermes\\foundry`, `$install` to
   `C:\\Users\\b\\AppData\\Local\\JARVIS`, and prepend
   `C:\\Program Files\\nodejs` to `$env:Path`; set `$env:PYTHONPATH` to the
   source `src` directory. Verify the source commit/tree and expected SHA
   read-only.
2. Run `scripts/lane2_runtime_truth.py` as a read-only preflight with
   `--expected-sha $sha --source-repo $repo`. A pre-install mismatch is
   expected; stop for any unexpected DB integrity or process damage.
3. Quiesce only the exact `\\JARVIS-Foundry-Supervisor` owner through the
   durable owner STOP/control path. Wait for `STOPPED` and lease release. Do
   not broad-kill processes or disable unrelated Scheduler tasks.
4. Create and hash a backup with
   `python -m jarvis_foundry.cli.main --home $home release backup-db --destination $backup`.
   Require SQLite `quick_check` success before proceeding.
5. Rehearse migration on a disposable clone with
   `python -m jarvis_foundry.cli.main --home $home release migrate-clone --destination $clone`.
   Require schema 37, integrity, projection rebuild, and reconciliation
   success. Never migrate the live database in this step.
6. Build the immutable release from the clean Lane 1 checkout:
   `python -m jarvis_foundry.cli.main release build --source-repo $repo --sha $sha --install-root $install`.
   Validate `RELEASE.json`, source/tree identity, schema compatibility, and
   artifact hashes. A failed build must leave neither a final nor staging
   directory.
7. Point `JARVIS_INSTALL_ROOT` at `$install` and activate through the
   database-backed command:
   `python -m jarvis_foundry.cli.main --home $home release activate --sha $sha --install-root $install`.
   The command records the live slot first, writes the transition marker, then
   atomically writes `CURRENT`/`PREVIOUS` and retargets the exact Scheduler
   task. Resolve any `POINTER_WRITE_PENDING` marker before launch.
8. Launch only `schtasks /run /tn "\\JARVIS-Foundry-Supervisor"` and wait for
   the repeating trigger/lease to settle. The VBS must synchronously invoke the
   immutable release and export the same release ID/SHA; `IgnoreNew` must be
   present and no legacy worker task may be enabled.
9. Run the same Lane 2 probe with `--expected-sha $sha`. Promotion passes only
   when `/api/health` and `/api/status` report `RUNNING`, authority is
   `DATABASE_LIVE_SLOT` and consistent, source/install/database/runtime SHAs
   are equal, exactly one valid owner is present with zero duplicate/stale
   processes, Scheduler action and launcher contracts pass, the running
   identity is verified, trust and event reconciliation pass, idle growth is
   clear in the recent window, and no transition marker remains.
10. Exercise signed owner STOP, PAUSE, RESUME, and REVOKE; verify durability
    after Supervisor restart and a fresh status read. Only then clear STOP and
    resume normal operation.
11. In an approved disposable/maintenance window, kill only the database-
    recorded PID after matching its creation identity and executable. Let the
    one-minute repeating trigger recover it, then require a new valid owner,
    reclaimed lease, no duplicate, and equal runtime SHA. A reboot drill uses
    the same exact task and checks the same predicate.
12. Test rollback only with approval:
    `python -m jarvis_foundry.cli.main --home $home release rollback --install-root $install`.
    Verify the DB live slot, pointers, generation, strict identity, and
    `/api/status`; preserve the backup and evidence. Re-activate `$sha` through
    the same command when rollback validation is complete.
13. Restore only an explicitly approved known-good backup after STOP and a
    fresh backup. Validate `quick_check`, schema, projections, and
    reconciliation before restart. No ad-hoc SQL deletion or live migration is
    part of this runbook.

## External blockers and non-claims

- Lane 1 has not supplied the final converged source SHA. The candidate SHA
  above is a runtime rehearsal identity, not a production promotion target.
- Production is still on `260abd961ea5f3dca5f0a429a2db4642b828c078` with
  filesystem-only release authority, the prior asynchronous VBS, duplicate
  processes, and recent legacy idle growth. This lane intentionally did not
  mutate that state.
- The host's existing long hard-kill test cannot locate its PID because its
  PowerShell `Select-Object` utility probe is unavailable. The candidate
  process census uses CIM/raw process data and has its own duplicate, stale,
  executable, and PID-reuse tests.
- The unrelated high-cardinality Command Center benchmark remains above its
  historical two-second bound; Command Center design and that V4-V7 path are
  outside this lane.
- Ruff is unavailable in the bundled interpreter; syntax compilation and
  whitespace checks are clean.

## Verdict

`RUNTIME_READY_FOR_FINAL_RELEASE`

The isolated runtime/deployment/state machinery is ready to accept Lane 1's
final SHA and execute the deterministic procedure above. The live environment
is not yet promoted and must remain `DEGRADED` until that separately authorized
procedure proves the full production predicate.

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
