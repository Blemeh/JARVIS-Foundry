# V3 Events and State Machines

Version: 1.0 (design)
Base SHA: `a743fdeb535f78c5ad455e8b09944f721f2515a7`

## 1. Event catalog (V3 additive and binding)

### 1.1 Discovery cycle events (NEW — WP3)

| Event type | Owner | Required payload | Idempotency key | Causation |
|------------|-------|------------------|-----------------|-----------|
| `discovery.cycle_started` | Supervisor | `loop_id`, `policy_version`, `activation_epoch` | `discovery.cycle_started:{loop_id}` | prior wake or schedule |
| `discovery.cycle_completed` | Supervisor | `loop_id`, `outcome`, `observed`, `candidates_created`, `decision_id`, `degraded_sources` | `discovery.cycle_completed:{loop_id}` | `discovery.cycle_started` |
| `discovery.cycle_throttled` | Supervisor | `loop_id`, `reason`, `retry_after` | `discovery.cycle_throttled:{loop_id}:{window}` | rate limit |

### 1.2 Candidate events (EXISTING — extend payload)

| Event type | Owner | Required payload | Idempotency key |
|------------|-------|------------------|-----------------|
| `discovery.candidate_generated` | discovery service | `candidate_id`, `fingerprint`, `candidate_type`, `objective`, `expected_outcome`, `evidence`, `provenance_hash`, `source_observations` | `candidate:{fingerprint}` |
| `discovery.candidate_scored` | scoring service | `candidate_id`, `policy_version`, `total_score`, `dimensions`, `hard_filters_passed`, `trace` | `score:{candidate_id}:{policy_version}` |
| `discovery.candidate_approved` | scoring service | `candidate_id`, `reason` | `approve:{candidate_id}` |
| `discovery.candidate_rejected` | scoring service | `candidate_id`, `reason`, `filter_or_score` | `reject:{candidate_id}:{reason_code}` |
| `discovery.candidate_superseded` | discovery service | `candidate_id`, `superseded_by`, `reason` | `supersede:{candidate_id}:{superseded_by}` |
| `discovery.candidate_outcome_recorded` | outcome service | `candidate_id`, `mission_id`, `predicted_value`, `actual_value`, `window`, `confidence`, `status` | `outcome:{candidate_id}:{window}` |

**Payload change:** Add `fingerprint`, `candidate_type`, `expected_outcome`, `provenance_hash` to generated; add `policy_version`, `trace` to scored. Backward compatible (optional fields default).

### 1.3 Portfolio events (NEW — WP2)

| Event type | Owner | Required payload | Idempotency key |
|------------|-------|------------------|-----------------|
| `portfolio.evaluated` | portfolio service | `loop_id`, `policy_version`, `eligible_count`, `deferred_count`, `rejected_count`, `capacity_snapshot` | `portfolio:{loop_id}:{policy_version}` |
| `portfolio.candidate_deferred` | portfolio service | `candidate_id`, `reason`, `cooldown_until` | `defer:{candidate_id}:{loop_id}` |
| `portfolio.candidate_requeued` | portfolio service | `candidate_id`, `reason` | `requeue:{candidate_id}:{timestamp}` |

### 1.4 Scoring policy events (NEW — WP2)

| Event type | Owner | Required payload | Idempotency key |
|------------|-------|------------------|-----------------|
| `scoring.policy_registered` | config authority | `policy_version`, `weights`, `thresholds`, `hard_filters` | `scoring.policy:{policy_version}` |
| `scoring.policy_superseded` | config authority | `old_version`, `new_version`, `reason` | `scoring.supersede:{old_version}:{new_version}` |

### 1.5 Executive events (EXISTING — binding)

| Event type | Owner | Required payload | Idempotency key |
|------------|-------|------------------|-----------------|
| `executive.decision_recorded` | executive | `decision_id`, `decision`, `candidate_id`, `score`, `rejected`, `deferred`, `wait`, `policy_version` | `executive.decision:{decision_id}` |
| `executive.prediction_recorded` | executive | `decision_id`, `candidate_id`, `predicted_outcome` | `executive.prediction:{decision_id}` |
| `executive.decision_materialized` | executive | `decision_id`, `mission_id`, `candidate_id` | `materialize:{decision_id}` |
| `executive.materialization_blocked` | executive | `decision_id`, `candidate_id`, `block_reason` | `materialize_blocked:{decision_id}:{reason}` |
| `executive.calibration_recorded` | executive | `candidate_id`, `predicted`, `actual`, `delta`, `direction` | `calibration:{candidate_id}:{mission_id}` |

### 1.6 Monitor events (EXISTING)

| Event type | Owner | Required payload | Idempotency key |
|------------|-------|------------------|-----------------|
| `discovery.monitor_registered` | discovery | `monitor_id`, `kind`, `source_ref`, `cadence` | `monitor:{kind}:{source_ref}` |
| `discovery.monitor_polled` | discovery | `monitor_id`, `poll_key`, `items_seen`, `candidates_found` | `poll:{monitor_id}:{poll_key}` |
| `discovery.monitor_disabled` | discovery | `monitor_id`, `reason` | `disable:{monitor_id}` |

### 1.7 Mission and verification events (EXISTING — referenced)

`mission.created`, `mission.state_changed`, `mission.completed`, `agent_run.*`, `verification.*`, `action.*` — unchanged semantics per `mission/loop.py`.

### 1.8 External effect events (EXISTING + extend)

| Event type | Owner | Required payload | Idempotency key |
|------------|-------|------------------|-----------------|
| `system.action_started` | broker | `action_id`, `target`, `expected_head`, `idempotency_key` | client-supplied |
| `system.action_completed` | broker | `action_id`, `provider_state`, `reconciled` | `complete:{action_id}` |
| `effect.reconcile_mismatch` | reconcile | `action_id`, `expected`, `actual` | `mismatch:{action_id}` |
| `effect.reconcile_pending` | reconcile | `action_id`, `timeout_at` | `pending:{action_id}` |

## 2. Causation and correlation

- `correlation_id`: `loop_id` for all events in one discovery cycle.
- `causation_id`: `event_id` of triggering event.
- Chain: `cycle_started` → `candidate_generated` → `candidate_scored` → `portfolio.evaluated` → `executive.decision_recorded` → `mission.created` → `agent_run.*` → `verification.*` → `mission.completed` → `candidate_outcome_recorded` → `calibration_recorded`.

## 3. Projections

| Projection table | Source events | Rebuild |
|------------------|---------------|---------|
| `candidates` | `discovery.candidate_*` | Full scan |
| `candidate_scores` | `discovery.candidate_scored` | Latest per candidate+policy |
| `candidate_outcomes` (NEW) | `discovery.candidate_outcome_recorded` | Append |
| `discovery_portfolio` (NEW) | `portfolio.*`, `executive.decision_*` | Full scan |
| `scoring_policy_registry` (NEW) | `scoring.policy_*` | Latest version wins |
| `missions` | `mission.*` | Existing |
| `verification_verdicts` | `verification.*` | Existing |

**Tamper detection:** `verify_event_integrity` + reconcile drift check (existing `reconcile.py`).

## 4. Portfolio state machine

```text
                    ┌─────────┐
                    │   NEW   │
                    └────┬────┘
                         │ score (pass filters)
                         ▼
                    ┌─────────┐
         ┌─────────│ SCORED  │─────────┐
         │ reject  └────┬────┘  defer   │
         ▼              │ eligible      ▼
    ┌─────────┐         ▼          ┌─────────┐
    │REJECTED │    ┌─────────┐     │DEFERRED │
    └─────────┘    │ELIGIBLE │     └────┬────┘
                   └────┬────┘          │ cooldown expired
                        │ select        └──────► ELIGIBLE
                        ▼
                   ┌─────────┐
                   │SELECTED │
                   └────┬────┘
                        │ materialize
                        ▼
              ┌─────────────────┐
              │ MISSION_LINKED  │
              └────────┬────────┘
                       │ mission PASSED
                       ▼
              ┌─────────────────┐
              │    EXECUTED     │
              └────────┬────────┘
                       │ outcome window
                       ▼
              ┌─────────────────┐
              │   MEASURING     │
              └────────┬────────┘
                       │ outcome recorded
                       ▼
              ┌─────────────────┐
              │     CLOSED      │
              └─────────────────┘

    SUPERSEDED ← any non-terminal (via supersede event)
```

### Forbidden transitions

| From | To | Reason |
|------|-----|--------|
| REJECTED | SELECTED | Must regenerate with new fingerprint |
| EXECUTED | ELIGIBLE | Terminal for that mission link |
| CLOSED | SCORED | Requires new candidate_id |
| SELECTED | ELIGIBLE | Must cancel mission first |
| Any | SELECTED | STOP/veto active |

## 5. Research-to-build state machine

```text
OBSERVE ──► CANDIDATE_GENERATED ──► SCORED ──► PORTFOLIO_EVAL
                                                  │
                    ┌─────────────────────────────┼─────────────────────────────┐
                    ▼                             ▼                             ▼
                  WAIT                          DEFER                        SELECT
                    │                             │                             │
                    └──────── idle ───────────────┘                             ▼
                                                                         COMPILE_MISSION
                                                                                │
                                                                         ┌──────┴──────┐
                                                                         ▼             ▼
                                                                    BLOCKED      MISSION_CREATED
                                                                                      │
                                                                                      ▼
                                                                               PLANNING (PLANNED→
                                                                               ORIENTING→ACTION_SELECTION→READY)
                                                                                      │
                                                                                      ▼
                                                                               DISPATCH_BUILDER
                                                                                      │
                                                                                      ▼
                                                                               VERIFYING
                                                                                 │    │
                                                                    FAIL ◄───────┘    └──────► PASS
                                                                     │                          │
                                                                     ▼                          ▼
                                                                 REPAIRING                  RECONCILE
                                                                     │                          │
                                                                     └──► READY (retry)         ▼
                                                                                          MEASURE_OUTCOME
                                                                                                 │
                                                                                                 ▼
                                                                                          MAINTENANCE_SCAN
                                                                                                 │
                                                                                                 ▼
                                                                                          NEXT_CYCLE
```

### Transition table (selected)

| Transition | Trigger | Invariant checks | Durable output | Retry |
|------------|---------|------------------|----------------|-------|
| OBSERVE→GENERATE | cycle step 3 | monitors available | observations | skip failed monitor |
| GENERATE→SCORE | candidate_created | fingerprint unique | candidate row | idempotent |
| SCORE→SELECT | portfolio_eval | policy_version match | score trace | same inputs → same score |
| SELECT→COMPILE | executive SELECT | STOP clear, capability READY | mission.created | idempotent key |
| DISPATCH→VERIFY | builder complete | lease valid | agent_run.completed | reclaim on timeout |
| VERIFY→REPAIR | verdict FAIL | repair_budget > 0 | verification.failed | bounded |
| VERIFY→RECONCILE | verdict PASS | evidence hash match | verification.passed | fresh verifier |
| RECONCILE→MEASURE | effects match | provider state | effect completed | pending reconcile |
| MEASURE→NEXT | outcome recorded or UNKNOWN | window elapsed | outcome event | no manufacture PASS |

## 6. Reconciliation rules

1. **Git push:** Query `git ls-remote` before retry; match `expected_head` + 1.
2. **Ambiguous timeout:** `effect.reconcile_pending`; no retry until resolved.
3. **Dirty worktree:** `materialization_blocked`; never force commit.
4. **Projection drift:** `reconcile.inspect` → rebuild; mismatch raises alert.
5. **Duplicate event:** Idempotency key collision → no-op success.
6. **Crash mid-cycle:** Lease expires → reaper; `loop_id` idempotency prevents double select.

## 7. Recovery behavior

| Crash point | Recovery |
|-------------|----------|
| After cycle_started, before completed | Re-run cycle; idempotent steps no-op |
| After SELECT, before mission.created | Re-materialize or block if candidate stale |
| Mid-mission | MissionLoop resume; lease reclaim |
| Mid-verification | BLOCKED → fresh verifier session |
| Mid-external-effect | reconcile_pending → provider query |

## 8. Schema migration ordering

V3 schema changes are a single additive migration at `V3_SCHEMA_VERSION`:

```text
V3_SCHEMA_VERSION = CANONICAL_V2_SCHEMA_VERSION + 1
```

| Stage | Version | Owner | Evidence |
|-------|---------|-------|----------|
| Pre-V2 merge (`a743fde`) | 34 | `main` | `migrations.py SCHEMA_VERSION=34` |
| V2 Knowledge Brain (`f1660f6`) | **35** | V2 candidate | `SCHEMA_VERSION=35` — **occupied; V3 must not use** |
| V3 Discovery WP1 (expected) | **36** | V3 implementation | `CANONICAL_V2_SCHEMA_VERSION + 1` after V2 on `main` |

**Compatibility:** Projections added at v36 MUST rebuild from ledger on databases at v35 (forward migration). Downgrade is not supported; rollback is restore-from-backup, not schema reverse.

**Interrupted migration:** If process dies during v36 DDL, `initialize()` / `migrate()` resumes from last committed version in `schema_migrations`; partial DDL within a version is transactional per `migrations.py` statement batching.

**Projection replay:** After v36 migration, `rebuild_projections()` must converge with zero drift before V3 cycle enable.
