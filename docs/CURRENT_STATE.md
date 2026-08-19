# JARVIS FOUNDRY — CURRENT STATE

**The anti-staleness surface of the repository.**
This document is the single authoritative current-state record. It is maintained by
every promotion/remaster mission (see `docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md`).
If this document and another current-status document disagree, THIS document wins
for "what exists now" claims; the architecture contract wins for binding invariants.

Last reconciled: 2026-08-19 (exact-head convergence evidence reconciliation; legacy evidence preserved)

```text
CURRENT CANONICAL HEAD:      65bc25e (main; current promoted docs-only
                             descendant; local==origin)
TESTED SOURCE HEAD:          2af8191 (exact source head for the 1243-test full
                             suite; docs-only audit commits follow)
LETTA PROMOTION ANCESTOR:    2f7360e742559e051109e04ef7f3a82cc64cc2d2 (still an ancestor of HEAD)
PRE-V8 HISTORICAL BASELINE:  93a3ddc0965dfaf3e4cb9dc0952467bd5250520a (historical only — do not reset to it)
CURRENT SCHEMA:              v33 (six additive tier_c_* tables; event-derived)
FULL SUITE:                  1243 collected on the exact tested canonical path;
                             failures=0, errors=0, skipped=0 (see
                             docs/convergence/CANONICAL_FULL_SUITE_20260819_EXACT_HEAD.json)
SUPERVISOR STATE:            RUNNING (single lease owner, heartbeat advancing)
STOP STATE:                  OFF (no sentinel, no active STOP/PAUSE directives)
ACTIVE DIRECTIVES:           redirect, waive, extend_duty_window, align
TRUST STATE:                 LAB
MISSION STATE:               3 COMPLETE, 1 PLANNED (live DB)
TIER C STATE:                6 tables; 2 memories (live canary records), 1 block, 0 procedures
WORKER IMPLEMENTATIONS:      Hermes adapter + OpenHands adapter — CANONICAL (merged)
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
schema v33, ledger live (event count growing), Tier C live.

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
| Full suite (current) | 1,225 collected, 0 failures (Phase 4 remediation gate) |
| JARVIS Eyes Phase 2 | PASS — 20/20 faults, rollback/live probe, read-only boundary |
| Workflow Discovery Phase 3 | PASS — 36/36 focused, F1–F25, bounded probe, independent review |
| Procedural Learning Phase 4 | PASS — 23/23 focused, F1–F24, artifact-byte tamper, zero-effect shadow, local browser probe, rollback, fresh independent review |
| Post-V7 canonical convergence (R7–R12 semantic closure) | PASS — exact Supervisor authority across low-level evolution/control surfaces; 76 convergence tests + 1,225 canonical tests; isolated promotion and post-promotion runtime probe PASS |

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
