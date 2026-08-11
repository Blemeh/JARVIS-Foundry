# Incident Record — Frozen Headless Run Touched var/wp1-staging

Status: CLOSED (S4 — non-consequential, isolated artifact)
incident_id: INC-2026-08-11-001
detected_at: 2026-08-11T19:10+02:00 (executive reconciliation after owner mandate)
detected_by: JARVIS executive (visible desktop session)

## Summary

A temporary headless continuation run was frozen by the owner. Before freezing,
it created `var/wp1-staging/` (foundry.db at 19:02) and `var/wp1-hermes-home/`
(19:00) despite the standing preservation instruction for canonical databases.
All work produced by that run is treated as unverified existing work, not PASS
evidence.

## Observations

- `var/wp1-staging/foundry.db`: created by the frozen run.
  - size 135168 bytes, sha256 `a524c85a20a6f383…` (full hash recorded in
    `var/evidence/wp1-gate/canonical-databases.json`),
  - `PRAGMA quick_check` = `ok`,
  - schema version 3, event count 0.
- `var/wp1-hermes-home/`: empty directory created by the frozen run.
- No background python process and no JARVIS/Foundry scheduled task remained
  after the freeze.
- The frozen run's code changes (atomic per-statement migration, crash
  fixture, reconciliation diagnostics, artifact hashing, doctor integration)
  are unverified existing work; they were subsequently inspected and tested
  independently in this session (45/45 Foundry tests, Hermes baseline
  317 passed / 8 skipped).

## Canonical Database Preservation Evidence (re-checked read-only)

| Database | schema | events | quick_check | sha256 (prefix) |
|---|---|---|---|---|
| `var/staging-foundation/foundry.db` | 2 | 11 | ok | `6e02e818…` |
| `var/cli-proof/foundry.db` | 1 | 0 | ok | captured in evidence bundle |
| `var/final-proof/foundry.db` | 2 | 0 | ok | captured in evidence bundle |

All three canonical databases are byte-identical in behavior to their
documented baselines (WP0/WP1 evidence) and were NOT modified by the frozen
run. `var/wp1-staging/foundry.db` is a new, empty, consistent database — not a
mutation of any canonical artifact.

## Classification And Root Cause

Severity S4 (non-consequential defect/observability issue): the frozen run
created a new isolated artifact instead of using the documented staging path,
and did not record any evidence. Root cause: the headless run lacked the
preservation instruction context (or ignored it) when initializing its own
staging home.

## Containment And Decision

- Mutation frozen: no further writes to `var/wp1-staging` by the frozen run
  (process terminated).
- No rollback performed: the database is consistent (`quick_check=ok`), empty,
  and isolated; rolling back a consistent empty artifact would destroy
  evidence without recovering anything.
- Decision: `var/wp1-staging` is adopted as the project-local WP1/WP2 staging
  area going forward; `var/staging-foundation` remains the canonical WP0/WP1
  evidence artifact and is never mutated.
- The empty `var/wp1-hermes-home` is retained as the isolated HERMES_HOME for
  Hermes baseline runs.

## Preventive Actions

- Recovery notes (`docs/handoffs/RECOVERY_NOTES.md`) now state the canonical
  database paths explicitly.
- All future staging work uses copies under `var/compat-proof/` or the
  designated `var/wp1-staging/`; canonical artifacts are read-only.

## Verification

- `var/wp1-staging/foundry.db` re-opened read-only: quick_check ok, schema 3,
  0 events, and migrates/opens normally.
- Full hash records stored in `var/evidence/wp1-gate/canonical-databases.json`
  (sha256 sidecars via `jarvis_foundry.artifacts`).
