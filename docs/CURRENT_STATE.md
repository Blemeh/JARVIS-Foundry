# JARVIS FOUNDRY — CURRENT STATE

This is the authoritative current-truth surface for the isolated final
recovery candidate. Source, immutable release, runtime, canonical checkout,
trust, and acceptance evidence are reported separately; a candidate is not
described as live merely because it builds or passes a disposable test.

DOCUMENTATION STATE BASE SHA: `08024de4c3b881a3bfcef08e4c7d363540636443`

The SHA above is the source parent of the final documentation reconciliation
commit. It includes the isolated UI endurance-harness hardening; the immutable
runtime release remains the separately identified `fe3eee6…` code build. The
freshness gate requires this relationship and rejects source or test drift
after the pin.

## Identity and release truth

```text
STARTING CANDIDATE SHA:       260abd961ea5f3dca5f0a429a2db4642b828c078
CANDIDATE RELEASE CODE SHA:   fe3eee6340ad2264bd185608807cdc13aedd1445
DOCUMENTED SOURCE PARENT:     08024de4c3b881a3bfcef08e4c7d363540636443
CANONICAL MAIN SHA:           8dfb981876985525621cf10b8b9b67e34468b450
CANDIDATE INSTALLED SHA:      fe3eee6340ad2264bd185608807cdc13aedd1445
CANDIDATE LAST RUNTIME SHA:   fe3eee6340ad2264bd185608807cdc13aedd1445
CANDIDATE SOURCE/RELEASE:     SOURCE_AHEAD (test-harness/docs commits after release build)
CANDIDATE LAST PARITY:        MATCH (release-code worktree while running)
CANDIDATE CURRENT RUNTIME:    STOPPED_AFTER_PROOF (MISSING_RUNTIME)
CANDIDATE INSTALL ROOT:       C:\temp\JARVIS-Full-Product-Recovery-install-fe3eee6
CANDIDATE RUNTIME HOME:       C:\temp\JARVIS-Full-Product-Recovery-runtime-fe3eee6

LIVE INSTALLED SHA:           260abd961ea5f3dca5f0a429a2db4642b828c078
LIVE RUNTIME SHA:             260abd961ea5f3dca5f0a429a2db4642b828c078
LIVE RELEASE ID:              rel-260abd961ea5-36f0f3ad
LIVE SOURCE/RELEASE PARITY:   SOURCE_AHEAD (candidate is not promoted)
RELEASE AUTHORITY:            FILESYSTEM_ONLY_AUTHORITY (no DB current record)
```

Canonical `main` and the live Hermes home were not changed by this lane. The
active production path is:

`Task Scheduler -> jarvis-supervisor-headless.vbs -> immutable CURRENT release -> deployment.headless_launcher -> deployment.launcher -> Supervisor`.

The candidate endpoint proof found one logical Supervisor identity after
collapsing the expected virtual-environment interpreter wrapper. PID,
creation-time identity, exact home, executable, module, release SHA, and
duplicate/stale process state are part of the same runtime contract.

## Schema, runtime, and trust

```text
CURRENT/CANDIDATE SCHEMA:     v37
CURRENT SCHEMA:               v37 (v34 live baseline migrated through v35/v36)
V2 KNOWLEDGE SCHEMA:          v35
V3 DISCOVERY/BUILDING SCHEMA: v36
CANDIDATE TRUST STATE:        LAB (fresh runtime has no certification evidence)
LIVE TRUST STATE:             LAB
TRUST LIFECYCLE:              LAB -> PROVING -> TRUSTED_AUTONOMOUS
                              explicit Supervisor-owned evidence is required;
                              transitions are persisted, auditable, reversible,
                              and never time-based.
```

The trust gate requires acceptance profiles, durability/restart, STOP and
revocation, verifier integrity and false-PASS resistance, routing, release
parity, bounded failures, schema integrity, and owner-control evidence. A
missing item leaves the state in `LAB` or `PROVING`.

## Repaired production spine

The candidate has one canonical path:

`observe -> choose -> act -> mission -> worker -> evidence -> verifier -> repair/continue -> owner visibility`.

The closure repairs include:

- contract-versioned, content-addressed verifier identities and verdicts;
  historical evidence can be re-evaluated under changed manifests without
  idempotency collisions, stale PASS resurrection, or mutable evidence;
- one process/runtime identity and release-parity contract covering
  `deployment.headless_launcher`, PID creation identity, exact home, wrapper
  collapse, duplicate detection, schema, and source/installed/runtime SHA;
- cheap `/api/health` liveness/readiness separate from bounded `/api/status`
  and the expensive full Command Center snapshot;
- sparse idle episodes (`waiting_started`, bounded heartbeats, and
  `waiting_ended`) instead of per-cycle no-op events;
- signed scoped PAUSE/RESUME/REVOKE, mission cancellation, and gate
  re-evaluation, all durable, auditable, reflected in API state, and safe over
  restart;
- evidence-backed trust certification and explicit demotion;
- synchronous immutable-release Task Scheduler launch, deterministic restart,
  and test-artifact cleanup;
- isolated UI startup with dynamic ports and tracked-capture protection.

## Latest validated evidence

The final closure collection contains 2,303 Python tests across Foundry,
convergence, and acceptance: 2,302 passed, one host-capability test was
skipped, and zero failed. Focused verifier, replay, owner-control, trust,
runtime identity, scheduler, migration, API, adversarial, and convergence
suites are included.

Acceptance evidence is retained outside the repository:

```text
C:\temp\JARVIS-closure-evidence-37764ac\accelerated
C:\temp\JARVIS-closure-evidence-37764ac\false-pass
C:\temp\JARVIS-closure-evidence-37764ac\quick-30m
```

The accelerated profile completed 714 cycles in 21.189 seconds of wall time
representing 12 logical hours and is labeled `ACCELERATED_EQUIVALENT_ONLY`.
The real wall-clock `QUICK_30M` profile completed 28 cycles in 1,811.370
seconds (`REAL_TIME_DISPOSABLE`). Both include interruption/restart and
false-PASS checks; the 12-hour and 24-hour real profiles are `NOT_RUN`.

The isolated final endpoint measured five `/api/health` calls at roughly
0.67–0.70 seconds after a 0.99-second cold call, with a stable 3,793-byte
payload. Three `/api/status` calls measured roughly 0.70–0.74 seconds with a
4,424-byte bounded payload. Before the repair, health construction took
approximately 3.6–4.5 seconds and built an approximately 0.9 MB Command
Center snapshot. The high-cardinality full snapshot benchmark now completes
in approximately 0.35–0.42 seconds at about 460 KiB. The endpoint reports
`DEGRADED` when release authority is only filesystem-backed, even while parity
is `MATCH`.

The old live idle pattern was approximately 16,500 events per reason per day
(about 49,500 across the three repetitive reasons). The repaired loop emits
one episode start, at most one five-minute heartbeat, and one end per reason
episode; a 1,000-poll simulation produced four events. The bound is one start
plus 288 heartbeats plus one end per reason per day.

The disposable Windows hard-kill/repeating-trigger probe completed in 125.48
seconds, observed a new scheduled Supervisor owner, and left no test task or
process. The final candidate was also stopped and restarted directly; stopped
state reported `MISSING_RUNTIME`, and the restarted endpoint reported one
logical Supervisor with `MATCH` parity. The isolated console-check launched a
source-backed Vite preview on an allocated port and passed all 12 routes with
zero errors. The long-run endurance harness now uses the same allocated-port,
direct-Node, shell-free ownership contract (static launch gate passed). The
release was built from the code parent `fe3eee6…`; the subsequent test-harness
and documentation-only commits intentionally make the candidate source HEAD
`SOURCE_AHEAD` under strict SHA parity without changing the shipped runtime
code.

## Known limitations and honest boundaries

- The candidate is not promoted to canonical `main` or the live Hermes home;
  live remains on the starting SHA and therefore reports `SOURCE_AHEAD`.
- The candidate release is immutable at `fe3eee6…`; post-build UI harness and
  documentation commits are outside that runtime package and are reported as
  source-ahead rather than silently relabeled as the installed release.
- Candidate and live release authority are filesystem-only until an activation
  records the matching database release manifest; health intentionally remains
  degraded rather than presenting a false current/healthy state.
- Trust remains `LAB` because certification evidence has not been submitted
  for this fresh runtime; no automatic promotion was attempted.
- Provider live inference was skipped by billing protection in the historical
  probe. Muse is configured, but the verifier is marked `session_independent`
  and `provider_independent: false` when provider diversity is unavailable.
- Full 12-hour, 24-hour, and reboot-assisted real-time acceptance profiles are
  `NOT_RUN`; no long-duration PASS is claimed.
- Single-host operation, native desktop interaction, and continuous workflow
  mining remain outside this closure pass.

## Documentation freshness contract

`scripts/doc_staleness_check.py` is the single freshness authority. It pins
this file to the immediate parent source commit, validates the live schema
markers and historical v34/v35/v36 markers, rejects forbidden stale claims in
all current-status surfaces (including the acceptance README), and checks
referenced paths. The final reconciliation commit may change only
`docs/CURRENT_STATE.md`, `docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md`, and
`tests/acceptance/README.md`.
