# JARVIS FOUNDRY — CURRENT STATE

**The anti-staleness surface of the repository.**
This document is the single authoritative current-state record. It is maintained by
every promotion/remaster mission (see `docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md`).
If this document and another current-status document disagree, THIS document wins
for "what exists now" claims; the architecture contract wins for binding invariants.

Last reconciled: 2026-08-16 (documentation truth remaster)

```text
CURRENT CANONICAL HEAD:      4552dc7 (main; docs-truth remaster commit)
LETTA PROMOTION ANCESTOR:    2f7360e742559e051109e04ef7f3a82cc64cc2d2 (still an ancestor of HEAD)
PRE-V8 HISTORICAL BASELINE:  93a3ddc0965dfaf3e4cb9dc0952467bd5250520a (historical only — do not reset to it)
CURRENT SCHEMA:              v33 (six additive tier_c_* tables; event-derived)
FULL SUITE:                  1124 tests collected; latest full-suite gate records 1124 EXIT=0
                             (985 pre-V8 + 41 hybrid-memory + 97 worker + 1 supervisor-takeover regression)
SUPERVISOR STATE:            RUNNING (single lease owner, heartbeat advancing)
STOP STATE:                  OFF (no sentinel, no active STOP/PAUSE directives)
ACTIVE DIRECTIVES:           redirect, waive, extend_duty_window, align
TRUST STATE:                 LAB
MISSION STATE:               3 COMPLETE, 1 PLANNED (live DB)
TIER C STATE:                6 tables; 2 memories (live canary records), 1 block, 0 procedures
WORKER IMPLEMENTATIONS:      Hermes adapter + OpenHands adapter — CANONICAL (merged)
OWNER FILES:                 6 untracked docs under docs/handoffs/, docs/planning/ (hash-protected)
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

## What is running

Live daemon (single supervisor pair) with home `C:\Users\b\AppData\Local\hermes\foundry`,
schema v33, ledger live (event count growing), Tier C live.

## What has been proven

| Gate | Result |
|---|---|
| V1–V7 evidence slices (WP0–WP8, V4–V7 S0–S13) | PASS (records under `docs/evidence/`) |
| V7 final gate | PASS — 13/13 slices, suite 985 EXIT=0 |
| V8 hybrid-memory certification | PASS — 9/9 criteria (candidate) |
| V8 canonical promotion | PASS — 1027/0/0 post-merge; live canary PASS; rollback drill PASS; post-promotion remediation certification PASS |
| Worker canonical gate | PASS — 18/18 + independent adversarial review |
| Full suite (current) | 1124 collected, 0 failures (latest gate) |

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
- Scanner patterns are configuration (defense-in-depth, not proof).
- Rollback strategy is RESTORE-FROM-BACKUP for schema v33 (no reverse migration; by design).

## Documentation rules (anti-staleness)

1. This document must be updated by any mission that changes HEAD, schema, suite count, supervisor/STOP state, capability state, or evidence roots.
2. `docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md` defines the maintenance contract.
3. `scripts/doc_staleness_check.py` mechanically validates this document's HEAD/schema claims against the repository (run in CI or before gate closure).
4. Old planning/version documents are HISTORICAL: interpret their status lines through `docs/CAPABILITY_MATRIX.md`, never as current truth.

## Next real architectural dependency

Per the owner's own planning record (`docs/planning/POST_V7_CODEX_SOL_REMASTER_GOAL.md`),
the declared next program is the **Post-V7 Remaster** executed by an external
authority (Codex Sol, max reasoning) from the now-promoted V7+V8+worker baseline
into full real-operation purpose fulfillment. Nothing in this repository queues
a V9 capability stage; the roadmap's automatic progression operates on the
existing stage sequence, all of whose stages are now IMPLEMENTED_AND_PROVEN or
PARTIAL (see `docs/CAPABILITY_MATRIX.md`).
