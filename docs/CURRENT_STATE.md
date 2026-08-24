# JARVIS FOUNDRY — CURRENT STATE

**The anti-staleness surface of the repository.**
This document is the single authoritative current-state record. It is maintained by
every promotion/remaster mission (see `docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md`).
If this document and another current-status document disagree, THIS document wins
for "what exists now" claims; the architecture contract wins for binding invariants.

Last reconciled: 2026-08-24 (V7 Full Autonomy stage: V7A portfolio + V7B
broad operations + V7C resilience integrated onto canonical, released, and
proven live; source and live runtime reported together below)

```text
CURRENT CANONICAL HEAD:      0887a3b (canonical main/origin; pushed)
V7 STAGE INTEGRATION:        integration/v7-full-autonomy-20260824 merged;
                             lanes V7A 9accb24d + V7B 8662fae7 +
                             V7C 22422059 all represented (no missing work)
STAGE EVIDENCE:              evidence/integration/v7-stage/
FULL SUITE:                  PASS — 1,972 collected, 0 failures, 1 explicit
                             skip at final integration head
DEPLOYED RELEASE:            rel-f12a2215f0bc-c3bb04f6 (CURRENT,
                             git_commit f12a2215..., tree e5054369...)
PREVIOUS RELEASE:            rel-441113bcfd27-de548929 (PREVIOUS)
CURRENT SCHEMA:              v36 live and current; no pending migration
                             V2 KNOWLEDGE SCHEMA: v35 (applied 2026-08-23)
                             V3 DISCOVERY/BUILDING SCHEMA: v36 (applied 2026-08-23)
                             CURRENT/CANDIDATE SCHEMA: v36 (migrations.SCHEMA_VERSION)
                             v34 live baseline was migrated to v36 on 2026-08-23
                             (schema_migrations head 36; production database
                             opens at 36 only)
SUPERVISOR STATE:            RUNNING singleton on the live release;
                             heartbeat advancing; STOP false
STOP/RESUME PROOF:           PASS live (sentinel freeze -> FROZEN/SUSPENDED,
                             resume -> RUNNING) on 2026-08-24
V7A PORTFOLIO TIER:          ACTIVE live via worker config
                             executive.portfolio_selection_enabled=true:
                             selects/executes/verifies/records outcomes,
                             retires unexecutable repair targets, closes
                             completed items (mirror shadowing), reaches
                             honest WAIT with wake conditions
V7B BROAD OPERATIONS:        live multi-capability disposable procedure
                             proven (filesystem/git/http/deploy with exact
                             targets, effect tickets, freeze->reconcile)
V7C RESILIENCE:              coordinator ARMED live (worker config
                             resilience.coordinator_enabled=true): detected
                             real connector outage + duplicate authority;
                             DETECTED->TRIAGED->CONTAINED->DIAGNOSED->
                             honest BLOCKED lifecycle recorded
COMMAND CENTER:              /api/command-center projects ledger truth incl.
                             new additive "portfolio" section (decisions,
                             wake conditions, outcomes, incidents); source
                             of truth = append-only ledger; release identity
                             from immutable pointers; cinematic UI unchanged
DB BACKUP/ROLLBACK:          pre-V7 backup verified (quick_check ok, sha256
                             matches record); PREVIOUS release validates;
                             restore path resolves
VOICE/LOCALIZATION:          DEFERRED_BY_OWNER
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

Live Supervisor uses home `C:\Users\b\AppData\Local\hermes\foundry`. The fresh
read-only doctor/status check observed schema v34 versus source v36, STOP false,
one active `worker-loop`, and CURRENT `769715a7...` under filesystem-only release
authority. The live manifest has source provenance but no git commit/tree fields.
No live database, scheduler, provider, process, or release mutation was made by
this integration stage.

`JARVIS-Foundry-Supervisor` is the single production lifecycle authority.
`JARVIS-Foundry-Worker-Loop` is `LEGACY_DISABLED`; its fail-closed classification
prevents accidental re-enable. The R3 proving-window monitor remains a separate
read-only task and is not a worker launch authority.

The canonical merge adds deterministic PID reconciliation, duplicate-launch
fail-closed behavior, explicit release-authority detection, Git commit/tree
release manifests, scheduler-safe test boundaries, V3 hard/operational lineage,
and the Command Center truth/resilience package. A provider-disabled disposable
release factory run (`20260824-v456-final`) built schema-36 previous/candidate
releases, exercised migration, Supervisor/worker leases, scheduler XML,
controlled faults, and rollback with all checks PASS. These source changes are
not live until independently verified, promoted, and restarted.

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
| Integrated V4/V5/V6 focused source gate | PASS — focused truth/evolution/connector/briefing tests plus independent adversarial checks; details are recorded under `evidence/integration/v456-stage/` |
| Command Center UI source gate | PASS — typecheck, lint, 115 Vitest tests, build, product 212/212, and security scan |
| Repository-wide source suite | PASS — 1,981 collected, 1,980 passed, 0 failures, 0 errors, 1 explicit filesystem-boundary skip; fresh JUnit `full-suite-20260823-r3.xml` |
| V3 hard acceptance | BLOCKED/FAIL-CLOSED — focused/supporting/migration/independent checks PASS and bounded soak PASS; AC-17 four-hour proving window is NOT_TESTED |
| Live release/migration/restart/provider proof | BLOCKED — live schema v34 and legacy filesystem-only manifest remain unchanged; no post-promotion proof was authorized |
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
