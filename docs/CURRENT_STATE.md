# JARVIS FOUNDRY — CURRENT STATE

**The anti-staleness surface of the repository.**
This document is the single authoritative current-state record. It is maintained by
every promotion/remaster mission (see `docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md`).
If this document and another current-status document disagree, THIS document wins
for "what exists now" claims; the architecture contract wins for binding invariants.

Last reconciled: 2026-09-01 for roadmap and agent-control documentation only.
No product source, live runtime, database, Scheduler, installation, release
pointer, provider, or trust state was changed or refreshed by this task.

DOCUMENTATION STATE BASE SHA: `a9e6a48bdff79811adab6f48201db96065011138`

The SHA above is the immediate parent of the final current-state reconciliation
commit. It contains the selectively landed roadmap and agent control tower.

```text
CANONICAL SOURCE START:      fe0592991e98de3f5de27c53d40339f4281156f1
ROADMAP SOURCE COMMIT:       e62b2451de15896bf4266d07f47c6346105b17d2
ROADMAP CONTENT LANDING:     ec6b164ec912697c07463a302efe60d6395ef41e
CONTROL-TOWER SOURCE:        921bd82562ebcf09abc17589416a526b266ba568
CONTROL-TOWER LANDING:       a9e6a48bdff79811adab6f48201db96065011138
PRODUCT STABILIZATION SHA:   c41a3ad8ef3debda6f84e6d4dac2e0ac525c0864
PRODUCT SHA RELATIONSHIP:    c41a3ad is NOT an ancestor of this canonical line;
                             no V9 product history was imported by this task
CURRENT/CANDIDATE SCHEMA:    v37 (canonical source migrations.SCHEMA_VERSION)
V2 KNOWLEDGE SCHEMA:         v35 (historical additive migration)
V3 DISCOVERY/BUILDING SCHEMA:v36 (historical additive migration)
v34 live baseline:           historical recorded baseline; not refreshed here
LIVE CURRENT/PREVIOUS:       NOT INSPECTED BY THIS DOCUMENTATION TASK
LIVE SUPERVISOR/STOP/TRUST:  NOT INSPECTED BY THIS DOCUMENTATION TASK
VOICE/LOCALIZATION:          PLANNED FOR V15 BY POST-V9 ROADMAP
```

## Roadmap and control-plane state

The canonical roadmap is
`docs/handoffs/JARVIS_POST_V9_TO_V16_ROADMAP_HANDOFF_2026-08-31.md`:

```text
V9 Core Loop -> V10 Capability -> V11 Long-Horizon -> V12 Self-Expansion
-> V13 Personal Executive -> V14 Digital Operator -> V15 Human Interface
-> V16 Final Convergence / JARVIS 1.0
```

Root `AGENTS.md` is the cross-agent control tower. It routes agents to this
current-state authority, the post-V9 roadmap, and the bounded build,
convergence, promotion, and proving workflows under `docs/agent/`. Historical
V6/V7 plans remain preserved but are not mandatory root guidance for unrelated
V10+ work.

The roadmap branch was based on `c41a3ad...`, while canonical `main` started at
its ancestor `fe059299...`. Integration therefore cherry-picked only the
roadmap/control documentation and did not merge or fast-forward the V9 product
candidate line. The V9 candidate, canonical source, installed release, running
runtime, and production trust remain separate truths.

## Canonical source capability record

(The inventory below is inherited from earlier recorded source/evidence
reconciliations. This documentation task did not re-run or upgrade those
product gates.)

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

## Last recorded runtime snapshot (not refreshed here)

At the earlier recorded observation, the live Supervisor used home
`C:\Users\b\AppData\Local\hermes\foundry`. That read-only doctor/status check
observed schema v34 versus source v36, STOP false,
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

## Recorded proof history

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

## Next development action

Run the V10 inventory plus external-capability gap map defined by the post-V9
roadmap. Before V10 implementation, verify the actual V9 stabilization/live
state read-only; inventory existing and historical JARVIS capabilities; compare
mature OSS, APIs, SDKs, MCP servers, and tools; classify wiring/proof gaps; and
select the smallest non-overlapping lanes plus one real end-to-end proof
scenario. This documentation task does not start V10 or authorize live changes.
