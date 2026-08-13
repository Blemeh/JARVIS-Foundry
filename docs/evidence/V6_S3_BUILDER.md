# V6-S3 — ISOLATED CANDIDATE BUILDER

```text
STATUS: PASS
PHASE: V6_CONTINUOUS_SELF_EVOLUTION
SLICE: S3 — isolated candidate builder
OBJECTIVE: candidates build ONLY inside registered canonical candidate
  roots with separate runtime homes, sanitized environments, secret-
  scanned commands, per-root build locking with stale-marker reaping,
  outside-write detection, partial-build/crash/timeout handling,
  concurrency refusal, and unreachable release-pointer surfaces.
BASE COMMIT: e86a0bd (S2 close)
CANDIDATE RELEASE: none (machinery slice)
RISK CLASS: R3-class machinery (candidate execution control) built and
  verified inside the isolated V6 construction; live surfaces untouched
HYPOTHESIS: n/a (machinery; exercised by the S11 ladder)
BASELINE METRIC: full suite 760 EXIT=0 at S2 close
SUCCESS THRESHOLD: root registration canonical + idempotent; unregistered
  roots / unknown improvements / secret-bearing commands refused; env
  sanitized (no HERMES_HOME/JARVIS_HOME/credential vars; JARVIS_HOME
  scoped inside the root); stale markers reaped; live markers refuse
  concurrency; outside writes fail the build; release pointers
  unreachable from the candidate process; all five projection touch
  points wired; suite green
GUARDRAILS: isolated runtime DB only (var/v6-runtime/v6.db, now schema
  v23); live DB stays v20; no live writes; build subprocesses run with
  cwd=root and a stripped environment; never pass the runtime DB path
  to a candidate
ACTIONS TAKEN: TDD red (15 tests) -> implemented evolution/builder.py
  (register_candidate_root, registered_roots, build_candidate,
  candidate_builds, BuilderError; Windows-safe process liveness probe)
  -> migration v23 + PROJECTION_TABLES + reconcile key map + database
  row/all/count allowlists -> focused green (56 total across S1-S3) ->
  runtime DB migrated to v23 (reconcile consistent) -> full suite
FILES CHANGED: src/jarvis_foundry/evolution/builder.py (new);
  tests/foundry/test_evolution_builder.py (new, 15 tests);
  src/jarvis_foundry/migrations.py (SCHEMA_VERSION 23 + migration 23);
  src/jarvis_foundry/projections.py (candidate.build_started/completed/
  failed/reaped handlers, PROJECTION_TABLES); src/jarvis_foundry/
  reconcile.py (key map); src/jarvis_foundry/database.py (row/all/count
  allowlists)
LIVE SYSTEMS CHANGED: NONE (verified read-only at close time)
TESTS: 15 new S3 tests + S1/S2 regression (41) + FULL SUITE 775 passed
  EXIT=0 (324.01s; var/evidence/v6-s3/suite-v6-s3-full.txt,
  sha256 18f84d8295dd3168516a83cf7a556a96cafd0f67e3e25f18bd3e19bf7800cdbf)
VERIFIER: <fill after dispatch>
EVIDENCE: var/evidence/v6-s3/ (suite artifacts, verdict JSON);
  var/v6-runtime/v6.db (isolated runtime, schema v23, consistent)
CANARY: none (machinery slice)
PROMOTION OR ROLLBACK: none
COST AND RESOURCE USE: local only (pytest runs; subprocess builds)
UNRESOLVED RISKS: same-provider verifier limitation (D-031); build
  sandboxing is best-effort containment (canonical roots + stripped env
  + outside-write detection), not an OS jail — the outside-write scan
  covers the root's parent and the runtime DB family is excluded; deep
  escapes (writes to arbitrary other paths) are caught only when they
  land in the parent — recorded as a documented boundary, mitigated by
  the release gate (artifacts are hashed and verified before any
  promotion can reference them)
NEXT AUTONOMOUS ACTION: S4 — reproducible evaluation laboratory
OWNER INPUT REQUIRED: NONE
```

## Design

`src/jarvis_foundry/evolution/builder.py`:

- `register_candidate_root(db, root, *, controller)` — canonicalizes,
  refuses roots inside the runtime home (live-like surface), creates
  the dir, records `candidate_root.registered` (idempotent per resolved
  path; roots live in the event stream, no projection table).
- `build_candidate(db, *, improvement_id, build_command, candidate_root,
  controller, timeout_s)`:
  - refusals: unknown improvement; unregistered root; secret-like
    command content (V5-S7 boundary guard family); live build marker
    on the root (concurrency refusal via Windows-safe process
    liveness probe — `os.kill(pid, 0)` would TERMINATE on Windows);
  - stale marker (dead pid) → `candidate.build_reaped` (build row
    REAPED, history preserved) then the new build proceeds;
  - env sanitization: strips HERMES_HOME/JARVIS_HOME and every
    *_API_KEY/*_TOKEN/*_SECRET/*_PASSWORD variable; sets JARVIS_HOME
    to <root>/runtime-home (candidate-scoped runtime home); the
    runtime DB path is never passed to the subprocess (release
    pointers unreachable by construction);
  - lifecycle: candidate.build_started → build_completed (exit 0,
    artifact hashes of deliverables, duration) | build_failed (exit
    code / timeout / outside-write-detected) | build_reaped (crashed
    executor); each event keyed by build_id (fresh attempt = fresh
    id; re-delivery dedupes); the marker is removed on completion/
    failure so retries are bounded and allowed;
  - outside-write detection: parent-directory entry diff before/after
    (the runtime DB's own WAL/-shm family excluded).

Projection `candidate_builds` (migration v23, status CHECK
BUILDING/COMPLETED/FAILED/REAPED) wired into PROJECTION_TABLES, the
reconcile key map, and database row/all/count allowlists. Handlers
validate mandatory keys and refuse unknown builds inside the append
transaction (completed/failed on a missing build → rollback).

## Tests (15)

root registration canonical + idempotent; runtime-home roots refused;
unregistered root refused; unknown improvement refused; secret-bearing
command refused (zero build events); successful build (artifacts,
started+completed); failed build (exit code); timeout; env sanitized
(no HERMES_HOME/JARVIS_HOME/API keys; JARVIS_HOME scoped inside root);
outside write detected → FAILED; release pointers unreachable (cwd and
runtime home inside root, no DB path); stale marker reaped then build
proceeds; live marker refuses concurrent build; parallel builds on
distinct roots isolated; projection/schema integrity (pinned to the
imported SCHEMA_VERSION constant >= 23).
