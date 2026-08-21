# V3 Implementation Handoff

Version: 1.0 (design)
Status: BLOCKED — do not execute until V2 canonical acceptance proven
Base SHA: `a743fdeb535f78c5ad455e8b09944f721f2515a7`

## 1. Prerequisites checklist

| # | Prerequisite | Current status |
|---|--------------|----------------|
| 1 | V2 independent PASS (Session-04 or successor) | PENDING — candidate `f1660f6` |
| 2 | V2 merged to canonical `main` | PENDING |
| 3 | Integration gates PASS post-merge | PENDING |
| 4 | Live schema matches accepted V2 migration | V2 candidate `f1660f6` defines v35; `main` at design base is v34 until V2 merges |
| 5 | This design package committed on `candidate/v3-discovery-design` | THIS SESSION |
| 6 | No severity-one defect in discovery/knowledge/mission paths | Verify at implementation start |

**IMPLEMENTATION AUTHORIZED: NO**

## 2. Frozen decisions (do not re-litigate without new evidence)

1. Single orchestrator `V3DiscoveryCycle` unifies observation and selection.
2. `ExecutiveDecisionService` remains production selection authority.
3. Event namespace `discovery.candidate_*` and `executive.*` — not `candidate.*`.
4. Scoring policy version `v3-policy-2026-08-21` as initial registry default.
5. `min_total_score=0.40` unified threshold.
6. Hard filters reject; never negative-weight STOP/veto/capability gaps.
7. Mission compilation fail-closed on missing provenance/verifier/rollback.
8. Outcome only on `mission.completed` + PASSED.
9. Disposable bare-remote required for gate; no production mutation in acceptance.
10. Additive schema migration at `V3_SCHEMA_VERSION` only (= `CANONICAL_V2_SCHEMA_VERSION + 1`; expected **v36**; v35 occupied by V2).

## 3. Unresolved decisions (implementation must record choice)

| ID | Decision | Options | Default if no owner veto |
|----|----------|---------|--------------------------|
| UD-01 | Canary duration on lab clone | 4h / 24h | 4h lab soak |
| UD-02 | Max active missions default | 2 / 3 / 5 | 3 |
| UD-03 | Research override margin | 0.05 / 0.08 | 0.08 |
| UD-04 | `discovery_portfolio` materialized vs ledger scan | table / scan | table if >10k candidates |

## 4. First vertical slice (WP0 + WP1 + WP2 minimal)

Implement in one PR after V2 PASS:

1. **WP0 fixtures:** `tests/fixtures/v3/policy_v3-policy-2026-08-21.json`, disposable bare remote helper, v35 baseline DB fixture.
2. **WP1 migration at `V3_SCHEMA_VERSION` (expected v36):** Read `CANONICAL_V2_SCHEMA_VERSION` from merged `main`; add migration `CANONICAL_V2_SCHEMA_VERSION + 1` only. Extend `candidates` columns; create `candidate_outcomes`, `scoring_policy_registry`; projection handlers. **Do not allocate v35.**
3. **WP2 `ScoringService`:** Register default policy event; hard filters; deterministic trace; extend `discovery.candidate_scored` payload.
4. **Tests:** `test_v3_scoring.py` with AC-02 replay fixture; extend `test_discovery_candidates.py`.

**Success:** Score 10 fixture candidates deterministically; rebuild projections; zero reconcile drift.

**Non-goals for slice 1:** V3DiscoveryCycle, Git remote adapter, gate script, production flag enable.

## 5. File and test targets (slice 1)

| Action | Path |
|--------|------|
| MODIFY | `src/jarvis_foundry/migrations.py` |
| MODIFY | `src/jarvis_foundry/projections.py` |
| MODIFY | `src/jarvis_foundry/discovery_candidates.py` |
| CREATE | `src/jarvis_foundry/v3/scoring.py` |
| CREATE | `src/jarvis_foundry/v3/__init__.py` |
| CREATE | `tests/foundry/test_v3_scoring.py` |
| CREATE | `tests/fixtures/v3/policy_v3-policy-2026-08-21.json` |

## 6. Integration order (full V3)

```text
WP0 → WP1 → WP2 → WP3 → WP4 (parallel WP5 after WP3) → WP6 → WP7 → WP8
```

## 7. Non-goals

- V4 Command Center UI redesign.
- V5 new connectors beyond READY registry.
- V6 self-evolution candidate pipeline merge (separate `discovery.observed` track stays).
- Production worker-loop activation without gate PASS.
- Letta runtime integration.

## 8. First build prompt (BLOCKED — copy when V2 PASS)

```text
IMPLEMENTATION BLOCKED UNTIL: V2 canonical PASS on main with integration gates green.

You are implementing JARVIS Foundry V3 Discovery and Building per the design
package at docs/v3/ on branch candidate/v3-discovery-design (merge after V2).

Read in order:
1. docs/v3/00_V3_READ_FIRST.md
2. docs/v3/01_V3_DISCOVERY_BUILDING_SPEC.md
3. docs/v3/04_V3_WORK_PACKAGES.md
4. evidence/v3/design/V3_CAPABILITY_GAP.json

Execute WP0+WP1+WP2 first vertical slice only. Preserve all invariants in
02_ARCHITECTURE_CONTRACT.md. At WP1 start read CANONICAL_V2_SCHEMA_VERSION from
merged main migrations.SCHEMA_VERSION; add V3 migration at that value + 1 only
(expected v36 after V2 f1660f6 lands v35). Do not enable discovery.cycle_enabled
in production. Run test_v3_scoring.py and full suite.

Report STATUS per AGENTS.md reporting contract.
```

## 9. Worktree for implementation (future)

```text
Worktree: C:\temp\JARVIS-V3-Implementation
Branch: candidate/v3-implementation
Base: main at V2 merge SHA
```

## 10. Handoff to Release Captain

After WP8 gate PASS:

1. Freeze SHA.
2. Independent verifier session (Hermes authority).
3. Integration branch `candidate/v3-integration`.
4. Promotion per existing `deployment/release.py` workflow.
5. Update `docs/CURRENT_STATE.md` — not in V3 design scope.
