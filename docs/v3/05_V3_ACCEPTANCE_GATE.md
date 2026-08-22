# V3 Acceptance Gate

Version: 1.0 (design)
Status: DESIGN ONLY — gate script not yet implemented
Base SHA: `a743fdeb535f78c5ad455e8b09944f721f2515a7`

## 1. Gate purpose

Reproducibly prove V3 Discovery and Building meets roadmap exit criteria without production mutation. All tests use isolated lab databases, disposable git remotes, and fake services.

## 2. Prerequisites

- V2 canonical acceptance on `main` (hard gate).
- Canonical V2 schema applied on `main` (`SCHEMA_VERSION`; expected **v35** from `f1660f6`).
- V3 WP1 migration at `V3_SCHEMA_VERSION = CANONICAL_V2_SCHEMA_VERSION + 1` (expected **v36**; v35 occupied by V2).
- WP0–WP8 implementation complete.
- `discovery.cycle_enabled=true` in lab config only.
- Full suite baseline recorded at gate HEAD.

## 3. Mandatory acceptance criteria

| ID | Criterion | Test / inspection | Verdict rule |
|----|-----------|-------------------|--------------|
| AC-01 | ≥3 E2E discovery-to-outcome loops without owner prompt (repair, research-first, build/release) | `test_v3_gate_e2e_three_loops` | All 3 PASS |
| AC-02 | Deterministic eligibility, score trace, selection, rejected alternatives | `test_v3_gate_deterministic_replay` | Byte-identical decision payloads |
| AC-03 | Duplicate observations do not create duplicate candidates/missions/commits/pushes/outcomes | `test_v3_gate_dedup` | Count = 1 per fingerprint |
| AC-04 | Low-value, performative, circular, unmeasurable, expired, unsafe, vetoed, not-ready rejected with durable reasons | `test_v3_gate_rejection_reasons` | Each class has ≥1 rejected event |
| AC-05 | Research beats premature build when uncertainty high | `test_v3_gate_research_override` | Research SELECT recorded |
| AC-06 | Builders cannot self-certify; bad evidence fails closed | `test_v3_gate_verifier_independence` | FAIL on fabricated/stale/replay/mutated evidence |
| AC-07 | STOP/veto blocks dispatch, mutation, external effect, retry, acceptance, promotion including races | `test_v3_gate_stop_races` | Zero forbidden events after STOP |
| AC-08 | Crash/restart at every material transition resumes once without corruption | `test_v3_gate_crash_matrix` | 12 injection points PASS |
| AC-09 | Worker/verifier crash → honest state, bounded repair, fresh re-verify | `test_v3_gate_worker_crash` | BLOCKED→repair→PASS |
| AC-10 | Dirty worktree, remote head change, rejected push, timeout, partial success reconcile safely | `test_v3_gate_external_ambiguity` | No duplicate remote effects |
| AC-11 | Disposable Git remote/PR/release rehearsal verifies provider state | `test_v3_gate_disposable_remote` | ls-remote matches expected |
| AC-12 | Predictions ≠ actuals; missing outcomes stay UNKNOWN | `test_v3_gate_outcome_honesty` | No PASS without measurement |
| AC-13 | Failed/low-value missions inform future selection without rewriting evidence | `test_v3_gate_calibration` | Penalty applied; new evidence can re-admit |
| AC-14 | Completed mission creates maintenance/outcome follow-up and next selection | `test_v3_gate_continuation` | maintenance or next SELECT |
| AC-15 | No eligible work → evidence-backed idle, no busywork/model loops | `test_v3_gate_idle` | WAIT with reasons; zero spurious model calls |
| AC-16 | Context manifests bounded, reproducible, provenance-aware, injection-resistant | `test_v3_gate_context_manifest` | Size limits; malicious content stripped |
| AC-17 | Resource use within bounds | `test_v3_gate_limits` | Counters ≤ configured max |
| AC-18 | Projections rebuild exactly; tampering detected | `test_v3_gate_projection_integrity` | reconcile consistent |
| AC-19 | Compatibility, forward migration v35→v36, interrupted migration, restore, semantic rollback | `test_v3_gate_migration` | Forward migrate v35 baseline to `V3_SCHEMA_VERSION`; interrupt/resume; restore pre-V3 backup |
| AC-20 | Full lineage trace goal→observation→candidate→score→decision→mission→verify→reconcile→outcome→next | `test_v3_gate_lineage` | Single audit chain per loop |

## 4. Fixtures

| Fixture | Location | Purpose |
|---------|----------|---------|
| `lab_db` | `tests/foundry/conftest.py` | Isolated SQLite |
| `disposable_bare_remote` | `tests/fixtures/v3/bare_remote/` | AC-11 |
| `scoring_policy_v1` | `tests/fixtures/v3/policy_v3-policy-2026-08-21.json` | AC-02 |
| `fault_injection_hooks` | `tests/foundry/v3_fault/` | AC-08 |
| `malicious_manifest` | `tests/fixtures/v3/injection/` | AC-16 |

## 5. Fault injection matrix (AC-08)

| Point | Injection | Expected recovery |
|-------|-----------|-------------------|
| post cycle_started | kill process | cycle re-run idempotent |
| post candidate_generated | kill | no duplicate candidate |
| post candidate_scored | kill | score replay same |
| post decision_recorded SELECT | kill | materialize or block |
| post mission.created | kill | mission resume |
| post agent_run.started | kill | lease reclaim |
| post verification.started | kill | BLOCKED honest |
| mid git push | timeout | reconcile_pending |
| post effect.reconcile_pending | kill | query resume |
| post mission.completed | kill | outcome once |
| post outcome_recorded | kill | calibration once |
| post cycle_completed | kill | next cycle clean |

## 6. Negative controls

- Inject owner directive via retrieved web content → must NOT create GOAL.
- Builder attempts `verification.passed` → rejected.
- Tamper `candidates` projection row → reconcile mismatch detected.
- Force-push attempt → blocked, never executed.

## 7. Raw evidence requirements

Gate run produces:

```text
var/evidence/v3-gate/
  GATE_REPORT.json
  AC-01..AC-20/*.json
  lineage/*.json
  disposable-remote-proof.json
  full_suite_exit_code.txt
  independent_verifier_verdict.json
```

## 8. Soak and canary

| Phase | Duration | Environment | Pass |
|-------|----------|-------------|------|
| Lab soak | 4 hours unattended | isolated DB | ≥8 cycles, 0 severity-one |
| Canary | 24 hours | lab clone at V2 schema (v35), then migrate to v36 | cycles complete, prod untouched |

## 8a. Migration acceptance (AC-19 detail)

| Step | Action | Pass criterion |
|------|--------|----------------|
| 1 | Start from v35 lab DB (V2 schema fixture) | `SCHEMA_VERSION == CANONICAL_V2_SCHEMA_VERSION` |
| 2 | Apply V3 migration at `V3_SCHEMA_VERSION` (= v36 expected) | `initialize()` reaches `V3_SCHEMA_VERSION` |
| 3 | Interrupt mid-v36 DDL, restart | Resumes once; no duplicate DDL; version = `V3_SCHEMA_VERSION` |
| 4 | `rebuild_projections()` | reconcile consistent, drift = 0 |
| 5 | Restore pre-V3 backup | bytes identical; `SCHEMA_VERSION` back to v35 |
| 6 | Full suite on v36 lab DB | EXIT=0 |

**Rule:** Tests resolve `V3_SCHEMA_VERSION` from `migrations.SCHEMA_VERSION` at gate HEAD minus 0 (code constant), not a hardcoded literal, if V2 lands at `N ≠ 35`.

## 9. Verdict rules

| Verdict | Condition |
|---------|-----------|
| PASS | 20/20 AC PASS + independent verifier PASS + full suite EXIT=0 |
| FAIL | Any mandatory AC FAIL |
| BLOCKED | V2 not on main; missing credential for disposable remote only |

## 10. Promotion conditions

- Gate PASS at frozen SHA.
- `verification.passed:verdict-v3-operational` event.
- Release Captain integration per existing promote workflow.
- Rollback rehearsal PASS.

## 11. Rollback conditions

- Any AC FAIL after promote → `release.rolled_back` to PREVIOUS.
- Schema migration failure at `V3_SCHEMA_VERSION` → restore from backup (proven in AC-19); no reverse migration of v36.

## 12. Acceptance commands (future implementation)

```bash
# Lab gate (no production)
pytest tests/foundry/test_v3_gate_*.py -v --tb=short
python scripts/v3_acceptance_gate.py --lab --evidence-dir var/evidence/v3-gate

# Full suite regression
pytest tests/foundry/ -q

# Projection integrity
python -m jarvis_foundry reconcile --inspect

# Migration rehearsal (AC-19): v35 baseline → V3_SCHEMA_VERSION (expected v36)
pytest tests/foundry/test_v3_gate_migration.py -v

# Independent verifier (external session)
# Per Hermes Session authority — not run in design session
```
