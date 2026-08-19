# JARVIS FOUNDRY — CURRENT STATE

**The anti-staleness surface of the repository.**
This document is the single authoritative current-state record. It is maintained by
every promotion/remaster mission (see `docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md`).
If this document and another current-status document disagree, THIS document wins
for "what exists now" claims; the architecture contract wins for binding invariants.

Last reconciled: 2026-08-19 (docs-only convergence promotion at 33efa57; source gates verified at 84a22b4; c60db92 runtime freeze/restart, R3 proving-window registration, disabled worker-loop registration, schema34 deployment/restore rehearsal, and legacy evidence preserved)

```text
CURRENT CANONICAL HEAD:      33efa57 (latest canonical repository/evidence head;
                             source gates were verified at 84a22b4, semantic
                             runtime source remains 93d23f5, and the running
                             deployed release remains c60db92)
CURRENT REPOSITORY HEAD:     33efa57 (docs-only descendant of the tested
                             source base; the exact c60db92 runtime is not
                             silently replaced)
CURRENT STATE RECORD:        33efa57 (current-head convergence evidence is
                             recorded under docs/convergence/)
TESTED SOURCE HEAD:          84a22b4 (fresh 1260-test full suite and 100-test
                             R7-R12 convergence suite; docs-only candidate
                             evidence commits follow)
LATEST R10 FOCUSED SOURCE:   93d23f5 (authenticated nonce-bound
                             REPRIORITIZE/REDIRECT consumer plus exactly-once
                             replay repair; live provider dispatch remains NOT_TESTED)
LETTA PROMOTION ANCESTOR:    2f7360e742559e051109e04ef7f3a82cc64cc2d2 (still an ancestor of HEAD)
PRE-V8 HISTORICAL BASELINE:  93a3ddc0965dfaf3e4cb9dc0952467bd5250520a (historical only — do not reset to it)
CURRENT SCHEMA:              v34 (adds event-derived mission_step_contracts;
                             the live deployed database remains v33 until a
                             controlled migration)
FULL SUITE:                  1260 collected at tested source base 84a22b4;
                             failures=0, errors=0, skipped=0 (see
                             docs/convergence/CURRENT_CANONICAL_FULL_SUITE_20260819_JUNIT.xml)
SUPERVISOR STATE:            RUNNING (single lease owner, heartbeat advancing)
STOP STATE:                  OFF (no sentinel, no active STOP/PAUSE directives)
ACTIVE DIRECTIVES:           redirect, waive, extend_duty_window, align
TRUST STATE:                 LAB
MISSION STATE:               7 ACTION_SELECTION, 8 BLOCKED, 8 COMPLETE,
                             1 PLANNED (read-only reconciliation 2026-08-19)
TIER C STATE:                6 tables; 2 memories (live canary records), 1 block, 0 procedures
WORKER IMPLEMENTATIONS:      Hermes adapter + OpenHands adapter — CANONICAL (merged)
R7-R12 CONVERGENCE:          current-head gates 100/100 PASS; independent
                             verifier PASS_WITH_EXTERNAL_BOUNDARIES; see
                             docs/convergence/INDEPENDENT_CONVERGENCE_VERIFIER_CURRENT_20260819.json
OWNER FILES:                 6 tracked docs under docs/handoffs/, docs/planning/ (preserved from 1abc388)
PERCEPTION INPUT (Phase 2):  SCREENPIPE-LOCAL registered read-only; perception core READY/PROVEN;
                             raw-frame PROBING; audio DISABLED; no claim/candidate/mission/control path
WORKFLOW DISCOVERY (Phase 3): deterministic sessionization/tokenization/mining and
                             evidence-to-existing-candidate bridge READY/PROVEN;
                             disabled by default; no continuous mining or execution
PROCEDURAL LEARNING (Phase 4): governed deterministic OpenAdapt browser substrate
                             READY/PROVEN with complete artifact-byte binding and
                             zero-effect shadow proof; disabled by default;
                             desktop PROBING; no continuous learning, broad
                             promotion, or Phase 5 loop
```

## What exists now

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

Live daemon (single supervisor pair) with home `C:\Users\b\AppData\Local\hermes\foundry`,
deployed schema v33, ledger live (event count growing), Tier C live. The
candidate source schema is v34, but no live migration or worker-loop activation
has been performed.

The exact current runtime/source record is `c60db92`; its R3 proving window is
`WAITING_ELAPSED` under the read-only task
`JARVIS-Foundry-Proving-Window-Monitor-Canonical-R3`. The previous f37f852 R2
window is preserved as `SUSPENDED` history.

The separate `JARVIS-Foundry-Worker-Loop` task is registered but `Disabled`.
Its triggers are disabled before registration, it points to the canonical
`jarvis_foundry.runtime.worker_loop` command and worker-routing configuration,
and no worker-loop process or provider effect has been observed. Activation is
deferred until an authoritative executable mission contract and provider
readiness are independently reconciled.

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
| Post-V7 canonical convergence (R7–R12 semantic closure) | PASS_WITH_BOUNDARY — exact Supervisor authority across low-level evolution/control surfaces; focused gates and the 1,260-test current-source suite pass; isolated promotion and post-promotion runtime probe PASS; live/provider/elapsed boundaries remain explicit |
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
- Voice/dashboard surfaces beyond the command-center control layer — limited.
- Provider diversity for verification — currently single-family (recorded per round as INDEPENDENCE: REDUCED where applicable).
- Continuous workflow mining, learned computer execution, native desktop replay,
  and audio-driven workflow discovery — explicitly not active; Phase 5/6 remain locked.
- Scanner patterns are configuration (defense-in-depth, not proof).
- Rollback strategy is RESTORE-FROM-BACKUP for schema v33 (no reverse migration; by design).
- The prior canonical R2 proving window on f37f852 is durably SUSPENDED. The exact current-release R3 proving window is active and WAITING_ELAPSED against source c60db92, with a fresh Supervisor restart, read-only monitor, 0/10 verified post-start missions, and all external/provider/voice/physical boundaries explicitly NOT_TESTED or BLOCKED.
- The worker-loop activation package is source-verified and pushed, but its live task remains disabled; this does not restart or promote the c60db92 proving release.

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
