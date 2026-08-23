# V4/V5/V6 + Cinematic Stage Orientation

Status: IN_PROGRESS
Observed: 2026-08-23

## Exit predicate

The stage is complete only when the accepted cinematic UI, V4, V5, and V6
source work is accounted for and semantically integrated; the combined source
gate and adversarial review are fresh and green; local `main` equals
`origin/main`; an immutable release is built from that exact tree; rollback
identity and a readable database backup are retained; the supported live
runtime proves the required runtime, STOP/resume, restart, V4, V5, V6,
cinematic UI, and bounded end-to-end flows; and voice/localization remain
`DEFERRED_BY_OWNER`.

## Integration lane

- Worktree: `C:\temp\JARVIS-V456-Cinematic-Integration`
- Branch: `integration/v456-cinematic-stage-20260823`
- Base: `origin/main` at `6db467a2b0e6bdc37adbc4cbfac309418e33afec`
- Main and origin/main matched at orientation and after fetch.
- Existing worktrees and candidate histories remain untouched.

## Current source truth

- Cinematic commits `b52381e`, `c6f623f`, and `55b2946` are ancestors of
  current `main`.
- The older mock UI candidate `f38f6b1` is not an ancestor of current `main`.
- `candidate/jarvis-frontend-adaptation` and
  `integration/cinematic-command-center` both resolve to `55b2946`.
- V4, V5, and V6 candidate local and remote refs resolve to the same exact
  heads recorded in `CANDIDATE_MATRIX.md`.

## Live ownership snapshot

Read-only checks found:

- `CURRENT=769715a7f8aee22bf4ab2db8870c37e6e9c9ef1b`.
- `PREVIOUS=9a10910f78a4a618fba4c916b418056e4ada1991`.
- The live database is `C:\Users\b\AppData\Local\hermes\foundry\foundry.db`.
- Live schema is 34. The integration source expects schema 36. Doctor reports
  `FILESYSTEM_ONLY_AUTHORITY` and a version mismatch, with no integrity,
  missing-row, or unexpected-row mismatches.
- The durable supervisor row is `RUNNING`, trust `LAB`, and the emergency
  stop sentinel is absent. The installed `JARVIS-Foundry-Supervisor` task is
  `Ready`.
- Two release-769 launchers are present, PID 7676 and PID 16864. Separate
  pytest supervisor processes from another disposable lane are present and
  are not owned by this worktree.
- There is no repository release-lock row and no `JARVIS\locks` directory.

The live process and database state are evidence to reconcile before any
promotion, not permission to mutate it.

## Owner-control boundary

The live ledger has older active rows for bounded local probes and for goals
that prohibited deployment, provider use, repository effects, and JARVIS
source effects. The current owner goal explicitly requests this full
V4/V5/V6/cinematic source, release, and live-closure stage. The older rows are
preserved and must be rechecked against the current goal before the live
mutation phase. No current STOP was observed.

## Scope decisions

- Voice interaction, speech I/O, wake word, and English/Albanian localization
  remain `DEFERRED_BY_OWNER`.
- Credentialless external accounts remain truthful `UNCONFIGURED` or
  `BLOCKED`; no credentials will be invented.
- The cinematic frontend already in main is the baseline. The older UI is not
  merged a second time.
- Source integration is isolated from the running Supervisor and live DB.

## Governing choices

- Foundational thinking selects the verified current main and one stage lane
  before any candidate merge.
- Boundary discipline keeps source integration, release construction, and
  live promotion as separate evidence classes.
- Outcome-oriented execution keeps the full stage predicate intact instead of
  stopping at a green candidate suite or push.
- Prove-it-works requires fresh checks against the real artifacts and runtime,
  not screenshots, fixtures, or historical reports.
