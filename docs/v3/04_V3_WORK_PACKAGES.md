# V3 Work Packages

Version: 1.0 (design)
Base SHA: `a743fdeb535f78c5ad455e8b09944f721f2515a7`
Prerequisite: Canonical V2 acceptance on `main`

## Dependency graph

```text
WP0 ──► WP1 ──► WP2 ──► WP3 ──► WP4
                  │         │
                  │         └──► WP5
                  │                │
                  └────────► WP6 ◄─┘
                              │
                         WP7 ──► WP8
```

## WP0: Orientation, capability map, contracts, fixtures, frozen baseline

| Field | Value |
|-------|-------|
| Scope | Capability gap JSON, scoring policy fixture, disposable git remote fixture, baseline SHA pin |
| Integration points | `evidence/v3/design/`, `tests/foundry/conftest.py` (fixtures only) |
| Risk | LOW |
| Migration | None |
| Acceptance | `V3_CAPABILITY_GAP.json` complete; fixture repos created; `pytest tests/foundry/test_v3_fixtures.py -q` |
| Rollback | Delete fixtures |
| Independent review | Read-only gap audit |
| Stop condition | V2 not on main |

## WP1: Typed discovery observations and candidate/event projections

| Field | Value |
|-------|-------|
| Scope | Extend `candidates` columns (`fingerprint`, `candidate_type`, `expected_outcome`, `provenance_hash`, `expires_at`); `candidate_outcomes` table; `discovery_portfolio` projection; projection handlers in `projections.py` |
| Integration points | `src/jarvis_foundry/discovery_candidates.py`, `projections.py`, `migrations.py` |
| Risk | MEDIUM (schema additive) |
| Migration | v35 additive |
| Acceptance | `pytest tests/foundry/test_discovery_candidates.py tests/foundry/test_v3_projections.py -q`; rebuild projections match live |
| Rollback | Migration down script; feature flag off |
| Independent review | Schema + projection replay |
| Stop condition | Reconcile drift > 0 |

## WP2: Evidence requirements, semantic deduplication, scoring, deterministic selection

| Field | Value |
|-------|-------|
| Scope | `ScoringService`, `scoring_policy_registry`, hard filters, `PortfolioService`, unify score thresholds, extend `ExecutiveDecisionService` to read ELIGIBLE portfolio |
| Integration points | `discovery_candidates.py`, `executive/scoring.py`, `executive/decisions.py`, `executive/eligibility.py` |
| Risk | MEDIUM |
| Migration | Uses WP1 tables |
| Acceptance | `pytest tests/foundry/test_v3_scoring.py tests/foundry/test_executive_intelligence.py -q`; AC-02 determinism tests |
| Rollback | `scoring.policy_superseded` to prior version |
| Independent review | Adversarial score gaming tests |
| Stop condition | Non-deterministic selection in replay |

## WP3: Portfolio lifecycle and candidate-to-mission compiler

| Field | Value |
|-------|-------|
| Scope | `V3DiscoveryCycle`, `MissionCompiler`, wire `runtime/worker_loop.py`, remove production WAIT from `v3_loop` |
| Integration points | `v3_loop.py`, `runtime/worker_loop.py`, `executive/missions.py`, `mission/proposal.py` |
| Risk | HIGH |
| Migration | None |
| Acceptance | `pytest tests/foundry/test_v3_loop.py tests/foundry/test_v3_discovery_cycle.py -q`; end-to-end lab cycle |
| Rollback | Feature flag `discovery.cycle_enabled=false`; revert to executive-only selection |
| Independent review | Full cycle trace audit |
| Stop condition | Duplicate mission from same candidate |

## WP4: Bounded temporary-team orchestration, role capabilities, verifier binding, repair, resume

| Field | Value |
|-------|-------|
| Scope | Role manifest schema, delegation limits, fresh verifier enforcement, repair budget in mission config |
| Integration points | `roles/dispatcher.py`, `mission/loop.py`, `mission/verification.py`, `workers/mission_bridge.py` |
| Risk | MEDIUM |
| Migration | None |
| Acceptance | `pytest tests/foundry/test_role_dispatch.py tests/foundry/test_verifier_truth.py tests/foundry/test_v3_teams.py -q` |
| Rollback | Disable delegation depth > 1 |
| Independent review | H10/H21 verifier independence tests |
| Stop condition | Builder self-certify path exists |

## WP5: Registered Git/remote/release external-effect execution and reconciliation

| Field | Value |
|-------|-------|
| Scope | `GitEffectAdapter`, disposable bare remote tests, `effect.reconcile_pending` handling |
| Integration points | `effects.py`, `mission/commit.py`, `workers/evidence.py`, `connectors/` |
| Risk | HIGH |
| Migration | None |
| Acceptance | `pytest tests/foundry/test_v3_git_reconcile.py -q`; disposable remote push/pull proof |
| Rollback | Disable remote effects; local-only |
| Independent review | Remote state inspection |
| Stop condition | Ambiguous push retried without reconcile |

## WP6: Outcome measurement, calibration records, maintenance triggers, automatic continuation

| Field | Value |
|-------|-------|
| Scope | `OutcomeService`, delayed windows, maintenance scan integration, `discovery.cycle_completed` → next wake |
| Integration points | `discovery_candidates.record_outcome`, `executive/calibration.py`, `v3_loop._maintenance_candidates` |
| Risk | MEDIUM |
| Migration | WP1 `candidate_outcomes` |
| Acceptance | `pytest tests/foundry/test_v3_outcomes.py -q`; calibration both directions |
| Rollback | Outcome windows disabled |
| Independent review | UNKNOWN not manufactured as PASS |
| Stop condition | Outcome recorded on BLOCKED mission |

## WP7: Security/fault battery, migration/restore, backward compatibility, adversarial review

| Field | Value |
|-------|-------|
| Scope | Fault injection suite per threat model, migration rehearsal, projection tamper detection |
| Integration points | `tests/foundry/test_v3_fault_*.py`, `reconcile.py`, `migrations.py` |
| Risk | MEDIUM |
| Migration | v35 rehearsal |
| Acceptance | Full fault matrix PASS; `pytest tests/foundry/ -k v3 -q` |
| Rollback | Restore from backup proven |
| Independent review | Mandatory adversarial verifier |
| Stop condition | Any AC-07..AC-17 failure |

## WP8: Multiple unattended research-to-build acceptance loops, soak/canary, independent verification, promotion packet

| Field | Value |
|-------|-------|
| Scope | Three E2E loops (repair, research-first, build/release), 24h soak config, promotion evidence, rollback rehearsal |
| Integration points | `docs/v3/05_V3_ACCEPTANCE_GATE.md`, `scripts/v3_acceptance_gate.py` (new) |
| Risk | HIGH |
| Migration | Live canary optional; lab default |
| Acceptance | Gate 20/20 criteria PASS; independent verifier PASS |
| Rollback | `release.rolled_back` rehearsal |
| Independent review | Session authority (Hermes or designated) |
| Stop condition | Any mandatory criterion FAIL |

## Summary table

| WP | Depends | Risk | Est. tests added |
|----|---------|------|------------------|
| WP0 | — | LOW | 5 |
| WP1 | WP0 | MED | 15 |
| WP2 | WP1 | MED | 25 |
| WP3 | WP2 | HIGH | 20 |
| WP4 | WP3 | MED | 18 |
| WP5 | WP3 | HIGH | 12 |
| WP6 | WP2,WP3 | MED | 15 |
| WP7 | WP1-6 | MED | 40 |
| WP8 | WP7 | HIGH | gate script |
