# JARVIS FOUNDRY — CURRENT STATE

This is the authoritative current-truth surface for the isolated recovery
candidate. It describes source, installed release, runtime, schema, trust,
and evidence as separate facts; it does not imply that a candidate is live.

DOCUMENTATION STATE BASE SHA: `46f20c712a870eb78b84bb96d743fe4ee5ee7d1d`

The full source SHA above is the parent of the final documentation-only
commit. The mechanical gate checks that pin and rejects any later source
change until this file is reconciled again.

## Identity and release truth

```text
CANDIDATE SOURCE SHA:       46f20c712a870eb78b84bb96d743fe4ee5ee7d1d
STARTING CANDIDATE SHA:     260abd961ea5f3dca5f0a429a2db4642b828c078
CANONICAL MAIN SHA:         8dfb981876985525621cf10b8b9b67e34468b450
INSTALLED CURRENT SHA:      260abd961ea5f3dca5f0a429a2db4642b828c078
RUNTIME RELEASE SHA:        260abd961ea5f3dca5f0a429a2db4642b828c078
CURRENT RELEASE ID:         rel-260abd961ea5-36f0f3ad
PREVIOUS RELEASE SHA:       a0aaac5b169d29acaa2ee9f6d081b9a83bef7242
SOURCE/INSTALLED/RUNTIME:   SOURCE_AHEAD (candidate is not promoted)
RELEASE AUTHORITY:          FILESYSTEM_ONLY_AUTHORITY (live DB has no current record)
ACTIVE PRODUCTION PATH:     Task Scheduler -> jarvis-supervisor-headless.vbs
                             -> immutable CURRENT release -> headless launcher
```

The canonical checkout is intentionally untouched by this lane. The live
Supervisor home is `C:\Users\b\AppData\Local\hermes\foundry`; the live
runtime was left running on the installed SHA. The candidate's headless VBS
launcher now waits for its Supervisor child, so Task Scheduler can enforce
`IgnoreNew`; promotion and restart of the live installation remain separate
operations.

## Schema, runtime, and trust

```text
CURRENT/CANDIDATE SCHEMA:     v37
CURRENT SCHEMA:               v37 (v34 live baseline was migrated; live database currently reports v37)
V2 KNOWLEDGE SCHEMA:          v35
V3 DISCOVERY/BUILDING SCHEMA: v36
TRUST STATE (live):           LAB
TRUST LIFECYCLE:              LAB -> PROVING -> TRUSTED_AUTONOMOUS
                              explicit Supervisor evidence and transitions required;
                              no elapsed-time promotion
LIVE SUPERVISOR:              RUNNING, STOP false, one logical process after
                              venv-wrapper collapse; transient duplicates are degraded
```

Trust evidence is append-only and Supervisor-owned. Required evidence covers
acceptance profiles, durability/restart, STOP and revocation, verifier
integrity and false-PASS resistance, routing, release parity, bounded
failures, schema integrity, and owner-control gates. Missing evidence keeps
the system in `LAB`/`PROVING`; it cannot become trusted by heartbeat alone.

## Repaired production spine

The candidate has one canonical path:

`observe -> choose -> act -> mission -> worker -> evidence -> verifier -> repair/continue -> owner visibility`

Key repairs are:

- content-addressed, contract-versioned verifier identities and verdicts;
  immutable historical evidence can be replayed under changed instructions,
  manifests, retries, crashes, and concurrent callers;
- one runtime identity contract for Supervisor, launcher, and
  `deployment.headless_launcher`, including PID creation identity, home scope,
  venv wrapper collapse, duplicate detection, and source/installed/runtime
  parity;
- a lightweight `/api/health` projection separate from the full Command
  Center snapshot;
- sparse idle episodes (`waiting_started`, bounded heartbeats, and
  `waiting_ended`) instead of per-cycle no-op events;
- signed scoped PAUSE/RESUME/REVOKE, mission cancellation, and acceptance-gate
  re-evaluation, all durable and restart-safe;
- explicit evidence-backed trust certification and demotion;
- synchronous, immutable-release Task Scheduler launch with scoped cleanup;
- isolated UI captures and dynamic Playwright ports.

## Latest validated evidence

The latest candidate Python suite contains 2,284 tests and includes verifier,
owner-control, trust, runtime identity, scheduler, migration, API, adversarial,
and convergence coverage. The real Windows hard-kill/repeating-trigger probe
completed once in 125.48 seconds against a disposable home; 24-hour and other
long wall-clock profiles are `NOT_RUN` unless a complete run is recorded.

The candidate health probe measured approximately 0.66–0.74 seconds and
3,871 bytes for the new lightweight payload, while the pre-repair full
Command Center construction was approximately 0.9 MB and 3.6–4.5 seconds.
The live installed process has not been replaced in this lane, so live
runtime parity is intentionally reported as `SOURCE_AHEAD`.

The live ledger's previous idle pattern was approximately 16,500 events per
idle reason per day. The repaired loop records one episode plus a five-minute
heartbeat at most; representative simulation and post-promotion real-time
growth must be recorded before claiming a live bound.

## Known limitations and honest boundaries

- The candidate is not promoted to canonical `main`, and the installed live
  release remains the starting SHA. A final promotion/restart requires a
  separate proven release operation.
- Live provider dispatch and paid Muse use remain provider/account dependent;
  same-provider verification is marked `session_independent` but not
  `provider_independent` when diversity is unavailable.
- A 24-hour real-time acceptance profile is `NOT_RUN` in this session.
- The live legacy release authority and historical reconciliation findings
  remain visible and degraded until a release record and clean runtime proof
  are produced.
- Single-host operation, lexical memory recall, native desktop learning, and
  continuous workflow mining remain outside this closure pass.

## Documentation freshness contract

`scripts/doc_staleness_check.py` defines one freshness rule: this file pins
the immediate parent source commit and the final commit may change only the
two documentation contract surfaces. It also validates the schema markers,
forbidden stale claims, and referenced paths. Any source or test change after
the pinned commit fails closed.
