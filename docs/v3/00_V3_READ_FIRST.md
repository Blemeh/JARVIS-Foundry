# V3 Discovery and Building — Read First

Version: 1.0 (design session)
Status: DESIGN ONLY — implementation blocked until canonical V2 acceptance
Date: 2026-08-21
Branch: `candidate/v3-discovery-design`
Base SHA: `a743fdeb535f78c5ad455e8b09944f721f2515a7` (origin/main at orientation)
Worktree: `C:\temp\JARVIS-V3-Discovery-Design`

## 1. Objective

Design the complete V3 Discovery and Building stage that turns JARVIS from a system that executes supplied goals into a system that continuously:

```text
OBSERVE → DISCOVER → EVIDENCE CANDIDATES → SCORE/PRIORITIZE → SELECT
→ CREATE MISSIONS → RESEARCH → BUILD → TEST → VERIFY → REPAIR/ACCEPT
→ RECONCILE EFFECTS → MEASURE OUTCOMES → MAINTAIN → SELECT NEXT
```

This package is a decision-complete specification and executable acceptance gate. It does not implement or activate V3.

## 2. Scope

### In scope

- Architecture, typed models, events, state machines, scoring policy, portfolio lifecycle, mission compilation, team roles, external-effect reconciliation design, outcome measurement, maintenance triggers, threat model, work packages, and acceptance gate for V3.
- Evidence-backed capability gap map against repository state at base SHA.
- Integration design reusing V1 kernel, V2 knowledge brain, mission machinery, verifier hardening, action envelopes, resource/capability registry, release controller, Command Center projections, and Executive Intelligence M2.

### Out of scope

- Source code, schema, migration, or test implementation.
- V4 UI redesign, V5 connector expansion beyond registered READY capabilities, V6/V7/V8 feature work.
- Production mutation, deploy, promote, or Hermes agent invocation.
- Claiming V3 is operational.

## 3. Entry and exit

### V3 implementation entry (hard gate)

All must be true before any V3 implementation work begins:

1. V2 Knowledge Brain receives independent PASS on canonical `main` (Hermes Session-04 or successor authority).
2. V2 integration merge to canonical `main` is complete with passing integration gates.
3. Merged schema and runtime evidence show live production at accepted V2 schema (`SCHEMA_VERSION` on merged `main`; V2 candidate `f1660f6` defines **v35**).
4. No unresolved severity-one defect in discovery, knowledge, mission, or verifier paths.

Current evidence (2026-08-21): V2 candidate frozen at `e79a8a5` / evidence `f1660f6` on `candidate/24h-v2-knowledge-brain`; independent Session-04 review and Release Captain integration **pending**. **Entry condition NOT satisfied.**

### V3 design exit (this session)

PASS when all ten deliverables in `docs/v3/` and `evidence/v3/design/` are committed, validated, internally consistent, and traceable.

### V3 operational exit (future implementation)

Per `docs/architecture/05_POST_V1_ROADMAP.md` §6 and `05_V3_ACCEPTANCE_GATE.md`: reproducible discovery-to-outcome loops, deterministic selection, no duplicate work, honest idle, full lineage traceability, disposable external-effect rehearsal — all without production mutation.

## 4. Precedence

When instructions conflict:

1. Latest authenticated owner veto, STOP, constraint, or goal.
2. `docs/governance/AUTONOMY_MANDATE.md`.
3. `docs/CURRENT_STATE.md` for "what exists now".
4. `docs/architecture/02_ARCHITECTURE_CONTRACT.md`.
5. This V3 design package (`docs/v3/*`).
6. Historical documents (`PROJECT_STATE.md`, `docs/planning/V3_SLICE1_DESIGN.md`) where superseded by code or CURRENT_STATE.

## 5. Permanent invariants (preserved)

| Invariant | V3 enforcement |
|-----------|----------------|
| One event-first mission/control plane | All V3 state via ledger events and rebuildable projections; no parallel scheduler or evidence store |
| Deterministic Supervisor owns STOP/veto/leases/release | Pre-dispatch, pre-mutation, pre-acceptance, pre-promotion checks |
| Hermes workers cannot certify canonical truth | Verifier role separation; builders never final PASS |
| Append-only events; corrections supersede | All V3 transitions emit events; no projection rewrite |
| Observation ≠ Evidence ≠ Claim ≠ Belief ≠ Decision ≠ Unknown | Typed records with provenance hashes |
| Untrusted retrieved content | Cannot impersonate owner, disable STOP, widen capability, or request raw credentials |
| Scoped capabilities not raw credentials | Team role manifests via broker |
| STOP/veto before dispatch/mutation/acceptance/retry/promotion | Hard filters in selection and mission compiler |
| Idempotent external effects with reconciliation | Git/remote/release adapters per §8 of spec |
| Crash/restart safety | Lease recovery, idempotency keys, exactly-once resume |
| Model names are configuration | Scoring policy versioned in registry, not prompts |
| Predicted vs actual value recorded | Outcome measurement separate from technical PASS |
| Standing autonomy; no routine approval queue | Genuine blockers only (credentials, physical act, outage, contradiction) |
| Reuse V2, verifier, mission, CC projections | Compatibility map in `01_V3_DISCOVERY_BUILDING_SPEC.md` |

## 6. Terminology

| Term | Meaning |
|------|---------|
| **Observation** | Raw signal from a registered monitor (deterministic collection) |
| **Evidence** | Durable, provenance-bound artifact supporting or refuting a claim |
| **Candidate** | Typed opportunity/repair/maintenance record in portfolio (table `candidates`, events `discovery.candidate_*`) |
| **Portfolio** | Active set of candidates with lifecycle state, capacity, and selection context |
| **Scoring policy** | Versioned deterministic weights/thresholds with auditable trace |
| **Selection** | Executive decision: SELECT, REJECT, DEFER, or WAIT (`executive.decision_recorded`) |
| **Mission compilation** | Deterministic transformation of accepted candidate → `mission.created` with envelopes |
| **Team** | Bounded temporary role set (scout through curator) under lease |
| **Outcome** | Measured real-world effect after technical completion |
| **Idle** | Evidence-backed state with no eligible valuable work (`WAIT` with `wait_kind`) |
| **Hard filter** | Eligibility rejection (STOP, veto, missing capability) — not a score penalty |

## 7. Verified prerequisites (repository evidence)

| Prerequisite | Status | Evidence |
|--------------|--------|----------|
| V1 autonomy kernel | EXISTING | `src/jarvis_foundry/database.py`, `events.py`, `projections.py`; tests `test_event_store.py`, WP8 gate |
| V2 knowledge brain | PARTIAL (candidate pending canonical merge) | `knowledge.py`, `knowledge_retrieval.py`; WP11 gate PASS on branch |
| V3 slice-1 pipeline | PARTIAL | `discovery_candidates.py`, `v3_loop.py`; events `discovery.candidate_*`; WP10/WP12 gates on historical HEAD |
| Executive selection M2 | EXISTING on main | `executive/decisions.py`, `runtime/worker_loop.py`; 32 tests `test_executive_intelligence.py` |
| Mission loop + verifier | EXISTING | `mission/loop.py`, `mission/verification.py`; `test_mission_loop.py`, `test_verifier_truth.py` |
| Discovery monitors | EXISTING | `discovery_monitors.py`; `test_discovery_monitors.py` |
| Command Center | EXISTING | `command_center.py`; `_executive_projection` |
| Git helpers | PARTIAL | `live_ingestion._repo_source`, `mission/commit.py`; no remote reconcile adapter |
| Schema (design base SHA) | v34 on `main` at `a743fde` | `migrations.py` `SCHEMA_VERSION=34` |
| Schema (V2 candidate) | **v35 occupied** | `f1660f6:src/jarvis_foundry/migrations.py` `SCHEMA_VERSION=35` |
| Schema (V3 WP1 target) | **v36 expected** | next version after canonical V2 baseline (see §7a) |

## 7a. Schema version rule (binding)

V3 additive migrations MUST NOT reuse a version already occupied by canonical V2.

**Mechanical rule at WP1 start:**

```text
CANONICAL_V2_SCHEMA_VERSION = migrations.SCHEMA_VERSION on merged main
V3_SCHEMA_VERSION           = CANONICAL_V2_SCHEMA_VERSION + 1
```

**At design time (2026-08-21):**

| Snapshot | SHA / branch | `SCHEMA_VERSION` |
|----------|--------------|------------------|
| V3 design base | `a743fde` (`origin/main`) | 34 |
| V2 candidate (evidence) | `f1660f6` (`candidate/24h-v2-knowledge-brain`) | **35** |
| V3 WP1 expected | after V2 merge | **36** (= 35 + 1) |

If canonical V2 integration lands at a different `N`, V3 uses `N + 1`. Never hardcode `v36` without verifying `SCHEMA_VERSION` on merged `main`. Never allocate **v35** for V3 (occupied by V2).

**Ordering:** V2 migration must be applied and proven on `main` before V3 migration is registered. V3 migration is a single additive step at `V3_SCHEMA_VERSION` only.

## 8. Anti-drift rules

1. **Git identity first.** Record base SHA, branch, worktree, dirty state before authoring.
2. **Code over prose.** EXISTING claims require path/symbol/test/commit citation in `V3_CAPABILITY_GAP.json`.
3. **No stale BUILD NOT STARTED.** Implementation exists; design extends and unifies partial paths.
4. **Two authorities today.** `v3_loop.run_one_loop` observes then WAITs; `ExecutiveDecisionService` selects in `runtime/worker_loop`. V3 design unifies via `V3DiscoveryCycle` orchestrator (spec §3.1).
5. **Event namespace.** Production uses `discovery.candidate_*` and `executive.*`, not historical `candidate.*` from slice-1 design doc.
6. **Re-read after interruption.** Reread this file, work record, branch, and diff before continuing.
7. **Design ≠ implementation.** No certification language in deliverables.

## 9. Document map

| File | Purpose |
|------|---------|
| `01_V3_DISCOVERY_BUILDING_SPEC.md` | Complete architecture and interfaces |
| `02_V3_EVENTS_AND_STATE_MACHINES.md` | Event catalog, transitions, projections |
| `03_V3_SCORING_AND_PORTFOLIO.md` | Policy, portfolio, worked examples |
| `04_V3_WORK_PACKAGES.md` | Dependency-ordered implementation plan |
| `05_V3_ACCEPTANCE_GATE.md` | Reproducible verification gate |
| `06_V3_THREAT_MODEL.md` | Assets, threats, mitigations, test traceability |
| `07_V3_IMPLEMENTATION_HANDOFF.md` | Blocked first-build prompt |
| `08_V3_GOD_MODE_EXECUTION_PLAN.md` | Autonomous WP0–WP8 execution plan with phase diagram |
| `evidence/v3/design/V3_CAPABILITY_GAP.json` | Machine-readable gap inventory |
| `evidence/v3/design/V3_DESIGN_VALIDATION.json` | Validation record |

## 10. Known contradictions resolved in this design

| Contradiction | Resolution |
|---------------|------------|
| `V3_SLICE1_DESIGN.md` schema v6 / `candidate.*` events | Historical; implement against `discovery.*` and additive migration at `V3_SCHEMA_VERSION` (expected v36) |
| V3 design v35 vs V2 candidate v35 collision | V2 occupies v35 (`f1660f6`); V3 uses `CANONICAL_V2_SCHEMA_VERSION + 1` (expected v36) per §7a |
| WP12 describes `v3_loop` selecting; code WAITs by default | V3 unifies: observation in cycle, selection via Executive path, single orchestrator |
| Two score thresholds (0.35 loop vs 0.40 executive) | Single `scoring_policy_version` registry; executive reads policy version |
| No `candidate_outcomes` table | Add projection in WP2; events `discovery.candidate_outcome_recorded` already exist |
| Remote Git "reconciled" as local-only in WP12 | V3 acceptance requires disposable bare-remote rehearsal (gate §11) |
