# V3 Implementation Handoff

Version: 2.0 (fast experimental build)
Status: BLOCKED — do not execute until V2 canonical acceptance proven
Base SHA: `a743fdeb535f78c5ad455e8b09944f721f2515a7` (design); implementation bases from V2 merge SHA on `main`
Execution plan: [`08_V3_GOD_MODE_EXECUTION_PLAN.md`](08_V3_GOD_MODE_EXECUTION_PLAN.md) v2.0

## 1. Prerequisites checklist

| # | Prerequisite | Current status |
|---|--------------|----------------|
| 1 | V2 independent PASS (Session-04 or successor) | PENDING — candidate `f1660f6` |
| 2 | V2 merged to canonical `main` | PENDING |
| 3 | Baseline suite manifest recorded at V2 merge SHA | PENDING |
| 4 | Live schema matches accepted V2 migration | V2 candidate `f1660f6` defines v35; `main` at design base is v34 until V2 merges |
| 5 | Design package on `candidate/v3-discovery-design` | DONE (`5cd0b88`) |
| 6 | No severity-one defect in discovery/knowledge/mission paths | Verify at implementation start |

**IMPLEMENTATION AUTHORIZED: NO**

**Hard entry gate:** V2 canonical PASS on `main` only. No other gate blocks starting `src/` work.

## 2. Frozen decisions (do not re-litigate without new evidence)

1. `V3DiscoveryCycle` unifies observe → select → compile **when `discovery.cycle_enabled=true`**.
2. `v3_loop` honest **WAIT** is **preserved** as the fallback when `discovery.cycle_enabled=false` (default).
3. `ExecutiveDecisionService` remains production selection authority on the fallback path.
4. Event namespace `discovery.candidate_*` and `executive.*` — not `candidate.*`.
5. Scoring policy version `v3-policy-2026-08-21` as initial registry default.
6. `min_total_score=0.40` unified threshold.
7. Hard filters reject; never negative-weight STOP/veto/capability gaps.
8. Mission compilation fail-closed on missing provenance/verifier/rollback.
9. Outcome only on `mission.completed` + PASSED.
10. Disposable bare-remote required for MVP gate; no production mutation in acceptance.
11. Additive schema migration at `V3_SCHEMA_VERSION` only (= `CANONICAL_V2_SCHEMA_VERSION + 1`; expected **v36**; v35 occupied by V2).
12. Test gate per WP: **baseline delta = 0** (no new failures vs V2 merge manifest); all new `test_v3_*` must pass.

## 3. Test baseline rule (binding)

| Step | Action |
|------|--------|
| 1 | At V2 merge, run `pytest tests/foundry/ -q` and save `evidence/v3/impl/BASELINE_SUITE.json` |
| 2 | After each WP/PR, run the same command |
| 3 | **PASS** if: (a) no new failing tests vs baseline, (b) all new `test_v3_*` pass, (c) reconcile drift = 0 when projections touched |
| 4 | Record delta in `evidence/v3/impl/WP{n}_SUITE_DELTA.json` |

Pre-existing baseline failures are listed in the manifest. V3 must not increase the failure set. Unconditional full-suite EXIT=0 is **not** required at every WP.

## 4. Unresolved decisions (implementation must record choice)

| ID | Decision | Options | Default if no owner veto |
|----|----------|---------|--------------------------|
| UD-01 | Canary duration on lab clone | 4h / 24h | 4h lab soak (WP8-HARD only) |
| UD-02 | Max active missions default | 2 / 3 / 5 | 3 |
| UD-03 | Research override margin | 0.05 / 0.08 | 0.08 |
| UD-04 | `discovery_portfolio` materialized vs ledger scan | table / scan | table if >10k candidates |

## 5. First vertical slice (WP0 + WP1 + WP2)

Implement in **one PR** after V2 PASS and baseline manifest:

1. **WP0 fixtures:** `tests/fixtures/v3/policy_v3-policy-2026-08-21.json`, disposable bare remote helper, v35 baseline DB fixture, `test_v3_fixtures.py`.
2. **WP1 migration at `V3_SCHEMA_VERSION` (expected v36):** Read `CANONICAL_V2_SCHEMA_VERSION` from merged `main`; add migration `CANONICAL_V2_SCHEMA_VERSION + 1` only. Extend `candidates` columns; create `candidate_outcomes`, `scoring_policy_registry`, `discovery_portfolio`; projection handlers. **Do not allocate v35.**
3. **WP2 `ScoringService`:** Register default policy event; hard filters; deterministic trace; extend `discovery_portfolio` ELIGIBLE state; extend `discovery.candidate_scored` payload.
4. **Flag:** Add `discovery.cycle_enabled` defaulting to `false`. Do not wire orchestrator yet.
5. **Tests:** `test_v3_projections.py`, `test_v3_scoring.py` (AC-02 replay); extend `test_discovery_candidates.py`.

**Success:** Score 10 fixture candidates deterministically; rebuild projections; zero reconcile drift; baseline delta = 0.

**Non-goals for slice 1:** `V3DiscoveryCycle`, `worker_loop` dual-path wiring, Git remote adapter, gate script, production flag enable.

## 6. File and test targets (slice 1)

| Action | Path |
|--------|------|
| MODIFY | `src/jarvis_foundry/migrations.py` |
| MODIFY | `src/jarvis_foundry/projections.py` |
| MODIFY | `src/jarvis_foundry/discovery_candidates.py` |
| CREATE | `src/jarvis_foundry/v3/__init__.py` |
| CREATE | `src/jarvis_foundry/v3/scoring.py` |
| CREATE | `tests/foundry/test_v3_fixtures.py` |
| CREATE | `tests/foundry/test_v3_projections.py` |
| CREATE | `tests/foundry/test_v3_scoring.py` |
| CREATE | `tests/fixtures/v3/policy_v3-policy-2026-08-21.json` |

## 7. Integration order (full V3)

```text
Phase 0 — V2 PASS + BASELINE_SUITE.json + V3_ENTRY_BASELINE.json
    ▼
PR1  WP0 → WP1 → WP2          (foundation; flag OFF)
    ▼
PR2  WP3                       (V3DiscoveryCycle behind flag; KEEP v3_loop WAIT fallback)
    ▼
PR3a WP4 (teams MVP)  ┐ parallel
PR3b WP5 (git MVP)    ┘
    ▼
PR4  WP6 (outcomes — immediate window)
PR5  WP7 (STOP/restart smoke)
    ▼
PR6  WP8-MVP                   (M1–M8 + one focused review)
    ▼
Later WP8-HARD                 (AC-01..AC-20, soak, fault matrix, deep verifier)
```

## 8. WP3 dual-path (critical — read before coding)

| Mode | `discovery.cycle_enabled` | Behavior |
|------|---------------------------|----------|
| **Fallback (default)** | `false` | `v3_loop.run_one_loop` observes → honest **WAIT**; `ExecutiveDecisionService` selects in `worker_loop` |
| **Experimental** | `true` | `V3DiscoveryCycle` runs observe → score → select → compile in one cycle record |

**Implementation rules:**

- Add `V3DiscoveryCycle` as new module (`src/jarvis_foundry/v3/cycle.py`); do not delete `v3_loop` WAIT.
- Extract shared observe steps from `v3_loop` for reuse; avoid duplicating the observe block.
- Reuse `ExecutiveDecisionService` + `materialize_selection` inside the flagged path.
- Wire the flag in `runtime/worker_loop.py` `run_cycle()` — single choke point.
- Rollback = set `discovery.cycle_enabled=false`; no code revert required.

**WP3 tests:** flag OFF → `test_v3_loop.py` WAIT unchanged; flag ON → `test_v3_discovery_cycle.py` one lab loop; no duplicate `mission.created` per fingerprint.

## 9. Acceptance split

### WP8-MVP (ship experimental MVP)

| # | Criterion | Test |
|---|-----------|------|
| M1 | Schema + projections rebuild | `test_v3_projections.py` |
| M2 | Deterministic scoring | `test_v3_scoring.py` |
| M3 | One discovery-to-mission loop (flag ON, lab) | `test_v3_discovery_cycle.py` |
| M4 | Duplicate prevention | `test_v3_dedup.py` |
| M5 | Basic STOP + restart | `test_v3_smoke.py` |
| M6 | Disposable Git reconciliation | `test_v3_git_reconcile.py` |
| M7 | Outcome recording | `test_v3_outcomes.py` |
| M8 | Baseline delta = 0 | `BASELINE_SUITE.json` compare |

**Review:** One focused review → `evidence/v3/impl/MVP_REVIEW.json`. Not triple independent verification.

**Deliverables:** `var/evidence/v3-mvp/MVP_GATE_REPORT.json`

**Production:** `discovery.cycle_enabled` stays OFF until WP8-HARD PASS.

### WP8-HARD (later — not required for experimental MVP)

- Full AC-01..AC-20 per [`05_V3_ACCEPTANCE_GATE.md`](05_V3_ACCEPTANCE_GATE.md)
- 4h lab soak (UD-01 default)
- 12-point crash matrix, STOP races, tamper detection
- Independent adversarial verifier session (Hermes authority)
- AC-16 injection battery, AC-19 migration interrupt+restore
- Promotion packet + rollback rehearsal

## 10. Non-goals

- V4 Command Center UI redesign.
- V5 new connectors beyond READY registry.
- V6 self-evolution candidate pipeline merge (separate `discovery.observed` track stays).
- Removing `v3_loop` WAIT fallback.
- Unconditional full-suite EXIT=0 at every WP.
- Triple independent verification for MVP.
- Production `discovery.cycle_enabled=true` before WP8-HARD.
- Letta runtime integration.

## 11. First build prompt (BLOCKED — copy when V2 PASS)

```text
IMPLEMENTATION BLOCKED UNTIL: V2 canonical PASS on main.

Worktree: C:\temp\JARVIS-V3-Implementation
Branch:   candidate/v3-implementation
Base:     <V2 merge SHA on main> — NOT the design-base SHA

You are implementing JARVIS Foundry V3 Discovery and Building per the fast
experimental build plan.

Read in order:
1. docs/v3/00_V3_READ_FIRST.md
2. docs/v3/07_V3_IMPLEMENTATION_HANDOFF.md (this file, v2.0)
3. docs/v3/08_V3_GOD_MODE_EXECUTION_PLAN.md (v2.0)
4. docs/v3/01_V3_DISCOVERY_BUILDING_SPEC.md
5. evidence/v3/design/V3_CAPABILITY_GAP.json

Phase 0 — before any src/ edits:
1. Run: pytest tests/foundry/ -q
2. Save: evidence/v3/impl/BASELINE_SUITE.json (counts + failing node ids)
3. Record: evidence/v3/impl/V3_ENTRY_BASELINE.json
   (merge SHA, CANONICAL_V2_SCHEMA_VERSION, V3_SCHEMA_VERSION = N + 1)

Phase 1 — execute WP0+WP1+WP2 first vertical slice only (one PR):
- WP0: fixtures + test_v3_fixtures.py
- WP1: v36 migration (V3_SCHEMA_VERSION only — never v35), projections
- WP2: ScoringService, hard filters, ELIGIBLE portfolio, determinism tests
- Add discovery.cycle_enabled defaulting to false; do NOT wire orchestrator
- Do NOT touch v3_loop WAIT behavior
- Preserve all invariants in docs/architecture/02_ARCHITECTURE_CONTRACT.md

Gate for this PR:
- All new test_v3_* tests PASS
- Baseline delta = 0 (no new failures vs BASELINE_SUITE.json)
- Reconcile drift = 0 after projection rebuild

Do NOT implement WP3+ in this session.

Report STATUS per AGENTS.md reporting contract.
```

## 12. Subsequent build prompts (after PR1 merges)

### PR2 — WP3 orchestrator

```text
Continue V3 on candidate/v3-implementation. WP3 only.

Implement V3DiscoveryCycle behind discovery.cycle_enabled (default false).
KEEP v3_loop WAIT fallback when flag is false — do not remove production WAIT.
Wire dual-path in runtime/worker_loop.py run_cycle().
Reuse ExecutiveDecisionService + materialize_selection in flagged path.

Tests: test_v3_loop.py (flag OFF unchanged), test_v3_discovery_cycle.py (flag ON lab loop).
Gate: baseline delta = 0; no duplicate mission.created per fingerprint.
```

### PR6 — WP8-MVP gate

```text
Run MVP gate M1–M8 per docs/v3/07_V3_IMPLEMENTATION_HANDOFF.md §9.
Produce var/evidence/v3-mvp/MVP_GATE_REPORT.json.
One focused review → evidence/v3/impl/MVP_REVIEW.json.
Do NOT require AC-01..AC-20 or triple independent verification.
discovery.cycle_enabled remains false in production config.
```

## 13. Worktree for implementation

```text
Worktree: C:\temp\JARVIS-V3-Implementation
Branch:   candidate/v3-implementation
Base:     main at V2 merge SHA
```

Merge design package (`docs/v3/*`, `evidence/v3/design/*`) onto implementation branch at Phase 0.

## 14. Handoff to Release Captain

### After WP8-MVP PASS (experimental only)

1. Freeze SHA on `candidate/v3-implementation`.
2. Attach `MVP_GATE_REPORT.json` + `MVP_REVIEW.json`.
3. **Do not promote to production.** Flag stays OFF.

### After WP8-HARD PASS (production-ready)

1. Freeze SHA.
2. Independent verifier session (Hermes authority).
3. Integration branch `candidate/v3-integration`.
4. Promotion per existing `deployment/release.py` workflow.
5. Update `docs/CURRENT_STATE.md` — not in V3 design scope.
