# JARVIS FOUNDRY — CURRENT STATE

**The anti-staleness surface of the repository.**
This document is the single authoritative current-state record. It is maintained by
every promotion/remaster mission (see `docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md`).
If this document and another current-status document disagree, THIS document wins
for "what exists now" claims; the architecture contract wins for binding invariants.

Last reconciled: 2026-08-23 (full candidate archaeology, serial source
integration, and isolated verification completed through the Command Center
truth package; canonical push is the remaining owner-authorized step)

```text
CURRENT CANONICAL HEAD:      3733e73 (canonical main and origin/main after the normal
                             full-reconciliation push; source promotion is complete,
                             live runtime promotion remains owner-authorized)
INTEGRATION BASE:            ceb90d7d201b4775757cae0f7e03ae1e2d6f22e2
AUDIT SOURCE:                26e73c7 (residual audit environment scoping, absorbed
                             after Q1-Q3 semantic review)
CURRENT WORK BRANCH:         integration/full-reconciliation-2026-08-23
LOCAL RUNTIME LANE CHECKPOINT: 3733e73
                              (isolated source/test checkpoint; no live runtime,
                              provider, scheduler, or production mutation)
CURRENT STATE RECORD:        this source checkpoint is not a live promotion claim;
                             canonicalization and the normal push are pending
TESTED CODE HEAD:            2084e5b source checkpoint (parent of the evidence-only
                             canonical pin); release, V3 hard/operational,
                             worker-control, and Command Center UI gates were run
                             in the isolated integration worktree
FINAL EVIDENCE HEAD:         evidence/release/overnight/run-final-20260823-02/RUN.json
                             is the final source-vs-runtime evidence record
FULL SUITE:                  FINAL combined source suite is pending on this
                             checkpoint; historical candidate-suite results remain
                             provenance only
FOCUSED REMEDIATION TESTS:   Fresh serial release/V3/worker-control gates PASS;
                             Command Center UI: lint, typecheck, 108 unit tests,
                             163 e2e tests, build, product 212/212, and security PASS
FULL SUITE FINAL GATE:       pending final repository-wide source validation; no
                             live provider, database, scheduler, or runtime probe
DEPLOYED RELEASE:            769715a7f8aee22bf4ab2db8870c37e6e9c9ef1b (CURRENT)
PREVIOUS RELEASE:            9a10910f78a4a618fba4c916b418056e4ada1991 (PREVIOUS)
DEPLOYED MANIFEST:           rel-769715a7f8ae-060c9abc; legacy pre-remediation
                             manifest has source_sha but lacks git_commit and
                             git_tree; replacement after verifier PASS is required
PROVIDER CONFIG:             %LOCALAPPDATA%\JARVIS\config\provider.yaml
                             (opencode_go / muse-spark-1.2-contributor / OpenCode Go URL)
CURRENT SCHEMA:              v34 live deployed database baseline (production unchanged)
                             V2 KNOWLEDGE SCHEMA: v35 (24h-integration knowledge brain)
                             V3 DISCOVERY/BUILDING SCHEMA: v36 (integrated on main)
                             CURRENT/CANDIDATE SCHEMA: v36 (migrations.SCHEMA_VERSION);
                             live database remains v34 until owner-approved migration;
                             LIVE_PROMOTION_DEFERRED
FOCUSED EXECUTIVE SUITE:     32 passed (tests/foundry/test_executive_intelligence.py)
FRONTEND:                    isolated source checkpoint typecheck clean; vitest
                             108/108, Playwright 163/163, product 212/212, build,
                             lint, and security scan PASS; live UI remains unproven
EXECUTIVE INTELLIGENCE:      ACTIVE-CAPABLE, feature-flag OFF by default
                             (worker config `executive.selection_enabled`).
                             Durable owner goals from authenticated GOAL
                             directives; organic candidate eligibility via
                             trusted admission events (epoch-gated); historical
                             firewall; comparative decision model with recorded
                             rejection rationale for every runner-up; keyword-
                             bait damping; useful idleness as distinct WAIT kinds
                             (NO_ELIGIBLE_CANDIDATES vs COMPARATIVE_WAIT);
                             outcome calibration PASSED positive / FAILED negative /
                             BLOCKED neutral; predictions recorded before SELECT;
                             restart-idempotent decision cycles; STOP/veto checked
                             at selection AND materialization.
COMMAND CENTER:              /executive route + snapshot.executive section project
                             the decision spine (latest decision, rationale,
                             rejected alternatives, prediction-vs-actual,
                             calibration history) from ledger events only.
SUPERVISOR STATE:            NOT_TESTED_AT_INTEGRATION_HEAD (production was not
                             restarted or mutated; historical live snapshots are
                             not promotion proof)
STOP STATE:                  SOURCE_GATED_ONLY (STOP/PID controls pass isolated
                             tests; production STOP state was not changed)
TRUST STATE:                 LAB / SOURCE_CHECKPOINT
RELEASE AUTHORITY:           FILESYSTEM_ONLY_AUTHORITY is explicit and detected
                             by doctor: CURRENT/PREVIOUS are canonical, DB
                             release records are a projection/audit cache and
                             currently contain 0 rows; no manual DB fabrication
PID LEDGER:                  startup/heartbeat reconciliation requires live PID,
                             matching creation identity, and JARVIS ownership;
                             dead PID 15452 classification and reuse rejection
                             are covered by focused regressions
OWNER-GOAL:                  SUPERSEDED_BY_NEWER_IMPLEMENTATION — authenticated
                             GOAL directives in executive/goals.py are current
RELEASE-HANDOFF:            SUPERSEDED — immutable CURRENT/PREVIOUS release
                             authority replaces the deleted handoff mechanism
SCHEDULER AUTHORITY:         JARVIS-Foundry-Supervisor Ready is the production
                             lifecycle authority; JARVIS-Foundry-Worker-Loop is
                             LEGACY_DISABLED and fail-closed against enabling;
                             R3 proving monitor remains a separate read-only task
HEADLESS FIX:                preserved and integrated; live promotion proof pending
AUDIT LIVE STATUS:           IMPLEMENTED_UNPROVEN_AT_LIVE_HEAD — remediation and
                             Q1-Q5 source semantics integrated offline; restart/
                             promotion proof remains owner-authorized and pending
MISSION STATE:               unchanged from 2026-08-19 reconciliation plus M2
                             proof missions in isolated lab databases (live home
                             untouched by proofs)
LIVE PROOF (2026-08-21):     scripts/m2_live_product_proof.py — 12/12 PASS on an
                             isolated lab ledger through production code paths:
                             durable goal -> organic portfolio -> firewall ->
                             selection w/ alternatives -> planted false-success
                             rejected by evidence reconciliation -> auto-repair ->
                             fresh verifier PASS -> outcome calibration both
                             directions -> prediction recorded -> CC projection
                             newest-first -> restart continuity.
WORKER IMPLEMENTATIONS:      Hermes adapter + OpenHands adapter — CANONICAL (merged)
OWNER FILES:                 6 tracked docs under docs/handoffs/, docs/planning/
PERCEPTION INPUT (Phase 2):  SCREENPIPE-LOCAL registered read-only; unchanged
WORKFLOW DISCOVERY (Phase 3): unchanged (disabled by default)
PROCEDURAL LEARNING (Phase 4): unchanged (disabled by default)
```

## What exists now

(Sections below inherited from the cc5cde2 reconciliation remain accurate for
subsystems M2 did not touch. Executive Intelligence entries above supersede any
older text elsewhere in this file.)

- **Deterministic autonomy kernel (V1)** — Supervisor independent of Hermes/LLM
  health: durable append-only event ledger, event-derived projections, directives,
  STOP/EmergencyStop sentinel, leases, recovery, missions, actions, evidence,
  verification, worker context, provider routing, resource registration, Obsidian
  integration, CLI/runtime controls. Proven through WP0–WP8 gates.
- **Knowledge brain (V2/V3)** — knowledge_claims (freshness/contradictions),
  retrieval manifests, worker_manifests, consolidation, discovery runs, cycles.
- **Command center (V4)** — model routing, monitors, scoring, capability states,
  support semantics, verifier truth, lease monotonicity, exactly-once.
- **Multi-system intelligence (V5)** — systems, broker, capabilities, connectors,
  correlation, effects, activation loop. (External platform integration surface.)
- **Self-evolution (V6)** — hypothesis, release registry, builder/lab/verifier,
  shadow, canary, promote, restore, discovery, cycles, acceptance, closure.
- **Full autonomous operation (V7)** — release management, migration rehearsal,
  supervisor adoption, discovery, cycles, executive, command center, capabilities,
  minigates, owner families, continuity, final gate (13/13 slices, suite 985 EXIT=0).
- **Hybrid memory (V8 / Letta influence, NATIVE_JARVIS)** — Tier C experiential
  memory as canonical infrastructure: lifecycle with validation stamps, bounded
  blocks, lexical recall, procedures, curation (provenance/dup/contradiction/
  defense gates), defense (poison/secret), git-backed versioning with divergence
  protection, role context compiler with verifier isolation. Schema v33.
  Promoted at 2f7360e; independently certified; live-canary tested; rollback
  (RESTORE-FROM-BACKUP) proven.
- **Worker system (canonical post-V8)** — framework-neutral worker contract,
  Hermes adapter, OpenHands adapter (disposable-home containment), structured
  result envelopes, worker execution lifecycle, provider identity, role
  isolation, bounded working directories, verifier independence, evidence
  reconciliation, mission-loop bridge, worker-crash recovery. 18-item canonical
  gate PASS at 01e9cc9; merged dea4042; suite 1124 EXIT=0.
- **Workflow Discovery (Phase 3)** — deterministic sessionization of the frozen
  Phase-2 `PerceptionObservation` contract, safe structural tokens, versioned
  fingerprints, bounded similarity, repeated evidence, and a governed bridge
  to the existing candidate generator/scorer. Proven with 36 adversarial tests,
  1,202-test full suite, controlled 10k-observation probe, and independent
  read-only review. Disabled by default; no computer control, audio, continuous
  loop, candidate approval, or mission creation.
- **Procedural Learning (Phase 4)** — versioned/hash-bound OpenAdapt browser
  bundle with a JARVIS hash for every artifact byte, reference-only Tier-C
  procedure, existing ActionEnvelope/capability/connector/effect reconciliation,
  independent local effect oracle, scoped process cleanup, STOP/recovery/
  idempotency, existing V6 zero-effect shadow bridge, and zero-model synthetic
  replay. Proven by the Phase-4 remediation evidence package and 1,225-test
  integration suite; disabled by default; native desktop remains PROBING.

## What is running

Live Supervisor uses home `C:\Users\b\AppData\Local\hermes\foundry`, schema v34,
with a healthy reconciled ledger. At the 2026-08-22 reconciliation doctor
reported 180,915 events, quick check ok, reconciliation consistent, STOP false,
and one active `worker-loop` identity. Supervisor PID 11672 held the singleton
lease. This is the pre-remediation deployed release `769715a7...`; its manifest
predates the new Git provenance fields.

`JARVIS-Foundry-Supervisor` is the single production lifecycle authority.
`JARVIS-Foundry-Worker-Loop` is `LEGACY_DISABLED`; its fail-closed classification
prevents accidental re-enable. The R3 proving-window monitor remains a separate
read-only task and is not a worker launch authority.

The integration merge adds deterministic PID reconciliation, duplicate-launch
fail-closed behavior, explicit release-authority detection, Git commit/tree
release manifests, scheduler-safe test boundaries, V3 hard/operational lineage,
and the Command Center truth/resilience package. These source changes are not
live until independently verified, released, promoted, and restarted.

A disposable deployment rehearsal is recorded at
`docs/convergence/WORKER_LOOP_DEPLOYMENT_CANARY_REHEARSAL_20260819.json`:
a read-only live backup migrated from schema v33 to v34, reconciled with zero
mismatches, passed a Supervisor `--once` smoke, and restored source bytes
unchanged. It is rehearsal evidence only; live migration, restart, activation,
and provider dispatch remain unperformed.

Workflow Discovery is not wired into the live supervisor or perception loop;
only an explicitly enabled bounded cycle can be called by code/tests.

Procedural Learning is not wired into a continuous learner or autonomous
promotion loop. Only the explicit bounded local probe can call the governed
browser substrate, and it cannot activate native desktop execution.

## What has been proven

| Gate | Result |
|---|---|
| V1–V7 evidence slices (WP0–WP8, V4–V7 S0–S13) | PASS (records under `docs/evidence/`) |
| V7 final gate | PASS — 13/13 slices, suite 985 EXIT=0 |
| V8 hybrid-memory certification | PASS — 9/9 criteria (candidate) |
| V8 canonical promotion | PASS — 1027/0/0 post-merge; live canary PASS; rollback drill PASS; post-promotion remediation certification PASS |
| Worker canonical gate | PASS — 18/18 + independent adversarial review |
| Worker-loop deployment rehearsal | PASS_WITH_BOUNDARY — disposable schema34 migration/reconciliation, Supervisor startup, and source-unchanged restore proof; live activation remains deferred |
| Full suite (current source snapshot) | 1,260 collected at reproducible snapshot `f172147` with `PYTHONHASHSEED=0`, 0 failures; the earlier 1,248-test semantic-source record and 1,225-test Phase 4 remediation gate remain historical evidence |
| JARVIS Eyes Phase 2 | PASS — 20/20 faults, rollback/live probe, read-only boundary |
| Workflow Discovery Phase 3 | PASS — 36/36 focused, F1–F25, bounded probe, independent review |
| Procedural Learning Phase 4 | PASS — 23/23 focused, F1–F24, artifact-byte tamper, zero-effect shadow, local browser probe, rollback, fresh independent review |
| Provider-config security closure | PASS — subject-bound historical identity, YAML secret isolation, 1465/1465 suite, promoted to main cc5cde2, immutable release deployed, Muse live probe PASS |
| Post-V7 canonical convergence (R7–R12 semantic closure) | PASS_WITH_BOUNDARY — exact Supervisor authority across low-level evolution/control surfaces; focused gates and the 1,260-test current-source suite pass; isolated promotion, post-promotion runtime probe, and disposable local accessibility audit PASS; live/provider/elapsed boundaries remain explicit |
| R10 authenticated mission-directive consumer | PASS_WITH_BOUNDARY — nonce-bound REPRIORITIZE/REDIRECT reaches the existing MissionLoop path; 26 focused tests and fresh verifier PASS; live Supervisor/Hermes/provider dispatch remains NOT_TESTED |
| R10 exactly-once replay repair | PASS — ordinary mission instructions preserve the caller manifest hash; authenticated REDIRECT remains canonical; 36 focused tests and the 1,260-test current-source suite PASS |

## What is experimental / not canonical

- `C:/Users/b/Documents/JARVIS-Lab/jarvis-hybrid-memory` — completed lab reference (historical).
- `C:/Users/b/Documents/JARVIS-External-Candidate` (experiment/graphiti-openhands-a2a) — external experiment, NOT canonical.
- V8 candidate worktree `JARVIS-Foundry-V8-HybridMemory` — superseded by canonical promotion; keep as historical reference only.
- `.h/` — isolated Hermes development checkout (dev tooling, not JARVIS runtime).

## What is missing / known limitations

- Distributed/multi-machine operation — NOT implemented (single host).
- Semantic (embedding-based) recall/dedup/contradiction detection — lexical/negation only; hook reserved.
- Voice/dashboard surfaces beyond the command-center control layer — limited; disposable local Command Center accessibility audit PASS, live voice and production UI remain unproven.
- Provider diversity for verification — currently single-family (recorded per round as INDEPENDENCE: REDUCED where applicable).
- Continuous workflow mining, learned computer execution, native desktop replay,
  and audio-driven workflow discovery — explicitly not active; Phase 5/6 remain locked.
- Scanner patterns are configuration (defense-in-depth, not proof).
- Rollback strategy remains restore-oriented; schema v34 is live and no reverse migration is provided.
- The deployed legacy manifest does not yet carry the new git_commit/git_tree fields. Until post-verification promotion completes, live release provenance is source_sha-only and must not be described as fully compliant with the new contract.

## Documentation rules (anti-staleness)

1. This document must be updated by any mission that changes HEAD, schema, suite count, supervisor/STOP state, capability state, or evidence roots.
2. `docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md` defines the maintenance contract.
3. `scripts/doc_staleness_check.py` mechanically validates this document's HEAD/schema claims against the repository (run in CI or before gate closure).
4. Old planning/version documents are HISTORICAL: interpret their status lines through `docs/CAPABILITY_MATRIX.md`, never as current truth.

## Next real architectural dependency

The next architectural dependency is the owner-authorized Phase 5 learned-
procedure gate. It requires a fresh canonical reconciliation, preserved
ActionEnvelope/effect/STOP authority, and an independent end-to-end verifier.
Phase 5 is not started by this promotion; native desktop and autonomous
continuous learning remain outside the current boundary.
