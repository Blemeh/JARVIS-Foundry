# JARVIS FOUNDRY — CURRENT STATE

**The anti-staleness surface of the repository.**
This document is the single authoritative current-state record. It is maintained by
every promotion/remaster mission (see `docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md`).
If this document and another current-status document disagree, this document wins
for "what exists now" claims; the architecture contract wins for binding invariants.

Last reconciled: 2026-09-01 for V9 stabilization source canonicalization and
read-only live-state reconciliation.
The accepted V9 stabilization history is now represented in canonical source.
This task did not promote, restart, migrate, STOP/RESUME, alter Scheduler, write
the live database, or change live release pointers.

DOCUMENTATION STATE BASE SHA: `a9e6a48bdff79811adab6f48201db96065011138`

The SHA above is the temporary parent pin used while resolving the source
integration. The final docs-only reconciliation commit updates it to the
immediate parent required by `scripts/doc_staleness_check.py`.

```text
CANONICAL SOURCE START:       fe0592991e98de3f5de27c53d40339f4281156f1
CANONICAL MAIN START:         681d1f777106a01998551f4706d981233ccf0e43
PRODUCT STABILIZATION SHA:    c41a3ad8ef3debda6f84e6d4dac2e0ac525c0864
PRODUCT SHA RELATIONSHIP:     c41a3ad is an ancestor of the canonical integration line
CANONICAL PRODUCT INTEGRATION: this source integration merge; see final report
ROADMAP SOURCE COMMIT:        e62b2451de15896bf4266d07f47c6346105b17d2
ROADMAP CONTENT LANDING:      ec6b164ec912697c07463a302efe60d6395ef41e
CONTROL-TOWER SOURCE:         921bd82562ebcf09abc17589416a526b266ba568
CONTROL-TOWER LANDING:        a9e6a48bdff79811adab6f48201db96065011138
CURRENT/CANDIDATE SCHEMA:     v38 (canonical source migrations.SCHEMA_VERSION)
LIVE SCHEMA:                  v38
V2 KNOWLEDGE SCHEMA:          v35 (historical additive migration)
V3 DISCOVERY/BUILDING SCHEMA: v36 (historical additive migration)
v34 live baseline:            historical predecessor retained for migration proof
VOICE/LOCALIZATION:           PLANNED FOR V15 BY POST-V9 ROADMAP
```

## Roadmap and control-plane state

The canonical roadmap remains
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

The roadmap and control-tower commits remain present on the canonical line.
The source integration is semantic: the candidate range was inspected before
landing, and `docs/CURRENT_STATE.md` was reconciled manually rather than taking
either side wholesale.

## Canonical source capability record

The following record combines the earlier canonical capability history with the
accepted V9 product work now reachable from canonical main. Candidate tests and
disposable evidence prove source behavior only; they do not by themselves prove
production trust.

- **Deterministic autonomy kernel (V1)** — Supervisor independent of Hermes/LLM
  health: durable append-only event ledger, event-derived projections, directives,
  STOP/EmergencyStop sentinel, leases, recovery, missions, actions, evidence,
  verification, worker context, provider routing, resource registration, Obsidian
  integration, CLI/runtime controls. Proven through WP0–WP8 gates.
- **Knowledge brain (V2/V3)** — knowledge claims with freshness and contradiction
  handling, retrieval manifests, worker manifests, consolidation, discovery runs,
  and cycles.
- **Command center (V4)** — model routing, monitors, scoring, capability states,
  support semantics, verifier truth, lease monotonicity, exactly-once, and the
  current bounded read projections.
- **Multi-system intelligence (V5)** — systems, broker, capabilities, connectors,
  correlation, effects, and activation loop.
- **Self-evolution (V6)** — hypothesis, release registry, builder/lab/verifier,
  shadow, canary, promote, restore, discovery, cycles, acceptance, and closure.
- **Full autonomous operation (V7)** — release management, migration rehearsal,
  Supervisor adoption, discovery, cycles, executive, command center, capabilities,
  minigates, owner families, continuity, and final gates.
- **Hybrid memory (V8 / Letta influence, NATIVE_JARVIS)** — Tier C experiential
  memory with validation stamps, bounded blocks, lexical recall, procedures,
  curation and defense gates, git-backed versioning, divergence protection, and
  verifier-isolated role context compilation. Schema v33; rollback from backup was
  proven in the accepted candidate history.
- **Worker system (canonical post-V8)** — framework-neutral worker contract,
  Hermes and OpenHands adapters, disposable-home containment, structured result
  envelopes, worker lifecycle, provider identity, role isolation, bounded working
  directories, verifier independence, evidence reconciliation, mission-loop bridge,
  and worker-crash recovery.
- **V9 World Intelligence** — durable observations derive advisory entities,
  relationships, patterns, predictions, gaps, signals, research handoffs, and
  procedural knowledge; mission outcomes write back with replay deduplication.
- **V9 Continuous Executive** — durable wakes and conditions rank objectives,
  wait for blocked dependencies, replan, retry or escalate, honor STOP/PAUSE/RESUME,
  and avoid wake storms without becoming a second scheduler.
- **V9 Capability Fabric** — the canonical worker path selects eligible
  capabilities, composes typed workflows, performs disposable or real-shaped
  effects, verifies effects, records reliability, and preserves `UNKNOWN` and
  `DEGRADED` truth. It is an adapter on the existing MissionLoop, worker, evidence,
  and verifier spine.
- **V9 stabilization** — Command Center, Executive, timeline, briefing, objective,
  database, and related UI read paths use bounded projections and request-scoped
  read-only snapshots; heartbeat churn does not invalidate expensive product state;
  GET-side write attempts and `BEGIN IMMEDIATE` activity are guarded by focused
  tests; schema and trust labels use backend truth.

## Live production observation

The live install and runtime were observed read-only during this task. These are
point-in-time facts and must be re-read before a later operational action:

```text
LIVE INSTALL ROOT:             C:\Users\b\AppData\Local\JARVIS
LIVE HOME / DATABASE:          C:\Users\b\AppData\Local\hermes\foundry
LIVE CURRENT:                  c41a3ad8ef3debda6f84e6d4dac2e0ac525c0864
LIVE PREVIOUS:                 5167e2cbaa5df3c1ad00b660138a5a8b6b2bd1bd
LIVE RUNTIME SHA:              c41a3ad8ef3debda6f84e6d4dac2e0ac525c0864
LIVE INSTALLED SHA:            c41a3ad8ef3debda6f84e6d4dac2e0ac525c0864
LIVE RELEASE ID:               rel-c41a3ad8ef3d-876ae617
LIVE RELEASE AUTHORITY:        DATABASE_LIVE_SLOT / CONSISTENT
LIVE HEALTH:                   RUNNING
LIVE TRUST:                    SUSPENDED
LIVE PARITY:                   PASS (source/install/database/runtime match)
LIVE RECONCILIATION:           CONSISTENT; 140730 events at capture
LIVE STOP:                     FALSE
LIVE SUPERVISOR:               exactly one valid owner; PID 24572 at capture
LIVE SCHEDULER:                contract PASS; IgnoreNew last result 0x800710E0
LIVE OBSERVED:                 2026-09-01 read-only status capture
LIVE MUTATED BY THIS TASK:     NO
STABILIZATION STATUS:          STABILIZATION_CANONICAL_AND_LIVE
```

The status observation used the accepted V9 read-only status path, SQLite
`mode=ro` connections, filesystem pointer reads, Task Scheduler query, and a
read-only Supervisor process census. `RUNNING` health and identity parity do not
restore trust: provider-diverse proof, full-duration endurance, reboot evidence,
and the owner-approved trust gates remain incomplete. The observed Scheduler
result is retained as evidence under the configured `MultipleInstancesPolicy=
IgnoreNew` repeating-trigger behavior while one valid Supervisor remains proven.

`JARVIS-Foundry-Supervisor` remains the sole production lifecycle authority.
The worker loop is launched under that authority; a separate disabled legacy
Scheduler task is not a second launch owner.

## V9 source-closure and evidence record

The accepted candidate range contains 111 JARVIS commits from the merge base.
The range audit classified 61 as accepted product-bearing history (including
required merge records), 12 as tooling/test-only, 8 as evidence-only, and 30 as
documentation-only. No commit was classified as unrelated or superseded, and
no newly added range file was a live database, release pointer, lock, runtime,
or Scheduler artifact. Existing tracked lab fixtures remain historical evidence.

Relevant source-closure records remain at:

- `docs/handoffs/V9_FULL_CONVERGENCE_20260830.md`
- `docs/handoffs/V9_CAPABILITY_FABRIC_CHECKPOINT.md`
- `scripts/v9_stabilization_benchmark.py`
- `tests/foundry/test_v9_stabilization.py`

The older candidate-only statements in V9 handoffs are historical evidence. The
current live and canonical claims are recorded here after fresh reconciliation.

## Recorded proof history

| Gate | Result |
|---|---|
| V1–V7 evidence slices (WP0–WP8, V4–V7 S0–S13) | PASS in the accepted evidence history |
| V8 hybrid-memory and worker-system certification | PASS / PASS_WITH_BOUNDARY in accepted candidate evidence |
| Post-V7 canonical convergence (R7–R12) | PASS_WITH_BOUNDARY; live/provider/elapsed boundaries remain explicit |
| V9 world intelligence, continuous executive, and capability fabric source gates | PASS in disposable cross-lane evidence; live external effects remain separately bounded |
| V9 integrated source closure | PASS — focused cross-lane scenarios and source regression recorded in `docs/handoffs/V9_FULL_CONVERGENCE_20260830.md` |
| V9 stabilization focused contracts | PENDING until the post-integration focused validation run completes |
| Repository-wide source regression | PENDING until the post-integration broad validation run completes |
| Live release/runtime identity and four-way parity | PASS at this observation; all four identities are `c41a3ad...` |
| Live health / reconciliation | RUNNING / CONSISTENT at this observation |
| Production trust | SUSPENDED — provider, reboot, endurance, and trust-gate proof remain incomplete |

## What is experimental / not canonical

- `C:/Users/b/Documents/JARVIS-Lab/jarvis-hybrid-memory` — completed lab reference.
- `C:/Users/b/Documents/JARVIS-External-Candidate` — external experiment, not canonical.
- Superseded V8 candidate worktrees — retained for historical reference only.
- `.h/` — isolated Hermes development checkout, not the JARVIS runtime.
- The original V9 candidate worktree remains preserved; its branch is historical
  input/reference after this source canonicalization and was not deleted.

## What is missing / known limitations

- Distributed or multi-machine operation — not implemented; the runtime remains
  single-host.
- Semantic embedding-based recall, deduplication, and contradiction detection —
  lexical/negation behavior remains canonical; the semantic hook is reserved.
- Provider diversity for verification remains limited; recorded rounds may retain
  `INDEPENDENCE: REDUCED`.
- Continuous workflow mining, learned computer execution, native desktop replay,
  and audio-driven workflow discovery are not active; later phases remain locked.
- Live production trust remains `SUSPENDED` until provider, reboot, endurance, and
  owner-authorized trust evidence is complete, even though c41a3ad is live and
  current source/install/database/runtime identities match.
- Rollback remains restore-oriented; schema v38 has no reverse migration.
- Live UI beyond the inspected local Command Center projection and any real
  provider/network effects remain unproven by this source task.

## Documentation rules (anti-staleness)

1. This document must be updated by any mission that changes HEAD, schema, suite
   count, Supervisor/STOP/trust state, capability state, or evidence roots.
2. `docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md` defines the maintenance contract.
3. `scripts/doc_staleness_check.py` mechanically validates this document's HEAD
   and schema claims against the repository.
4. Old planning/version documents are historical; interpret their status through
   `docs/CAPABILITY_MATRIX.md`, never as current truth.

## Next development action

V10 INVENTORY + EXTERNAL CAPABILITY GAP MAP: verify the actual live stabilization
state read-only before any operational action; inventory existing and historical
JARVIS capabilities; compare mature OSS, APIs, SDKs, MCP servers, and tools;
classify wiring and proof gaps; select the smallest non-overlapping lanes; and
define one real end-to-end V10 proof scenario. Do not start V10 implementation
in this canonicalization task.
