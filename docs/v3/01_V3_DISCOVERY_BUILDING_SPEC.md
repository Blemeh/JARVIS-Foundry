# V3 Discovery and Building — Architecture Specification

Version: 1.0 (design)
Status: DESIGN ONLY
Base SHA: `a743fdeb535f78c5ad455e8b09944f721f2515a7`

## 1. Purpose

Specify the complete V3 stage that closes the gap between partial implementations (`v3_loop`, `discovery_candidates`, `ExecutiveDecisionService`, `MissionLoop`) and the roadmap exit criteria in `docs/architecture/05_POST_V1_ROADMAP.md` §6.

## 2. System context

```text
┌─────────────────────────────────────────────────────────────────┐
│                    JARVIS Supervisor (deterministic)             │
│  STOP/veto · leases · recovery · release pointer · wake          │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│              V3DiscoveryCycle (new orchestrator, WP3)              │
│  observe → candidates → score → portfolio → select → compile      │
└─┬──────────┬──────────┬──────────┬──────────┬──────────┬────────┘
  │          │          │          │          │          │
  ▼          ▼          ▼          ▼          ▼          ▼
Monitors  Candidates  Scoring   Executive  Mission   Outcome
(existing) (existing+) (WP2)    (extend)   Loop      (WP6)
                                              │
                                              ▼
                                    RoleDispatcher / Workers
                                    (existing Hermes/OpenHands)
```

**Reuse decision:** No second mission engine, evidence store, or scheduler. V3 adds orchestration, policy registry, portfolio projection, mission compiler, and outcome projection atop existing modules.

## 3. Component boundaries

### 3.1 V3DiscoveryCycle (NEW — WP3)

**Responsibility:** Single entry for one discovery iteration. Replaces the split where `v3_loop.run_one_loop` stops at WAIT and `runtime/worker_loop` selects without re-running observation in one atomic cycle record.

**Inputs:** `loop_id`, resource snapshot, policy version, activation epoch.

**Outputs:** `V3CycleReport` event `discovery.cycle_completed` with observe/score/select/mission/idle breakdown.

**Algorithm (deterministic shell, model judgment inside bounded steps):**

1. Safety gate: STOP, veto, lease acquire (`discovery.cycle` lease key).
2. Observe: `run_ingestion_cycle` + `poll_monitor` (existing).
3. Generate candidates: `generate_research_candidates`, `_maintenance_candidates` (existing).
4. Score: `ScoringService.score_all_eligible` (WP2) — wraps `score_candidate` with policy version.
5. Portfolio update: `PortfolioService.apply_scores` (WP2).
6. Select: `ExecutiveDecisionService.evaluate` (existing, extended to read portfolio).
7. On SELECT: `MissionCompiler.compile` (WP3) → `executive.decision_materialized`.
8. On WAIT: emit honest idle with rejection ledger.
9. Release lease; append `discovery.cycle_completed`.

**Idempotency:** `idempotency_key = discovery.cycle_completed:{loop_id}`.

**Failure:** Isolated per monitor; cycle continues with `degraded_sources` list.

### 3.2 Discovery inputs (§1 requirement)

| Monitor kind | Source | Cadence | Collection | Degrade |
|--------------|--------|---------|------------|---------|
| `vault` | Obsidian vault indexer | Per cycle | Deterministic file scan | Skip; record `monitor_unavailable` |
| `handoffs` | `docs/handoffs/` | Per cycle | File read | Skip |
| `repo` | Git log via `live_ingestion._repo_source` | Per cycle | `git log --format=%H\|%s` | Skip if not a git root |
| `system` | Ledger event types | Per cycle | SQL projection | Always available |
| `ci` | Registered READY capability only | Configurable | Connector or local artifact | Skip |
| `runtime_health` | Supervisor heartbeat projection | Per cycle | `supervisor_state` | Unknown if stale |
| `goals` | `owner_directive` GOAL events | Per cycle | `executive/goals.py` | Empty set |
| `knowledge_gaps` | `research_queue.detect_research_items` | Per cycle | STALE/CONTRADICTED/UNSUPPORTED | Skip |
| `maintenance` | `_maintenance_candidates` signals | Per cycle | Count thresholds | N/A |
| `releases` | `deployment/release.py` pointers | Per cycle | Read CURRENT/PREVIOUS | Local only |
| `cost` | `budget.usage_recorded` aggregation | Per cycle | Sum by window | Unknown if no data |

**Separation:** Monitors emit observations and evidence handles. Model judgment occurs only in `research.interpret` (optional, bounded) and team roles — never in monitor collection.

**Deduplication:** `covered_objective` + fingerprint registry (existing + extended in WP2).

**Backpressure:** `max_items`, `max_bytes`, `score_budget` per cycle (existing defaults in `v3_loop`).

### 3.3 Opportunity/candidate model (§2 requirement)

**Table:** `candidates` (existing, extended columns in migration WP1).

**Typed record `DiscoveryCandidate`:**

```python
@dataclass(frozen=True)
class DiscoveryCandidate:
    candidate_id: str                    # cand-{uuid12}
    fingerprint: str                     # sha256(canonical_objective + type + primary_entity)
    candidate_type: Literal[
        "repair", "maintenance", "research", "build", "optimization",
        "knowledge_resolution", "release", "goal_advancement"
    ]
  # Provenance
    source_observations: tuple[str, ...] # observation_ids
    evidence_refs: tuple[str, ...]       # evidence_ids
    provenance_hash: str                 # sha256 of sorted evidence_refs
  # Links
    goal_ids: tuple[str, ...]
    project_ids: tuple[str, ...]
    entity_ids: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    incident_ids: tuple[str, ...]
    release_ids: tuple[str, ...]
    prior_candidate_ids: tuple[str, ...]
    prior_mission_ids: tuple[str, ...]
  # Statement
    objective: str
    expected_outcome: str                # falsifiable
  # Scoring dimensions (0..1 unless noted)
    strategic_fit: float
    expected_value: float
    information_gain: float
    urgency: float
    confidence: float
    effort: float
    duration_hours: float
    cost_estimate: float
    risk: float
    reversibility: float
    blast_radius: float
    opportunity_cost: float
  # Dependencies
    depends_on: tuple[str, ...]          # candidate or mission ids
    required_capabilities: tuple[str, ...]
    capability_readiness: dict[str, str] # capability_id → READY|NOT_READY
  # Plans
    measurement_plan: dict
    acceptance_evidence_plan: dict
    rollback_plan: dict | None
    maintenance_expectation: str | None
  # Lifecycle
    status: CandidateStatus
    rejection_reason: str | None
    superseded_by: str | None
    expires_at: str | None
    created_from: str
    audit_identity: str
```

**Status enum (unified):**

```text
NEW → SCORED → ELIGIBLE → SELECTED → MISSION_LINKED → EXECUTED → MEASURING → CLOSED
                    ↓           ↓
                REJECTED    SUPERSEDED
                    ↓
                DEFERRED (re-eligible after cooldown)
```

Maps to existing `NEW|SCORED|APPROVED|REJECTED|EXECUTED|SUPERSEDED` with additive states `ELIGIBLE`, `MISSION_LINKED`, `MEASURING`, `CLOSED`, `DEFERRED`.

**Duplicate semantics:**

| Kind | Rule |
|------|------|
| Semantic duplicate | Same `fingerprint` and unresolved status |
| Causal duplicate | Same root `incident_id` or `unknown_id` within resolution window |
| Superseding | New evidence changes `provenance_hash`; old → SUPERSEDED |
| Recurring maintenance | Same signal class but new measurement window → new fingerprint suffix |
| Meaningfully new evidence | `provenance_hash` differs → not duplicate |

**Events:** `discovery.candidate_generated|scored|approved|rejected|superseded|outcome_recorded` (existing).

### 3.4 Evidence-based scoring (§3 requirement)

See `03_V3_SCORING_AND_PORTFOLIO.md`. Implemented as `ScoringService` reading `scoring_policy_registry` projection.

**Hard filters (reject, not penalize):** STOP, veto, illegal action, missing required capability, blast-radius violation, severity-one open defect.

### 3.5 Portfolio and selection (§4 requirement)

**Portfolio state machine:** See `02_V3_EVENTS_AND_STATE_MACHINES.md` §4.

**Selection authority:** `ExecutiveDecisionService.evaluate` remains canonical. V3 extends eligibility input to portfolio-projected candidates with `status=ELIGIBLE`.

**Reproducibility:** Same event history + `policy_version` + resource snapshot → identical `executive.decision_recorded` payload (test AC-02).

### 3.6 Autonomous mission creation (§5 requirement)

**MissionCompiler** (WP3):

**Inputs:** SELECT decision, candidate record, resource registry snapshot.

**Outputs:** `mission.created` with `created_from=candidate:{id}`, action envelopes, verifier plan, evidence plan, rollback plan, outcome plan.

**Fail-closed conditions:**

| Condition | Result |
|-----------|--------|
| Missing provenance | `executive.materialization_blocked` |
| Ambiguous target identity | blocked |
| Dirty worktree unresolved | blocked |
| Insufficient capability | blocked |
| Missing verification plan | blocked |
| Mutable action without rollback | blocked |
| Stale candidate (`expires_at` passed) | blocked |

**Reuse:** `executive/missions.py` `materialize_decision` (extend).

### 3.7 Temporary teams and role separation (§6 requirement)

| Role | Read | Propose | Mutate | Verify | Accept |
|------|------|---------|--------|--------|--------|
| scout | observations, candidates | candidates | no | no | no |
| researcher | knowledge, web (scoped) | research artifacts | worktree read | no | no |
| architect | codebase, evidence | design docs | no | no | no |
| builder | mission scope | code changes | worktree write | no | no |
| tester | build output | test results | test fixtures | no | no |
| critic | all mission artifacts | review notes | no | no | no |
| verifier | all mission artifacts | verdict | no | yes (verdict only) | no |
| repairer | failure evidence | fix | worktree write | no | no |
| documenter | mission state | docs | docs only | no | no |
| knowledge_curator | vault, claims | claim proposals | vault write (governed) | no | no |

**Binding:** `RoleDispatcher` (existing) with `agent_run` leases. Max delegation depth: 2. Max team size: 6 concurrent roles per mission.

**Fresh session:** Verifier must have `fresh_session=true` in manifest; same provider recorded as `independence=REDUCED`.

**Verdicts:** PASS, FAIL, BLOCKED, NOT_TESTED only (`verification.*` events).

### 3.8 Research-to-build loop (§7 requirement)

State machine in `02_V3_EVENTS_AND_STATE_MACHINES.md` §5. Orchestrated by `V3DiscoveryCycle` + `MissionLoop.run_until_settled`.

### 3.9 Git, release, deployment (§8 requirement)

**GitEffectAdapter** (WP5):

- Verify `remote_url`, `branch`, `expected_head` before push.
- Idempotency key per `action_id`.
- On ambiguous timeout: `effect.reconcile_pending` → provider query before retry.
- Never trust exit code alone; reconcile `git ls-remote`.

**Disposable rehearsal:** Bare remote in temp dir; acceptance gate §11.

**Reuse:** `mission/commit.py`, `workers/evidence.py`, `effects.py` journal.

### 3.10 Outcome measurement (§9 requirement)

**OutcomeService** (WP6):

- Trigger on `mission.completed` where `outcome=PASSED` only (existing regression).
- Observation windows: immediate (technical), delayed (7d, 30d configurable).
- Record `discovery.candidate_outcome_recorded` + `candidate_outcomes` projection.
- Failed hypothesis preserved; calibration via `executive.calibration_recorded`.

### 3.11 Maintenance and continuation (§10 requirement)

**Maintenance triggers:** Post-mission scan in cycle step 3b (existing `_maintenance_candidates`).

**Idle:** `ExecutiveDecision` kind=WAIT with `wait_kind=NO_ELIGIBLE_CANDIDATES` or `COMPARATIVE_WAIT`; no model loop.

### 3.12 Security and runaway control (§11 requirement)

See `06_V3_THREAT_MODEL.md`. Limits table:

| Limit | Default | Observable behavior |
|-------|---------|---------------------|
| Cycles per hour | 12 | `discovery.cycle_throttled` |
| Candidates per cycle | 20 | truncate + log |
| Score invocations | 50/cycle | deterministic only |
| Model calls per mission | 30 | mission BLOCKED |
| Repair rounds | 3 | mission BLOCKED |
| Delegation depth | 2 | dispatch rejected |
| External effects per mission | 10 | broker NOT_READY |
| Discovery context bytes | 80_000 | truncate manifest |

### 3.13 Command Center (§12 requirement)

**Additive projection fields** in `snapshot.discovery`:

- `candidates_recent`, `score_breakdown`, `selected`, `rejected_alternatives`
- `portfolio_summary`, `active_team`, `cost_window`
- `outcome_measurements`, `maintenance_due`, `idle_reason`, `next_action`

**Reuse:** `_executive_projection` pattern (ledger scan); optional `discovery_portfolio` projection table in WP1 for performance.

## 4. Compatibility map

| Existing module | V3 role | Change |
|-----------------|---------|--------|
| `v3_loop.py` | Observation steps 1–3 | Called by V3DiscoveryCycle; remove standalone WAIT return |
| `discovery_candidates.py` | Generate, score base | Extend types; policy registry |
| `executive/decisions.py` | Selection | Read portfolio ELIGIBLE set |
| `executive/missions.py` | Materialize | MissionCompiler wrapper |
| `mission/loop.py` | Execute | Unchanged |
| `runtime/worker_loop.py` | Production driver | Call V3DiscoveryCycle |
| `command_center.py` | Visibility | Add discovery section |
| `discovery_monitors.py` | Input monitors | Unchanged |
| `live_ingestion.py` | Observe | Unchanged |

## 5. Interfaces (commands/APIs)

| Command | Event emitted | Idempotency key |
|---------|---------------|-----------------|
| `run_discovery_cycle` | `discovery.cycle_started`, `discovery.cycle_completed` | `discovery.cycle:{loop_id}` |
| `register_monitor` | `discovery.monitor_registered` | `monitor:{kind}:{source_ref}` |
| `generate_candidate` | `discovery.candidate_generated` | `candidate:{fingerprint}` |
| `score_candidate` | `discovery.candidate_scored` | `score:{candidate_id}:{policy_version}` |
| `evaluate_portfolio` | `portfolio.evaluated` | `portfolio:{loop_id}:{policy_version}` |
| `executive_evaluate` | `executive.decision_recorded` | `executive.decision:{decision_id}` |
| `compile_mission` | `mission.created` | `mission:from-candidate:{candidate_id}` |
| `record_outcome` | `discovery.candidate_outcome_recorded` | `outcome:{candidate_id}:{window}` |

## 6. Observability

- Metrics: `discovery_cycles_total`, `candidates_generated`, `candidates_rejected_reason`, `selection_latency_ms`, `idle_cycles`, `outcome_calibration_error`.
- Audit: Full lineage in ledger; Command Center timeline.
- Alerts: `severity_one_defect`, `effect.reconcile_mismatch`, `portfolio.starvation`.

## 7. Rollback

- Policy version rollback: emit `scoring.policy_superseded`; old version remains in registry.
- Schema: additive migrations only; forward-compatible projections.
- Feature flag: `discovery.cycle_enabled` (default false until gate PASS).
