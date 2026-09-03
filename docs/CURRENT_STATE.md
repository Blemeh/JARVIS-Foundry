# JARVIS FOUNDRY — CURRENT STATE

**The anti-staleness surface of the repository.**

This document is the authoritative current-state record. The frozen roadmap
remains authoritative for sequencing and scope; its execution pointer is
`docs/roadmap/JARVIS_1_0_EXECUTION_STATE.yaml`.

Last reconciled: 2026-09-03 for the isolated V11 A3/A4 exit-proof freeze.
The A3 bridge is integrated and the candidate-neutral A3/A4 runner is ready;
real A3, immediate A4, and the 48-hour elapsed gate are still pending at this
documentation boundary. V10 remains live and unchanged.

DOCUMENTATION STATE BASE SHA: `5f8646289c8d4cdc8ddb27bc688ce9141d8cc973`

The SHA above is the immediate parent of this final docs-only reconciliation
commit, as required by `scripts/doc_staleness_check.py`.

```text
V10 ACCEPTED PREDECESSOR:     042942680687b0ee0382b439b1ddba5a9db7f089
V10 PROMOTED PRODUCT SHA:     faa861c67399d5a3755d778224ff65513bd933a1
V10 CANONICAL LANDING SHA:    faa861c67399d5a3755d778224ff65513bd933a1
V10 LIVE RELEASE ID:          rel-faa861c67399-5afeb024
V10 ROADMAP AUTHORITY SHA:    e9b2155bd669887c4778db0b16a4ef7861d2c9ff
CURRENT/CANDIDATE SCHEMA:     v40 (isolated V11 exit-proof candidate only)
LIVE SCHEMA:                  v38
V2 KNOWLEDGE SCHEMA:          v35 (historical additive migration)
V3 DISCOVERY/BUILDING SCHEMA: v36 (historical additive migration)
v34 live baseline:            historical predecessor retained for migration proof
V10 STATUS:                   CLOSED
V10 EXIT GATE:                PASSED
CURRENT PHASE:                V11_INITIATIVE
CURRENT GATE:                 A3_A4_EXIT / PROOF_READY
EXECUTION STATUS:             V11_INITIATIVE / A3_A4_EXIT / PROOF_READY
V11 STARTED:                  YES (candidate only)
V11 COMPLETE:                 NO (A3 real-provider and A4 endurance gates remain)
V11 PROMOTED/LIVE:            NO
A2 IMPLEMENTATION SHA:        91c3deaeefb3fc71ffd091ad0f4abc2e2fd8023c
PRECONVERGENCE SOURCE SHA:    51eb998642fee713458998bd3f44219a76ab670d
CONTINUOUS OBSERVATION INPUT: 8004a8c16aceb888db1d1401c3bc67842cb4746f
INTEGRATION BASE 2 SOURCE:    899e2d3affee41d02145514acb9c308db57afaf6
A4 INPUT SHA:                 daae32cb14d6d8882166d30d515daa9d796c6857
OUTCOME-TRUTH INPUT SHA:      02e62a85a50bd642a1abdc6e415a2215ac5d2689
AUTHORITATIVE V11 INPUT:      ae9ac06b6f9174c810b0e658daaf81c7b683ae8b
SOURCE FREEZE:                f7cee034384ac191dfbac9f910dd6f6d3eb520d4
A3/A4 HARNESS INPUT:          3716294bf7d104f03e1fa2d990b2a28654ada1cb
EXIT-PROOF SOURCE SHA:        5f8646289c8d4cdc8ddb27bc688ce9141d8cc973
TRAINING-TIER POLICY:         OWNER_AUTHORIZED / CANONICAL CONFIG ENABLED
```

## V11 A3/A4 exit-proof source candidate

The isolated proof candidate is branch `candidate/v11-exit-proof-20260903` in
`C:\temp\JARVIS-V11-Exit-Proof-20260903`. It starts from the exact overnight
candidate `ae9ac06b6f9174c810b0e658daaf81c7b683ae8b`, retains source freeze
`f7cee034384ac191dfbac9f910dd6f6d3eb520d4`, and remains schema v40. Exit-proof
implementation source is `5f8646289c8d4cdc8ddb27bc688ce9141d8cc973`;
the final proof SHA is the clean docs-only commit immediately following that
source, as enforced by `scripts/doc_staleness_check.py`.

Eligible clean input lanes and their source SHAs were:

- Backend Stack: `c8006303fc769cf6d46e22f3cc3203a70bf4aa73`
- A3 initiative bridge: `630304b7809de85850e53307191f0f054d86ad61`
- Command Center final: `af1911392915c87d369baa12e5ea02d9c1cf7304`
- A3/A4 proving harness: `3716294bf7d104f03e1fa2d990b2a28654ada1cb`

Integration was semantic and ordered Backend Stack, A3, Command Center, then
the proving harness. Documentation-only and historical terminal commits were
not consumed. The candidate preserves one Executive, scheduler, Fabric Catalog,
MissionLoop/effects authority, WIP=1, STOP, idempotent effect identity, and
final-outcome truth.

The autonomous loop is:

`observe -> meaningful signal -> candidate -> dedupe -> research dossier ->
score/reject -> capability requirements -> project context -> admission ->
objective -> verification plan -> MissionLoop/Fabric -> isolated build/test ->
reconciliation -> verification.accepted -> final outcome -> learning ->
briefing -> WAIT/continue`

The A3 source bridge is integrated into the existing Executive/MissionLoop
spine, with explicit A3/continuous-observation mutual exclusion and the
`cap.tests.run` admission requirement. The owner explicitly authorized the
existing canonical `security.allow_data_training_tiers_noninteractive: true`
policy for unattended proving. The repository authority is enabled through
`config/worker_routing.yaml`; no bypass or alternate policy mechanism exists.
The real `opencode_go/muse-spark-1.2-contributor` flagship remains
`PENDING/NOT_TESTED` until it runs against the final proof SHA.

The A4 proving tooling is integrated and now includes an exact-SHA,
candidate-neutral real driver, immediate six-point fault campaign, durable
post-candidate and post-selection recovery, hard-zero duplicate and false-
success assertions, and the separately guarded `--allow-48h` runner. Full
fault-injection and elapsed proving remains `PENDING/NOT_TESTED` at this freeze.

The first real A3 attempt against proof SHA `cb85fa8a4717fbed4866b18d6c745fd3dbd23381`
correctly ended `BLOCKED`: the observer treated TODO text inside a Python test
fixture string as work, and the real provider returned HTTP 500 after its three
bounded retries. It created no effect or accepted completion. The observer now
uses Python comment tokens and anchored markers, and the pinned Ruff receipt
truthfully establishes `cap.system.diagnose`; all affected proof restarts from
the new exact SHA.

Accepted final mission outcomes now feed calibration, World Intelligence, and
Tier-C memory exactly once by outcome identity. Failed and blocked outcomes
remain distinct from success learning and are projected as human-readable
`mission outcome <status>` memory summaries. Command Center autonomy is an
additive read projection/API/UI surface with candidate-scoped optional payloads;
it does not create a competing authority. Frontend checks passed: 132 Vitest
tests, TypeScript build, ESLint, and production Vite build. Twenty-four
simulated/source-labelled truth screenshots were captured and visually
inspected across eight routes and three viewports.

One broad Foundry regression was executed exactly once: `2493 passed, 14
skipped, 3 initial failures`. Two failures were the expected stale
documentation pin after evidence capture; the third exposed the Tier-C failed
mission-summary regression and was repaired in `f7cee034`. The repaired failure
and adjacent outcome/Command Center coverage passed in a focused `20 passed`
rerun. The post-pin documentation check is required before any readiness
claim. No promotion, live mutation, or push was performed.

## Roadmap and execution state

The frozen authority is
`docs/handoffs/JARVIS_FROZEN_MASTER_ROADMAP_TO_1_0_2026-09-01.md`.
`V10_EXIT = PASSED` and `V10 = CLOSED`. The isolated V11 A2 candidate is now
`PASSED_CANDIDATE`; V11 itself is neither complete nor promoted. The V11
exit-proof candidate carries A2, continuous observation, the integrated A3
candidate-to-objective bridge, A4 durability, accepted-outcome truth, and the
candidate-neutral proving driver together. The next authorized scope is the
real A3 flagship, immediate A4 campaign, and then the guarded 48-hour run
against one exact proof SHA. This corrects the stale earlier statement that
the A3 bridge was absent.

The accepted history contains the V10 convergence line, the semantic
roadmap/control-tower authority, V10_EXIT sensory completion and audit fixes,
and packaging repair `faa861c...`. Canonical integration was a clean
fast-forward. Promotion-rehearsal worktree changes and externally deleted
CHECKPOINT files were not swept into canonical history.

## V11 pre-convergence candidate truth

Branch `candidate/v11-preconvergence-20260902` in
`C:\temp\JARVIS-V11-PreConvergence-20260902` starts at exact A2 head
`00ee2e148c12151699a796649a1cb87ed892a69b`. A4 input
`daae32cb14d6d8882166d30d515daa9d796c6857` and accepted-outcome truth input
`02e62a85a50bd642a1abdc6e415a2215ac5d2689` were selected as patch-equivalent
single commits from their shared canonical base `2905c206...`, not merged as
terminal histories. Their preserved integration commits are `318f73f...` and
`6743d1e...`.

The only cross-lane semantic repair is `51eb998...`: if a process dies after
the atomic `mission.completed` append but before WorkerLoop writeback, the
existing Executive terminal reconciler now creates or replays final outcome,
calibration, WorldModel, and Tier-C learning before parent-plan continuation.
This preserves the canonical order `verification.accepted -> evidence ->
mission completion -> final outcome -> learning/briefing` and prevents an
accepted outcome from being lost after restart.

The A2 real-source flagship was rerun from this tree and passed with schema
v39, three ready source classes (GitHub, project, World), truthful local web
unavailability, zero objective/mission/effect/external-write creation, zero
duplicate candidates, replay event delta zero, STOP-before/after proof, SQLite
quick check `ok`, and 188-event reconciliation with zero discrepancies. The
A4 eight-point hard-kill/restart/STOP harness passed, including zero duplicate
missions, builder steps, effects, and terminal continuation. Post-completion
replay created exactly one final outcome, calibration, World observation, and
memory observation. The 11 accepted-outcome tests proved preliminary PASS
cannot create success learning and accepted replay is exactly-once.

This is a pre-convergence source base, not A3, not the V11 exit gate, and not a
promotion candidate. The final broad V11 regression, A3 flagship, elapsed
unattended gate, live activation, and production trust remain untested.

## V11 Integration Base 2 candidate truth

Branch `candidate/v11-integration-base2-20260902` in
`C:\temp\JARVIS-V11-Integration-Base2-20260902` starts at exact
pre-convergence SHA `d5cca15df800831f0409253b8a65d90ea16249ba`.
Continuous-observation implementation `5282a31dd6e7f52aaa280e665cf03deefdf4057f`
was selected from source tip `8004a8c16aceb888db1d1401c3bc67842cb4746f`
as an exact stable patch (`ae7ea2f13c0093d48556756795bbd6744ece792d`),
not by merging its terminal history. Integration source is `899e2d3...`.

Schema v40 is one additive successor to A2's v39 migration. It extends the
existing monitor projection as the durable source/configuration/due/backoff
authority and adds per-evidence-revision identity to the existing ResearchQueue.
The disposable migration test now explicitly reopens one database through
v38 -> v39 -> v40, verifies one migration row per version, reopens v40 again as
a no-op, and passes SQLite quick check and ledger/projection reconciliation.

Continuous observation remains at the A2 boundary:
`observe -> candidate -> dedupe -> research -> reject/eligible -> WAIT`.
It uses existing Continuous Executive time wakes, dispatches only due monitors
in a bounded rotation, suppresses unchanged input before candidate work,
permits one bounded research pass per meaningful evidence revision, persists
cadence/backoff/readiness across reopen, and checks STOP before configuration,
wake, source read, or research dispatch. It creates no objective, mission,
effect, worktree, external write, scheduler, Executive, MissionLoop, or
capability authority.

The fresh A2 flagship passed on schema v40 with 192 reconciled events, zero
objectives/missions/effects/external writes, and same-input replay delta zero.
The fresh continuous-observation proof passed with 148 reconciled events:
pre-due added zero events; unchanged due input added zero observations,
candidates, refreshes, or research; one changed revision refreshed one existing
candidate and ran one research pass; reopen preserved the due cycle; STOP added
zero events. GitHub/project/World reads were READY, local web was honestly
unavailable, and Gmail remained `BLOCKED/DEFERRED`.

The combined focused set contains 57 tests: A2, continuous observation, A4's
eight-point durability harness, 11 outcome-learning cases, and migrations.
A3 and the final broad V11 suite remain intentionally unrun here.

## V11 A2 candidate truth

The isolated branch `candidate/v11-a2-initiative-20260902` was built from
verified `origin/main` `2905c206fed0461bf3085bc0cb43773be913300a` in
`C:\temp\JARVIS-V11-A2-Initiative-20260902`. Implementation source is
`91c3deaeefb3fc71ffd091ad0f4abc2e2fd8023c`; canonical main/origin and live
production were not changed.

A2 reuses World Intelligence observations, discovery candidates/monitors, the
existing ResearchQueue, `ExecutiveDecisionService`, Tier-C repository-seeded
owner context, and the Capability Fabric Catalog. It adds schema v39 only to
disposable/candidate databases for indexed observation lineage, judgment,
research, and cycle projections. The runtime path is feature-gated off by
default and is mutually exclusive with execution-capable Executive/discovery
flags. It returns before candidate admission, objective creation, MissionLoop,
effects, worktree creation, or provider writes.

The final unattended proof used real GitHub, curated Playwright web, and
bounded owner-project reads; World signals were available but idle, and Gmail
truthfully remained `BLOCKED/DEFERRED`. The cold cycle recorded 10 observations,
generated 6 raw and 4 unique candidates, suppressed 2 duplicates, researched
2 eligible finalists, rejected 3 candidates, and retained 1 at the admission
boundary. It returned `WAIT`. The survivor was
`Review Python 3.14 release notes for JARVIS runtime compatibility`.

Hard truth was zero objective creations, mission creations, mutating effects,
external writes, discovered-work worktrees, and duplicate candidate records;
no unbounded loop was detected. Same-input replay added zero events. Changed
input added one candidate delta. Prior rejection, STOP-before, STOP-after-
observation, interruption/restart, partial source failure, DATA_ONLY adversarial
content, SQLite quick check, and 196-event ledger/projection reconciliation all
passed. The compact proof is
`C:\temp\JARVIS-V11-A2-Initiative-20260902-evidence-final4\V11_A2_FLAGSHIP_PROOF.json`
(SHA-256 `b3eb1709b1a14f37d5964767e762b2fc5456d507e93ca07b1be21ac948817267`).

## Live release and rollback authority

```text
LIVE INSTALL ROOT:             C:\Users\b\AppData\Local\JARVIS
LIVE HOME / DATABASE:          C:\Users\b\AppData\Local\hermes\foundry
PREVIOUS LIVE:                 c41a3ad8ef3debda6f84e6d4dac2e0ac525c0864
CURRENT LIVE:                  faa861c67399d5a3755d778224ff65513bd933a1
INSTALLED SHA:                 faa861c67399d5a3755d778224ff65513bd933a1
RUNTIME SHA:                   faa861c67399d5a3755d778224ff65513bd933a1
RELEASE ID:                    rel-faa861c67399-5afeb024
RELEASE TREE:                  6d66b73a75a07a95360cfa2a9c7bbfa72821137b
RELEASE AUTHORITY:             DATABASE_LIVE_SLOT / CONSISTENT
TRUST:                         SUSPENDED
```

Before activation, canonical owner STOP quiesced the runtime. The WAL-safe
rollback anchor is
`C:\temp\JARVIS-V10-Live-Closure-20260902\foundry-backup-pre-v10-faa861.db`:
121602048 bytes, SHA-256
`16add2591d0f444f41c86ebc7a7f7fed2a457421a2a2891b3fdd4737c20be7c3`,
schema v38, readable, SQLite integrity and quick checks `ok`. CURRENT/PREVIOUS,
database live slot, Supervisor, Scheduler, process census, STOP, and trust were
captured before activation. Rollback to c41 remains executable; no hard rollback
condition fired.

## Installed V10 capability truth

The immutable release was built only from `faa861c...`. Manifest source and Git
SHA match; tree is `6d66b73...`; schema compatibility remains v38; and the
release-local runtime closes its own web dependencies:

- Playwright 1.62.0.
- Trafilatura 2.2.0.
- Chromium 151.0.7922.34 under the release-local `.pw` directory.
- Browser health `READY`, Chromium launch, bounded public navigation,
  Trafilatura extraction, and cleanup passed from the installed release.
- No worktree, user-site, global package, or global browser fallback was used.
- Bounded interactive structural submit produced one completed receipt, one
  verified readback, one effect, and no orphan Chromium.
- Disposable authenticated-session persistence and restore passed. A real
  owner-authorized authenticated site remains `BLOCKED/NOT_TESTED` because no
  authorized credential/session was available.
- Disposable CSV-to-DOCX generation, reopen, and content verification passed.
- Public GitHub READ passed. Public GitHub WRITE is `NOT_TESTED` and was not
  performed.
- Gmail remains `BLOCKED/DEFERRED`; no credentials were manufactured.

The V10 capability spine remains the established World Intelligence ->
Continuous Executive -> Capability Fabric -> MissionLoop/effects/evidence/
verifier -> World update path. `effects.py` remains the effect identity and
journal authority. Ambiguous external outcomes remain `UNKNOWN`/`FROZEN` until
reconciled, and unavailable provider evidence is not inferred from local proof.

## Live validation, control, and observation

Immediately after promotion, `/api/health`, `/api/status`, and the Command
Center primary projection passed with source/install/database/runtime parity,
schema v38, SQLite integrity `ok`, consistent ledger/projection reconciliation,
one valid Supervisor, Scheduler ownership, and truthful `SUSPENDED` trust.

STOP froze the Supervisor and blocked new dispatch/event continuation across
two separated observations. RESUME returned canonical state to `RUNNING`. The
exact owned Supervisor PID was then hard-killed; Scheduler restored exactly one
valid owner in 15.59 seconds. The recovered runtime remained `faa861c...` with
no duplicate lineage, duplicate effects, nonterminal action residue, false
mission completion, or orphan Chromium. Post-recovery database integrity and
reconciliation passed.

The bounded post-promotion sampler ran 11 samples over 316.4 seconds. Every
sample passed release identity, release authority, single-Supervisor ownership,
Scheduler, reconciliation, SQLite quick check, and browser cleanup. Full SQLite
integrity passed at both ends. Event growth was 25. Health latency max was
1273.67 ms; Command Center latency ranged from 69.95 to 2339.39 ms, with no
24-second-class regression. Trust remained consistently `SUSPENDED`.

## Trust and remaining boundaries

`SUSPENDED` is the correct production trust state. V10 closure proves this
exact release, its installed capabilities, rollback/control/recovery, and the
bounded live window. It does not prove V11 endurance, a reboot cycle, provider
diversity, Gmail, public GitHub write, a live authenticated account, or the
owner-governed trust gates. Those remain explicitly deferred or not tested.

Distributed/multi-machine operation and later roadmap capabilities remain out
of scope. Rollback is restore-oriented because schema v38 has no reverse
migration.

## Evidence and maintenance

The current Integration Base 2 handoff and exact A3 integration map are
`docs/handoffs/V11_INTEGRATION_BASE2_20260902.md`. The pre-convergence handoff
remains `docs/handoffs/V11_PRECONVERGENCE_20260902.md`; the earlier A2 handoff is
`docs/handoffs/V11_A2_CANDIDATE_20260902.md`. The compact V10 live-closure record is
`docs/handoffs/V10_LIVE_CLOSURE_20260902.md`. Detailed disposable evidence and
probe scripts are retained outside the repository under
`C:\temp\JARVIS-V10-Live-Closure-20260902`. Historical V9/V10 candidate
handoffs remain evidence, but this file and the execution-state YAML govern
current claims.

Any later mission that changes HEAD, schema, live release, Supervisor/STOP,
trust, capability state, or evidence roots must update this file. The final
documentation staleness check must remain green.

## Next authorized action

Run the genuine cold A3 flagship against the final clean proof SHA, then the
immediate six-point A4 campaign. Only after both pass, start the separately
prepared 48-hour runner with `--allow-48h` against that same SHA. Until the
elapsed gate completes, V11 exit remains pending. Only a later, separate
owner-authorized promotion gate may consider live state; this candidate is not
promotion authority or production trust.
